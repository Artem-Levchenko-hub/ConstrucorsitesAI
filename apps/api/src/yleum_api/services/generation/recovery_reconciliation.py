"""Bind logical effects to their existing owning journals without replaying them."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
from yleum_api.services.generation.recovery_contract import (
    ContinuationClaim,
    RecoveryContractError,
    RecoveryStateV2,
    Sha256,
    decode_recovery,
)
from yleum_api.services.generation.recovery_execution import _require_session_owner
from yleum_api.services.generation_deployment_drain import require_generation_admission


class ProviderDispatchPlan(BaseModel):
    """Arithmetic only, never permission to repeat an uncertain dispatch."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: int = 1
    clock_basis: Literal["utc"] = "utc"
    kind: Literal["chat", "sse", "native"]
    deadline: AwareDatetime
    outer_attempt: NonNegativeInt
    outer_allowance: PositiveInt
    wire_allowance: PositiveInt

    @model_validator(mode="after")
    def valid_arithmetic(self) -> Self:
        if self.version != 1 or self.outer_attempt >= self.outer_allowance:
            raise ValueError("invalid original provider arithmetic")
        return self


class ProviderWireReservation(ProviderDispatchPlan):
    root_effect_id: UUID
    provider_effect_id: UUID
    attempt_id: UUID
    parent_attempt_id: UUID | None
    request_sha256: Sha256
    wire_attempt: NonNegativeInt

    @model_validator(mode="after")
    def valid_wire(self) -> Self:
        if self.wire_attempt >= self.wire_allowance:
            raise ValueError("invalid original wire arithmetic")
        return self


@dataclass(slots=True)
class _DispatchState:
    plan: ProviderDispatchPlan | None = None
    wire_used: int = 0
    resume_authorized: bool = False
    history_plan: ProviderDispatchPlan | None = None
    outer_advanced: bool = False


@dataclass(frozen=True, slots=True)
class ProviderEffectCursor:
    claim: ContinuationClaim
    state_revision: int
    logical_step_seq: int
    reconcile_only: bool = False
    outer_attempt: int = 0
    outer_allowance: int = 1
    call_deadline: datetime | None = None
    dispatch: _DispatchState = field(default_factory=_DispatchState, compare=False)

    @property
    def root_effect_id(self) -> UUID:
        return uuid5(self.claim.run_id, f"recovery-provider-v1:{self.logical_step_seq}")


_PROVIDER_EFFECT: ContextVar[ProviderEffectCursor | None] = ContextVar(
    "generation_provider_effect",
    default=None,
)


def has_provider_effect_context() -> bool:
    return _PROVIDER_EFFECT.get() is not None


def prospective_provider_attempt() -> tuple[UUID, int] | None:
    """Identity only; prepare_provider_effect still validates current SQL ownership."""
    cursor = _PROVIDER_EFFECT.get()
    if cursor is None:
        return None
    return uuid5(cursor.root_effect_id, "provider-attempt-v1"), cursor.outer_attempt


def provider_reconciliation_requested() -> bool:
    cursor = _PROVIDER_EFFECT.get()
    return cursor is not None and cursor.reconcile_only and not cursor.dispatch.resume_authorized


def requires_retained_provider(agent_state: object) -> bool:
    raw = agent_state if isinstance(agent_state, dict) else {}
    if "recovery_v1" not in raw:
        return False
    state = decode_recovery(raw["recovery_v1"])
    if state is None:
        raise RecoveryContractError("present recovery namespace is corrupt")
    return isinstance(state, RecoveryStateV2)


@contextmanager
def provider_effect_scope(cursor: ProviderEffectCursor) -> Iterator[None]:
    """Internal caller context; SQL/session ownership is still checked per intent."""
    token = _PROVIDER_EFFECT.set(cursor)
    try:
        yield
    finally:
        _PROVIDER_EFFECT.reset(token)


def begin_provider_dispatch(
    kind: Literal["chat", "sse", "native"],
    *,
    allowance: int,
    seconds: float,
) -> None:
    cursor = _PROVIDER_EFFECT.get()
    if cursor is None:
        return
    if cursor.dispatch.plan is not None:
        raise RecoveryContractError("dispatch arithmetic was already initialized")
    cursor.dispatch.plan = ProviderDispatchPlan(
        kind=kind,
        deadline=min(
            datetime.now(UTC) + timedelta(seconds=seconds),
            cursor.call_deadline or datetime.max.replace(tzinfo=UTC),
        ),
        outer_attempt=cursor.outer_attempt,
        outer_allowance=cursor.outer_allowance,
        wire_allowance=allowance,
    )


