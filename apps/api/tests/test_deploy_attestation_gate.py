from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.attestation import Attestation
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services.attestation import build_attestation
from omnia_api.services.deploy_attestation import (
    ensure_current_release_proof,
    resolve_deploy_proof,
)
from omnia_api.services.functional_gate import Check, FunctionalVerdict

pytestmark = pytest.mark.asyncio


async def _project_with_snapshot(session: AsyncSession) -> tuple[User, Project, Snapshot]:
    user = User(email="release-gate@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    project = Project(
        owner_id=user.id,
        name="Release gate",
        slug=f"release-gate-{uuid.uuid4().hex[:6]}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha="a" * 40,
        prompt_text="build",
        model_id="test",
    )
    session.add(snapshot)
    await session.flush()
    project.current_snapshot_id = snapshot.id
    await session.commit()
    return user, project, snapshot


def _passing_attestation(project: Project, snapshot: Snapshot) -> Attestation:
    check = type("Check", (), {"name": "isolation", "ok": True, "detail": "passed"})()
    verdict = type("Verdict", (), {"passed": True, "checks": [check]})()
    record = build_attestation(
        gates=[("security", verdict)],
        stack="blank",
        project_id=str(project.id),
        created_at="2026-07-31T00:00:00+00:00",
        commit_sha=snapshot.commit_sha,
    )
    return Attestation(
        project_id=project.id,
        snapshot_id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        stack="blank",
        issued_at=str(record["created_at"]),
        overall_passed=True,
        digest=str(record["digest"]),
        gates=record["gates"],
    )


async def test_exact_current_commit_requires_digest_valid_proof(
    db_session: AsyncSession,
) -> None:
    _, project, snapshot = await _project_with_snapshot(db_session)
    missing = await resolve_deploy_proof(db_session, project, None)
    assert not missing.passed
    assert missing.reason == "attestation_missing"
    assert missing.commit_sha == snapshot.commit_sha

    attestation = _passing_attestation(project, snapshot)
    db_session.add(attestation)
    await db_session.commit()
    proven = await resolve_deploy_proof(db_session, project, None)
    assert proven.passed
    assert proven.reason == "proven"

    attestation.gates[0]["checks"][0]["ok"] = False
    await db_session.commit()
    tampered = await resolve_deploy_proof(db_session, project, None)
    assert not tampered.passed
    assert tampered.reason == "digest_invalid"


