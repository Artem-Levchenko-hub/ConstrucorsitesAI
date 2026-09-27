"""MoySklad installs are tenant-scoped and cannot outlive an uninstall."""

from __future__ import annotations

import time
from uuid import uuid4

import httpx
import jwt
import pytest

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.moysklad import MoyskladInstallation
from yleum_api.routers import moysklad_vendor


def test_vendor_jwt_rejects_wrong_signer_and_expired_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    settings = get_settings()
    monkeypatch.setattr(settings, "integration_moysklad_app_id", str(uuid4()))
    monkeypatch.setattr(settings, "integration_moysklad_app_uid", "yleum.yleum")
    signing_key = "vendor-test-key-with-at-least-32-chars"
    monkeypatch.setattr(settings, "integration_moysklad_secret_key", SecretStr(signing_key))
    claims = {"iat": int(time.time()), "exp": int(time.time()) + 120, "jti": str(uuid4())}
    valid = jwt.encode(claims, signing_key, algorithm="HS256")
    assert moysklad_vendor._verify_jwt(f"Bearer {valid}")[0]
    wrong = jwt.encode(claims, "attacker-key-with-at-least-32-chars", algorithm="HS256")
    with pytest.raises(ApiError) as invalid:
        moysklad_vendor._verify_jwt(f"Bearer {wrong}")
    assert invalid.value.status_code == 401
    expired = jwt.encode(
        {**claims, "iat": int(time.time()) - 600, "exp": int(time.time()) - 300},
        signing_key,
        algorithm="HS256",
    )
    with pytest.raises(ApiError) as expired_error:
        moysklad_vendor._verify_jwt(f"Bearer {expired}")
    assert expired_error.value.status_code == 401


@pytest.mark.asyncio
async def test_setup_page_only_embeds_in_moysklad_and_does_not_cache() -> None:
    page = await moysklad_vendor.setup()
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors https://online.moysklad.ru" in page.headers["content-security-policy"]
    assert b"UserContextRequest" in page.body


@pytest.mark.asyncio
async def test_warehouse_options_only_return_valid_choices() -> None:
    organization_id = uuid4()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/entity/organization")
        return httpx.Response(
            200, json={"rows": [{"id": str(organization_id), "name": "Основная"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await moysklad_vendor._warehouse_options(client, "organization") == [
            {"id": str(organization_id), "name": "Основная"}
        ]


@pytest.mark.asyncio
async def test_warehouse_options_reject_malformed_provider_response() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"rows": [{}]}))
    ) as client:
        with pytest.raises(ApiError) as error:
            await moysklad_vendor._warehouse_options(client, "store")
    assert error.value.status_code == 503


def test_suspend_keeps_owner_for_resume_but_uninstall_forgets_owner() -> None:
    owner = uuid4()
    installation = MoyskladInstallation(
        account_id=uuid4(),
        app_id=uuid4(),
        account_name="warehouse",
        token_enc="encrypted-token",
        user_id=owner,
        state="installed",
        pairing_code_hash="one-time-code",
        vendor_activated=True,
    )
    moysklad_vendor._deactivate_installation(installation, "Suspend")
    assert installation.state == "suspended"
    assert installation.user_id == owner
    assert installation.token_enc is None
    assert installation.pairing_code_hash is None
    installation.token_enc = "new-encrypted-token"
    moysklad_vendor._deactivate_installation(installation, "Uninstall")
    assert installation.state == "uninstalled"
    assert installation.user_id is None
    assert installation.token_enc is None


def test_install_accepts_provider_resource_url_with_trailing_slash() -> None:
    payload = {
        "access": [
            {
                "resource": "https://api.moysklad.ru/api/remap/1.2/",
                "access_token": "warehouse-private-token",
            }
        ]
    }
    assert moysklad_vendor._access_token(payload) == "warehouse-private-token"
    payload["access"][0]["resource"] = "https://other.example/api/remap/1.2/"
    assert moysklad_vendor._access_token(payload) is None


def test_warehouse_selection_routes_are_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setenv("JWT_SECRET", "test-only-jwt-secret-with-at-least-32-characters")
    from yleum_api.main import create_app

    get_settings.cache_clear()
    try:
        routes = create_app().openapi()["paths"]
        root = "/api/projects/{project_id}/app-integrations/moysklad"
        assert "get" in routes[f"{root}/options"]
        assert "put" in routes[f"{root}/settings"]
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_vendor_install_requires_signature_and_uninstall_revokes_token(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id, account_id = uuid4(), uuid4()
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_moysklad_app_id", str(app_id), raising=False)
    monkeypatch.setattr(settings, "integration_moysklad_app_uid", "yleum.yleum", raising=False)
    from pydantic import SecretStr

    monkeypatch.setattr(
        settings,
        "integration_moysklad_secret_key",
        SecretStr("test-vendor-secret-with-at-least-32-chars"),
        raising=False,
    )
    path = f"/api/moysklad/vendor/1.0/apps/{app_id}/{account_id}"
    payload = {
        "appUid": "yleum.yleum",
        "accountName": "test-business",
        "cause": "Install",
        "access": [
            {
                "resource": "https://api.moysklad.ru/api/remap/1.2",
                "scope": ["custom"],
                "access_token": "warehouse-private-token",
            }
        ],
    }
    bad = await client.put(path, json=payload)
    assert bad.status_code == 401
    token = jwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 120, "jti": str(uuid4())},
        "test-vendor-secret-with-at-least-32-chars",
        algorithm="HS256",
    )
    installed = await client.put(path, json=payload, headers={"Authorization": f"Bearer {token}"})
    assert installed.status_code == 200
    assert installed.json() == {"status": "SettingsRequired"}
    assert "warehouse-private-token" not in installed.text

    remove_token = jwt.encode(
        {"iat": int(time.time()), "exp": int(time.time()) + 120, "jti": str(uuid4())},
        "test-vendor-secret-with-at-least-32-chars",
        algorithm="HS256",
    )
    removed = await client.request(
        "DELETE",
        path,
        json={"appUid": "yleum.yleum", "accountName": "test-business", "cause": "Uninstall"},
        headers={"Authorization": f"Bearer {remove_token}"},
    )
    assert removed.status_code == 200
    assert "warehouse-private-token" not in removed.text
