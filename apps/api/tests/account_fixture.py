"""Real account/email proof for domain tests; only outbound delivery is replaced."""

from typing import Literal
from unittest.mock import patch

import httpx

from yleum_api.core.config import get_settings
from yleum_api.routers import auth


async def register_verified_account(
    client: httpx.AsyncClient,
    email: str,
    *,
    product: Literal["general", "max"] = "general",
    password: str = "secret123",
) -> httpx.Response:
    tokens: list[str] = []

    async def capture(_user, token: str) -> None:
        tokens.append(token)

    payload: dict[str, object] = {"email": email, "password": password, "product": product}
    if product == "max":
        payload.update(
            {
                "terms_accepted": True,
                "privacy_accepted": True,
                "personal_data_accepted": True,
                "document_version": get_settings().legal_document_version,
                "phone": "+79991234567",
            }
        )
    with patch.object(auth, "_send_verification", capture):
        registered = await client.post("/api/auth/register", json=payload)
        assert registered.status_code == 201, registered.text
        cookie_name = get_settings().jwt_cookie_name
        signed_cookie = registered.cookies.get(cookie_name)
        assert signed_cookie
        # The real signed cookie is transport-local because ASGI's test origin
        # is HTTP even when production correctly configures Secure cookies.
        client.cookies.clear()
        client.cookies.set(cookie_name, signed_cookie)
        if not tokens:
            requested = await client.post("/api/auth/email/verify/request")
            assert requested.status_code == 202, requested.text
        assert len(tokens) == 1
        verified = await client.post("/api/auth/email/verify", json={"token": tokens[0]})
        assert verified.status_code == 200, verified.text
    return registered
