"""Missing-email diagnostics must explain the VK boundary without leaking credentials."""

import logging
from typing import Any

import httpx
import pytest

from yleum_api.services import oauth_login


@pytest.mark.parametrize(
    ("profile_email", "expected_state", "scope", "expected_grant"),
    [
        ({}, "absent", "email", True),
        ({"email": None}, "null", "", False),
        ({"email": ""}, "empty", None, None),
        ({"email": "broken-sensitive-value"}, "invalid", "email", True),
        ({"email": {"secret": "nested-sensitive-value"}}, "invalid", ["email"], None),
    ],
)
async def test_missing_vk_email_logs_only_fixed_metadata(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    profile_email: dict[str, Any],
    expected_state: str,
    scope: object,
    expected_grant: bool | None,
) -> None:
    monkeypatch.setattr(
        oauth_login, "credentials", lambda _: oauth_login.OAuthClient("test-app", None)
    )
    monkeypatch.setattr(oauth_login, "callback_url", lambda _: "https://app.test/callback")
    token = {
        "access_token": "sensitive-access-token",
        "id_token": "sensitive-id-token",
        "email": "sensitive-token-email@example.test",
        "scope": scope,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == oauth_login.VK_TOKEN_URL:
            return httpx.Response(200, json=token)
        assert str(request.url) == oauth_login.VK_USER_INFO_URL
        return httpx.Response(
            200,
            json={
                "user": {
                    "user_id": "sensitive-subject",
                    "phone": "sensitive-phone",
                    "first_name": "sensitive-name",
                    **profile_email,
                }
            },
        )

    monkeypatch.setattr(
        oauth_login,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    with caplog.at_level(logging.WARNING, logger=oauth_login.__name__):
        identity = await oauth_login.fetch_identity(
            "vk", code="sensitive-code", code_verifier="sensitive-verifier",
            device_id="sensitive-device", state="sensitive-state",
        )
    assert identity.email is None  # Diagnostics never fall back to unverified token fields.
    records = [r for r in caplog.records if "vk email missing" in r.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"profile_email_state={expected_state}" in message
    assert f"email_scope_granted={expected_grant}" in message
    assert "token_email_state=valid" in message
    assert "id_token_present=True" in message
    assert "sensitive" not in caplog.text


async def test_valid_vk_email_is_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        oauth_login, "credentials", lambda _: oauth_login.OAuthClient("test-app", None)
    )
    monkeypatch.setattr(oauth_login, "callback_url", lambda _: "https://app.test/callback")

    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == oauth_login.VK_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "sensitive-token"})
        return httpx.Response(
            200, json={"user": {"user_id": 42, "email": "sensitive-email@example.test"}}
        )

    monkeypatch.setattr(
        oauth_login,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    with caplog.at_level(logging.INFO, logger=oauth_login.__name__):
        identity = await oauth_login.fetch_identity(
            "vk", code="code", code_verifier="verifier", device_id="device", state="state",
        )
    assert identity.email == "sensitive-email@example.test"
    assert not [r for r in caplog.records if r.name == oauth_login.__name__]