def provider_dispatch_remaining() -> float | None:
    cursor = _PROVIDER_EFFECT.get()
    if cursor is None:
        return None
    plan = cursor.dispatch.plan
    if plan is None:
        raise RecoveryContractError("provider dispatch clock is unavailable")
    remaining = (plan.deadline - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise RecoveryContractError("original provider deadline expired")
    return remaining


async def _lock_effect_namespace(session: AsyncSession, run_id: UUID, seq: int) -> None:
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(1195724367, hashtext(:key))"),
        {"key": f"recovery-v2:{run_id}:{seq}"},
    )
    if acquired is not True:
        raise RecoveryContractError("logical effect reservation is already in progress")


async def _next_effect_ordinal(session: AsyncSession, run_id: UUID, seq: int) -> int:
    # Caller holds the logical root advisory transaction lock. Provider children,
    # wire reservations and tool effects share one ordering, not adjacent chains.
    value = await session.scalar(
        select(func.max(GenerationRecoveryEffect.effect_ordinal)).where(
            GenerationRecoveryEffect.run_id == run_id,
            GenerationRecoveryEffect.logical_step_seq == seq,
        )
    )
    return 0 if value is None else value + 1


def _dispatch_plan(binding: object) -> ProviderDispatchPlan:
    if type(binding) is not dict or set(binding) != {"source", "dispatch_v1"}:
        raise RecoveryContractError("provider dispatch arithmetic is unavailable")
    import json

    return ProviderDispatchPlan.model_validate_json(json.dumps(binding["dispatch_v1"]), strict=True)


