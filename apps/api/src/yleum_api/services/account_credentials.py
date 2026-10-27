"""Credential mutations lock the owner before challenges and commit notices atomically."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.core.security import hash_password, verify_password
from yleum_api.models.account import AuthSession, AuthToken
from yleum_api.models.account_security import AccountSecurityEmail, EmailChangeChallenge
from yleum_api.models.user import User


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def invalid_confirmation() -> ApiError:
    return ApiError(
        "token_invalid", "Ссылка недействительна или истекла. Запросите смену email заново.", 400
    )


async def lock_account(session: AsyncSession, user_id: UUID) -> User:
    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.status != "active" or user.is_anon or not user.email:
        raise ApiError("unauthorized", "Войдите в действующий аккаунт", 401)
    return user


async def authenticate_change(session: AsyncSession, user_id: UUID, password: str) -> User:
    user = await lock_account(session, user_id)
    if user.password_hash is None:
        raise ApiError("bad_request", "Сначала задайте пароль через восстановление доступа", 400)
    if not await verify_password(password, user.password_hash):
        raise ApiError("bad_request", "Текущий пароль неверен", 400)
    return user


async def revoke_credentials(session: AsyncSession, user: User, now: datetime) -> None:
    user.session_version += 1
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None))
        .values(used_at=now)
    )
    await session.execute(
        update(EmailChangeChallenge)
        .where(EmailChangeChallenge.user_id == user.id, EmailChangeChallenge.used_at.is_(None))
        .values(used_at=now)
    )


def enqueue_notice(
    session: AsyncSession, user: User, kind: str, recipient: str, *, event_key: str
) -> None:
    session.add(
        AccountSecurityEmail(
            user_id=user.id, kind=kind, recipient_email=recipient, event_key=event_key
        )
    )


async def apply_password_change(
    session: AsyncSession, user: User, password_hash: str, *, kind: str
) -> None:
    """Caller holds User lock; reset token spending shares this transaction."""
    user.password_hash = password_hash
    await revoke_credentials(session, user, datetime.now(UTC))
    if user.email:
        enqueue_notice(session, user, kind, user.email, event_key=f"{kind}:{uuid4()}")


async def change_password(
    session: AsyncSession, user_id: UUID, current_password: str, new_password: str
) -> None:
    user = await authenticate_change(session, user_id, current_password)
    await apply_password_change(
        session, user, await hash_password(new_password), kind="password_changed"
    )
    await session.commit()


async def request_email_change(
    session: AsyncSession, user_id: UUID, current_password: str, new_email: str
) -> tuple[str, str, str, str]:
    user = await authenticate_change(session, user_id, current_password)
    assert user.email is not None
    new_email = new_email.lower()
    if user.email.lower() == new_email:
        raise ApiError("bad_request", "Укажите новый email", 400)
    if await session.scalar(select(User.id).where(User.email == new_email)):
        raise ApiError("conflict", "Этот email недоступен", 409)
    now = datetime.now(UTC)
    await session.execute(
        update(EmailChangeChallenge)
        .where(EmailChangeChallenge.user_id == user.id, EmailChangeChallenge.used_at.is_(None))
        .values(used_at=now)
    )
    old_token, new_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    session.add(
        EmailChangeChallenge(
            user_id=user.id,
            old_email=user.email,
            new_email=new_email,
            session_version=user.session_version,
            old_token_hash=token_hash(old_token),
            new_token_hash=token_hash(new_token),
            expires_at=now + timedelta(minutes=30),
        )
    )
    old_email = str(user.email)
    await session.commit()
    return old_email, new_email, old_token, new_token


async def confirm_email_change(session: AsyncSession, raw: str) -> UUID | None:
    hashed = token_hash(raw)
    matches = or_(
        EmailChangeChallenge.old_token_hash == hashed, EmailChangeChallenge.new_token_hash == hashed
    )
    user_id = await session.scalar(select(EmailChangeChallenge.user_id).where(matches))
    if user_id is None:
        raise invalid_confirmation()
    try:
        user = await lock_account(session, user_id)
    except ApiError:
        raise invalid_confirmation() from None
    challenge = await session.scalar(
        select(EmailChangeChallenge)
        .where(matches)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    if (
        challenge is None
        or challenge.used_at is not None
        or challenge.expires_at <= now
        or challenge.session_version != user.session_version
        or not user.email
        or challenge.old_email.lower() != user.email.lower()
    ):
        raise invalid_confirmation()
    if hashed == challenge.old_token_hash:
        challenge.old_confirmed_at = challenge.old_confirmed_at or now
    else:
        challenge.new_confirmed_at = challenge.new_confirmed_at or now
    if challenge.old_confirmed_at is None or challenge.new_confirmed_at is None:
        await session.commit()
        return None
    if await session.scalar(
        select(User.id).where(User.email == challenge.new_email, User.id != user.id)
    ):
        raise invalid_confirmation()
    old_email, new_email, challenge_id = challenge.old_email, challenge.new_email, challenge.id
    user.email = new_email
    user.email_verified_at = now
    try:
        await revoke_credentials(session, user, now)
        for side, recipient in (("old", old_email), ("new", new_email)):
            enqueue_notice(
                session,
                user,
                "email_changed",
                recipient,
                event_key=f"email_changed:{challenge_id}:{side}",
            )
        await session.commit()
    except IntegrityError:
        # Concurrent claims of one address share the database unique boundary.
        await session.rollback()
        raise invalid_confirmation() from None
    return user_id
