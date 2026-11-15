"""One signed, admitted canary attempt; never an alternative dispatcher."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.services.generation.response_auth import _secret, _signed, _verified

if TYPE_CHECKING:
    from yleum_api.services.generation.recovery_contract import RecoveryStateV3

KEY = "diagnostic_output_budget_v1"
DOMAIN = b"yleum:operator-generation-output-diagnostic:v1"
TIMEOUT_KEY = "diagnostic_response_timeout_v1"
_TIMEOUT_DOMAIN = b"yleum:operator-generation-response-timeout:v1"
_TIMEOUT_PURPOSE = "operator.response-timeout.once"
_TIMEOUT_LIFETIME = 20 * 60
_INTENT: ContextVar[dict[str, Any] | None] = ContextVar("diagnostic_output_intent", default=None)


def mint_intent(
    *,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    attempt_id: UUID,
    prompt_hash: str,
    now: datetime | None = None,
    secret: str | None = None,
) -> str:
    """Private operator helper. Persisting it still requires normal admitted ownership."""
    seconds = int((now or datetime.now(UTC)).timestamp())
    claims: dict[str, object] = {
        "aud": "generation-provider-attempt",
        "purpose": "operator.output-budget.once",
        "run_id": str(run_id),
        "project_id": str(project_id),
        "user_id": str(user_id),
        "attempt_id": str(attempt_id),
        "prompt_hash": prompt_hash,
        "max_tokens": 16,
        "iat": seconds,
        "exp": seconds + 60,
    }
    token = _signed(claims, _secret(secret), DOMAIN)
    verify_intent(token, now=datetime.fromtimestamp(seconds, UTC), secret=secret)
    return token


def verify_intent(
    token: str, *, now: datetime | None = None, secret: str | None = None
) -> dict[str, Any]:
    claims = _verified(token, secret=_secret(secret), domain=DOMAIN, now=now or datetime.now(UTC))
    if (
        set(claims)
        != {
            "aud",
            "purpose",
            "run_id",
            "project_id",
            "user_id",
            "attempt_id",
            "prompt_hash",
            "max_tokens",
            "iat",
            "exp",
        }
        or claims["aud"] != "generation-provider-attempt"
        or claims["purpose"] != "operator.output-budget.once"
        or type(claims["max_tokens"]) is not int
        or claims["max_tokens"] != 16
        or not isinstance(claims["prompt_hash"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", claims["prompt_hash"])
    ):
        raise ValueError("invalid diagnostic intent")
    for key in ("run_id", "project_id", "user_id", "attempt_id"):
        if not isinstance(claims[key], str) or str(UUID(claims[key])) != claims[key]:
            raise ValueError("invalid diagnostic identity")
    return claims


@contextmanager
def _budget_scope(intent: dict[str, Any]) -> Iterator[None]:
    token = _INTENT.set(intent)
    try:
        yield
    finally:
        _INTENT.reset(token)


def diagnostic_output_budget(
    *, run_id: UUID, attempt_id: UUID, requested_max: int | None
) -> int | None:
    intent = _INTENT.get()
    if intent is None:
        return requested_max
    if intent["run_id"] != str(run_id):
        raise ValueError("diagnostic run conflicts")
    return 16 if intent["attempt_id"] == str(attempt_id) else requested_max


def _canary(project_id: UUID) -> None:
    settings = get_settings()
    allowed = {part.strip() for part in settings.generation_recovery_canary_project_ids.split(",")}
    if (
        settings.generation_recovery_mode != "canary"
        or not settings.generation_recovery_execution_enabled
        or not settings.generation_provider_attempts_enabled
        or not settings.generation_response_retention_enabled
        or str(project_id) not in allowed
    ):
        raise ValueError("diagnostic canary is not enabled")


async def lock_diagnostic_namespace(session: AsyncSession, run_id: UUID) -> None:
    # No caller-owned Run/Project lock in the dispatch transaction. Arm uses NOWAIT
    # for its row locks, so an executing caller can never deadlock with the operator.
    if (
        await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(1195724368, hashtext(:key))"),
            {"key": str(run_id)},
        )
        is not True
    ):
        raise ValueError("diagnostic/first-attempt reservation is busy")


async def _unused_diagnostic_run(
    session: AsyncSession, *, run_id: UUID, project_id: UUID, user_id: UUID,
) -> GenerationRun:
    _canary(project_id)
    await lock_diagnostic_namespace(session, run_id)
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update(nowait=True)
    )
    run = await session.scalar(
        select(GenerationRun)
        .where(
            GenerationRun.id == run_id,
            GenerationRun.project_id == project_id,
        )
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    )
    if (
        project is None
        or run is None
        or project.owner_id != user_id
        or run.user_id != user_id
        or run.status not in {"pending", "queued_for_capacity", "running"}
        or run.user_message_id is None
        or run.assistant_message_id is None
        or KEY in run.agent_state
        or TIMEOUT_KEY in run.agent_state
        or await session.scalar(
            select(GenerationProviderAttempt.attempt_id)
            .where(
                GenerationProviderAttempt.run_id == run_id,
            )
            .limit(1)
        )
        is not None
    ):
        raise ValueError("diagnostic requires an unused, normally admitted owned run")
    user_message = await session.get(Message, run.user_message_id)
    assistant = await session.get(Message, run.assistant_message_id)
    if (
        user_message is None
        or assistant is None
        or user_message.project_id != project_id
        or assistant.project_id != project_id
        or user_message.role != "user"
        or assistant.role != "assistant"
    ):
        raise ValueError("diagnostic admission message binding conflicts")
    from yleum_api.services.generation.request_binding import bound_message_request

    bound_message_request(run, user_message, project_id=project_id, run_id=run_id)
    return run


async def arm_diagnostic_output_budget(
    session: AsyncSession, *, run_id: UUID, project_id: UUID, user_id: UUID, attempt_id: UUID,
) -> None:
    """Operator-only transaction; caller commits. No admission, dispatch, or public route."""
    run = await _unused_diagnostic_run(
        session, run_id=run_id, project_id=project_id, user_id=user_id,
    )
    now = datetime.now(UTC)
    run.agent_state = {
        **run.agent_state,
        KEY: {
            "token": mint_intent(
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                attempt_id=attempt_id,
                prompt_hash=run.prompt_hash,
                now=now,
            ),
            "issued_at": int(now.timestamp()),
        },
    }
    session.add(run)
    await session.flush()


async def _diagnostic_qa_owner(session: AsyncSession, user_id: UUID) -> bool:
    from yleum_api.models.user import User
    from yleum_api.services.entitlements import has_qa_unlimited_generations, load_plan_context

    user = await session.get(User, user_id)
    return user is not None and has_qa_unlimited_generations(
        user, await load_plan_context(session, user_id),
    )


def _timeout_scope(run: GenerationRun) -> dict[str, str]:
    return {
        "purpose": _TIMEOUT_PURPOSE,
        "run_id": str(run.id),
        "project_id": str(run.project_id),
        "user_id": str(run.user_id),
        "prompt_hash": run.prompt_hash,
    }


async def arm_diagnostic_response_timeout(
    session: AsyncSession, *, run_id: UUID, project_id: UUID, user_id: UUID,
    release_cell: bool = False,
) -> None:
    """Arm a read timeout on the first confirmed response of one admitted QA run."""
    run = await _unused_diagnostic_run(
        session, run_id=run_id, project_id=project_id, user_id=user_id,
    )
    if not await _diagnostic_qa_owner(session, user_id):
        raise ValueError("response timeout requires an internal QA account")
    issued = int(datetime.now(UTC).timestamp())
    token = _signed(
        {**_timeout_scope(run), "release_cell": release_cell,
         "iat": issued, "exp": issued + _TIMEOUT_LIFETIME},
        _secret(None), _TIMEOUT_DOMAIN,
    )
    run.agent_state = {**run.agent_state, TIMEOUT_KEY: {"token": token}}
    await session.flush()


async def consume_diagnostic_response_timeout(
    factory: async_sessionmaker[AsyncSession], state: RecoveryStateV3,
) -> Literal["replay", "release"] | None:
    """Take a one-shot test intent only after the original response is retained."""
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        _retained_completion_attempt,
        recovery_state,
    )
    from yleum_api.services.generation.recovery_store import _locked_owner
    from yleum_api.services.generation_deployment_drain import require_generation_admission

    owner = state.identity
    try:
        _canary(owner.project_id)
        async with factory() as session:
            hint = await session.get(GenerationRun, owner.run_id)
            pending = hint.agent_state.get(TIMEOUT_KEY) if hint is not None else None
            if type(pending) is not dict or set(pending) != {"token"}:
                return None
            await require_generation_admission(session)
            run, _ = await _locked_owner(
                session, run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
            )
            raw = run.agent_state.get(TIMEOUT_KEY)
            if type(raw) is not dict or set(raw) != {"token"} or recovery_state(run) != state:
                return None
            claims = _verified(
                raw["token"], secret=_secret(None), domain=_TIMEOUT_DOMAIN, now=datetime.now(UTC),
                max_lifetime_seconds=_TIMEOUT_LIFETIME,
            )
            scope = _timeout_scope(run)
            release_cell = claims.get("release_cell", False)
            if (
                set(claims) - {"release_cell"} != {*scope, "iat", "exp"}
                or any(claims[key] != value for key, value in scope.items())
                or type(release_cell) is not bool
                or run.prompt_hash != owner.prompt_hash
                or not await _diagnostic_qa_owner(session, owner.user_id)
            ):
                return None
            attempt = await _retained_completion_attempt(session, state)
            if attempt is None:
                return None
            await _require_session_owner(run.id)
            run.agent_state = {
                **run.agent_state, TIMEOUT_KEY: {
                    **raw, "attempt_id": str(attempt.attempt_id), "release_cell": release_cell,
                },
            }
            await session.commit()
            return "release" if release_cell else "replay"
    except (ApiError, ValueError, SQLAlchemyError):
        # The test hook cannot replace a real reply on unknown authorization or commit.
        return None


def _saved_intent(
    agent_state: object, *, run_id: UUID, project_id: UUID, user_id: UUID, prompt_hash: str
) -> dict[str, Any] | None:
    if not isinstance(agent_state, dict) or KEY not in agent_state:
        return None
    raw = agent_state[KEY]
    if (
        type(raw) is not dict
        or set(raw) != {"token", "issued_at"}
        or type(raw["issued_at"]) is not int
    ):
        raise ValueError("diagnostic intent is malformed")
    # Historical verification is only body reconstruction; the owning committed
    # attempt below is required, and the wire guard separately forbids a second POST.
    claims = verify_intent(raw["token"], now=datetime.fromtimestamp(raw["issued_at"], UTC))
    if (
        any(
            claims[key] != str(value)
            for key, value in {
                "run_id": run_id,
                "project_id": project_id,
                "user_id": user_id,
                "prompt_hash": prompt_hash,
            }.items()
        )
        or claims["iat"] != raw["issued_at"]
    ):
        raise ValueError("diagnostic owning identity conflicts")
    return claims


async def apply_diagnostic_budget(
    session: AsyncSession,
    body: dict[str, Any],
    *,
    agent_state: object,
    run_id: UUID,
    project_id: UUID,
    user_id: UUID,
    prompt_hash: str,
    parent_attempt_id: UUID | None = None,
) -> UUID | None:
    from yleum_api.services.generation.recovery_reconciliation import prospective_provider_attempt

    intent = _saved_intent(
        agent_state, run_id=run_id, project_id=project_id, user_id=user_id, prompt_hash=prompt_hash
    )
    if intent is None:
        return None
    prospective = prospective_provider_attempt()
    if prospective is None:
        raise ValueError("diagnostic requires an owned durable provider cursor")
    attempt_id, outer_attempt = prospective
    target = UUID(intent["attempt_id"])
    saved = await session.get(GenerationProviderAttempt, target)
    if saved is not None and any(
        getattr(saved, key) != value
        for key, value in {
            "run_id": run_id,
            "project_id": project_id,
            "user_id": user_id,
            "prompt_hash": prompt_hash,
            "parent_attempt_id": None,
        }.items()
    ):
        raise ValueError("diagnostic consumed attempt conflicts")
    if saved is None:
        _canary(project_id)
        verify_intent(agent_state[KEY]["token"])  # type: ignore[index]
        if (
            attempt_id != target
            or outer_attempt != 0
            or parent_attempt_id is not None
            or await session.scalar(
                select(GenerationProviderAttempt.attempt_id)
                .where(
                    GenerationProviderAttempt.run_id == run_id,
                )
                .limit(1)
            )
            is not None
        ):
            raise ValueError("diagnostic cannot target a later attempt")
    if parent_attempt_id == target or (attempt_id == target and outer_attempt != 0):
        raise ValueError("diagnostic attempt cannot be retried")
    original = body.get("max_tokens")
    if original is not None and type(original) is not int:
        raise ValueError("invalid output budget")
    with _budget_scope(intent):
        selected = diagnostic_output_budget(
            run_id=run_id, attempt_id=attempt_id, requested_max=original
        )
    if selected is not None:
        body["max_tokens"] = selected
    return target if attempt_id == target else None


def require_diagnostic_wire(
    agent_state: object, identity: Any, *, outer_attempt: int, wire_attempt: int
) -> None:
    intent = _saved_intent(
        agent_state,
        run_id=identity.run_id,
        project_id=identity.project_id,
        user_id=identity.user_id,
        prompt_hash=identity.prompt_hash,
    )
    if intent is not None and intent["attempt_id"] == str(identity.attempt_id):
        _canary(identity.project_id)
        if outer_attempt != 0 or wire_attempt != 0:
            raise ValueError("diagnostic wire is one-shot; reconcile only")