async def _latest_provider_identity(
    session: AsyncSession,
    cursor: ProviderEffectCursor,
    *,
    project_id: UUID,
    user_id: UUID,
    prompt_hash: str,
    digest: str,
) -> GenerationProviderIdentity:
    """Validate the provider/wire chain within the shared logical-effect namespace."""
    import json

    from yleum_api.services.generation.provider_attempts import require_closed_provider_parent

    rows = list(
        await session.scalars(
            select(GenerationRecoveryEffect)
            .where(
                GenerationRecoveryEffect.run_id == cursor.claim.run_id,
                GenerationRecoveryEffect.logical_step_seq == cursor.logical_step_seq,
                # Tool observations share this sequence but have their own immutable
                # inputs and receipt loader. A mislabeled provider link still fails
                # below; every progress row must retain its strict dispatch_v1 tag.
                or_(
                    GenerationRecoveryEffect.kind.in_(("provider", "progress")),
                    GenerationRecoveryEffect.provider_attempt_id.is_not(None),
                ),
            )
            .order_by(GenerationRecoveryEffect.effect_ordinal)
        )
    )
    previous: GenerationProviderIdentity | None = None
    previous_effect: GenerationRecoveryEffect | None = None
    providers: dict[UUID, GenerationProviderIdentity] = {}
    parents: dict[UUID, UUID | None] = {}
    wire_counts: dict[int, int] = {}
    plans: dict[int, ProviderDispatchPlan] = {}
    latest_plan: ProviderDispatchPlan | None = None
    last_wire: GenerationRecoveryEffect | None = None
    for row in rows:
        if (
            row.project_id != project_id
            or row.user_id != user_id
            or row.input_digest != digest
            or row.payload_version != 1
        ):
            raise RecoveryContractError("provider history identity conflicts")
        if row.kind == "provider":
            expected_id = (
                uuid5(previous_effect.effect_id, "definitive-refusal-child-v1")
                if previous_effect
                else cursor.root_effect_id
            )
            if row.effect_id != expected_id or row.parent_effect_id != (
                previous_effect.effect_id if previous_effect else None
            ):
                raise RecoveryContractError("provider history is not one immutable chain")
            attempt = await session.get(GenerationProviderAttempt, row.provider_attempt_id)
            if attempt is None or any(
                getattr(attempt, key) != value
                for key, value in (
                    ("run_id", cursor.claim.run_id),
                    ("project_id", project_id),
                    ("user_id", user_id),
                    ("prompt_hash", prompt_hash),
                    ("request_sha256", digest),
                    ("parent_attempt_id", previous.attempt_id if previous else None),
                )
            ):
                raise RecoveryContractError("provider history journal conflicts")
            identity = GenerationProviderIdentity(
                cursor.claim.run_id,
                project_id,
                user_id,
                attempt.attempt_id,
                prompt_hash,
                digest,
            )
            if previous is not None:
                await require_closed_provider_parent(session, previous, identity)
            if cursor.dispatch.plan is not None:
                plan = _dispatch_plan(row.pre_binding)
                if (
                    plan.kind != cursor.dispatch.plan.kind
                    or plan.wire_allowance != cursor.dispatch.plan.wire_allowance
                    or plan.outer_allowance != cursor.outer_allowance
                ):
                    raise RecoveryContractError("provider history policy conflicts")
                latest_plan = plan
            previous, previous_effect = identity, row
            providers[row.effect_id] = identity
            parents[row.effect_id] = attempt.parent_attempt_id
        elif row.kind == "progress":
            if type(row.pre_binding) is not dict or set(row.pre_binding) != {"dispatch_v1"}:
                raise RecoveryContractError("unknown provider progress subkind")
            bound = ProviderWireReservation.model_validate_json(
                json.dumps(row.pre_binding["dispatch_v1"]),
                strict=True,
            )
            linked = providers.get(bound.provider_effect_id)
            if (
                linked is None
                or bound.attempt_id != linked.attempt_id
                or bound.parent_attempt_id != parents.get(bound.provider_effect_id)
                or bound.root_effect_id != cursor.root_effect_id
                or bound.request_sha256 != digest
                or row.provider_attempt_id is not None
                or row.parent_effect_id != bound.provider_effect_id
                or row.effect_id
                != uuid5(
                    cursor.root_effect_id, f"wire-v1:{bound.outer_attempt}:{bound.wire_attempt}"
                )
                or bound.wire_attempt != wire_counts.get(bound.outer_attempt, 0)
            ):
                raise RecoveryContractError("wire history is incomplete or conflicts")
            wire_plan = ProviderDispatchPlan(
                **{key: getattr(bound, key) for key in ProviderDispatchPlan.model_fields},
            )
            linked_row = await session.get(GenerationRecoveryEffect, bound.provider_effect_id)
            if linked_row is None:
                raise RecoveryContractError("wire history provider disappeared")
            provider_plan = _dispatch_plan(linked_row.pre_binding)
            if (
                wire_plan.kind != provider_plan.kind
                or wire_plan.wire_allowance != provider_plan.wire_allowance
                or wire_plan.outer_allowance != provider_plan.outer_allowance
                or wire_plan.outer_attempt < provider_plan.outer_attempt
            ):
                raise RecoveryContractError("wire history policy changed")
            if bound.outer_attempt in plans:
                if plans[bound.outer_attempt] != wire_plan:
                    raise RecoveryContractError("wire history clock changed within one call")
            elif bound.outer_attempt == 0:
                if provider_plan != wire_plan:
                    raise RecoveryContractError("wire root clock changed")
                plans[0] = wire_plan
            else:
                if bound.outer_attempt - 1 not in plans or last_wire is None:
                    raise RecoveryContractError("outer retry history is incomplete")
                await _require_safe_wire_transition(session, last_wire, linked)
                plans[bound.outer_attempt] = wire_plan
            latest_plan, last_wire = wire_plan, row
            wire_counts[bound.outer_attempt] = bound.wire_attempt + 1
        else:
            raise RecoveryContractError("unsupported provider history entry")
    if previous is None or previous_effect is None:
        raise RecoveryContractError("provider history has no owning attempt")
    if cursor.dispatch.plan is not None:
        if latest_plan is None:
            raise RecoveryContractError("provider history has no original clock")
        cursor.dispatch.history_plan = latest_plan
        cursor.dispatch.wire_used = wire_counts.get(latest_plan.outer_attempt, 0)
    return previous


async def _require_safe_wire_transition(
    session: AsyncSession,
    prior: GenerationRecoveryEffect,
    identity: GenerationProviderIdentity,
) -> None:
    import json

    from yleum_api.services.generation.provider_attempts import require_closed_provider_parent

    if (
        prior.kind != "progress"
        or type(prior.pre_binding) is not dict
        or set(prior.pre_binding) != {"dispatch_v1"}
        or prior.post_binding is not None
    ):
        raise RecoveryContractError("wire cannot be continued after delivery or unknown evidence")
    bound = ProviderWireReservation.model_validate_json(
        json.dumps(prior.pre_binding["dispatch_v1"]),
        strict=True,
    )
    if bound.attempt_id == identity.attempt_id:
        if prior.state != "confirmed" or prior.receipt_ref != {
            "version": 1,
            "kind": "gateway_preconnect",
            "attempt_id": str(identity.attempt_id),
        }:
            raise RecoveryContractError("wire has no committed preconnect outcome")
    else:
        attempt = await session.get(GenerationProviderAttempt, identity.attempt_id)
        if attempt is None or attempt.parent_attempt_id != bound.attempt_id:
            raise RecoveryContractError("wire is not the exact financial parent")
        await require_closed_provider_parent(
            session,
            GenerationProviderIdentity(
                identity.run_id,
                identity.project_id,
                identity.user_id,
                bound.attempt_id,
                identity.prompt_hash,
                identity.request_sha256,
            ),
            identity,
        )


