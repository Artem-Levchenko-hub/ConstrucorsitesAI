"""One-time phone proof with durable, serialized SMS cost admission."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.phone import PhoneChallenge
from yleum_api.models.user import User
from yleum_api.services.sms_delivery import (
    SmsDeliveryAmbiguous,
    SmsDeliveryFailed,
    send_verification_sms,
    sms_delivery_configured,
)

CODE_TTL_SECONDS = 600
RESEND_SECONDS = 60


def normalize_phone(raw: str) -> str:
    # Restrict to Russian mobile numbers. +7 also covers Kazakhstan; accepting
    # any eleven digits would silently route OTPs outside the supported market.
    if len(raw) <= 40 and re.fullmatch(r"\+?[0-9 ()-]+", raw):
        digits = re.sub(r"[ ()-]", "", raw).removeprefix("+")
        if re.fullmatch(r"[78]9[0-9]{9}", digits):
            return "+7" + digits[1:]
    raise ApiError("phone_invalid", "Укажите российский мобильный номер в формате +7 999 123-45-67")


def _digest(message: str) -> str:
    key = get_settings().jwt_secret.get_secret_value().encode()
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def otp_hash(user_id: UUID, challenge_id: UUID, code: str) -> str:
    return _digest(f"phone-otp:{user_id}:{challenge_id}:{code}")


def phone_verification_required(user: User) -> bool:
    return bool(
        get_settings().phone_verification_enabled
        and not user.is_anon
        and not (user.phone_e164 and user.phone_verified_at)
    )


def require_verified_phone(user: User) -> None:
    if user.is_anon or user.email is None:
        raise ApiError("max_registration_required", "Для Yleum нужна регистрация", 403)
    if phone_verification_required(user):
        raise ApiError("phone_verification_required", "Подтвердите телефон для продолжения", 403)


def _delivery_unavailable() -> ApiError:
    return ApiError(
        "phone_delivery_unavailable",
        "Отправка SMS временно недоступна. Попробуйте позже или обратитесь в поддержку",
        503,
    )


def _rate_limited() -> ApiError:
    return ApiError(
        "phone_rate_limited",
        "Слишком много запросов SMS. Повторите позже",
        429,
        details={"resend_after": RESEND_SECONDS},
    )


async def _locked_user(session: AsyncSession, user: User) -> User:
    locked = (
        await session.scalars(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    if locked.status != "active" or locked.is_anon or locked.email is None:
        raise ApiError("account_unavailable", "Нужен действующий аккаунт с email", 403)
    return locked


async def request_phone_code(
    session: AsyncSession,
    user: User,
    raw_phone: str,
    *,
    ip_address: str | None,
) -> dict[str, bool | int]:
    phone = normalize_phone(raw_phone)
    if not sms_delivery_configured():
        raise _delivery_unavailable()
    # Short global transaction lock makes ALL cost scopes exact across workers,
    # including first requests where no per-number/IP row exists to lock yet.
    # Never hold it during network I/O.
    await session.execute(text("SELECT pg_advisory_xact_lock(734295812)"))
    locked = await _locked_user(session, user)
    if locked.phone_verified_at:
        if locked.phone_e164 != phone:
            raise ApiError(
                "forbidden", "Телефон уже подтверждён. Для смены обратитесь в поддержку", 403
            )
        return {"accepted": True, "expires_in": CODE_TTL_SECONDS, "resend_after": RESEND_SECONDS}
    owner = await session.scalar(select(User.id).where(User.phone_e164 == phone))
    if owner is not None:
        raise ApiError("phone_already_registered", "Телефон уже связан с другим аккаунтом", 409)
    now = datetime.now(UTC)
    # Retain only a short operational trail. Seven days preserves every rolling
    # 24-hour cost claim; neither expired nor consumed OTPs authorize identity.
    await session.execute(delete(PhoneChallenge).where(
        PhoneChallenge.created_at < now - timedelta(days=7),
        PhoneChallenge.expires_at < now,
    ))
    ip_hash = _digest(f"phone-ip:{ip_address or 'unknown'}")
    recent = await session.scalar(
        select(PhoneChallenge.id)
        .where(
            PhoneChallenge.created_at > now - timedelta(seconds=RESEND_SECONDS),
            or_(PhoneChallenge.user_id == locked.id, PhoneChallenge.phone_e164 == phone),
        )
        .limit(1)
    )
    if recent is not None:
        raise _rate_limited()
    settings = get_settings()
    scopes = (
        (PhoneChallenge.user_id == locked.id, settings.sms_user_daily_limit),
        (PhoneChallenge.phone_e164 == phone, settings.sms_phone_daily_limit),
        (PhoneChallenge.ip_hash == ip_hash, settings.sms_ip_daily_limit),
        (true(), settings.sms_global_daily_limit),
    )
    for scope, limit in scopes:
        count = await session.scalar(
            select(func.count())
            .select_from(PhoneChallenge)
            .where(
                PhoneChallenge.created_at > now - timedelta(days=1),
                scope,
            )
        )
        if count is not None and count >= limit:
            raise _rate_limited()
    await session.execute(
        update(PhoneChallenge)
        .where(
            PhoneChallenge.user_id == locked.id,
            PhoneChallenge.used_at.is_(None),
        )
        .values(used_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge_id = uuid4()
    claim = PhoneChallenge(
        id=challenge_id,
        user_id=locked.id,
        session_version=locked.session_version,
        phone_e164=phone,
        code_hash=otp_hash(locked.id, challenge_id, code),
        ip_hash=ip_hash,
        created_at=now,
        expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
        delivery_status="sending",
    )
    session.add(claim)
    await session.commit()
    # The durable claim counts failures and ambiguous outcomes as well as sends.
    try:
        message_id = await send_verification_sms(phone, code)
    except SmsDeliveryFailed as exc:
        delivery_status = "ambiguous" if isinstance(exc, SmsDeliveryAmbiguous) else "failed"
        await session.execute(
            update(PhoneChallenge)
            .where(PhoneChallenge.id == challenge_id)
            .values(
                delivery_status=delivery_status,
            )
        )
        await session.commit()
        raise _delivery_unavailable() from None
    await session.execute(
        update(PhoneChallenge)
        .where(PhoneChallenge.id == challenge_id)
        .values(
            delivery_status="sent",
            provider_message_id=message_id,
        )
    )
    await session.commit()
    return {"accepted": True, "expires_in": CODE_TTL_SECONDS, "resend_after": RESEND_SECONDS}


async def verify_phone_code(session: AsyncSession, user: User, code: str) -> None:
    locked = await _locked_user(session, user)
    now = datetime.now(UTC)
    challenge = (
        await session.scalars(
            select(PhoneChallenge)
            .where(
                PhoneChallenge.user_id == locked.id,
                PhoneChallenge.used_at.is_(None),
            )
            .order_by(PhoneChallenge.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).first()
    if (
        locked.phone_verified_at
        or challenge is None
        or challenge.session_version != locked.session_version
        or challenge.delivery_status == "failed"
    ):
        raise ApiError("code_invalid", "Код недействителен. Запросите новый")
    if challenge.expires_at <= now:
        raise ApiError("code_expired", "Срок кода истёк. Запросите новый")
    if challenge.attempts >= 5:
        raise ApiError("code_attempts_exceeded", "Попытки исчерпаны. Запросите новый код")
    if not hmac.compare_digest(challenge.code_hash, otp_hash(locked.id, challenge.id, code)):
        challenge.attempts += 1
        await session.commit()
        raise ApiError("code_invalid", "Неверный код")
    locked.phone_e164 = challenge.phone_e164
    locked.phone_verified_at = now
    challenge.used_at = now
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent users can receive codes, but only one verified owner wins.
        await session.rollback()
        raise ApiError(
            "phone_already_registered", "Телефон уже связан с другим аккаунтом", 409
        ) from None
