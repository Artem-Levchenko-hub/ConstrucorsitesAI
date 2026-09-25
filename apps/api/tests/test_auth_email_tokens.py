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

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_session
from omnia_api.models.account import AuthSession, AuthToken
from omnia_api.models.user import User
from omnia_api.routers import auth as auth_router
from omnia_api.services.transactional_email import (
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

    async def send(self, *, recipient: str, subject: str, text: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.letters.append({"recipient": recipient, "subject": subject, "text": text})

    def token(self, index: int = -1) -> str:
        found = re.search(r"token=([A-Za-z0-9_-]+)", self.letters[index]["text"])
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


async def test_verification_request_stays_silent_for_unknown_and_verified_addresses(client, outbox):
    unknown = await client.post("/api/auth/email/verify/request", json={"email": "no@example.com"})
    assert (unknown.status_code, unknown.json()) == (202, {"accepted": True})
    assert outbox.letters == []

    await _register(client)
    await client.post("/api/auth/email/verify", json={"token": outbox.token()})
    verified = await client.post("/api/auth/email/verify/request", json={"email": EMAIL})
    assert (verified.status_code, verified.json()) == (202, {"accepted": True})
    assert len(outbox.letters) == 1


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


@pytest.mark.parametrize(
    "failure", [EmailDeliveryNotConfigured("no smtp"), EmailDeliveryFailed("smtp said no")]
)
@pytest.mark.parametrize("path", ["/api/auth/email/verify/request", "/api/auth/password/forgot"])
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
    from omnia_api.main import app

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