async def prepare_provider_effect(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    prompt_hash: str,
    agent_state: object,
    digest: str,
    parent: GenerationProviderIdentity | None,
    existing: GenerationProviderIdentity | None = None,
) -> tuple[GenerationRecoveryEffect | None, GenerationProviderIdentity | None]:
    """Return a new unflushed link or the exact already-committed attempt.

    Does not lock run/project rows: a legacy caller may hold them in its own
    transaction. Dedicated session ownership and the committed claim are required;
    the gateway independently checks current ownership before physical dispatch.
    """
    raw = agent_state if isinstance(agent_state, dict) else {}
    if "recovery_v1" not in raw:
        if _PROVIDER_EFFECT.get() is not None:
            raise RecoveryContractError("provider cursor has no durable state")
        return None, None
    state = decode_recovery(raw["recovery_v1"])
    if state is None:
        raise RecoveryContractError("present recovery namespace is corrupt")
    if not isinstance(state, RecoveryStateV2):
        if _PROVIDER_EFFECT.get() is not None:
            raise RecoveryContractError("provider cursor requires explicit v2")
        return None, None
    cursor = _PROVIDER_EFFECT.get()
    if (
        cursor is None
        or state.phase != "working"
        or state.continuation_claim != cursor.claim
        or state.state_revision != cursor.state_revision
        or state.execution.logical_step_seq != cursor.logical_step_seq
        or (
            state.execution.pending_effect_id != cursor.root_effect_id
            and not (
                cursor.reconcile_only
                and state.execution.substep in {"tool_ready", "tool_wait"}
                and state.execution.provider_effect_id == cursor.root_effect_id
            )
        )
        or state.identity.run_id != run_id
        or cursor.claim.run_id != run_id
        or state.identity.project_id != project_id
        or state.identity.user_id != user_id
        or state.identity.prompt_hash != prompt_hash
    ):
        raise RecoveryContractError("provider effect cursor is not currently owned")
    await _require_session_owner(run_id)
    await require_generation_admission(session)
    # Per-call arbitration, not a global lock. Unknown/concurrent writes fail
    # closed; the original deterministic key remains the only recovery target.
    await _lock_effect_namespace(session, run_id, cursor.logical_step_seq)
    if provider_reconciliation_requested() and existing is None and parent is None:
        existing = await _latest_provider_identity(
            session,
            cursor,
            project_id=project_id,
            user_id=user_id,
            prompt_hash=prompt_hash,
            digest=digest,
        )
    if (
        not provider_reconciliation_requested()
        and cursor.outer_attempt > 0
        and existing is None
        and parent is None
    ):
        latest = await _latest_provider_identity(
            session,
            cursor,
            project_id=project_id,
            user_id=user_id,
            prompt_hash=prompt_hash,
            digest=digest,
        )
        old, plan = cursor.dispatch.history_plan, cursor.dispatch.plan
        if (
            old is None
            or plan is None
            or plan.outer_attempt != old.outer_attempt + 1
            or plan.outer_allowance != old.outer_allowance
            or cursor.dispatch.wire_used == 0
        ):
            raise RecoveryContractError("outer retry arithmetic conflicts")
        prior_wire = await session.get(
            GenerationRecoveryEffect,
            uuid5(
                cursor.root_effect_id,
                f"wire-v1:{old.outer_attempt}:{cursor.dispatch.wire_used - 1}",
            ),
        )
        if prior_wire is None or prior_wire.post_binding is not None:
            raise RecoveryContractError("outer retry has no undelivered terminal wire")
        if prior_wire.state == "confirmed" and prior_wire.receipt_ref == {
            "version": 1,
            "kind": "gateway_preconnect",
            "attempt_id": str(latest.attempt_id),
        }:
            existing = latest
        else:
            from yleum_api.services.generation.provider_attempts import (
                require_closed_provider_parent,
            )

            await require_closed_provider_parent(session, latest, latest)
            parent = latest
        cursor.dispatch.outer_advanced = True
    if existing is not None:
        saved = await session.get(GenerationProviderAttempt, existing.attempt_id)
        if saved is None:
            raise RecoveryContractError("existing provider attempt is missing")
        saved_parent = None
        if saved.parent_attempt_id is not None:
            row = await session.get(GenerationProviderAttempt, saved.parent_attempt_id)
            if row is None:
                raise RecoveryContractError("existing provider parent is missing")
            saved_parent = GenerationProviderIdentity(
                row.run_id,
                row.project_id,
                row.user_id,
                row.attempt_id,
                row.prompt_hash,
                row.request_sha256,
            )
        if parent is not None and parent != saved_parent:
            raise RecoveryContractError("existing provider parent conflicts")
        parent = saved_parent
    parent_effect = None
    ordinal = 0
    effect_id = cursor.root_effect_id
    if parent is not None:
        parent_effect = await session.scalar(
            select(GenerationRecoveryEffect).where(
                GenerationRecoveryEffect.provider_attempt_id == parent.attempt_id,
            )
        )
        if (
            parent_effect is None
            or parent_effect.run_id != run_id
            or parent_effect.project_id != project_id
            or parent_effect.user_id != user_id
            or parent_effect.kind != "provider"
            or parent_effect.input_digest != digest
            or parent_effect.logical_step_seq != cursor.logical_step_seq
        ):
            raise RecoveryContractError("provider child has no owned logical parent")
        ordinal = await _next_effect_ordinal(session, run_id, cursor.logical_step_seq)
        effect_id = uuid5(parent_effect.effect_id, "definitive-refusal-child-v1")
    previous = await session.get(GenerationRecoveryEffect, effect_id)
    if existing is not None and (
        previous is None or previous.provider_attempt_id != existing.attempt_id
    ):
        raise RecoveryContractError("existing provider has no exact logical effect")
    if previous is not None:
        if (
            previous.run_id != run_id
            or previous.project_id != project_id
            or previous.user_id != user_id
            or previous.kind != "provider"
            or previous.input_digest != digest
            or previous.logical_step_seq != cursor.logical_step_seq
            or (parent_effect is None and previous.effect_ordinal != 0)
            or (
                parent_effect is not None
                and previous.effect_ordinal <= parent_effect.effect_ordinal
            )
            or previous.parent_effect_id != (parent_effect.effect_id if parent_effect else None)
        ):
            raise RecoveryContractError("provider logical effect was rebound")
        attempt = await session.get(GenerationProviderAttempt, previous.provider_attempt_id)
        if attempt is None or any(
            getattr(attempt, key) != value
            for key, value in (
                ("run_id", run_id),
                ("project_id", project_id),
                ("user_id", user_id),
                ("prompt_hash", prompt_hash),
                ("request_sha256", digest),
                ("parent_attempt_id", parent.attempt_id if parent else None),
            )
        ):
            raise RecoveryContractError("provider effect lost its owning attempt")
        if cursor.dispatch.plan is not None:
            saved_plan = _dispatch_plan(previous.pre_binding)
            if provider_reconciliation_requested():
                # Only the persisted absolute UTC deadline crosses process origin.
                cursor.dispatch.plan = cursor.dispatch.history_plan or saved_plan
            elif saved_plan != cursor.dispatch.plan and not cursor.dispatch.outer_advanced:
                raise RecoveryContractError("provider dispatch arithmetic conflicts")
        return None, GenerationProviderIdentity(
            run_id,
            project_id,
            user_id,
            attempt.attempt_id,
            prompt_hash,
            digest,
        )
    if provider_reconciliation_requested():
        raise RecoveryContractError("provider reconciliation has no committed owning attempt")
    return GenerationRecoveryEffect(
        effect_id=effect_id,
        run_id=run_id,
        project_id=project_id,
        user_id=user_id,
        logical_step_seq=cursor.logical_step_seq,
        effect_ordinal=ordinal,
        kind="provider",
        input_digest=digest,
        attempt_seq=cursor.claim.attempt_seq,
        claim_token=cursor.claim.token,
        parent_effect_id=parent_effect.effect_id if parent_effect else None,
        pre_binding=(
            {
                "source": state.binding.model_dump(mode="json") if state.binding else None,
                "dispatch_v1": cursor.dispatch.plan.model_dump(mode="json"),
            }
            if cursor.dispatch.plan is not None
            else state.binding.model_dump(mode="json")
            if state.binding
            else None
        ),
    ), None


