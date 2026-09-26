from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.models.attestation import Attestation
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services.attestation import build_attestation
from yleum_api.services.deploy_attestation import (
    ensure_current_release_proof,
    resolve_deploy_proof,
)

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

    proof = await ensure_current_release_proof(db_session, project)
    assert proof.passed is False
    assert proof.reason == "project_cell_publish_unavailable"
    explicit = await resolve_deploy_proof(db_session, project, snapshot.commit_sha)
    assert explicit.passed is False
    assert explicit.reason == "project_cell_publish_unavailable"
