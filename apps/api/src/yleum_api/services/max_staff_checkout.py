"""Order-bound YooKassa payment attempts and independent server-side verification."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import case, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.max_staff_checkout import MaxStaffPaymentAttempt
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.services.integration_credentials import load_credentials, lock_account_provider
from yleum_api.services.integration_responses import payment_response
from yleum_api.services.max_management import _owned, _State, _target

log = structlog.get_logger(__name__)

_PAYMENTS = "https://api.yookassa.ru/v3/payments"
_ATTEMPT_KEY = "yleum_staff_attempt_id"
_MAX_CREATE_AGE = timedelta(hours=23, minutes=50)
_MAX_VERIFY_AGE = timedelta(days=7)
_CREATE_CLAIM_DURATION = timedelta(seconds=30)
_MAX_AMOUNT_KOPECKS = 100_000_000
_PAYMENT_ID = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")
_BindingPair = tuple[ProjectIntegrationBinding, AccountIntegration]


class InvalidProviderPayment(ValueError):
    """The provider response did not establish this attempt's identity or outcome."""


@dataclass(frozen=True)
class _ObservedPayment:
    id: str
    status: str
    is_test: bool
    confirmation_url: str | None


def _money(kopecks: int) -> str:
    return f"{kopecks // 100}.{kopecks % 100:02d}"


def _build_return_url(target_url: str, order_id: UUID, supplied: str | None) -> str:
    target = urlsplit(target_url)
    origin = f"https://{target.netloc}"
    if supplied is not None and supplied not in {origin, f"{origin}/"}:
        raise ApiError("invalid_request", "Вернуться можно только в опубликованное приложение", 422)
    return f"{origin}/?checkout_order_id={order_id}"


def _observe_payment(
    attempt: MaxStaffPaymentAttempt,
    payload: object,
) -> _ObservedPayment:
    if not isinstance(payload, dict):
        raise InvalidProviderPayment
    amount = payload.get("amount")
    recipient = payload.get("recipient")
    metadata = payload.get("metadata")
    test = payload.get("test")
    paid = payload.get("paid")
    try:
        parsed = payment_response(payload)
    except (ApiError, ValueError):
        raise InvalidProviderPayment from None
    if (
        _PAYMENT_ID.fullmatch(parsed.id) is None
        or (attempt.provider_payment_id is not None and parsed.id != attempt.provider_payment_id)
        or not isinstance(amount, dict)
        or amount.get("value") != _money(attempt.amount_kopecks)
        or amount.get("currency") != attempt.currency
        or not isinstance(recipient, dict)
        or recipient.get("account_id") != attempt.shop_id
        or not isinstance(metadata, dict)
        or metadata.get(_ATTEMPT_KEY) != str(attempt.id)
        or type(test) is not bool
        or (attempt.is_test is not None and test is not attempt.is_test)
        or type(paid) is not bool
        or (parsed.status == "succeeded" and paid is not True)
    ):
        raise InvalidProviderPayment
    return _ObservedPayment(parsed.id, parsed.status, test, parsed.confirmation_url)