async def reserve_provider_wire(
    factory: async_sessionmaker[AsyncSession],
    identity: GenerationProviderIdentity | None,
    *,
    wire_attempt: int,
) -> UUID | None:
    """Commit arithmetic consumption before HTTP; a committed row cannot be replayed."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project

    cursor = _PROVIDER_EFFECT.get()
    if cursor is None:
        return None
    if identity is None or provider_reconciliation_requested() or cursor.dispatch.plan is None:
        raise RecoveryContractError("wire dispatch has no current execution arithmetic")
    plan = cursor.dispatch.plan
    if (
        plan.outer_attempt >= plan.outer_allowance
        or wire_attempt < 0
        or wire_attempt >= plan.wire_allowance
        or datetime.now(UTC) >= plan.deadline
    ):
        raise RecoveryContractError("original provider allowance exhausted")
    async with factory() as session:
        run = await session.get(GenerationRun, identity.run_id)
        project = await session.get(Project, identity.project_id)
        if (
            run is None
            or project is None
            or run.status != "running"
            or project.owner_id != identity.user_id
            or run.user_id != identity.user_id
            or run.project_id != identity.project_id
            or run.prompt_hash != identity.prompt_hash
        ):
            raise RecoveryContractError("wire current owner is unavailable")
        _, exact = await prepare_provider_effect(
            session,
            run_id=identity.run_id,
            project_id=identity.project_id,
            user_id=identity.user_id,
            prompt_hash=identity.prompt_hash,
            agent_state=run.agent_state,
            digest=identity.request_sha256,
            parent=None,
            existing=identity,
        )
        if exact != identity:
            raise RecoveryContractError("wire attempt has no exact provider effect")
        attempt = await session.get(GenerationProviderAttempt, identity.attempt_id)
        provider = await session.scalar(
            select(GenerationRecoveryEffect).where(
                GenerationRecoveryEffect.provider_attempt_id == identity.attempt_id,
            )
        )
        if provider is None or attempt is None:
            raise RecoveryContractError("wire owning journal disappeared")
        from yleum_api.services.generation.recovery_diagnostic import require_diagnostic_wire

        require_diagnostic_wire(run.agent_state, identity,
                                outer_attempt=plan.outer_attempt, wire_attempt=wire_attempt)
        effect_id = uuid5(cursor.root_effect_id, f"wire-v1:{plan.outer_attempt}:{wire_attempt}")
        if await session.get(GenerationRecoveryEffect, effect_id) is not None:
            raise RecoveryContractError("wire reservation already consumed; reconcile first")
        if wire_attempt:
            prior = await session.get(
                GenerationRecoveryEffect,
                uuid5(
                    cursor.root_effect_id,
                    f"wire-v1:{plan.outer_attempt}:{wire_attempt - 1}",
                ),
            )
            if prior is None or type(prior.pre_binding) is not dict:
                raise RecoveryContractError("previous wire is unavailable")
            import json

            prior_bound = ProviderWireReservation.model_validate_json(
                json.dumps(prior.pre_binding.get("dispatch_v1")),
                strict=True,
            )
            if (
                prior_bound.root_effect_id != cursor.root_effect_id
                or prior_bound.wire_attempt != wire_attempt - 1
                or prior_bound.outer_attempt != plan.outer_attempt
                or prior_bound.request_sha256 != identity.request_sha256
            ):
                raise RecoveryContractError("previous wire binding conflicts")
            if prior_bound.attempt_id == identity.attempt_id:
                if prior.state != "confirmed" or prior.receipt_ref != {
                    "version": 1,
                    "kind": "gateway_preconnect",
                    "attempt_id": str(identity.attempt_id),
                }:
                    raise RecoveryContractError("previous wire has no definitive safe outcome")
            elif prior_bound.attempt_id == attempt.parent_attempt_id:
                from yleum_api.services.generation.provider_attempts import (
                    require_closed_provider_parent,
                )

                parent_identity = GenerationProviderIdentity(
                    identity.run_id,
                    identity.project_id,
                    identity.user_id,
                    prior_bound.attempt_id,
                    identity.prompt_hash,
                    identity.request_sha256,
                )
                await require_closed_provider_parent(session, parent_identity, identity)
            else:
                raise RecoveryContractError("previous wire is not this attempt's parent")
        if datetime.now(UTC) >= plan.deadline:
            raise RecoveryContractError("original provider deadline expired while reserving")
        binding = ProviderWireReservation(
            **plan.model_dump(),
            root_effect_id=cursor.root_effect_id,
            provider_effect_id=provider.effect_id,
            attempt_id=identity.attempt_id,
            parent_attempt_id=attempt.parent_attempt_id,
            request_sha256=identity.request_sha256,
            wire_attempt=wire_attempt,
        )
        session.add(
            GenerationRecoveryEffect(
                effect_id=effect_id,
                run_id=identity.run_id,
                project_id=identity.project_id,
                user_id=identity.user_id,
                logical_step_seq=cursor.logical_step_seq,
                effect_ordinal=await _next_effect_ordinal(
                    session,
                    identity.run_id,
                    cursor.logical_step_seq,
                ),
                kind="progress",
                input_digest=identity.request_sha256,
                state="inflight",
                attempt_seq=cursor.claim.attempt_seq,
                claim_token=cursor.claim.token,
                parent_effect_id=provider.effect_id,
                pre_binding={"dispatch_v1": binding.model_dump(mode="json")},
            )
        )
        await session.commit()
        return effect_id


async def authorize_cold_wire_continuation(
    factory: async_sessionmaker[AsyncSession],
    identity: GenerationProviderIdentity | None,
    *,
    allow_provider_refusal: bool,
) -> tuple[int, bool] | None:
    """A retained read happened first; only independently committed safe evidence can continue."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.services.generation.provider_attempts import require_closed_provider_parent

    cursor = _PROVIDER_EFFECT.get()
    if (
        cursor is None
        or identity is None
        or not provider_reconciliation_requested()
        or cursor.dispatch.plan is None
        or cursor.dispatch.wire_used == 0
    ):
        return None
    plan, used = cursor.dispatch.plan, cursor.dispatch.wire_used
    pending_outer = cursor.outer_attempt == plan.outer_attempt + 1
    if cursor.outer_attempt != plan.outer_attempt and not pending_outer:
        return None
    deadline = cursor.call_deadline if pending_outer else plan.deadline
    if deadline is None or datetime.now(UTC) >= deadline:
        return None
    async with factory() as session:
        run = await session.get(GenerationRun, identity.run_id)
        project = await session.get(Project, identity.project_id)
        if (
            run is None
            or project is None
            or run.status != "running"
            or project.owner_id != identity.user_id
            or run.user_id != identity.user_id
            or run.project_id != identity.project_id
        ):
            raise RecoveryContractError("cold wire current owner is unavailable")
        raw_state = (
            run.agent_state.get("recovery_v1") if isinstance(run.agent_state, dict) else None
        )
        saved_state = decode_recovery(raw_state)
        if isinstance(saved_state, RecoveryStateV2) and saved_state.execution.substep in {
            "tool_ready",
            "tool_wait",
        }:
            return None  # A consumed response can only be read, never dispatched again.
        _, exact = await prepare_provider_effect(
            session,
            run_id=identity.run_id,
            project_id=identity.project_id,
            user_id=identity.user_id,
            prompt_hash=identity.prompt_hash,
            agent_state=run.agent_state,
            digest=identity.request_sha256,
            parent=None,
            existing=identity,
        )
        if exact != identity:
            raise RecoveryContractError("cold wire lost its exact provider effect")
        prior = await session.get(
            GenerationRecoveryEffect,
            uuid5(
                cursor.root_effect_id,
                f"wire-v1:{plan.outer_attempt}:{used - 1}",
            ),
        )
        if prior is None or prior.post_binding is not None:
            return None  # Outward streaming delivery or unknown state forbids rewind.
        same_transport = prior.state == "confirmed" and prior.receipt_ref == {
            "version": 1,
            "kind": "gateway_preconnect",
            "attempt_id": str(identity.attempt_id),
        }
        if not same_transport:
            if not allow_provider_refusal:
                return None
            await require_closed_provider_parent(session, identity, identity)
        if datetime.now(UTC) >= deadline:
            return None
        await session.commit()
    if pending_outer:
        # The next outer frame was committed, but its first HTTP was not. Its
        # original clock was saved before that commit: neither skip nor refund.
        cursor.dispatch.plan = plan.model_copy(
            update={
                "outer_attempt": cursor.outer_attempt,
                "deadline": deadline,
            }
        )
        cursor.dispatch.outer_advanced = True
        cursor.dispatch.wire_used = used = 0
    if used >= plan.wire_allowance:
        if same_transport:
            from yleum_api.services.llm_client import LLMError

            # Recover the terminal inner outcome, not another wire allowance.
            # The existing outer loop owns whether its next attempt is allowed.
            raise LLMError(
                "Confirmed gateway preconnect allowance exhausted",
                code="preconnect",
                retry_safe=True,
                provider_charge_ambiguous=False,
            )
        return None  # Keep the already authenticated provider refusal unchanged.
    cursor.dispatch.resume_authorized = True
    return used, not same_transport


