from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.attestation import Attestation
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services.attestation import build_attestation
from omnia_api.services.deploy_attestation import resolve_deploy_proof

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
