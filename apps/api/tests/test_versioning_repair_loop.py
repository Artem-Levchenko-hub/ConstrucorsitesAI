"""AV06.3: a deliberately broken adaptation is sent to repair and only a real
fix earns the build; exhausted or failing repair never promotes. FV030/FV031:
a recognised adaptation without its baseline fails instead of passing.

Deterministic stubs (no model). The live model canary with a QA fault hook is
catalogued as not_run in fixtures/versioning_v4/mutations.json."""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.services.max_finalization import MaxFinalizationStatus
from omnia_api.services.project_cell_executor import ProjectCellCommandRole
from tests.test_max_finalization import (
    _CLIENTS_ROUTE,
    _VISITS_ROUTE,
    _adaptation_run,
    _files,
    _new_harness,
)

PROMPT = "Верни экраны выбранной исторической версии"


def _with_files(harness, box: list[dict[str, str]]) -> None:
    """Let the test decide what the workspace holds at every finalization pass."""

    async def snapshot_files() -> dict[str, str]:
        return dict(box[0])

    handle = harness.coordinator.executor
    if dataclasses.is_dataclass(handle):
        handle = dataclasses.replace(
            handle, snapshot_files=snapshot_files, export_files=snapshot_files
        )
    else:  # NamedTuple
        handle = handle._replace(snapshot_files=snapshot_files, export_files=snapshot_files)
    harness.coordinator.executor = handle


def _broken() -> dict[str, str]:
    # The adapted v1 screens came back, but reading visits disappeared. The kit's
    # own route stays byte-identical, so it is not owned by the app here.
    return {
        **_files(),
        "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
        "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
    }


def _fixed() -> dict[str, str]:
    return {**_broken(), "src/app/api/visits/route.ts": _VISITS_ROUTE}


async def test_missing_get_is_repaired_then_built(
    db_session: AsyncSession, test_engine: AsyncEngine
):
    harness = await _new_harness(db_session, test_engine)
    await _adaptation_run(db_session, harness)
    box = [_broken()]
    _with_files(harness, box)
    details: list[str] = []

    async def repair(detail: str) -> None:
        details.append(detail)
        box[0] = _fixed()

    outcome = await harness.coordinator.finalize_with_repair(prompt=PROMPT, repair=repair)

    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert len(details) == 1 and "GET /api/visits" in details[0]
    assert "ADAPTATION CAPABILITY CHECK" in details[0]
    # The build ran only after the fix — never on the broken candidate.
    assert harness.roles.count(ProjectCellCommandRole.FULL_BUILD) == 1


async def test_comment_only_fix_is_rejected_until_attempts_run_out(
    db_session: AsyncSession, test_engine: AsyncEngine
):
    harness = await _new_harness(db_session, test_engine)
    await _adaptation_run(db_session, harness)
    box = [_broken()]
    _with_files(harness, box)
    calls = 0

    async def repair(detail: str) -> None:
        nonlocal calls
        calls += 1
        # Satisfies a naive text search, not the check: the handler is a comment.
        box[0] = {**_broken(), "src/app/api/visits/route.ts": "// export async function GET() {}\n"}

    outcome = await harness.coordinator.finalize_with_repair(prompt=PROMPT, repair=repair)

    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert "GET /api/visits" in outcome.redacted_detail
    assert calls == 2  # unchanged source after the second repair ends the loop
    assert ProjectCellCommandRole.FULL_BUILD not in harness.roles  # nothing was promoted


async def test_provider_error_during_repair_aborts_without_a_build(
    db_session: AsyncSession, test_engine: AsyncEngine
):
    harness = await _new_harness(db_session, test_engine)
    await _adaptation_run(db_session, harness)
    _with_files(harness, [_broken()])

    async def repair(detail: str) -> None:
        raise RuntimeError("provider_error: upstream rejected the request")

    try:
        await harness.coordinator.finalize_with_repair(prompt=PROMPT, repair=repair)
    except RuntimeError as exc:
        assert "provider_error" in str(exc)
    else:
        raise AssertionError("a failing repair must not be swallowed")
    assert ProjectCellCommandRole.FULL_BUILD not in harness.roles


async def test_recognised_adaptation_without_baseline_fails_and_skips_repair(
    db_session: AsyncSession, test_engine: AsyncEngine
):  # FV030
    harness = await _new_harness(db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    run.agent_state = {"restoration_adaptation": {"base_draft_snapshot_id": str(uuid.uuid4())}}
    await db_session.commit()
    _with_files(harness, [_fixed()])
    repairs: list[str] = []

    async def repair(detail: str) -> None:
        repairs.append(detail)

    outcome = await harness.coordinator.finalize_with_repair(prompt=PROMPT, repair=repair)

    assert outcome.status is MaxFinalizationStatus.FAILED
    assert outcome.redacted_detail.startswith("baseline_unavailable")
    assert repairs == [] and harness.roles == []


async def test_foreign_baseline_is_never_compared(
    db_session: AsyncSession, test_engine: AsyncEngine
):  # FV031
    import asyncio

    from omnia_api.models.project import Project
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.models.user import User
    from omnia_api.services import repo

    harness = await _new_harness(db_session, test_engine)
    stranger = User(email=f"stranger-{uuid.uuid4().hex}@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.flush()
    other = Project(
        owner_id=stranger.id, name="Other", slug=f"other-{uuid.uuid4().hex}", template="max_miniapp"
    )
    db_session.add(other)
    await db_session.flush()
    sha = await asyncio.to_thread(repo.init_from_files, other.id, _fixed(), "other")
    foreign = Snapshot(project_id=other.id, commit_sha=sha, prompt_text="other")
    db_session.add(foreign)
    await db_session.flush()
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    run.agent_state = {"restoration_adaptation": {"base_draft_snapshot_id": str(foreign.id)}}
    await db_session.commit()
    _with_files(harness, [_fixed()])

    outcome = await harness.coordinator.finalize(files=_fixed(), prompt=PROMPT)

    assert outcome.status is MaxFinalizationStatus.FAILED
    assert "another project" in outcome.redacted_detail
    assert harness.roles == []