async def record_wire_observation(
    factory: async_sessionmaker[AsyncSession],
    effect_id: UUID | None,
    identity: GenerationProviderIdentity | None,
    *,
    outcome: Literal["preconnect", "outward"],
) -> None:
    """Server boundary only; an outward marker can never become safe-preconnect evidence."""
    if effect_id is None:
        return
    cursor = _PROVIDER_EFFECT.get()
    if cursor is None or identity is None:
        raise RecoveryContractError("preconnect receipt lost its owning invocation")
    async with factory() as session:
        await _require_session_owner(identity.run_id)
        row = await session.get(GenerationRecoveryEffect, effect_id, with_for_update=True)
        if (
            row is None
            or row.kind != "progress"
            or row.run_id != identity.run_id
            or row.project_id != identity.project_id
            or row.user_id != identity.user_id
            or row.claim_token != cursor.claim.token
            or row.state != "inflight"
            or row.pre_binding is None
        ):
            raise RecoveryContractError("wire preconnect receipt conflicts")
        import json

        bound = ProviderWireReservation.model_validate_json(
            json.dumps(row.pre_binding.get("dispatch_v1")),
            strict=True,
        )
        if bound.attempt_id != identity.attempt_id:
            raise RecoveryContractError("wire preconnect attempt conflicts")
        if outcome == "outward":
            row.post_binding = {"outward_started": True}
        else:
            if row.post_binding is not None:
                raise RecoveryContractError("delivered wire cannot become preconnect")
            row.state = "confirmed"
            row.receipt_ref = {
                "version": 1,
                "kind": "gateway_preconnect",
                "attempt_id": str(identity.attempt_id),
            }
        row.revision += 1
        await session.commit()