async def test_missing_current_proof_is_reissued_from_exact_live_tree(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, project, snapshot = await _project_with_snapshot(db_session)
    project.template = "max_miniapp"
    await db_session.commit()
    synced: list[tuple[uuid.UUID, str, dict[str, str]]] = []

    def read_files(project_id: uuid.UUID, commit_sha: str) -> dict[str, str]:
        assert project_id == project.id
        assert commit_sha == snapshot.commit_sha
        return {"src/app/page.tsx": "export default function Page() { return null }"}

    async def status(_project_id: uuid.UUID) -> dict[str, str]:
        return {"state": "running"}

    async def hot_reload(
        *,
        project_id: uuid.UUID,
        slug: str,
        files: dict[str, str],
    ) -> dict[str, int]:
        synced.append((project_id, slug, files))
        return {"written": len(files)}

    async def release_proof(
        _project_id: uuid.UUID,
        _slug: str,
        *,
        require_max_data: bool = False,
    ) -> FunctionalVerdict:
        assert require_max_data is True
        return FunctionalVerdict(
            passed=True,
            checks=[Check("typecheck", True, "clean")],
            summary="passed",
        )

    monkeypatch.setattr(
        "omnia_api.services.deploy_attestation.repo_svc.read_files",
        read_files,
    )
    monkeypatch.setattr(
        "omnia_api.services.deploy_attestation.orchestrator_client.get_status",
        status,
    )
    monkeypatch.setattr(
        "omnia_api.services.deploy_attestation.orchestrator_client.hot_reload",
        hot_reload,
    )
    monkeypatch.setattr(
        "omnia_api.services.deploy_attestation.run_release_proof",
        release_proof,
    )

    proof = await ensure_current_release_proof(db_session, project)
    assert proof.passed
    assert proof.commit_sha == snapshot.commit_sha
    assert synced == [
        (
            project.id,
            project.slug,
            {"src/app/page.tsx": "export default function Page() { return null }"},
        )
    ]

    # A valid exact proof is idempotent and does not touch the runtime again.
    repeated = await ensure_current_release_proof(db_session, project)
    assert repeated.passed
    assert len(synced) == 1


@pytest.mark.parametrize("existing_cell", [False, True])
async def test_cell_proof_never_refreshes_legacy_runtime(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    existing_cell: bool,
) -> None:
    user, project, snapshot = await _project_with_snapshot(db_session)
    project.template = "max_miniapp"
    user.email_verified_at = datetime.now(UTC)
    user.status = "active"
    if existing_cell:
        db_session.add(ProjectCellWorkspace(
            project_id=project.id, owner_id=user.id,
            provider="docker_owner_canary", state="ready",
        ))
    # Even an old valid proof must not authorize a different runtime path.
    db_session.add(_passing_attestation(project, snapshot))
    await db_session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "project_cell_docker_canary_enabled", not existing_cell)
    monkeypatch.setattr(settings, "project_cell_canary_emails", user.email or "")

    async def forbidden(*_args, **_kwargs):
        pytest.fail("cell proof must not inspect or hot-reload a legacy runtime")

    monkeypatch.setattr("omnia_api.services.deploy_attestation.orchestrator_client.get_status",
                        forbidden)
    monkeypatch.setattr("omnia_api.services.deploy_attestation.orchestrator_client.hot_reload",
                        forbidden)
    proof = await ensure_current_release_proof(db_session, project)
    assert proof.passed is False
    assert proof.reason == "project_cell_publish_unavailable"
    explicit = await resolve_deploy_proof(db_session, project, snapshot.commit_sha)
    assert explicit.passed is False
    assert explicit.reason == "project_cell_publish_unavailable"


async def test_production_deploy_blocks_unproven_and_allows_proven(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, project, snapshot = await _project_with_snapshot(db_session)

    async def current_user() -> User:
        return user

    calls: list[uuid.UUID] = []

    async def deploy(project_id: uuid.UUID, **_: object) -> dict[str, object]:
        calls.append(project_id)
        return {"phase": "queued"}

    prod_settings = get_settings().model_copy(
        update={"env": "prod", "deploy_attestation_blocking": False}
    )
    app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr("omnia_api.routers.runtime.get_settings", lambda: prod_settings)
    monkeypatch.setattr("omnia_api.routers.runtime.orchestrator_client.deploy", deploy)
    try:
        blocked = await client.post(f"/api/projects/{project.id}/deploy", json={})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "deploy_not_proven"
        assert calls == []

        db_session.add(_passing_attestation(project, snapshot))
        await db_session.commit()
        allowed = await client.post(f"/api/projects/{project.id}/deploy", json={})
        assert allowed.status_code == 200
        assert allowed.json()["phase"] == "queued"
        assert calls == [project.id]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_production_deploy_fails_closed_when_proof_store_is_unavailable(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, project, _ = await _project_with_snapshot(db_session)

    async def current_user() -> User:
        return user

    async def unavailable(*_: object) -> object:
        raise RuntimeError("database unavailable")

    prod_settings = get_settings().model_copy(update={"env": "production"})
    app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr("omnia_api.routers.runtime.get_settings", lambda: prod_settings)
    monkeypatch.setattr("omnia_api.routers.runtime.resolve_deploy_proof", unavailable)
    try:
        response = await client.post(f"/api/projects/{project.id}/deploy", json={})
        assert response.status_code == 503
        assert response.json()["error"]["details"]["reason"] == "proof_unavailable"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
