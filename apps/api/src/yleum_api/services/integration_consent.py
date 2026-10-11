"""Consent for first amoCRM transmissions; callers retain durable receipt semantics.

Dispatch holds actor_dispatch_guard across both gate checks and provider writes.
The dedicated connection holds session locks across receipt commits. All policy
changes must use set_policy, which excludes ongoing dispatches and consent writes.
"""

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.integration_consent import (
    IntegrationConsentEvent,
    IntegrationConsentPolicy,
    IntegrationConsentState,
)


@dataclass(frozen=True)
class ConsentPolicyContext:
    enabled: bool
    policy_version: str
    policy_hash: str


def _valid_policy(version: str, digest: str) -> bool:
    return (
        bool(version.strip()) and len(version) <= 32 and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
    )


async def policy_context(session: AsyncSession, project_id: UUID) -> ConsentPolicyContext:
    row = await session.scalar(
        select(IntegrationConsentPolicy)
        .where(IntegrationConsentPolicy.project_id == project_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return ConsentPolicyContext(False, "", "")
    if row.enabled and not _valid_policy(row.policy_version, row.policy_hash):
        raise ApiError("consent_policy_invalid", "Политика согласия не настроена.", 403)
    return ConsentPolicyContext(row.enabled, row.policy_version, row.policy_hash)


async def _state(
    session: AsyncSession, project_id: UUID, max_user_id: int | str
) -> IntegrationConsentState | None:
    return cast(
        IntegrationConsentState | None,
        await session.scalar(
            select(IntegrationConsentState)
            .where(
                IntegrationConsentState.project_id == project_id,
                IntegrationConsentState.max_user_id == str(max_user_id),
            )
            .execution_options(populate_existing=True)
        ),
    )


def _result(row: IntegrationConsentState | IntegrationConsentEvent | None) -> dict[str, Any]:
    return {
        "granted": row.granted if row is not None else False,
        "policy_version": row.policy_version if row is not None else "",
        "policy_hash": row.policy_hash if row is not None else "",
        "revision": row.revision if row is not None else 0,
    }


async def consent_state(
    session: AsyncSession, project_id: UUID, max_user_id: int | str
) -> dict[str, Any]:
    return _result(await _state(session, project_id, max_user_id))


async def require_consent(session: AsyncSession, project_id: UUID, max_user_id: int | str) -> None:
    """Call inside actor_dispatch_guard immediately before each first transmission."""
    policy = await policy_context(session, project_id)
    if not policy.enabled:
        return
    state = await _state(session, project_id, max_user_id)
    if state is None:
        raise ApiError("consent_required", "Для отправки заявки требуется согласие.", 403)
    if not state.granted:
        raise ApiError("consent_revoked", "Согласие на передачу данных отозвано.", 403)
    if (state.policy_version, state.policy_hash) != (policy.policy_version, policy.policy_hash):
        raise ApiError(
            "consent_policy_outdated", "Подтвердите актуальную политику обработки данных.", 403
        )


def _lock_key(scope: str) -> int:
    return int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True)


@asynccontextmanager
async def _guard(
    session: AsyncSession, project_id: UUID, actor: str | None, wait_seconds: float
) -> AsyncIterator[None]:
    bind = session.bind
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    if not isinstance(engine, AsyncEngine):
        raise RuntimeError("Consent locking requires a PostgreSQL async engine")
    connection: AsyncConnection | None = None
    keys = [(_lock_key(f"amocrm-consent:project:{project_id}"), actor is not None)]
    if actor is not None:
        keys.append((_lock_key(f"amocrm-consent:actor:{project_id}:{actor}"), False))
    try:
        try:
            async with asyncio.timeout(wait_seconds):
                connection = await engine.connect()
                for key, shared in keys:
                    function = "pg_advisory_lock_shared" if shared else "pg_advisory_lock"
                    await connection.execute(text(f"SELECT {function}(:key)"), {"key": key})
                await connection.commit()
        except TimeoutError as exc:
            raise ApiError(
                "consent_busy", "Операция с согласием занята. Повторите запрос.", 503
            ) from exc
        yield
    finally:
        if connection is not None:

            async def release() -> None:
                try:
                    async with asyncio.timeout(5):
                        await connection.rollback()
                        # This connection is dedicated to this guard. Also release a
                        # lock acquired server-side just as its response was cancelled.
                        await connection.execute(text("SELECT pg_advisory_unlock_all()"))
                        await connection.commit()
                except BaseException:
                    # Never return a session-level locked connection to the pool.
                    await connection.invalidate()
                    raise
                finally:
                    await connection.close()

            cleanup = asyncio.create_task(release())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


@asynccontextmanager
async def actor_dispatch_guard(
    session: AsyncSession, project_id: UUID, max_user_id: int | str, *, wait_seconds: float = 5.0
) -> AsyncIterator[None]:
    """Revoke returns only after earlier transmissions under this lock finish."""
    async with _guard(session, project_id, str(max_user_id), wait_seconds):
        yield


async def set_policy(
    session: AsyncSession, project_id: UUID, *, enabled: bool, policy_version: str, policy_hash: str
) -> ConsentPolicyContext:
    """Privileged rollout operation; never expose to a MAX actor endpoint."""
    if enabled and not _valid_policy(policy_version, policy_hash):
        raise ApiError("consent_policy_invalid", "Политика согласия не настроена.", 403)
    async with _guard(session, project_id, None, 5.0):
        row = await session.scalar(
            select(IntegrationConsentPolicy)
            .where(IntegrationConsentPolicy.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            row = IntegrationConsentPolicy(project_id=project_id)
            session.add(row)
        row.enabled, row.policy_version, row.policy_hash = enabled, policy_version, policy_hash
        await session.commit()
    return ConsentPolicyContext(enabled, policy_version, policy_hash)


async def record_consent(
    session: AsyncSession,
    project_id: UUID,
    max_user_id: int | str,
    *,
    event_id: UUID,
    expected_revision: int,
    granted: bool,
    policy_version: str,
    policy_hash: str,
) -> dict[str, Any]:
    """Caller authenticates a trusted Core assertion covering this exact body."""
    payload = [expected_revision, granted, policy_version, policy_hash]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    async with actor_dispatch_guard(session, project_id, max_user_id):
        previous = await session.scalar(
            select(IntegrationConsentEvent)
            .where(
                IntegrationConsentEvent.project_id == project_id,
                IntegrationConsentEvent.max_user_id == str(max_user_id),
                IntegrationConsentEvent.event_id == event_id,
            )
            .execution_options(populate_existing=True)
        )
        if previous is not None:
            if previous.request_digest != digest:
                raise ApiError(
                    "consent_event_conflict", "Событие согласия уже имеет другое содержание.", 409
                )
            return _result(previous)
        state = await _state(session, project_id, max_user_id)
        revision = state.revision if state is not None else 0
        if expected_revision != revision:
            raise ApiError(
                "consent_revision_conflict", "Состояние согласия изменилось. Обновите его.", 409
            )
        if granted:
            policy = await policy_context(session, project_id)
            if not policy.enabled:
                raise ApiError("consent_policy_invalid", "Политика согласия не включена.", 403)
            if (policy_version, policy_hash) != (policy.policy_version, policy.policy_hash):
                raise ApiError(
                    "consent_policy_outdated",
                    "Подтвердите актуальную политику обработки данных.",
                    403,
                )
        if state is None:
            state = IntegrationConsentState(project_id=project_id, max_user_id=str(max_user_id))
            session.add(state)
        state.granted = granted
        state.policy_version = policy_version
        state.policy_hash = policy_hash
        state.revision = revision + 1
        session.add(
            IntegrationConsentEvent(
                project_id=project_id,
                max_user_id=str(max_user_id),
                event_id=event_id,
                request_digest=digest,
                granted=granted,
                policy_version=policy_version,
                policy_hash=policy_hash,
                revision=state.revision,
            )
        )
        result = _result(state)
        await session.commit()
        return result
