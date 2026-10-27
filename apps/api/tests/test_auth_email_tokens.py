"""Characterisation of the one-time email tokens: verify email, reset password.

Frozen BEFORE the two consumers and the two senders in ``routers/auth.py`` got
single owners, unchanged AFTER. Real app, real database; only SMTP is replaced.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_session
from yleum_api.models.account import AuthSession, AuthToken
from yleum_api.models.user import User
from yleum_api.routers import auth as auth_router
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
)

pytestmark = pytest.mark.asyncio

EMAIL = "owner@example.com"
PASSWORD = "secret123"
REGISTRATION = {
    "email": EMAIL,
    "password": PASSWORD,
    "product": "max",
    "phone": "+79991234567",
    "terms_accepted": True,
    "privacy_accepted": True,
    "personal_data_accepted": True,
    "document_version": get_settings().legal_document_version,
}
INVALID = {"code": "token_invalid", "message": "Ссылка недействительна или истекла"}
UNAVAILABLE = {
    "code": "email_delivery_unavailable",
    "message": "Отправка писем ещё не настроена. Обратитесь в поддержку",
}


class Outbox:
    """Replaces SMTP: keeps every letter, optionally refuses to deliver."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.letters: list[dict[str, str]] = []
        self.failure: Exception | None = None
        monkeypatch.setattr(auth_router, "send_transactional_email", self.send)

    async def send(
        self, *, recipient: str, subject: str, text: str, html: str | None = None,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        self.letters.append({"recipient": recipient, "subject": subject, "text": text})

    def token(self, index: int = -1) -> str:
        found = re.search(r"token=([A-Za-z0-9_-]+)", self.letters[index]["text"])
        assert found is not None
        return found.group(1)

    def code(self, index: int = -1) -> str:
        found = re.search(r"Код: ([0-9]{6})", self.letters[index]["text"])
        assert found is not None
        return found.group(1)


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    return Outbox(monkeypatch)


async def _register(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/register", json=REGISTRATION)
    assert response.status_code == 201


def _error(response: httpx.Response) -> dict[str, str]:
    body = response.json()["error"]
    return {"code": body["code"], "message": body["message"]}


# ── verify email ──────────────────────────────────────────────────────────────


async def test_registration_letter_verifies_the_email_exactly_once(client, outbox, db_session):
    await _register(client)
    (letter,) = outbox.letters
    assert letter["recipient"] == EMAIL
    assert letter["subject"] == "Подтвердите email для Yleum"
    assert "/max/verify-email?token=" in letter["text"]

    first = await client.post("/api/auth/email/verify", json={"token": outbox.token()})
    assert (first.status_code, first.json()) == (200, {"verified": True})
    assert (await client.get("/api/auth/me")).json()["email_verified_at"] is not None
    row = (await db_session.execute(select(AuthToken))).scalar_one()
    assert row.purpose == "verify_email" and row.used_at is not None

    second = await client.post("/api/auth/email/verify", json={"token": outbox.token()})
    assert (second.status_code, _error(second)) == (400, INVALID)
    code_after_link = await client.post("/api/auth/email/verify/code", json={"code": outbox.code()})
    assert code_after_link.status_code == 400


async def test_verify_rejects_unknown_expired_and_foreign_purpose_tokens(
    client, outbox, db_session: AsyncSession
):
    await _register(client)
    unknown = await client.post("/api/auth/email/verify", json={"token": "x" * 64})
    assert (unknown.status_code, _error(unknown)) == (400, INVALID)

    await client.post("/api/auth/password/forgot", json={"email": EMAIL})
    reset_token = outbox.token()
    foreign = await client.post("/api/auth/email/verify", json={"token": reset_token})
    assert (foreign.status_code, _error(foreign)) == (400, INVALID)

    await db_session.execute(
        update(AuthToken)
        .where(AuthToken.purpose == "verify_email")
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    expired = await client.post("/api/auth/email/verify", json={"token": outbox.token(0)})
    assert (expired.status_code, _error(expired)) == (400, INVALID)
    assert (await client.get("/api/auth/me")).json()["email_verified_at"] is None


async def test_a_new_verification_letter_retires_the_previous_link(client, outbox):
    await _register(client)
    again = await client.post("/api/auth/email/verify/request", json={"email": EMAIL})
    assert (again.status_code, again.json()) == (202, {"accepted": True})
    assert len(outbox.letters) == 2 and outbox.token(0) != outbox.token(1)

    old = await client.post("/api/auth/email/verify", json={"token": outbox.token(0)})
    assert (old.status_code, _error(old)) == (400, INVALID)
    new = await client.post("/api/auth/email/verify", json={"token": outbox.token(1)})
    assert new.status_code == 200


async def test_verification_request_requires_session_and_ignores_body_email(client, outbox):
    unknown = await client.post("/api/auth/email/verify/request", json={"email": "no@example.com"})
    assert unknown.status_code == 401
    assert outbox.letters == []

    await _register(client)
    requested = await client.post(
        "/api/auth/email/verify/request", json={"email": "other@example.com"}
    )
    assert requested.status_code == 202
    assert outbox.letters[-1]["recipient"] == EMAIL
    await client.post("/api/auth/email/verify", json={"token": outbox.token()})
    verified = await client.post("/api/auth/email/verify/request", json={"email": EMAIL})
    assert (verified.status_code, verified.json()) == (202, {"accepted": True})
    assert len(outbox.letters) == 2


async def test_code_verifies_once_and_retires_link(client, outbox, db_session):
    await _register(client)
    code = outbox.code()
    row = (await db_session.execute(select(AuthToken))).scalar_one()
    assert row.code_hash and code not in row.code_hash and row.code_attempts == 0
    assert "10 минут" in outbox.letters[-1]["text"]

    first = await client.post("/api/auth/email/verify/code", json={"code": code})
    assert (first.status_code, first.json()) == (200, {"verified": True})
    replay = await client.post("/api/auth/email/verify/code", json={"code": code})
    assert replay.status_code == 400
    old_link = await client.post("/api/auth/email/verify", json={"token": outbox.token()})
    assert (old_link.status_code, _error(old_link)) == (400, INVALID)


async def test_wrong_expired_and_exhausted_codes(client, outbox, db_session):
    await _register(client)
    code = outbox.code()
    wrong = "000000" if code != "000000" else "000001"
    for attempt in range(5):
        response = await client.post("/api/auth/email/verify/code", json={"code": wrong})
        assert response.status_code == 400
        row = (await db_session.execute(select(AuthToken))).scalar_one()
        assert row.code_attempts == attempt + 1
    locked = await client.post("/api/auth/email/verify/code", json={"code": code})
    assert (locked.status_code, _error(locked)["code"]) == (400, "code_attempts_exceeded")

    await client.post("/api/auth/email/verify/request")
    old = await client.post("/api/auth/email/verify/code", json={"code": code})
    if code != outbox.code():
        assert old.status_code == 400
    await db_session.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == auth_router._token_hash(outbox.token()))
        .values(code_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    expired = await client.post("/api/auth/email/verify/code", json={"code": outbox.code()})
    assert (expired.status_code, _error(expired)["code"]) == (400, "code_expired")


async def test_code_cannot_be_used_without_auth_or_by_another_account(site, outbox):
    from yleum_api.main import app

    http, _factory = site
    unauth = await http.post("/api/auth/email/verify/code", json={"code": "123456"})
    assert unauth.status_code == 401
    await _register(http)
    owner_code = outbox.code()
    other = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        registration = {**REGISTRATION, "email": "other@example.com"}
        assert (await other.post("/api/auth/register", json=registration)).status_code == 201
        result = await other.post("/api/auth/email/verify/code", json={"code": owner_code})
        if owner_code != outbox.code():
            assert result.status_code == 400
        assert (await http.get("/api/auth/me")).json()["email_verified_at"] is None
    finally:
        await other.aclose()


async def test_preexisting_link_without_code_remains_usable(client, outbox, db_session):
    await _register(client)
    await db_session.execute(
        update(AuthToken).where(AuthToken.purpose == "verify_email")
        .values(code_hash=None, code_expires_at=None)
    )
    await db_session.commit()
    response = await client.post("/api/auth/email/verify", json={"token": outbox.token()})
    assert (response.status_code, response.json()) == (200, {"verified": True})


# ── reset password ────────────────────────────────────────────────────────────


async def test_reset_letter_changes_the_password_once_and_signs_everyone_out(
    client, outbox, db_session: AsyncSession
):
    await _register(client)
    forgot = await client.post("/api/auth/password/forgot", json={"email": EMAIL})
    assert (forgot.status_code, forgot.json()) == (202, {"accepted": True})
    letter = outbox.letters[-1]
    assert letter["subject"] == "Сброс пароля Yleum"
    assert "/reset-password?token=" in letter["text"] and "30 минут" in letter["text"]
    version = (await db_session.execute(select(User.session_version))).scalar_one()

    reset = await client.post(
        "/api/auth/password/reset", json={"token": outbox.token(), "password": "brand-new-456"}
    )
    assert (reset.status_code, reset.json()) == (200, {"reset": True})

    db_session.expire_all()
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.session_version == version + 1
    sessions = (await db_session.execute(select(AuthSession))).scalars().all()
    assert sessions and all(item.revoked_at is not None for item in sessions)
    assert (await client.get("/api/auth/me")).status_code == 401

    old = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert old.status_code == 401
    new = await client.post("/api/auth/login", json={"email": EMAIL, "password": "brand-new-456"})
    assert new.status_code == 200

    replay = await client.post(
        "/api/auth/password/reset", json={"token": outbox.token(), "password": "third-pass-789"}
    )
    assert (replay.status_code, _error(replay)) == (400, INVALID)


async def test_reset_rejects_unknown_expired_and_foreign_purpose_tokens(
    client, outbox, db_session: AsyncSession
):
    await _register(client)
    verify_token = outbox.token()
    body = {"password": "brand-new-456"}

    unknown = await client.post("/api/auth/password/reset", json={"token": "x" * 64, **body})
    assert (unknown.status_code, _error(unknown)) == (400, INVALID)
    foreign = await client.post("/api/auth/password/reset", json={"token": verify_token, **body})
    assert (foreign.status_code, _error(foreign)) == (400, INVALID)

    await client.post("/api/auth/password/forgot", json={"email": EMAIL})
    await db_session.execute(
        update(AuthToken)
        .where(AuthToken.purpose == "reset_password")
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    expired = await client.post(
        "/api/auth/password/reset", json={"token": outbox.token(), **body}
    )
    assert (expired.status_code, _error(expired)) == (400, INVALID)

    still = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert still.status_code == 200


async def test_forgot_password_stays_silent_for_an_unknown_address(client, outbox):
    response = await client.post("/api/auth/password/forgot", json={"email": "no@example.com"})
    assert (response.status_code, response.json()) == (202, {"accepted": True})
    assert outbox.letters == []


# ── delivery failures ─────────────────────────────────────────────────────────


async def test_green_phone_proof_survives_email_login_and_password_recovery_without_more_sms(
    client, outbox, monkeypatch,
):
    from pydantic import SecretStr

    from yleum_api.services import phone_verification

    settings = get_settings()
    monkeypatch.setattr(settings, "phone_verification_enabled", True)
    monkeypatch.setattr(settings, "sms_provider", "greensms")
    monkeypatch.setattr(settings, "greensms_token", SecretStr("qa-key"))
    monkeypatch.setattr(settings, "smsaero_email", None)
    codes = []

    async def capture_sms(_phone, code):
        codes.append(code)
        return "qa-message"

    monkeypatch.setattr(phone_verification, "send_verification_sms", capture_sms)
    await _register(client)
    requested = await client.post("/api/auth/phone/request", json={"phone": "+79991234567"})
    assert requested.status_code == 202
    assert (await client.post("/api/auth/phone/verify", json={"code": codes[0]})).status_code == 200
    assert (await client.post("/api/auth/logout")).status_code == 204
    logged_in = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert logged_in.status_code == 200
    assert logged_in.json()["phone_e164"] == "+79991234567"
    assert logged_in.json()["email_verified_at"] is None
    assert (await client.get("/api/max/account/access")).json()["can_create_project"] is True
    forgot = await client.post("/api/auth/password/forgot", json={"email": EMAIL})
    assert forgot.status_code == 202
    reset = await client.post(
        "/api/auth/password/reset", json={"token": outbox.token(), "password": "new-green-456"},
    )
    assert reset.status_code == 200
    logged_in = await client.post(
        "/api/auth/login", json={"email": EMAIL, "password": "new-green-456"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["phone_e164"] == "+79991234567"
    assert logged_in.json()["email_verified_at"] is None
    assert logged_in.json()["phone_verification_required"] is False
    assert (await client.get("/api/max/account/access")).json()["can_create_project"] is True
    assert len(codes) == 1


@pytest.mark.parametrize(
    "failure", [EmailDeliveryNotConfigured("no smtp"), EmailDeliveryFailed("smtp said no")]
)
@pytest.mark.parametrize("path", ["/api/auth/email/verify/request"])
async def test_undeliverable_letter_is_a_503_not_a_silent_success(client, outbox, path, failure):
    await _register(client)
    outbox.failure = failure

    response = await client.post(path, json={"email": EMAIL})

    assert (response.status_code, _error(response)) == (503, UNAVAILABLE)


# ── one database session per request, as in production ────────────────────────
# The shared ``client`` fixture gives every request and every assertion the same
# AsyncSession, so it cannot show what was really committed or how two requests
# race for one link. These tests can.


@pytest_asyncio.fixture
async def site(test_engine, db_session):  # db_session: table cleanup only
    from yleum_api.main import app

    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def per_request() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = per_request
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http, factory
    app.dependency_overrides.clear()


async def _reset_link(http: httpx.AsyncClient, outbox: Outbox) -> str:
    await _register(http)
    assert (await http.post("/api/auth/password/forgot", json={"email": EMAIL})).status_code == 202
    return outbox.token()


async def test_one_reset_link_submitted_twice_at_once_works_once(site, outbox):
    http, factory = site
    token = await _reset_link(http, outbox)

    first, second = await asyncio.gather(
        http.post("/api/auth/password/reset", json={"token": token, "password": "first-pass-111"}),
        http.post("/api/auth/password/reset", json={"token": token, "password": "other-pass-222"}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 400]
    async with factory() as fresh:
        assert (await fresh.execute(select(User.session_version))).scalar_one() == 1


async def test_one_verify_link_submitted_twice_at_once_works_once(site, outbox):
    http, _factory = site
    await _register(http)
    token = outbox.token()

    first, second = await asyncio.gather(
        http.post("/api/auth/email/verify", json={"token": token}),
        http.post("/api/auth/email/verify", json={"token": token}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 400]


async def test_one_code_submitted_twice_at_once_works_once(site, outbox):
    http, factory = site
    await _register(http)
    code = outbox.code()

    first, second = await asyncio.gather(
        http.post("/api/auth/email/verify/code", json={"code": code}),
        http.post("/api/auth/email/verify/code", json={"code": code}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 400]
    async with factory() as fresh:
        row = (await fresh.execute(select(AuthToken))).scalar_one()
        user = (await fresh.execute(select(User))).scalar_one()
        assert row.used_at is not None and user.email_verified_at is not None


async def test_code_and_link_race_spends_shared_challenge_once(site, outbox):
    http, factory = site
    await _register(http)

    by_code, by_link = await asyncio.gather(
        http.post("/api/auth/email/verify/code", json={"code": outbox.code()}),
        http.post("/api/auth/email/verify", json={"token": outbox.token()}),
    )

    assert sorted([by_code.status_code, by_link.status_code]) == [200, 400]
    async with factory() as fresh:
        row = (await fresh.execute(select(AuthToken))).scalar_one()
        user = (await fresh.execute(select(User))).scalar_one()
        assert row.used_at is not None and user.email_verified_at is not None


async def test_parallel_wrong_codes_share_five_attempt_budget(site, outbox):
    http, factory = site
    await _register(http)
    actual = outbox.code()
    wrong = "000000" if actual != "000000" else "000001"

    attempts = await asyncio.gather(*(
        http.post("/api/auth/email/verify/code", json={"code": wrong}) for _ in range(8)
    ))

    assert all(response.status_code == 400 for response in attempts)
    async with factory() as fresh:
        row = (await fresh.execute(select(AuthToken))).scalar_one()
        assert row.code_attempts == 5
    blocked = await http.post("/api/auth/email/verify/code", json={"code": actual})
    assert (blocked.status_code, _error(blocked)["code"]) == (400, "code_attempts_exceeded")


async def test_resend_and_old_code_race_has_no_replay(site, outbox):
    http, factory = site
    await _register(http)
    old_code = outbox.code()

    verification, resend = await asyncio.gather(
        http.post("/api/auth/email/verify/code", json={"code": old_code}),
        http.post("/api/auth/email/verify/request"),
    )

    assert verification.status_code in (200, 400)
    assert resend.status_code == 202
    replay = await http.post("/api/auth/email/verify/code", json={"code": old_code})
    if len(outbox.letters) > 1 and old_code != outbox.code():
        assert replay.status_code == 400
    async with factory() as fresh:
        user = (await fresh.execute(select(User))).scalar_one()
        rows = (
            await fresh.execute(select(AuthToken).order_by(AuthToken.created_at))
        ).scalars().all()
        assert rows[0].used_at is not None
        if verification.status_code == 200:
            assert user.email_verified_at is not None and len(rows) in (1, 2)
        else:
            assert user.email_verified_at is None and len(rows) == 2 and rows[1].used_at is None


async def test_consumed_links_are_committed_not_just_flushed(site, outbox):
    http, factory = site
    token = await _reset_link(http, outbox)
    verified = await http.post("/api/auth/email/verify", json={"token": outbox.token(0)})
    reset = await http.post(
        "/api/auth/password/reset", json={"token": token, "password": "brand-new-456"}
    )
    assert (verified.status_code, reset.status_code) == (200, 200)

    async with factory() as fresh:
        user = (await fresh.execute(select(User))).scalar_one()
        tokens = (await fresh.execute(select(AuthToken))).scalars().all()
    assert user.email_verified_at is not None and user.session_version == 1
    assert len(tokens) == 2 and all(row.used_at is not None for row in tokens)


async def test_a_reset_that_fails_midway_leaves_the_link_usable(site, outbox, monkeypatch):
    http, factory = site
    token = await _reset_link(http, outbox)
    hash_password = auth_router.hash_password

    async def broken(_password: str) -> str:
        raise RuntimeError("hashing pool is gone")

    monkeypatch.setattr(auth_router, "hash_password", broken)
    failed = await http.post(
        "/api/auth/password/reset", json={"token": token, "password": "brand-new-456"}
    )
    assert failed.status_code == 500
    async with factory() as fresh:
        row = (
            await fresh.execute(select(AuthToken).where(AuthToken.purpose == "reset_password"))
        ).scalar_one()
        version = (await fresh.execute(select(User.session_version))).scalar_one()
    assert row.used_at is None and version == 0, "nothing of a failed reset may be committed"

    monkeypatch.setattr(auth_router, "hash_password", hash_password)
    retry = await http.post(
        "/api/auth/password/reset", json={"token": token, "password": "brand-new-456"}
    )
    assert retry.status_code == 200
