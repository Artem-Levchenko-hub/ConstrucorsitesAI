from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services import max_finalization as finalization
from yleum_api.services.max_finalization import MaxFinalizationCoordinator, MaxFinalizationStatus


@pytest.fixture
def loop(monkeypatch):
    now = datetime.now(UTC)
    run = GenerationRun(status="running", agent_state={}, created_at=now, started_at=now)

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(commit=AsyncMock())

    files = {"src/app.py": "first =\nsecond =\nthird =\n"}
    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.session_factory = sessions
    coordinator.executor = SimpleNamespace(
        snapshot_files=AsyncMock(side_effect=lambda: dict(files))
    )
    coordinator._raise_persisted_infrastructure_failure = AsyncMock()
    coordinator._locked_run = AsyncMock(return_value=run)
    coordinator._repair_progress_receipts = AsyncMock(return_value=frozenset())
    monkeypatch.setattr(
        finalization,
        "generation_deadline",
        lambda run: SimpleNamespace(at=now + timedelta(hours=1)),
    )
    builds = []

    async def finalize(**kwargs):
        builds.append(dict(kwargs["files"]))
        try:
            compile(kwargs["files"]["src/app.py"], "src/app.py", "exec")
        except SyntaxError as exc:
            return SimpleNamespace(
                status=MaxFinalizationStatus.NEEDS_EDIT, redacted_detail=str(exc)
            )
        return SimpleNamespace(status=MaxFinalizationStatus.COMPLETE)

    coordinator.finalize = finalize
    return coordinator, files, builds, run


async def test_three_productive_repairs_reach_real_compiler_success(loop):
    coordinator, files, builds, _ = loop
    repairs = []

    async def repair(detail):
        repairs.append(detail)
        name = ("first", "second", "third")[len(repairs) - 1]
        files["src/app.py"] = files["src/app.py"].replace(name + " =", name + " = 1")

    outcome = await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)
    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert len(repairs) == 3 and len(builds) == 4


@pytest.mark.parametrize("cycle", [False, True])
async def test_unchanged_or_aba_source_stops_without_another_build(loop, cycle):
    coordinator, files, builds, _ = loop
    initial = files["src/app.py"]
    repairs = []

    async def repair(detail):
        repairs.append(detail)
        if cycle:
            files["src/app.py"] = initial + "# B\n" if len(repairs) == 1 else initial

    outcome = await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)
    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert len(repairs) == (2 if cycle else 1)
    assert len(builds) == (2 if cycle else 1)


async def test_new_verified_gate_receipt_allows_one_unchanged_recheck_only(loop):
    coordinator, _, builds, _ = loop
    receipts = set()
    coordinator._repair_progress_receipts.side_effect = lambda files: frozenset(receipts)
    repairs = []

    async def repair(detail):
        repairs.append(detail)
        receipts.add("fast_check")

    outcome = await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)
    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert len(repairs) == 2 and len(builds) == 2


async def test_reserve_blocks_repair_without_changing_deadline(loop, monkeypatch):
    coordinator, _, builds, run = loop
    run.started_at = datetime.now(UTC) - timedelta(hours=1)
    fixed = datetime.now(UTC) + timedelta(seconds=220)
    monkeypatch.setattr(finalization, "generation_deadline", lambda run: SimpleNamespace(at=fixed))
    repair = AsyncMock()
    with pytest.raises(TimeoutError, match="before source repair"):
        await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)
    repair.assert_not_awaited()
    assert len(builds) == 1 and run.started_at is not None


@pytest.mark.parametrize(
    "failure", [RuntimeError("PROVIDER_AUTH_FAILED"), RuntimeError("SECURITY_DENIED")]
)
async def test_editor_failure_is_not_retried(loop, failure):
    coordinator, _, builds, _ = loop
    repair = AsyncMock(side_effect=failure)
    with pytest.raises(RuntimeError, match=str(failure)):
        await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)
    repair.assert_awaited_once()
    assert len(builds) == 1


@pytest.mark.parametrize(
    "valid_source,build_state", [(True, "green"), (True, "red"), (True, "stale"), (False, "green")]
)
async def test_progress_receipts_require_current_source_and_artifact(
    monkeypatch, valid_source, build_state
):
    from uuid import uuid4

    from yleum_api.services.project_cell_proofs import ProofDimension
    from yleum_api.services.promotion_permit import (
        canonical_files_digest,
        workspace_revision_digest,
    )

    files = {"src/app.py": "value = 1"}
    identity = SimpleNamespace(
        workspace_id=uuid4(),
        generation_run_id=uuid4(),
        proof_key="1" * 64,
        workspace_revision=workspace_revision_digest(files) if valid_source else "2" * 64,
    )
    proof = SimpleNamespace(proof_key=identity.proof_key)
    scalar = AsyncMock(return_value=proof)

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(scalar=scalar)

    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.session_factory = sessions
    coordinator._identity = AsyncMock(return_value=identity)
    # Use the production bundle reader, including current-artifact release validation.
    build = SimpleNamespace(
        outcome="red" if build_state == "red" else "green",
        artifact_ref="build/sha256/"
        + (canonical_files_digest(files) if build_state != "stale" else "9" * 64),
        redacted_detail=finalization._versioned_build_detail("actual compile"),
    )
    bootstrap = SimpleNamespace(outcome="green")
    runtime = SimpleNamespace(outcome="green")
    forged_release = SimpleNamespace(
        outcome="green",
        redacted_detail="new log/UUID is not evidence",
        artifact_ref="verification/sha256/" + "7" * 64,
    )

    async def find(proof, dimension, **kwargs):
        return {
            ProofDimension.BOOTSTRAP: bootstrap,
            ProofDimension.FULL_BUILD: build,
            ProofDimension.RUNTIME: runtime,
            ProofDimension.RELEASE: forged_release,
        }.get(dimension)

    coordinator._find = find
    if not valid_source:
        assert await coordinator._repair_progress_receipts(files) == frozenset()
        scalar.assert_not_awaited()
    else:
        expected = (
            {"bootstrap", "full_build", "runtime"} if build_state == "green" else {"bootstrap"}
        )
        assert await coordinator._repair_progress_receipts(files) == frozenset(expected)
        forged_release.redacted_detail = "another operation and error text"
        assert await coordinator._repair_progress_receipts(files) == frozenset(expected)
