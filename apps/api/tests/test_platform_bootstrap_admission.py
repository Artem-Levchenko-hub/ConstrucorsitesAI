"""Current core admission precedes only a new provider effect, never reconciliation."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_generation_recovery_reconciliation import bootstrap
from yleum_api.core.config import Settings
from yleum_api.services import agent_builder, agent_native
from yleum_api.services.generation import recovery_execution as ex
from yleum_api.services.generation import recovery_reconciliation as rec
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    ContinuationClaim,
    RecoveryContractError,
    decode_recovery,
)
from yleum_api.services.max_finalization import MaxFinalizationCoordinator
from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError
from yleum_api.services.project_cell_proofs import ProofDimension


def driver_fixture(monkeypatch, *, reconcile=False):
    state = decode_recovery(bootstrap())
    claim = ContinuationClaim(
        run_id=state.identity.run_id, token=uuid4(), attempt_seq=0, worker_id="test",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    ref = CheckpointRef(object_key="test/context", sha256="a" * 64)
    frame = {"version": 1, "kind": "native", "value": {"step": 0}}
    state = state.model_copy(update={
        "continuation_claim": claim, "state_revision": 1,
        "execution": state.execution.model_copy(update={
            "stage": "agent", "substep": "response_wait" if reconcile else "request_ready",
            "logical_step_seq": 5, "execution_context_ref": ref,
        }),
    })
    driver = ex.ExecutionDriver(None, state, {"active_frame": frame if reconcile else None}, None)
    saves = []

    async def save(execution, *, context, phase="working"):
        saves.append(execution)
        driver.state = driver.state.model_copy(update={
            "execution": execution, "state_revision": driver.state.state_revision + 1,
        })
        driver.context = context

    monkeypatch.setattr(driver, "save", save)
    return driver, frame, saves


async def test_charged_response_reconciliation_precedes_current_bootstrap(monkeypatch):
    driver, frame, saves = driver_fixture(monkeypatch, reconcile=True)
    trace = []

    async def admission():
        trace.append("bootstrap")

    async def invoke():
        trace.append("reconcile" if rec._PROVIDER_EFFECT.get().reconcile_only else "new")
        return "response"

    driver.before_fresh_provider = admission
    await driver.provider(frame, invoke)
    assert trace == ["reconcile"] and saves == []
    assert driver.before_fresh_provider is admission
    await driver.provider({**frame, "value": {"step": 1}}, invoke)
    assert trace == ["reconcile", "bootstrap", "new"]
    assert len(saves) == 1 and driver.before_fresh_provider is None
    # Invocation-local readiness never becomes checkpoint authority.
    assert "before_fresh_provider" not in driver.state.model_dump_json()


@pytest.mark.parametrize("failure", ["red", "lost_claim", "clock", "cancel"])
async def test_failed_admission_cannot_reserve_or_dispatch_and_remains_required(
    monkeypatch, failure,
):
    driver, frame, saves = driver_fixture(monkeypatch)
    original = driver.state
    error = ProjectCellInfrastructureError("migration_reconciliation_required")
    calls = 0

    async def admission():
        nonlocal calls
        calls += 1
        if failure == "red":
            raise error
        if failure == "lost_claim":
            driver.state = driver.state.model_copy(update={"continuation_claim": None})

    async def boundary():
        if calls and failure in {"clock", "cancel"}:
            raise RecoveryContractError(failure)

    driver.before_fresh_provider = admission
    monkeypatch.setattr(driver, "soft_boundary", boundary)
    invoke = AsyncMock()
    with pytest.raises((ProjectCellInfrastructureError, RecoveryContractError)) as caught:
        await driver.provider(frame, invoke)
    if failure == "red":
        assert caught.value is error
    assert calls == 1 and driver.before_fresh_provider is admission
    assert saves == [] and driver.state.execution == original.execution
    invoke.assert_not_awaited()
    assert rec._PROVIDER_EFFECT.get() is None


async def test_bootstrap_advances_claim_and_revision_before_fresh_dispatch(monkeypatch):
    driver, frame, saves = driver_fixture(monkeypatch)
    renewed = driver.state.continuation_claim.model_copy(update={"token": uuid4()})

    async def admission():
        driver.state = driver.state.model_copy(update={
            "state_revision": 40, "continuation_claim": renewed,
            "execution": driver.state.execution.model_copy(update={"logical_step_seq": 17}),
        })

    async def invoke():
        cursor = rec._PROVIDER_EFFECT.get()
        assert cursor.claim == renewed
        assert cursor.state_revision == 41 and cursor.logical_step_seq == 18

    driver.before_fresh_provider = admission
    await driver.provider(frame, invoke)
    assert len(saves) == 1 and driver.state.state_revision == 41


@pytest.mark.parametrize("cached", [False, True])
async def test_bootstrap_only_gate_uses_normal_proof_without_fast_check(cached):
    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.executor = NS(prove_restoration_adaptation=None)
    coordinator._reconcile_tool_commands = AsyncMock()
    coordinator._raise_persisted_infrastructure_failure = AsyncMock()
    identity, proof, result = NS(), NS(), NS(outcome="green")
    coordinator._normal_fast_check_identity = AsyncMock(return_value=identity)
    coordinator._proof = AsyncMock(return_value=proof)
    coordinator._find = AsyncMock(return_value=result if cached else None)
    coordinator._run_bootstrap = AsyncMock(return_value=(result, identity, proof))
    coordinator._counter = AsyncMock()
    coordinator._run_role_result = AsyncMock()
    assert await coordinator.ensure_bootstrap() is result
    coordinator._find.assert_awaited_once_with(proof, ProofDimension.BOOTSTRAP)
    assert coordinator._run_bootstrap.await_count == int(not cached)
    coordinator._run_role_result.assert_not_awaited()


async def test_adaptation_bootstrap_guard_does_not_initialize_historical_database():
    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.executor = NS(prove_restoration_adaptation=AsyncMock())
    coordinator._reconcile_tool_commands = AsyncMock()
    coordinator._raise_persisted_infrastructure_failure = AsyncMock()
    coordinator._normal_bootstrap = AsyncMock()
    assert await coordinator.ensure_bootstrap() is None
    coordinator._normal_bootstrap.assert_not_awaited()


@pytest.mark.parametrize("native", [False, True])
async def test_real_loop_preserves_bootstrap_infrastructure_cause_before_provider(
    monkeypatch, native,
):
    driver, _frame, saves = driver_fixture(monkeypatch)
    error = ProjectCellInfrastructureError("migration_reconciliation_required")
    driver.before_fresh_provider = AsyncMock(side_effect=error)
    provider, execute = AsyncMock(), AsyncMock()
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    monkeypatch.setattr(agent_native, "get_settings", lambda: Settings(
        database_url="postgresql://unused:unused@127.0.0.1:1/unused", jwt_secret="test-only",
    ))
    with pytest.raises(ProjectCellInfrastructureError) as caught:
        if native:
            await agent_native.run_native_build(
                system="s", task="t", execute=execute, max_steps=2, continuation=driver,
            )
        else:
            await agent_builder.run_agent_build(
                system_prompt="s", user_prompt="t", model="synthetic", execute=execute,
                max_steps=2, complete=provider, continuation=driver,
            )
    assert caught.value is error
    provider.assert_not_awaited()
    assert not any(item.provider_effect_id is not None for item in saves)
