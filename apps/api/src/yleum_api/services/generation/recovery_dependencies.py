"""Free dependency advisories reschedule WAIT; they never authorize an effect."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation import provider_health
from yleum_api.services.generation.recovery_clock import next_dependency_check
from yleum_api.services.generation.recovery_contract import RecoveryStateV3
from yleum_api.services.generation.recovery_execution import RECOVERY_KEY, recovery_state
from yleum_api.services.generation.recovery_quiescence import _quiesced_physical_run
from yleum_api.services.generation.recovery_store import _locked_owner
from yleum_api.services.generation_deployment_drain import require_generation_admission


async def poll_due_dependency(factory: async_sessionmaker[AsyncSession], run_id: UUID) -> bool:
    """Run before execution-slot selection, with no lease or DB lock over HTTP."""
    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        if run is None:
            return False
        if run.status != "running":
            return True
        state = recovery_state(run)
        if not isinstance(state, RecoveryStateV3) or state.clock.wait_dependency is None:
            return True
        if (
            state.phase != "waiting_dependency"
            or state.next_check_at is None
            or state.next_check_at > datetime.now(UTC)
            or await _quiesced_physical_run(session, run) is None
        ):
            return False
        identity, dependency = state.identity, state.clock.wait_dependency
    advisory = await provider_health.free_health_for_run(
        run_id=run_id,
        project_id=identity.project_id,
        user_id=identity.user_id,
        dependency=dependency,
    )
    async with factory() as session:
        await require_generation_admission(session)
        run, _ = await _locked_owner(
            session, run_id=run_id, project_id=identity.project_id, user_id=identity.user_id
        )
        current = recovery_state(run)
        if current != state or await _quiesced_physical_run(session, run) is None:
            return False
        available = advisory.available_for(dependency)
        failures = state.clock.dependency_failures + (0 if available else 1)
        now = datetime.now(UTC)
        updated = state.model_copy(
            update={
                "state_revision": state.state_revision + 1,
                "next_check_at": now
                if available
                else next_dependency_check(
                    run_id,
                    failures=failures,
                    now=now,
                ),
                "clock": state.clock.model_copy(
                    update={
                        "dependency_failures": failures,
                        "wait_dependency": None if available else dependency,
                    }
                ),
            }
        )
        run.agent_state = {**run.agent_state, RECOVERY_KEY: updated.model_dump(mode="json")}
        await session.commit()
        return available
