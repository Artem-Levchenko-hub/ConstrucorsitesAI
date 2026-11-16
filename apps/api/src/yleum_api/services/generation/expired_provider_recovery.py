"""Internal confirmation of an expired reservation before worker admission."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
from yleum_api.services.generation.recovery_contract import RecoveryContractError, RecoveryStateV3
from yleum_api.services.generation.response_auth import HEADER, mint_expired_reservation_token
from yleum_api.services.generation_deadline import is_restoration_adaptation


async def seal_expired_reservation(
    identity: GenerationProviderIdentity, *, state_revision: int,
    context_sha256: str, source_sha256: str,
) -> bool:
    """Ask the gateway to seal a no-dispatch outcome; callers must recheck the SQL receipt."""
    from yleum_api.services.generation.recovery_readers import require_recovery_readers

    settings = get_settings()
    if not settings.generation_response_retention_enabled:
        return False
    try:
        await require_recovery_readers()
        token = mint_expired_reservation_token(
            identity, state_revision=state_revision, context_sha256=context_sha256,
            source_sha256=source_sha256,
        )
        url = (
            f"{settings.llm_gateway_url.rstrip('/')}"
            f"/_internal/generation-responses/{identity.attempt_id}/expire"
        )
        async with asyncio.timeout(8), httpx.AsyncClient(
            timeout=httpx.Timeout(8, connect=3), follow_redirects=False,
        ) as client:
            response = await client.post(url, headers={HEADER: token})
        if response.status_code != 200:
            return False
        value = response.json()
    except (httpx.HTTPError, TimeoutError, ValueError):
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"version", "identity", "state", "receipt"}
        and type(value["version"]) is int
        and value["version"] == 1
        and value["identity"] == identity.to_wire()
        and value["state"] == "expired_before_dispatch"
        and isinstance(value["receipt"], dict)
        and value["receipt"].get("kind") == "gateway_expired_before_dispatch"
        and value["receipt"].get("attempt_id") == str(identity.attempt_id)
        and value["receipt"].get("request_sha256") == identity.request_sha256
        and value["receipt"].get("source_sha256") == source_sha256
    )


def _owned_repair_budget(run: GenerationRun, state: RecoveryStateV3) -> bool:
    repair_calls = state.execution.budget.repair_calls
    if repair_calls != 3:
        return repair_calls in {1, 2}

    from yleum_api.services.generation.expired_native_finalization import CATALOG_REPAIR_RESUME_KEY

    marker = run.agent_state.get(CATALOG_REPAIR_RESUME_KEY)
    if (
        not isinstance(marker, dict)
        or set(marker) != {"version", "segment_seq", "deadline", "predecessor_attachment"}
        or type(marker["version"]) is not int or marker["version"] != 1
        or type(marker["segment_seq"]) is not int
        or marker["segment_seq"] != state.clock.segment_seq
        or not isinstance(marker["deadline"], str)
        or not (marker["predecessor_attachment"] is None
                or isinstance(marker["predecessor_attachment"], dict))
    ):
        return False
    try:
        deadline = datetime.fromisoformat(marker["deadline"])
    except ValueError:
        return False
    return (
        deadline.utcoffset() == timedelta(0)
        and deadline.isoformat() == marker["deadline"]
        and state.clock.segment_deadline <= deadline
    )


def resumable_expired_repair(run: GenerationRun, state: RecoveryStateV3) -> bool:
    execution = state.execution
    return (
        run.status == "running"
        and run.execution_backend == "worker"
        and run.response_mode in {"build", "edit"}
        and not is_restoration_adaptation(run)
        and state.phase in {"working", "waiting_dependency", "reconciling"}
        and execution.stage == "finalization"
        and execution.substep == "response_wait"
        and execution.physical_state == "released"
        and execution.release_operation_id is not None
        and execution.execution_context_ref is not None
        and execution.execution_context_ref == state.context_ref
        and execution.next_tool_index == 0
        and execution.response_sha256 is None
        and _owned_repair_budget(run, state)
        and state.binding is not None
        and state.clock.stage == "repair"
        and state.clock.repair_granted
        and not state.unresolved_operation_ids
    )


async def dispatch_window_expired(
    session: AsyncSession, state: RecoveryStateV3, attempt: GenerationProviderAttempt,
    *, now: datetime,
) -> bool:
    from yleum_api.services.generation.recovery_reconciliation import _dispatch_plan

    cursor = state.execution
    root = await session.get(
        GenerationRecoveryEffect, cursor.provider_effect_id,
    ) if cursor.provider_effect_id is not None else None
    if (
        attempt.phase != "reserved"
        or attempt.provider_call_id is not None
        or attempt.parent_attempt_id is not None
        or root is None
        or root.provider_attempt_id != attempt.attempt_id
        or root.effect_id != cursor.pending_effect_id
        or root.state != "prepared"
        or root.effect_ordinal != 0
        or not isinstance(root.pre_binding, dict)
    ):
        return False
    try:
        plan = _dispatch_plan(root.pre_binding)
    except (TypeError, ValueError):
        return False
    return plan.kind == "native" and plan.outer_attempt == 0 and plan.deadline <= now


async def settle_expired_repair_before_claim(
    factory: async_sessionmaker[AsyncSession], run_id: UUID,
) -> None:
    """Complete gateway-side sealing before the worker acquires the same run lock."""
    from yleum_api.services.generation.recovery_execution import (
        _response_wait_attempt,
        recovery_state,
    )

    now = datetime.now(UTC)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        if run is None or run.status != "running":
            return
        try:
            state = recovery_state(run)
        except RecoveryContractError:
            return
        if (
            not isinstance(state, RecoveryStateV3)
            or not resumable_expired_repair(run, state)
            or (state.next_check_at is not None and state.next_check_at > now)
        ):
            return
        attempt = await _response_wait_attempt(session, state)
        if attempt is None or not await dispatch_window_expired(session, state, attempt, now=now):
            return
        project = await session.get(Project, run.project_id)
        if project is None or project.owner_id != run.user_id or project.template != "max_miniapp":
            return
        identity = GenerationProviderIdentity(
            attempt.run_id, attempt.project_id, attempt.user_id, attempt.attempt_id,
            attempt.prompt_hash, attempt.request_sha256,
        )
        binding, context_ref = state.binding, state.context_ref
        if binding is None or context_ref is None:
            return

    await seal_expired_reservation(
        identity, state_revision=state.state_revision,
        context_sha256=context_ref.sha256, source_sha256=binding.source_sha256,
    )