async def _bound_yookassa(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    *,
    lock: bool = False,
) -> _BindingPair | None:
    query = (
        select(ProjectIntegrationBinding, AccountIntegration)
        .join(AccountIntegration, AccountIntegration.id == ProjectIntegrationBinding.integration_id)
        .join(Project, Project.id == ProjectIntegrationBinding.project_id)
        .where(
            Project.id == project_id,
            Project.owner_id == owner_id,
            ProjectIntegrationBinding.provider == "yookassa",
            ProjectIntegrationBinding.enabled.is_(True),
            ProjectIntegrationBinding.status == "ready",
            AccountIntegration.user_id == owner_id,
            AccountIntegration.provider == "yookassa",
            AccountIntegration.status == "active",
            AccountIntegration.auth_mode.in_(("oauth", "credentials")),
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(of=(ProjectIntegrationBinding, AccountIntegration, Project))
    row = (await session.execute(query)).one_or_none()
    return (row[0], row[1]) if row is not None else None


def _same_authority(
    attempt: MaxStaffPaymentAttempt,
    pair: _BindingPair,
) -> bool:
    binding, connection = pair
    if connection.public_config.get("shop_id") != attempt.shop_id:
        return False
    if attempt.provider_payment_id is not None:
        # The current project/owner may authorize another connection to the
        # same merchant to read a previously established payment ID.
        return True
    return (
        binding.id == attempt.binding_id
        and connection.id == attempt.integration_id
        and connection.auth_mode == attempt.auth_mode
        and connection.oauth_client_key == attempt.oauth_client_key
        and connection.grant_revision == attempt.grant_revision
    )


def _grant_stamp(connection: AccountIntegration) -> tuple[UUID, str, str | None, int, str]:
    # Compare in memory only, including Basic rotations and same-grant refresh.
    return (
        connection.id,
        connection.auth_mode,
        connection.oauth_client_key,
        connection.grant_revision,
        connection.credentials_enc,
    )


async def _call_provider(
    attempt: MaxStaffPaymentAttempt,
    credentials: dict[str, str],
    *,
    auth_mode: str,
) -> httpx.Response:
    headers = {"Accept": "application/json"}
    auth = None
    if auth_mode == "oauth":
        token = credentials.get("access_token")
        if not token:
            raise InvalidProviderPayment
        headers["Authorization"] = f"Bearer {token}"
    else:
        secret = credentials.get("secret_key")
        if not secret:
            raise InvalidProviderPayment
        auth = (attempt.shop_id, secret)
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=False,
        trust_env=False,
        auth=auth,
    ) as client:
        if attempt.provider_payment_id is not None:
            if _PAYMENT_ID.fullmatch(attempt.provider_payment_id) is None:
                raise InvalidProviderPayment
            return await client.get(
                f"{_PAYMENTS}/{attempt.provider_payment_id}",
                headers=headers,
            )
        headers.update({"Content-Type": "application/json", "Idempotence-Key": str(attempt.id)})
        body = json.dumps(
            attempt.request_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return await client.post(_PAYMENTS, content=body.encode(), headers=headers)


def _invalid_create_request(response: httpx.Response) -> dict[str, object] | None:
    if response.status_code != 400:
        return None
    try:
        error = response.json()
    except ValueError:
        return None
    if (
        isinstance(error, dict)
        and error.get("type") == "error"
        and error.get("code") == "invalid_request"
    ):
        return error
    return None


async def _project_deleting(session: AsyncSession, project_id: UUID) -> bool:
    marker = await session.scalar(
        select(ProjectCellWorkspace.id).where(
            ProjectCellWorkspace.project_id == project_id,
            or_(
                ProjectCellWorkspace.deleted_at.is_not(None),
                ProjectCellWorkspace.state.in_(("deleting", "deleted")),
            ),
        )
    )
    return marker is not None


async def assert_no_unsettled_staff_checkout(session: AsyncSession, project_id: UUID) -> None:
    """Call while holding the project row lock, before admitting deletion."""
    blocked = await session.scalar(
        select(MaxStaffPaymentAttempt.status)
        .where(
            MaxStaffPaymentAttempt.project_id == project_id,
            or_(
                MaxStaffPaymentAttempt.status == "pending",
                (MaxStaffPaymentAttempt.status == "needs_review")
                & MaxStaffPaymentAttempt.failure_reason.is_distinct_from("receipt_required"),
            ),
        )
        .order_by(case((MaxStaffPaymentAttempt.status == "needs_review", 0), else_=1))
        .limit(1)
    )
    if blocked == "needs_review":
        raise ApiError(
            "conflict",
            "Исход оплаты не подтверждён. Обратитесь в поддержку для сверки "
            "перед удалением проекта",
            409,
        )
    if blocked is not None:
        raise ApiError("conflict", "Дождитесь сверки оплаты перед удалением проекта", 409)


def _schedule(attempt: MaxStaffPaymentAttempt, now: datetime) -> None:
    delay = min(300, 5 * 2 ** min(attempt.attempts, 6))
    attempt.next_attempt_at = now + timedelta(seconds=delay)


def _terminal(attempt: MaxStaffPaymentAttempt, state: str) -> None:
    attempt.status = state
    attempt.next_attempt_at = None
    attempt.confirmation_url = None


def _due(attempt: MaxStaffPaymentAttempt | None, now: datetime, *, create_only: bool) -> bool:
    return bool(
        attempt is not None
        and attempt.status == "pending"
        and attempt.next_attempt_at is not None
        and attempt.next_attempt_at <= now
        and (not create_only or attempt.provider_payment_id is None)
    )


async def _lock_attempt_authority(
    session: AsyncSession,
    attempt_id: UUID,
    project_id: UUID,
    owner_id: UUID,
) -> tuple[_BindingPair | None, MaxStaffPaymentAttempt | None]:
    await session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
    await lock_account_provider(session, owner_id, "yookassa")
    # Project deletion also locks the project before its cascading rows.
    pair = await _bound_yookassa(session, project_id, owner_id, lock=True)
    row = await session.scalar(
        select(MaxStaffPaymentAttempt)
        .where(MaxStaffPaymentAttempt.id == attempt_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True),
    )
    return pair, row


async def _create_allowed(session: AsyncSession, attempt: MaxStaffPaymentAttempt) -> bool:
    from yleum_api.services.max_staff import get_order_checkout_settings

    try:
        settings = await get_order_checkout_settings(session, attempt.project_id)
    except ApiError:
        return False
    return (
        settings.owner_id == attempt.owner_id
        and settings.orders_enabled_at is not None
        and attempt.created_at >= settings.orders_enabled_at
        and not await _project_deleting(session, attempt.project_id)
    )


async def _advance_locked(
    session: AsyncSession,
    attempt_id: UUID,
    *,
    create_only: bool,
    now: datetime,
) -> None:
    attempt = await session.get(MaxStaffPaymentAttempt, attempt_id, populate_existing=True)
    if attempt is None or not _due(attempt, now, create_only=create_only):
        return
    project_id, owner_id = attempt.project_id, attempt.owner_id
    credentials: dict[str, str] | None = None
    stamp = None
    await session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
    pair = await _bound_yookassa(session, project_id, owner_id)
    if pair is not None and _same_authority(attempt, pair):
        try:
            credentials = await load_credentials(session, pair[1])
            stamp = _grant_stamp(pair[1])
        except ApiError:
            await session.rollback()

    # The credential loader commits. Establish the exact authority again
    # under the normal account lock before any remote effect.
    pair, attempt = await _lock_attempt_authority(session, attempt_id, project_id, owner_id)
    if attempt is None or not _due(attempt, now, create_only=create_only):
        await session.rollback()
        return
    creating = attempt.provider_payment_id is None
    unavailable = "canceled" if creating and not attempt.create_dispatch_claimed else "needs_review"
    if now >= attempt.created_at + (_MAX_CREATE_AGE if creating else _MAX_VERIFY_AGE):
        _terminal(attempt, unavailable)
        await session.commit()
        return
    if creating and not await _create_allowed(session, attempt):
        _terminal(attempt, unavailable)
        await session.commit()
        return
    attempt.attempts += 1
    _schedule(attempt, now)
    if (
        pair is None
        or not _same_authority(attempt, pair)
        or credentials is None
        or stamp != _grant_stamp(pair[1])
    ):
        await session.commit()
        return

    first_create = False
    if creating:
        first_create = not attempt.create_dispatch_claimed
        attempt.create_dispatch_claimed = True
        claim = attempt.attempts
        # Persist possible dispatch before network I/O. The cooldown excludes
        # another claim across this commit and the bounded in-flight request.
        claim_until = datetime.now(UTC) + _CREATE_CLAIM_DURATION
        if attempt.next_attempt_at is None or attempt.next_attempt_at < claim_until:
            attempt.next_attempt_at = claim_until
        await session.commit()
        pair, attempt = await _lock_attempt_authority(session, attempt_id, project_id, owner_id)
        if (
            attempt is None
            or attempt.status != "pending"
            or attempt.provider_payment_id is not None
            or attempt.attempts != claim
            or not attempt.create_dispatch_claimed
            or pair is None
            or not _same_authority(attempt, pair)
            or stamp != _grant_stamp(pair[1])
        ):
            await session.rollback()
            return
        if not await _create_allowed(session, attempt):
            _terminal(attempt, "canceled" if first_create else "needs_review")
            await session.commit()
            return

    try:
        response = await _call_provider(attempt, credentials, auth_mode=pair[1].auth_mode)
        if response.is_success:
            observed = _observe_payment(attempt, response.json())
            if creating:
                # Only an independent GET can confirm a paid order. Store the
                # provider ID first and immediately make it due for verification.
                try:
                    async with session.begin_nested():
                        attempt.provider_payment_id = observed.id
                        attempt.is_test = observed.is_test
                        attempt.confirmation_url = (
                            observed.confirmation_url if observed.status == "pending" else None
                        )
                        attempt.next_attempt_at = now
                        await session.flush()
                except IntegrityError:
                    await session.refresh(attempt)
                    _terminal(attempt, "needs_review")
            else:
                attempt.is_test = observed.is_test
                attempt.verified_at = now
                if observed.status == "succeeded":
                    attempt.paid_at = now
                    _terminal(attempt, "succeeded")
                elif observed.status == "canceled":
                    _terminal(attempt, "canceled")
                elif observed.confirmation_url is not None:
                    attempt.confirmation_url = observed.confirmation_url
        elif (
            creating and response.status_code not in {401, 403, 429} and response.status_code < 500
        ):
            rejection = _invalid_create_request(response) if first_create else None
            if rejection is None:
                _terminal(attempt, "needs_review")
            elif rejection.get("parameter") == "receipt":
                attempt.failure_reason = "receipt_required"
                _terminal(attempt, "needs_review")
            else:
                # This checkout is closed without a created provider payment.
                _terminal(attempt, "canceled")
    except httpx.RequestError:
        # The POST may have been accepted. The next attempt uses its exact
        # persisted key and body, never a replacement attempt.
        pass
    except ValueError:
        _terminal(attempt, "needs_review")
    await session.commit()


async def _advance(
    session: AsyncSession,
    attempt_id: UUID,
    *,
    create_only: bool = False,
) -> None:
    try:
        async with asyncio.timeout(20):
            await _advance_locked(
                session, attempt_id, create_only=create_only, now=datetime.now(UTC)
            )
    except Exception as error:
        await session.rollback()
        # Record a cooldown independently of the failed transaction. A busy or
        # broken attempt must not consume the first slot on every worker scan.
        try:
            async with asyncio.timeout(3):
                now = datetime.now(UTC)
                row = await session.scalar(
                    select(MaxStaffPaymentAttempt)
                    .where(
                        MaxStaffPaymentAttempt.id == attempt_id,
                        MaxStaffPaymentAttempt.status == "pending",
                        MaxStaffPaymentAttempt.next_attempt_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if row is not None:
                    row.attempts += 1
                    _schedule(row, now)
                await session.commit()
        except Exception as retry_error:
            await session.rollback()
            log.warning("staff_checkout.backoff_failed", error_type=type(retry_error).__name__)
        if not (
            isinstance(error, (TimeoutError, OperationalError))
            or (isinstance(error, DBAPIError) and getattr(error.orig, "sqlstate", None) == "55P03")
        ):
            raise


async def get_staff_checkout(
    session: AsyncSession,
    project_id: UUID,
    order_id: UUID,
    actor_id: int,
) -> MaxStaffPaymentAttempt:
    attempt = await session.scalar(
        select(MaxStaffPaymentAttempt)
        .join(Project, Project.id == MaxStaffPaymentAttempt.project_id)
        .where(
            MaxStaffPaymentAttempt.project_id == project_id,
            MaxStaffPaymentAttempt.order_id == order_id,
            MaxStaffPaymentAttempt.actor_id == actor_id,
            MaxStaffPaymentAttempt.owner_id == Project.owner_id,
        )
        .execution_options(populate_existing=True),
    )
    if attempt is None:
        raise ApiError("not_found", "Оплата заказа не найдена", 404)
    return attempt


async def create_staff_checkout(
    session: AsyncSession,
    project_id: UUID,
    order_id: UUID,
    actor_id: int,
    return_url: str | None,
) -> MaxStaffPaymentAttempt:
    from yleum_api.services.max_staff import get_order_checkout_settings
    from yleum_api.services.max_staff_source import read_staff_checkout_order

    settings = await get_order_checkout_settings(session, project_id)
    owner_id = settings.owner_id
    project = await _owned(session, project_id, owner_id)
    try:
        target = await _target(project)
    except _State:
        raise ApiError(
            "invalid_request", "Оплата доступна в опубликованном приложении", 409
        ) from None
    destination = _build_return_url(target.url, order_id, return_url)
    try:
        existing = await get_staff_checkout(session, project_id, order_id, actor_id)
    except ApiError as exc:
        if exc.status_code != 404:
            raise
    else:
        attempt_id = existing.id
        await _advance(session, attempt_id, create_only=True)
        return await get_staff_checkout(session, project_id, order_id, actor_id)

    order = await read_staff_checkout_order(session, project_id, order_id, actor_id)
    if (
        order.id != order_id
        or order.actor_id != actor_id
        or order.currency != "RUB"
        or not 0 < order.total_kopecks <= _MAX_AMOUNT_KOPECKS
        or settings.orders_enabled_at is None
        or order.created_at < settings.orders_enabled_at
    ):
        raise ApiError("invalid_request", "Заказ недоступен для онлайн-оплаты", 409)
    await lock_account_provider(session, owner_id, "yookassa")
    concurrent = await session.scalar(
        select(MaxStaffPaymentAttempt).where(
            MaxStaffPaymentAttempt.project_id == project_id,
            MaxStaffPaymentAttempt.order_id == order_id,
        )
    )
    if concurrent is not None:
        if concurrent.actor_id != actor_id or concurrent.owner_id != owner_id:
            raise ApiError("not_found", "Оплата заказа не найдена", 404)
        attempt_id = concurrent.id
    else:
        pair = await _bound_yookassa(session, project_id, owner_id, lock=True)
        shop_id = pair[1].public_config.get("shop_id") if pair is not None else None
        if pair is None or not isinstance(shop_id, str) or not shop_id:
            raise ApiError("payment_integration_required", "Подключите ЮKassa к приложению", 409)
        if await _project_deleting(session, project_id):
            raise ApiError("conflict", "Проект удаляется. Оплата недоступна", 409)
        binding, connection = pair
        attempt_id = uuid4()
        now = datetime.now(UTC)
        session.add(
            MaxStaffPaymentAttempt(
                id=attempt_id,
                project_id=project_id,
                order_id=order_id,
                owner_id=owner_id,
                binding_id=binding.id,
                integration_id=connection.id,
                actor_id=actor_id,
                shop_id=shop_id,
                auth_mode=connection.auth_mode,
                grant_revision=connection.grant_revision,
                oauth_client_key=connection.oauth_client_key,
                amount_kopecks=order.total_kopecks,
                currency=order.currency,
                status="pending",
                created_at=now,
                next_attempt_at=now,
                request_body={
                    "amount": {"value": _money(order.total_kopecks), "currency": order.currency},
                    "capture": True,
                    "confirmation": {"type": "redirect", "return_url": destination},
                    "description": f"Заказ {order_id}",
                    "metadata": {_ATTEMPT_KEY: str(attempt_id)},
                },
            )
        )
    # Commit the immutable request/key before the first possible external effect.
    await session.commit()
    await _advance(session, attempt_id, create_only=True)
    return await get_staff_checkout(session, project_id, order_id, actor_id)


async def read_staff_payment_proofs(
    session: AsyncSession,
    *,
    project_id: UUID,
    owner_id: UUID,
    order_amounts: Mapping[UUID, int],
    test: bool = False,
) -> dict[UUID, datetime]:
    if not order_amounts:
        return {}
    rows = await session.execute(
        select(
            MaxStaffPaymentAttempt.order_id,
            MaxStaffPaymentAttempt.amount_kopecks,
            MaxStaffPaymentAttempt.paid_at,
        )
        .join(Project, Project.id == MaxStaffPaymentAttempt.project_id)
        .where(
            MaxStaffPaymentAttempt.project_id == project_id,
            MaxStaffPaymentAttempt.owner_id == owner_id,
            Project.owner_id == owner_id,
            MaxStaffPaymentAttempt.order_id.in_(order_amounts),
            MaxStaffPaymentAttempt.currency == "RUB",
            MaxStaffPaymentAttempt.status == "succeeded",
            MaxStaffPaymentAttempt.is_test.is_(test),
            MaxStaffPaymentAttempt.verified_at.is_not(None),
            MaxStaffPaymentAttempt.provider_payment_id.is_not(None),
            MaxStaffPaymentAttempt.paid_at.is_not(None),
        )
    )
    return {
        order_id: paid_at
        for order_id, amount, paid_at in rows
        if paid_at is not None and order_amounts.get(order_id) == amount
    }


async def reconcile_due_staff_payments(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        due = (
            await session.scalars(
                select(MaxStaffPaymentAttempt.id)
                .where(
                    MaxStaffPaymentAttempt.status == "pending",
                    MaxStaffPaymentAttempt.next_attempt_at <= datetime.now(UTC),
                )
                .order_by(MaxStaffPaymentAttempt.next_attempt_at, MaxStaffPaymentAttempt.id)
                .limit(4)
                .with_for_update(skip_locked=True)
            )
        ).all()
    for attempt_id in due:
        try:
            async with factory() as session:
                await _advance(session, attempt_id)
        except Exception as exc:
            log.warning("staff_checkout.attempt_failed", error_type=type(exc).__name__)


async def run_staff_checkout_reconciliation_loop(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    while True:
        try:
            await reconcile_due_staff_payments(factory)
        except Exception as exc:
            log.warning("staff_checkout.scan_failed", error_type=type(exc).__name__)
        await asyncio.sleep(8)
