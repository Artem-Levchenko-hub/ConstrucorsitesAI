"""Disposable PostgreSQL security flows; all outgoing email is captured locally."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.core.security import create_access_token, hash_password, verify_password
from yleum_api.models.account import AuthSession, AuthToken
from yleum_api.models.account_security import AccountSecurityEmail, EmailChangeChallenge
from yleum_api.models.oauth_login import UserIdentity
from yleum_api.models.user import User
from yleum_api.routers import account_credentials as credential_routes
from yleum_api.routers import auth
from yleum_api.services import account_credentials as credentials
from yleum_api.services import account_security_email as delivery
from yleum_api.services.transactional_email import EmailDeliveryFailed, EmailDeliveryNotConfigured

PASSWORD = "current-password123"


@pytest.fixture
def mail(monkeypatch):
    letters = []

    async def capture(**values):
        letters.append(values)

    monkeypatch.setattr(credential_routes, "send_transactional_email", capture)
    monkeypatch.setattr(auth, "send_transactional_email", capture)
    monkeypatch.setattr(delivery, "send_transactional_email", capture)
    return letters


async def owner(session, *, email="owner@example.com", password=True):
    user = User(
        email=email,
        status="active",
        is_anon=False,
        password_hash=await hash_password(PASSWORD) if password else None,
    )
    session.add(user)
    await session.flush()
    login = AuthSession(user_id=user.id, expires_at=datetime.now(UTC) + timedelta(days=1))
    session.add(login)
    await session.commit()
    headers = {
        "origin": get_settings().web_base_url.rstrip("/"),
        "authorization": "Bearer "
        + create_access_token(user.id, session_id=login.id, session_version=user.session_version),
    }
    return user, headers


def cap(letter):
    found = re.search(r"#token=([A-Za-z0-9_-]+)", letter["text"])
    assert found
    return found.group(1)


@pytest.mark.parametrize(
    ("new_password", "wrong_password"),
    [
        pytest.param("replacement456", "replacement457", id="short"),
        pytest.param("🔐" * 127 + "1", "🔐" * 127 + "2", id="maximum-unicode-length"),
    ],
)
async def test_signed_in_password_change_revokes_sessions_and_queues_one_notice(
    client, db_session, mail, new_password, wrong_password,
):
    user, headers = await owner(db_session)
    assert (await client.get("/api/account/credentials", headers=headers)).json() == {
        "has_password": True
    }
    changed = await client.post(
        "/api/account/credentials/password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": new_password},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json() == {"ok": True, "reauthenticate": True}
    assert "Max-Age=0" in changed.headers["set-cookie"]
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    await db_session.refresh(user)
    assert await verify_password(new_password, user.password_hash)
    assert not await verify_password(wrong_password, user.password_hash)
    assert not await verify_password(PASSWORD, user.password_hash)
    assert all(row.revoked_at for row in (await db_session.scalars(select(AuthSession))).all())
    notices = (await db_session.scalars(select(AccountSecurityEmail))).all()
    assert len(notices) == 1 and notices[0].kind == "password_changed"
    assert notices[0].recipient_email == "owner@example.com"
    assert mail == []


async def test_current_password_origin_and_oauth_only_are_not_bypassed(client, db_session, mail):
    user, headers = await owner(db_session)
    data = {"current_password": "incorrect", "new_password": "replacement456"}
    assert (
        await client.post("/api/account/credentials/password", headers=headers, json=data)
    ).status_code == 400
    assert (
        await client.post(
            "/api/account/credentials/password",
            headers={**headers, "origin": "https://evil.example"},
            json={**data, "current_password": PASSWORD},
        )
    ).status_code == 403
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 0
    user.password_hash = None
    await db_session.commit()
    assert (await client.get("/api/account/credentials", headers=headers)).json() == {
        "has_password": False
    }
    assert (
        await client.post("/api/account/credentials/password", headers=headers, json=data)
    ).status_code == 400
    assert (
        await client.post(
            "/api/account/credentials/email",
            headers=headers,
            json={"current_password": PASSWORD, "new_email": "new@example.com"},
        )
    ).status_code == 400


@pytest.mark.parametrize("first", [0, 1])
async def test_email_requires_both_mailboxes_and_preserves_oauth_identity(
    client, db_session, mail, first
):
    user, headers = await owner(db_session)
    session = AuthToken(
        user_id=user.id,
        purpose="verify_email",
        token_hash=credentials.token_hash("legacy-token-" + "a" * 40),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    identity = UserIdentity(
        user_id=user.id, provider="yandex", provider_user_id="test-subject", email=user.email
    )
    db_session.add_all([session, identity])
    await db_session.commit()
    response = await client.post(
        "/api/account/credentials/email",
        headers=headers,
        json={"current_password": PASSWORD, "new_email": "new@example.com"},
    )
    assert (response.status_code, response.json()) == (202, {"status": "confirmation_sent"})
    assert [letter["recipient"] for letter in mail] == ["owner@example.com", "new@example.com"]
    assert all("cid:yleum-logo" in letter["html"] for letter in mail)
    tokens = [cap(letter) for letter in mail]
    row = (await db_session.scalars(select(EmailChangeChallenge))).one()
    assert row.old_token_hash not in tokens and row.new_token_hash not in tokens
    assert user.email == "owner@example.com"
    path = "/api/account/credentials/email/confirm"
    assert (await client.get(path + "?token=" + tokens[first])).status_code == 405
    pending = await client.post(path, headers=headers, json={"token": tokens[first]})
    assert pending.json() == {"status": "pending", "reauthenticate": False}
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    assert user.email == "owner@example.com"
    complete = await client.post(path, headers=headers, json={"token": tokens[1 - first]})
    assert complete.json() == {"status": "completed", "reauthenticate": True}
    await db_session.refresh(user)
    assert user.email == "new@example.com" and user.email_verified_at
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert (
        await client.post(path, headers=headers, json={"token": tokens[first]})
    ).status_code == 400
    await db_session.refresh(identity)
    assert identity.provider_user_id == "test-subject" and identity.email == "owner@example.com"
    old_verify = await client.post(
        "/api/auth/email/verify", json={"token": "legacy-token-" + "a" * 40}
    )
    assert old_verify.status_code == 400
    notices = (await db_session.scalars(select(AccountSecurityEmail))).all()
    assert {item.recipient_email for item in notices} == {"owner@example.com", "new@example.com"}
    assert len(notices) == 2


async def test_expired_superseded_and_session_changed_challenges_cannot_complete(db_session):
    user, _ = await owner(db_session)
    _, _, old1, new1 = await credentials.request_email_change(
        db_session, user.id, PASSWORD, "one@example.com"
    )
    _, _, old2, _new2 = await credentials.request_email_change(
        db_session, user.id, PASSWORD, "two@example.com"
    )
    for token in (old1, new1):
        with pytest.raises(ApiError):
            await credentials.confirm_email_change(db_session, token)
    await db_session.execute(
        update(EmailChangeChallenge)
        .where(EmailChangeChallenge.used_at.is_(None))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    with pytest.raises(ApiError):
        await credentials.confirm_email_change(db_session, old2)
    _, _, old3, new3 = await credentials.request_email_change(
        db_session, user.id, PASSWORD, "three@example.com"
    )
    user.session_version += 1
    await db_session.commit()
    for token in (old3, new3):
        with pytest.raises(ApiError):
            await credentials.confirm_email_change(db_session, token)
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 0


async def test_password_mutation_and_notice_rollback_together(db_session, monkeypatch):
    user, _ = await owner(db_session)
    user_id = user.id

    def fail(*args, **kwargs):
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(credentials, "enqueue_notice", fail)
    with pytest.raises(RuntimeError):
        await credentials.change_password(db_session, user_id, PASSWORD, "replacement456")
    await db_session.rollback()
    user = await db_session.get(User, user_id)
    assert await verify_password(PASSWORD, user.password_hash)
    assert user.session_version == 0
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 0
    assert (await db_session.scalars(select(AuthSession))).one().revoked_at is None


async def test_parallel_confirmations_complete_once(test_engine, db_session):
    user, _ = await owner(db_session)
    _, _, old, new = await credentials.request_email_change(
        db_session, user.id, PASSWORD, "new@example.com"
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def confirm(token):
        async with factory() as session:
            try:
                return bool(await credentials.confirm_email_change(session, token))
            except ApiError:
                return "used"

    results = await asyncio.gather(confirm(old), confirm(new), confirm(new))
    assert results.count(True) == 1
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 2


@pytest.mark.parametrize(
    "failure",
    [
        EmailDeliveryNotConfigured(),
        EmailDeliveryFailed("unavailable", retry_safe=True),
        EmailDeliveryFailed("ambiguous"),
    ],
)
async def test_forgot_failure_response_does_not_reveal_account(
    client, db_session, mail, monkeypatch, failure
):
    await owner(db_session)

    async def fail(**kwargs):
        raise failure

    monkeypatch.setattr(auth, "send_transactional_email", fail)
    known = await client.post("/api/auth/password/forgot", json={"email": "owner@example.com"})
    unknown = await client.post("/api/auth/password/forgot", json={"email": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"accepted": True}


@pytest.mark.parametrize("safe,expected", [(True, "pending"), (False, "uncertain")])
async def test_outbox_retry_safety_claims_and_frozen_recipient(
    test_engine, db_session, monkeypatch, safe, expected
):
    user, _ = await owner(db_session)
    await credentials.change_password(db_session, user.id, PASSWORD, "replacement456")
    user.email = "later@example.com"
    await db_session.commit()
    calls = []

    async def fail(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0.01)
        raise EmailDeliveryFailed("test", retry_safe=safe)

    monkeypatch.setattr(delivery, "send_transactional_email", fail)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    await asyncio.gather(*(delivery.deliver_one_account_security_email(factory) for _ in range(3)))
    row = (await db_session.scalars(select(AccountSecurityEmail))).one()
    assert row.status == expected and row.attempts == 1
    assert len(calls) == 1 and calls[0]["recipient"] == "owner@example.com"
    assert calls[0]["message_id"] == row.id


async def test_stale_sending_is_never_replayed(test_engine, db_session, mail):
    user, _ = await owner(db_session)
    await credentials.change_password(db_session, user.id, PASSWORD, "replacement456")
    row = (await db_session.scalars(select(AccountSecurityEmail))).one()
    row.status, row.claimed_at = "sending", datetime.now(UTC) - timedelta(minutes=11)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert not await delivery.deliver_one_account_security_email(factory)
    await db_session.refresh(row)
    assert row.status == "uncertain" and mail == []


@pytest.mark.parametrize(
    ("new_password", "wrong_password"),
    [
        pytest.param("replacement456", "replacement457", id="short"),
        pytest.param("я" * 127 + "1", "я" * 127 + "2", id="maximum-unicode-length"),
    ],
)
async def test_reset_sends_branded_link_and_commits_success_notice_once(
    client, db_session, mail, new_password, wrong_password,
):
    user, _ = await owner(db_session)
    requested = await client.post("/api/auth/password/forgot", json={"email": user.email})
    assert requested.status_code == 202
    assert len(mail) == 1 and "cid:yleum-logo" in mail[0]["html"]
    token = re.search(r"token=([A-Za-z0-9_-]+)", mail[0]["text"]).group(1)
    changed = await client.post(
        "/api/auth/password/reset", json={"token": token, "password": new_password}
    )
    assert changed.status_code == 200
    await db_session.refresh(user)
    assert await verify_password(new_password, user.password_hash)
    assert not await verify_password(wrong_password, user.password_hash)
    assert not await verify_password(PASSWORD, user.password_hash)
    notice = (await db_session.scalars(select(AccountSecurityEmail))).one()
    assert notice.kind == "password_reset" and notice.recipient_email == "owner@example.com"
    assert (
        await client.post(
            "/api/auth/password/reset", json={"token": token, "password": "replacement789"}
        )
    ).status_code == 400
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 1


async def test_competing_accounts_cannot_claim_the_same_email(test_engine, db_session):
    one, _ = await owner(db_session, email="one@example.com")
    two, _ = await owner(db_session, email="two@example.com")
    _, _, old_one, new_one = await credentials.request_email_change(
        db_session, one.id, PASSWORD, "shared@example.com"
    )
    _, _, old_two, new_two = await credentials.request_email_change(
        db_session, two.id, PASSWORD, "shared@example.com"
    )
    assert not await credentials.confirm_email_change(db_session, old_one)
    assert not await credentials.confirm_email_change(db_session, old_two)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def confirm(token):
        async with factory() as session:
            try:
                return bool(await credentials.confirm_email_change(session, token))
            except ApiError as error:
                return error.status_code

    results = await asyncio.gather(confirm(new_one), confirm(new_two))
    assert results.count(True) == 1 and results.count(400) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(User).where(User.email == "shared@example.com")
        )
        == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 2


async def test_email_completion_rollback_keeps_prior_login_and_proof_retryable(
    db_session, monkeypatch
):
    user, _ = await owner(db_session)
    user_id = user.id
    _, _, old, new = await credentials.request_email_change(
        db_session, user.id, PASSWORD, "new@example.com"
    )
    assert not await credentials.confirm_email_change(db_session, old)
    original = credentials.enqueue_notice

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected notice failure")

    monkeypatch.setattr(credentials, "enqueue_notice", fail)
    with pytest.raises(RuntimeError):
        await credentials.confirm_email_change(db_session, new)
    await db_session.rollback()
    user = await db_session.get(User, user_id)
    assert user.email == "owner@example.com" and user.session_version == 0
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 0
    monkeypatch.setattr(credentials, "enqueue_notice", original)
    assert await credentials.confirm_email_change(db_session, new)
    assert await db_session.scalar(select(func.count()).select_from(AccountSecurityEmail)) == 2


async def test_forgot_waiting_on_email_change_cannot_issue_old_mailbox_reset(
    test_engine, db_session, mail, monkeypatch
):
    from yleum_api.schemas.user import EmailTokenRequest

    user, _ = await owner(db_session)
    user_id = user.id
    _, _, old, new = await credentials.request_email_change(
        db_session, user_id, PASSWORD, "new@example.com"
    )
    assert not await credentials.confirm_email_change(db_session, old)
    reached, resume = asyncio.Event(), asyncio.Event()
    original = auth._issue_email_token

    async def paused_issue(*args, **kwargs):
        reached.set()
        await resume.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(auth, "_issue_email_token", paused_issue)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def forgot():
        async with factory() as session:
            return await auth.forgot_password(EmailTokenRequest(email="owner@example.com"), session)

    pending = asyncio.create_task(forgot())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        async with factory() as session:
            assert await credentials.confirm_email_change(session, new)
        resume.set()
        assert await asyncio.wait_for(pending, 5) == {"accepted": True}
    finally:
        resume.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert mail == []
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuthToken)
            .where(
                AuthToken.user_id == user_id,
                AuthToken.purpose == "reset_password",
                AuthToken.used_at.is_(None),
            )
        )
        == 0
    )


@pytest.mark.parametrize("kind", ["email", "password"])
async def test_target_capability_preserves_unrelated_browser_cookie(client, db_session, mail, kind):
    target, target_headers = await owner(db_session)
    other, other_headers = await owner(db_session, email="other@example.com")
    target_id, other_id = target.id, other.id
    if kind == "email":
        _, _, old, token = await credentials.request_email_change(
            db_session, target_id, PASSWORD, "new@example.com"
        )
        assert not await credentials.confirm_email_change(db_session, old)
        path, body = "/api/account/credentials/email/confirm", {"token": token}
    else:
        token = await auth._issue_email_token(
            db_session, target, "reset_password", ttl=timedelta(minutes=30)
        )
        path, body = "/api/auth/password/reset", {"token": token, "password": "replacement456"}
    client.cookies.set(get_settings().jwt_cookie_name, other_headers["authorization"][7:])
    response = await client.post(
        path, json=body, headers={"origin": get_settings().web_base_url.rstrip("/")}
    )
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    if kind == "email":
        assert response.json() == {"status": "completed", "reauthenticate": False}
    assert (await client.get("/api/auth/me")).json()["id"] == str(other_id)
    client.cookies.clear()
    assert (await client.get("/api/auth/me", headers=target_headers)).status_code == 401
    other_login = (
        await db_session.scalars(select(AuthSession).where(AuthSession.user_id == other_id))
    ).one()
    assert other_login.revoked_at is None


async def test_reset_consumption_refreshes_preloaded_principal_version(test_engine, db_session):
    user, _ = await owner(db_session)
    user_id = user.id
    token = await auth._issue_email_token(
        db_session, user, "reset_password", ttl=timedelta(minutes=30)
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as concurrent:
        await concurrent.execute(update(User).where(User.id == user_id).values(session_version=4))
        await concurrent.commit()
    consumed = await auth._consume_email_token(
        db_session, token, "reset_password", datetime.now(UTC)
    )
    await credentials.apply_password_change(
        db_session, consumed, await hash_password("replacement456"), kind="password_reset"
    )
    await db_session.commit()
    await db_session.refresh(user)
    assert user.session_version == 5