@dataclass(slots=True)
class ToolEffectCursor:
    claim: ContinuationClaim
    state_revision: int
    logical_step_seq: int
    tool_call_id: str
    ordinal: int = 0
    reconcile_only: bool = False

    @property
    def root_effect_id(self) -> UUID:
        return uuid5(
            self.claim.run_id, f"recovery-tool-v1:{self.logical_step_seq}:{self.tool_call_id}"
        )


_TOOL_EFFECT: ContextVar[ToolEffectCursor | None] = ContextVar(
    "generation_tool_effect",
    default=None,
)


@contextmanager
def tool_effect_scope(cursor: ToolEffectCursor) -> Iterator[None]:
    token = _TOOL_EFFECT.set(cursor)
    try:
        yield
    finally:
        _TOOL_EFFECT.reset(token)


async def require_tool_owner(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    consumed_index: int | None = None,
) -> tuple[RecoveryStateV2, ToolEffectCursor] | None:
    """Current SQL/session/drain authority shared by dispatch and receipt consumption."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.services.generation.recovery_execution import recovery_state

    cursor = _TOOL_EFFECT.get()
    run = await session.get(GenerationRun, run_id)
    project = await session.get(Project, project_id)
    if (
        run is None
        or project is None
        or run.project_id != project_id
        or run.user_id != user_id
        or project.owner_id != user_id
        or run.status != "running"
    ):
        raise RecoveryContractError("tool current ownership is unconfirmed")
    state = recovery_state(run)
    if not isinstance(state, RecoveryStateV2):
        if cursor is not None:
            raise RecoveryContractError("tool context has no persisted v2 state")
        return None
    if (
        cursor is None
        or state.phase != "working"
        or state.continuation_claim != cursor.claim
        or state.state_revision != cursor.state_revision
        or state.execution.logical_step_seq != cursor.logical_step_seq
        or (
            state.execution.pending_effect_id != cursor.root_effect_id
            and not (
                consumed_index is not None and consumed_index < state.execution.next_tool_index
            )
        )
    ):
        raise RecoveryContractError("tool effect cursor is not currently owned")
    await _require_session_owner(run_id)
    await require_generation_admission(session)
    await _lock_effect_namespace(session, run_id, cursor.logical_step_seq)
    return state, cursor


async def prepare_tool_dispatch(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    input_digest: str,
    binding: dict[str, Any],
) -> UUID | None:
    """Commit one physical intent before dispatch; an existing intent never replays.

    MVCC run reads avoid self-deadlock with legacy callers holding Project/Run.
    The same session advisory owner, current claim and controller fence remain
    independent dispatch requirements. Serialized context alone grants nothing.
    """
    async with factory() as session:
        owned = await require_tool_owner(
            session,
            run_id=run_id,
            project_id=project_id,
            user_id=user_id,
        )
        if owned is None:
            return None
        _, cursor = owned
        effect_id = uuid5(cursor.root_effect_id, f"physical:{cursor.ordinal}")
        previous = await session.get(GenerationRecoveryEffect, effect_id)
        if previous is not None:
            raise RecoveryContractError("tool effect already exists; reconcile without dispatch")
        if cursor.reconcile_only:
            raise RecoveryContractError("cold tool has no owning physical receipt")
        operation_id = uuid5(effect_id, "controller-operation-v1")
        session.add(
            GenerationRecoveryEffect(
                effect_id=effect_id,
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                logical_step_seq=cursor.logical_step_seq,
                effect_ordinal=await _next_effect_ordinal(
                    session,
                    run_id,
                    cursor.logical_step_seq,
                ),
                kind="tool",
                input_digest=input_digest,
                state="inflight",
                attempt_seq=cursor.claim.attempt_seq,
                claim_token=cursor.claim.token,
                operation_id=operation_id,
                tool_call_id=cursor.tool_call_id,
                pre_binding=binding,
            )
        )
        await session.commit()  # Unknown acknowledgement never reaches physical dispatch.
        cursor.ordinal += 1
        return operation_id
