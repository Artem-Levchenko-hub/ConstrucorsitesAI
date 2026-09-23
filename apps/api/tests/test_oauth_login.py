"""Вход через VK ID и Яндекс ID: реальный app и база, провайдеры подменены
через httpx.MockTransport. Проверяем контракт целиком: рукопожатие с
серверным state/PKCE, экран подтверждения документов перед созданием аккаунта,
привязку существующего аккаунта по email и минимизацию данных.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.models.account import AuthSession, LegalAcceptance
from omnia_api.models.billing import Subscription
from omnia_api.models.oauth_login import OAuthLoginState, UserIdentity
from omnia_api.models.user import User
from omnia_api.routers import auth_oauth
from omnia_api.services import oauth_login

WEB = "http://web.example.test"
LEGAL_VERSION = "2026-07-30"
CONSENT = {
    "terms_accepted": True,
    "privacy_accepted": True,
    "personal_data_accepted": True,
    "document_version": LEGAL_VERSION,
}
MAX_REGISTRATION = {
    "email": "owner@example.com",
    "password": "secret123",
    "product": "max",
    **CONSENT,
}


@pytest.fixture
def providers_configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Оба провайдера настроены; redirect base — адрес сайта."""
    monkeypatch.setenv("VK_ID_CLIENT_ID", "vk-app")
    monkeypatch.setenv("YANDEX_ID_CLIENT_ID", "ya-app")
    monkeypatch.setenv("YANDEX_ID_CLIENT_SECRET", "ya-secret")
    monkeypatch.setenv("WEB_BASE_URL", WEB)
    monkeypatch.setenv("OAUTH_LOGIN_REDIRECT_BASE_URL", "")
    monkeypatch.setenv("LEGAL_DOCUMENT_VERSION", LEGAL_VERSION)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Provider:
    """Подмена VK ID / Яндекс ID: помнит, что у него спросили, отвечает по сценарию."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.vk_profile: dict[str, Any] = {
            "user_id": 4242,
            "first_name": "Иван",
            "last_name": "Петров",
            "phone": "+79990000000",
            "avatar": "https://vk.example/avatar.png",
            "email": "owner@example.com",
        }
        self.yandex_profile: dict[str, Any] = {
            "id": "1000034426",
            "login": "owner",
            "default_email": "owner@example.com",
            "emails": ["owner@example.com"],
            "real_name": "Иван Петров",
        }
        monkeypatch.setattr(
            oauth_login,
            "http_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?", 1)[0]
        if url == oauth_login.VK_TOKEN_URL:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "server_error"})
            return httpx.Response(
                200, json={"access_token": "vk-access", "user_id": 4242, "token_type": "Bearer"}
            )
        if url == oauth_login.VK_USER_INFO_URL:
            assert parse_qs(request.content.decode())["access_token"] == ["vk-access"]
            return httpx.Response(200, json={"user": self.vk_profile})
        if url == oauth_login.YANDEX_TOKEN_URL:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "ya-access", "token_type": "bearer"})
        if url == oauth_login.YANDEX_USER_INFO_URL:
            assert request.headers["authorization"] == "OAuth ya-access"
            return httpx.Response(200, json=self.yandex_profile)
        raise AssertionError(f"unexpected provider call {request.method} {request.url}")

    def form(self, url: str) -> dict[str, list[str]]:
        request = next(r for r in self.requests if str(r.url).split("?", 1)[0] == url)
        return parse_qs(request.content.decode())


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    return Provider(monkeypatch)


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


async def _start(client: httpx.AsyncClient, provider: str, **params: str) -> dict[str, str]:
    response = await client.get(f"/api/auth/oauth/{provider}/start", params=params)
    assert response.status_code == 200, response.text
    return _query(response.json()["authorization_url"])


async def _callback(
    client: httpx.AsyncClient, provider: str, **params: str
) -> httpx.Response:
    response = await client.get(f"/api/auth/oauth/{provider}/callback", params=params)
    assert response.status_code == 303, response.text
    return response


async def _state_rows(db_session: AsyncSession) -> list[OAuthLoginState]:
    return list((await db_session.execute(select(OAuthLoginState))).scalars())


# ── чистые функции ────────────────────────────────────────────────────────


def test_pkce_pair_follows_rfc_7636() -> None:
    verifier, challenge = oauth_login.generate_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert "=" not in challenge


def test_only_configured_providers_are_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    base = get_settings()
    scenarios: list[tuple[dict[str, Any], list[str]]] = [
        ({}, []),
        ({"vk_id_client_id": "vk-app"}, ["vk"]),
        # Яндекс без секрета код не обменяет — кнопку не показываем.
        ({"yandex_id_client_id": "ya-app"}, []),
        (
            {
                "yandex_id_client_id": " ya-app ",
                "yandex_id_client_secret": SecretStr("ya-secret"),
            },
            ["yandex"],
        ),
        ({"vk_id_client_id": "  "}, []),
    ]
    for overrides, expected in scenarios:
        settings = base.model_copy(update=overrides)
        monkeypatch.setattr(oauth_login, "get_settings", lambda s=settings: s)
        assert oauth_login.configured_providers() == expected


def test_authorization_urls_request_only_email(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().model_copy(
        update={
            "vk_id_client_id": "vk-app",
            "yandex_id_client_id": "ya-app",
            "yandex_id_client_secret": SecretStr("ya-secret"),
            "web_base_url": WEB,
            "oauth_login_redirect_base_url": "",
        }
    )
    monkeypatch.setattr(oauth_login, "get_settings", lambda: settings)

    vk = urlparse(oauth_login.authorization_url("vk", state="st", code_challenge="ch"))
    assert f"{vk.scheme}://{vk.netloc}{vk.path}" == oauth_login.VK_AUTHORIZE_URL
    assert _query(vk.geturl()) == {
        "response_type": "code",
        "client_id": "vk-app",
        "redirect_uri": f"{WEB}/api/auth/oauth/vk/callback",
        "state": "st",
        "scope": "email",
        "code_challenge": "ch",
        "code_challenge_method": "s256",
    }
    with pytest.raises(oauth_login.OAuthLoginError):
        oauth_login.authorization_url("vk", state="st")

    yandex = urlparse(oauth_login.authorization_url("yandex", state="st"))
    assert f"{yandex.scheme}://{yandex.netloc}{yandex.path}" == oauth_login.YANDEX_AUTHORIZE_URL
    assert _query(yandex.geturl()) == {
        "response_type": "code",
        "client_id": "ya-app",
        "redirect_uri": f"{WEB}/api/auth/oauth/yandex/callback",
        "state": "st",
        "scope": "login:email",
    }


def test_redirect_base_override_serves_split_origin_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().model_copy(
        update={"web_base_url": WEB, "oauth_login_redirect_base_url": "http://localhost:8000/"}
    )
    monkeypatch.setattr(oauth_login, "get_settings", lambda: settings)
    assert oauth_login.callback_url("vk") == "http://localhost:8000/api/auth/oauth/vk/callback"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "/max"),
        ("", "/max"),
        ("/max/onboarding", "/max/onboarding"),
        ("/billing/plan?tab=pro", "/billing/plan?tab=pro"),
        ("//evil.example/x", "/max"),
        ("https://evil.example", "/max"),
        ("/max\\@evil", "/max"),
        ("/max\n", "/max"),
        ("/" + "a" * 600, "/max"),
    ],
)
def test_next_path_is_same_origin_only(raw: str | None, expected: str) -> None:
    assert auth_oauth.safe_next(raw) == expected


# ── контракт API ──────────────────────────────────────────────────────────


async def test_providers_endpoint_lists_only_configured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VK_ID_CLIENT_ID", raising=False)
    monkeypatch.delenv("YANDEX_ID_CLIENT_ID", raising=False)
    monkeypatch.delenv("YANDEX_ID_CLIENT_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        response = await client.get("/api/auth/oauth/providers")
        assert response.status_code == 200
        assert response.json()["providers"] == []

        monkeypatch.setenv("VK_ID_CLIENT_ID", "vk-app")
        get_settings.cache_clear()
        response = await client.get("/api/auth/oauth/providers")
        assert response.json() == {
            "providers": [{"provider": "vk", "label": "VK ID"}],
            "legal_document_version": get_settings().legal_document_version,
        }
    finally:
        get_settings.cache_clear()


async def test_start_refuses_unknown_or_unconfigured_provider(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("YANDEX_ID_CLIENT_ID", raising=False)
    monkeypatch.delenv("YANDEX_ID_CLIENT_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        for provider in ("github", "yandex"):
            response = await client.get(f"/api/auth/oauth/{provider}/start")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "oauth_provider_unavailable"
    finally:
        get_settings.cache_clear()


async def test_start_keeps_state_and_pkce_on_the_server(
    client: httpx.AsyncClient, db_session: AsyncSession, providers_configured: None
) -> None:
    params = await _start(client, "vk", next="/max/onboarding")
    rows = await _state_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "vk"
    assert row.state_hash == hashlib.sha256(params["state"].encode()).hexdigest()
    assert row.next_path == "/max/onboarding"
    assert row.used_at is None and row.ticket_hash is None
    assert row.expires_at - datetime.now(UTC) < auth_oauth.STATE_TTL
    # Verifier остаётся на сервере, в ссылке для браузера — только challenge.
    assert row.code_verifier is not None and row.code_verifier not in params.values()
    digest = hashlib.sha256(row.code_verifier.encode()).digest()
    assert params["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")

    yandex = await _start(client, "yandex", next="//evil.example")
    yandex_row = next(r for r in await _state_rows(db_session) if r.provider == "yandex")
    assert yandex_row.code_verifier is None
    assert yandex_row.next_path == "/max"
    assert "code_challenge" not in yandex


async def test_callback_rejects_unknown_reused_and_expired_state(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
    provider: Provider,
) -> None:
    unknown = await _callback(client, "vk", state="x" * 40, code="c", device_id="d")
    assert unknown.headers["location"] == f"{WEB}/login?oauth_error=oauth_state_invalid"

    params = await _start(client, "vk")
    (row,) = await _state_rows(db_session)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    expired = await _callback(client, "vk", state=params["state"], code="c", device_id="d")
    assert expired.headers["location"].endswith("oauth_error=oauth_state_invalid")

    params = await _start(client, "yandex", next="/billing")
    # Тот же state, но другой провайдер в пути — не наш.
    wrong = await _callback(client, "vk", state=params["state"], code="c", device_id="d")
    assert wrong.headers["location"].endswith("oauth_error=oauth_state_invalid")
    denied = await _callback(client, "yandex", state=params["state"], error="access_denied")
    assert denied.headers["location"] == f"{WEB}/login?oauth_error=oauth_cancelled&next=%2Fbilling"
    reused = await _callback(client, "yandex", state=params["state"], code="c")
    assert reused.headers["location"].endswith("oauth_error=oauth_state_invalid")
    assert provider.requests == []


async def test_new_account_needs_explicit_consent_before_creation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
    provider: Provider,
) -> None:
    params = await _start(client, "vk", next="/max/onboarding")
    (row,) = await _state_rows(db_session)
    verifier = row.code_verifier

    callback = await _callback(
        client, "vk", state=params["state"], code="vk-code", device_id="dev-1", type="code_v2"
    )
    location = callback.headers["location"]
    assert location.startswith(f"{WEB}/oauth/complete?ticket=")
    assert "omnia_session" not in callback.cookies
    ticket = _query(location)["ticket"]

    # Обмен кода: PKCE-verifier с сервера, device_id из callback-а, наш redirect_uri.
    token_form = provider.form(oauth_login.VK_TOKEN_URL)
    assert token_form["grant_type"] == ["authorization_code"]
    assert token_form["code"] == ["vk-code"]
    assert token_form["code_verifier"] == [verifier]
    assert token_form["device_id"] == ["dev-1"]
    assert token_form["client_id"] == ["vk-app"]
    assert token_form["redirect_uri"] == [f"{WEB}/api/auth/oauth/vk/callback"]
    assert token_form["state"] == [params["state"]]
    assert "client_secret" not in token_form

    # Аккаунта ещё нет: callback только выдал билет.
    assert (await db_session.execute(select(User))).scalars().all() == []
    await db_session.refresh(row)
    assert row.used_at is not None
    assert row.ticket_hash == hashlib.sha256(ticket.encode()).hexdigest()
    assert row.pending_email == "owner@example.com"
    assert row.pending_provider_user_id == "4242"

    pending = await client.get("/api/auth/oauth/pending", params={"ticket": ticket})
    assert pending.status_code == 200
    assert pending.json() == {
        "provider": "vk",
        "label": "VK ID",
        "email": "owner@example.com",
        "next": "/max/onboarding",
        "legal_document_version": LEGAL_VERSION,
    }

    missing = await client.post(
        "/api/auth/oauth/complete",
        json={"ticket": ticket, "terms_accepted": True, "document_version": LEGAL_VERSION},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "legal_acceptance_required"
    outdated = await client.post(
        "/api/auth/oauth/complete",
        json={"ticket": ticket, **CONSENT, "document_version": "2020-01-01"},
    )
    assert outdated.status_code == 409
    assert outdated.json()["error"]["code"] == "legal_version_outdated"
    assert (await db_session.execute(select(User))).scalars().all() == []

    created = await client.post(
        "/api/auth/oauth/complete",
        json={"ticket": ticket, **CONSENT, "marketing_accepted": True},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["email"] == "owner@example.com"
    assert body["email_verified_at"] is not None
    assert "omnia_session" in created.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["id"] == body["id"]

    user = (await db_session.execute(select(User))).scalar_one()
    assert user.password_hash is None
    assert user.email_verified_at is not None and user.last_login_at is not None
    assert user.wallet is not None
    assert (await db_session.execute(select(Subscription))).scalar_one().user_id == user.id
    identity = (await db_session.execute(select(UserIdentity))).scalar_one()
    assert (identity.user_id, identity.provider, identity.provider_user_id, identity.email) == (
        user.id,
        "vk",
        "4242",
        "owner@example.com",
    )
    acceptances = {
        item.document_type: item.document_version
        for item in (await db_session.execute(select(LegalAcceptance))).scalars()
    }
    assert acceptances == {
        "terms": LEGAL_VERSION,
        "privacy": LEGAL_VERSION,
        "personal_data": LEGAL_VERSION,
        "marketing": LEGAL_VERSION,
    }
    assert (await db_session.execute(select(AuthSession))).scalar_one().user_id == user.id

    # Билет одноразовый.
    again = await client.post("/api/auth/oauth/complete", json={"ticket": ticket, **CONSENT})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "oauth_ticket_invalid"
    stale = await client.get("/api/auth/oauth/pending", params={"ticket": ticket})
    assert stale.status_code == 400


async def test_password_login_is_impossible_for_provider_only_account(
    client: httpx.AsyncClient, providers_configured: None, provider: Provider
) -> None:
    params = await _start(client, "vk")
    callback = await _callback(client, "vk", state=params["state"], code="c", device_id="d")
    ticket = _query(callback.headers["location"])["ticket"]
    assert (
        await client.post("/api/auth/oauth/complete", json={"ticket": ticket, **CONSENT})
    ).status_code == 201
    client.cookies.clear()
    login = await client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "secret123"}
    )
    assert login.status_code == 401


async def test_existing_account_is_linked_by_verified_email_and_signed_in(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import auth as auth_router

    async def no_letter(_user: User, _token: str) -> None:
        return None

    monkeypatch.setattr(auth_router, "_send_verification", no_letter)
    registered = await client.post("/api/auth/register", json=MAX_REGISTRATION)
    assert registered.status_code == 201
    assert registered.json()["email_verified_at"] is None
    user_id = registered.json()["id"]
    client.cookies.clear()

    params = await _start(client, "yandex", next="/billing/plan")
    callback = await _callback(client, "yandex", state=params["state"], code="ya-code")
    assert callback.headers["location"] == f"{WEB}/billing/plan"
    assert "omnia_session" in callback.cookies

    token_form = provider.form(oauth_login.YANDEX_TOKEN_URL)
    assert token_form["grant_type"] == ["authorization_code"]
    assert token_form["code"] == ["ya-code"]
    assert token_form["client_id"] == ["ya-app"]
    assert token_form["client_secret"] == ["ya-secret"]
    assert "code_verifier" not in token_form

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == user_id
    # Провайдер подтвердил адрес — письмо больше не нужно.
    assert me.json()["email_verified_at"] is not None

    identity = (await db_session.execute(select(UserIdentity))).scalar_one()
    assert str(identity.user_id) == user_id
    assert (identity.provider, identity.provider_user_id) == ("yandex", "1000034426")
    user = await db_session.get(User, identity.user_id)
    assert user is not None and user.password_hash is not None

    # Повторный вход идёт по связке, даже если email у провайдера сменился.
    client.cookies.clear()
    provider.yandex_profile["default_email"] = "renamed@example.com"
    params = await _start(client, "yandex")
    second = await _callback(client, "yandex", state=params["state"], code="ya-code-2")
    assert second.headers["location"] == f"{WEB}/max"
    me = await client.get("/api/auth/me")
    assert me.json()["id"] == user_id
    await db_session.refresh(identity)
    assert identity.email == "renamed@example.com"
    identities = (await db_session.execute(select(UserIdentity))).scalars().all()
    assert len(identities) == 1
    assert len((await db_session.execute(select(User))).scalars().all()) == 1


async def test_provider_failures_send_the_browser_back_to_login(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
    provider: Provider,
) -> None:
    provider.token_status = 500
    params = await _start(client, "vk", next="/max/onboarding")
    failed = await _callback(client, "vk", state=params["state"], code="c", device_id="d")
    assert failed.headers["location"] == (
        f"{WEB}/login?oauth_error=oauth_exchange_failed&next=%2Fmax%2Fonboarding"
    )

    provider.token_status = 200
    del provider.vk_profile["email"]
    params = await _start(client, "vk")
    no_email = await _callback(client, "vk", state=params["state"], code="c", device_id="d")
    assert no_email.headers["location"] == f"{WEB}/login?oauth_error=oauth_email_required"

    # VK без device_id код не обменяет — провайдера даже не спрашиваем.
    calls = len(provider.requests)
    params = await _start(client, "vk")
    no_device = await _callback(client, "vk", state=params["state"], code="c")
    assert no_device.headers["location"].endswith("oauth_error=oauth_exchange_failed")
    assert len(provider.requests) == calls

    assert (await db_session.execute(select(User))).scalars().all() == []
    assert all(row.used_at is not None for row in await _state_rows(db_session))


async def test_inactive_account_cannot_sign_in_through_a_provider(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
    provider: Provider,
) -> None:
    registered = await client.post(
        "/api/auth/register", json={"email": "owner@example.com", "password": "secret123"}
    )
    assert registered.status_code == 201
    client.cookies.clear()
    user = (await db_session.execute(select(User))).scalar_one()
    user.status = "blocked"
    await db_session.commit()

    params = await _start(client, "yandex")
    refused = await _callback(client, "yandex", state=params["state"], code="c")
    assert refused.headers["location"] == f"{WEB}/login?oauth_error=account_unavailable"
    assert "omnia_session" not in refused.cookies
    assert (await db_session.execute(select(UserIdentity))).scalars().all() == []


async def test_platform_keeps_only_identifier_and_email(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
    provider: Provider,
) -> None:
    """Имя, телефон и аватар из профиля провайдера не попадают ни в одну таблицу."""
    params = await _start(client, "vk")
    callback = await _callback(client, "vk", state=params["state"], code="c", device_id="d")
    ticket = _query(callback.headers["location"])["ticket"]
    created = await client.post("/api/auth/oauth/complete", json={"ticket": ticket, **CONSENT})
    assert created.status_code == 201

    assert {column.name for column in UserIdentity.__table__.columns} == {
        "id",
        "user_id",
        "provider",
        "provider_user_id",
        "email",
        "created_at",
    }
    (state_row,) = await _state_rows(db_session)
    columns = [column.name for column in state_row.__table__.columns]
    stored = json.dumps(
        {name: str(getattr(state_row, name)) for name in columns}, ensure_ascii=False
    )
    for secret in ("Иван", "Петров", "+79990000000", "avatar.png", "vk-access"):
        assert secret not in stored

    # Выгрузка аккаунта показывает связку целиком — и в ней тоже только id и email.
    exported = await client.get("/api/account/export")
    assert exported.status_code == 200
    [identity] = exported.json()["identities"]
    assert set(identity) == {"provider", "provider_user_id", "email", "created_at"}
    assert (identity["provider"], identity["provider_user_id"], identity["email"]) == (
        "vk",
        "4242",
        "owner@example.com",
    )


async def test_stale_handshakes_are_swept_on_the_next_start(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    providers_configured: None,
) -> None:
    await _start(client, "vk")
    (old,) = await _state_rows(db_session)
    old.expires_at = datetime.now(UTC) - auth_oauth.STALE_AFTER - timedelta(minutes=1)
    await db_session.commit()
    await _start(client, "yandex")
    rows = await _state_rows(db_session)
    assert [row.provider for row in rows] == ["yandex"]

