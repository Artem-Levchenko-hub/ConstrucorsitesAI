"""Commit generation dispatch intent before gateway HTTP, with no request bodies."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity,
    request_digest,
)


async def prepare_generation_attempt(
    body: dict[str, Any],
    route: str,
    *,
    existing: GenerationProviderIdentity | None = None,
    parent: GenerationProviderIdentity | None = None,
) -> GenerationProviderIdentity | None:
    from yleum_api.services.generation.recovery_reconciliation import (
        has_provider_effect_context,
        prepare_provider_effect,
        requires_retained_provider,
    )

    metadata = body.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("run_id"):
        if existing is not None or parent is not None or has_provider_effect_context():
            raise RuntimeError("GENERATION_ATTEMPT_UNCONFIRMED: provider dispatch stopped")
        return None  # Generic service/runtime calls are not generation retention.
    try:
        settings = get_settings()
        run_id, project_id = UUID(str(metadata["run_id"])), UUID(str(metadata["project_id"]))
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            from yleum_api.services.generation.recovery_diagnostic import (
                KEY,
                apply_diagnostic_budget,
                lock_diagnostic_namespace,
            )

            if settings.generation_provider_attempts_enabled:
                await lock_diagnostic_namespace(session, run_id)
            # No FK/row lock against the caller's project/run transaction. The
            # gateway rechecks current committed ownership when granting dispatch.
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT r.user_id,r.prompt_hash,r.agent_state FROM generation_runs r "
                            "JOIN projects p ON p.id=r.project_id "
                            "WHERE r.id=:run AND r.project_id=:project "
                            "AND p.owner_id=r.user_id AND r.status='running'"
                        ),
                        {"run": run_id, "project": project_id},
                    )
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or body.get("user") not in {None, str(row["user_id"])}
                or (metadata.get("user_id") not in {None, str(row["user_id"])})
            ):
                raise ValueError
            # Writer switches never erase an already persisted executable run.
            # This short MVCC read also detects V2 after local context loss; it
            # takes no caller-owned run/project row locks.
            retained = requires_retained_provider(row["agent_state"])
            if retained and (
                not settings.generation_provider_attempts_enabled
                or not getattr(settings, "generation_response_retention_enabled", False)
            ):
                raise ValueError
            if not settings.generation_provider_attempts_enabled:
                if (existing is not None or parent is not None or has_provider_effect_context()
                        or KEY in (row["agent_state"] or {})):
                    raise ValueError
                return None
            if not retained and existing is None and parent is None:
                if has_provider_effect_context() or KEY in (row["agent_state"] or {}):
                    raise ValueError
                # A global transport switch is not admission into recovery.
                # Legacy and shadow runs retain their original uncaptured wire.
                return None
            diagnostic_attempt = await apply_diagnostic_budget(
                session, body, agent_state=row["agent_state"], run_id=run_id,
                project_id=project_id, user_id=row["user_id"], prompt_hash=row["prompt_hash"],
                parent_attempt_id=parent.attempt_id if parent else None,
            )
            digest = request_digest(route, body)
            for continuation in (existing, parent):
                if continuation is not None:
                    await _require_committed_identity(
                        session, continuation, run_id=run_id, project_id=project_id,
                        user_id=row["user_id"], prompt_hash=row["prompt_hash"], digest=digest,
                    )
            effect, prior_identity = await prepare_provider_effect(
                session, run_id=run_id, project_id=project_id, user_id=row["user_id"],
                prompt_hash=row["prompt_hash"], agent_state=row["agent_state"],
                digest=digest, parent=parent, existing=existing,
            )
            if existing is not None:
                if diagnostic_attempt is not None and existing.attempt_id != diagnostic_attempt:
                    raise ValueError
                if effect is not None or (
                    prior_identity is not None and prior_identity != existing
                ):
                    raise ValueError
                await session.commit()
                return existing
            if prior_identity is not None:
                if (diagnostic_attempt is not None
                        and prior_identity.attempt_id != diagnostic_attempt):
                    raise ValueError
                await session.commit()
                return prior_identity
            if effect is not None and effect.parent_effect_id is not None and parent is None:
                from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
                previous_effect = await session.get(
                    GenerationRecoveryEffect, effect.parent_effect_id,
                )
                if previous_effect is None:
                    raise ValueError
                previous_attempt = await session.get(
                    GenerationProviderAttempt, previous_effect.provider_attempt_id,
                )
                if previous_attempt is None:
                    raise ValueError
                parent = GenerationProviderIdentity(
                    previous_attempt.run_id, previous_attempt.project_id, previous_attempt.user_id,
                    previous_attempt.attempt_id, previous_attempt.prompt_hash,
                    previous_attempt.request_sha256,
                )
            attempt_id = uuid5(effect.effect_id, "provider-attempt-v1") if effect else uuid4()
            if parent is not None:
                attempt_id = uuid5(parent.attempt_id, "definitive-refusal-retry-v1")
            if diagnostic_attempt is not None and attempt_id != diagnostic_attempt:
                raise ValueError
            identity = GenerationProviderIdentity(
                run_id,
                project_id,
                row["user_id"],
                attempt_id,
                row["prompt_hash"],
                digest,
            )
            if parent is not None:
                await require_closed_provider_parent(session, parent, identity)
            await session.execute(
                text(
                    "INSERT INTO generation_provider_attempts "
                    "(attempt_id,run_id,project_id,user_id,prompt_hash,request_sha256,dispatch_token,"
                    "parent_attempt_id) VALUES(:attempt,:run,:project,:user,"
                    ":prompt,:digest,:token,:parent)"
                ),
                {
                    "attempt": identity.attempt_id,
                    "run": run_id,
                    "project": project_id,
                    "user": identity.user_id,
                    "prompt": identity.prompt_hash,
                    "digest": digest,
                    "token": uuid4(),
                    "parent": parent.attempt_id if parent else None,
                },
            )
            if effect is not None:
                effect.provider_attempt_id = identity.attempt_id
                session.add(effect)
            await session.commit()
        return identity
    except Exception:
        # Includes an unknown commit outcome: never send HTTP on uncertainty.
        raise RuntimeError("GENERATION_ATTEMPT_UNCONFIRMED: provider dispatch stopped") from None


async def _require_committed_identity(
    session: AsyncSession, identity: GenerationProviderIdentity, *,
    run_id: UUID, project_id: UUID, user_id: UUID, prompt_hash: str, digest: str,
) -> None:
    """Task7 continuation carries SQL authority, never just a supplied UUID."""
    expected = {
        "run_id": run_id, "project_id": project_id, "user_id": user_id,
        "prompt_hash": prompt_hash, "request_sha256": digest, "version": 1,
    }
    saved = await session.get(GenerationProviderAttempt, identity.attempt_id)
    if saved is None or any(
        getattr(identity, key) != value or getattr(saved, key) != value
        for key, value in expected.items()
    ):
        raise ValueError("provider continuation has no exact committed identity")


async def require_closed_provider_parent(
    session: AsyncSession, parent: GenerationProviderIdentity, identity: GenerationProviderIdentity,
) -> None:
    """Existing Task7 affirmative receipt predicate, shared by live and cold arithmetic."""
    prior = (
        (await session.execute(
            text(
                "SELECT a.* FROM generation_provider_attempts a "
                "JOIN provider_calls c "
                "ON c.id=a.provider_call_id WHERE a.attempt_id=:id "
                "AND a.phase='closed' "
                "AND a.terminal_reason IN ('preconnect','rate_limit_refused') "
                "AND c.status='failed' AND c.receipt_hash IS NOT NULL"
            ), {"id": parent.attempt_id},
        )).mappings().first()
    )
    if prior is None or any(
        prior[key] != getattr(identity, key)
        for key in ("run_id", "project_id", "user_id", "prompt_hash", "request_sha256")
    ):
        raise ValueError("provider parent has no affirmative committed receipt")


def attach_identity(body: dict[str, Any], identity: GenerationProviderIdentity | None) -> None:
    if identity is not None:
        body["metadata"]["generation_provider_identity"] = identity.to_wire()


async def reserve_generation_wire(
    identity: GenerationProviderIdentity | None, *, wire_attempt: int,
) -> UUID | None:
    from yleum_api.services.generation.recovery_reconciliation import (
        has_provider_effect_context,
        reserve_provider_wire,
    )
    if not has_provider_effect_context():
        return None
    return await reserve_provider_wire(
        async_sessionmaker(get_engine(), expire_on_commit=False), identity,
        wire_attempt=wire_attempt,
    )


async def confirm_generation_preconnect(
    effect_id: UUID | None, identity: GenerationProviderIdentity | None,
) -> None:
    if effect_id is None:
        return
    from yleum_api.services.generation.recovery_reconciliation import record_wire_observation
    await record_wire_observation(
        async_sessionmaker(get_engine(), expire_on_commit=False), effect_id, identity,
        outcome="preconnect",
    )


async def resume_generation_wire(
    identity: GenerationProviderIdentity | None, *, allow_provider_refusal: bool,
) -> tuple[int, bool] | None:
    from yleum_api.services.generation.recovery_reconciliation import (
        authorize_cold_wire_continuation,
        has_provider_effect_context,
    )
    if not has_provider_effect_context():
        return None
    return await authorize_cold_wire_continuation(
        async_sessionmaker(get_engine(), expire_on_commit=False), identity,
        allow_provider_refusal=allow_provider_refusal,
    )


async def mark_generation_wire_outward(
    effect_id: UUID | None, identity: GenerationProviderIdentity | None,
) -> None:
    if effect_id is None:
        return
    from yleum_api.services.generation.recovery_reconciliation import record_wire_observation
    await record_wire_observation(
        async_sessionmaker(get_engine(), expire_on_commit=False), effect_id, identity,
        outcome="outward",
    )
