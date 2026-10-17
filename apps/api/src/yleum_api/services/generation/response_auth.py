"""Short-lived, domain-separated service assertions; no browser authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

ResponsePurpose = Literal["generation_response.record", "generation_response.read"]
HEADER = "x-generation-response-authorization"
_DOMAIN = b"yleum:generation-response:v1"


def _secret(value: str | None) -> str:
    if value is None:
        from yleum_api.core.config import get_settings

        configured = get_settings().generation_response_signing_secret
        value = configured.get_secret_value() if configured else None
    if not value or len(value) < 32:
        raise ValueError("generation response authentication unavailable")
    return value


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate assertion field")
        result[key] = value
    return result


def _signed(claims: dict[str, object], secret: str, domain: bytes) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).rstrip(b"=")
    signed = b"v1." + payload
    key = hmac.digest(secret.encode(), domain, "sha256")
    return signed.decode() + "." + hmac.new(key, signed, hashlib.sha256).hexdigest()


def _verified(token: str, *, secret: str, domain: bytes, now: datetime) -> dict[str, Any]:
    if not isinstance(token, str) or len(token) > 4096:
        raise ValueError("invalid generation response assertion")
    match = re.fullmatch(r"v1\.([A-Za-z0-9_-]+)\.([a-f0-9]{64})", token)
    if match is None:
        raise ValueError("invalid generation response assertion")
    encoded, signature = match.groups()
    key = hmac.digest(secret.encode(), domain, "sha256")
    expected = hmac.new(key, ("v1." + encoded).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid generation response assertion")
    try:
        claims = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)), object_pairs_hook=_pairs
        )
        if not isinstance(claims, dict):
            raise ValueError
        issued, expires = claims["iat"], claims["exp"]
        seconds = int(now.timestamp())
        if (
            type(issued) is not int
            or type(expires) is not int
            or not 0 < expires - issued <= 60
            or issued > seconds
            or expires <= seconds
            or _signed(claims, secret, domain) != token
        ):
            raise ValueError
        return claims
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("invalid generation response assertion") from None


def mint_generation_response_token(
    identity: GenerationProviderIdentity,
    *,
    purpose: ResponsePurpose,
    now: datetime | None = None,
    secret: str | None = None,
) -> str:
    if purpose not in {"generation_response.record", "generation_response.read"}:
        raise ValueError("invalid generation response purpose")
    seconds = int((now or datetime.now(UTC)).timestamp())
    return _signed(
        {
            "aud": "llm-gateway",
            "purpose": purpose,
            "identity": identity.to_wire(),
            "iat": seconds,
            "exp": seconds + 60,
        },
        _secret(secret),
        _DOMAIN,
    )


def verify_generation_response_token(
    token: str,
    *,
    purpose: ResponsePurpose,
    now: datetime | None = None,
    secret: str | None = None,
) -> GenerationProviderIdentity:
    claims = _verified(token, secret=_secret(secret), domain=_DOMAIN, now=now or datetime.now(UTC))
    if (
        set(claims) != {"aud", "purpose", "identity", "iat", "exp"}
        or claims["aud"] != "llm-gateway"
        or claims["purpose"] != purpose
        or purpose not in {"generation_response.record", "generation_response.read"}
    ):
        raise ValueError("invalid generation response assertion")
    return GenerationProviderIdentity.from_wire(claims["identity"])


_CAPACITY_DOMAIN = b"yleum:generation-response-capacity:v1"
CAPACITY_HEADER = "x-generation-response-capacity-authorization"


def _capacity_secret(value: str | None) -> str:
    if value is None:
        from yleum_api.core.config import get_settings

        configured = get_settings().generation_response_capacity_signing_secret
        value = configured.get_secret_value() if configured else None
    if not value or len(value) < 32:
        raise ValueError("generation capacity authentication unavailable")
    return value


def mint_capacity_token(
    identity: GenerationProviderIdentity,
    nonce: str,
    *,
    now: datetime | None = None,
    secret: str | None = None,
) -> str:
    from uuid import UUID

    if str(UUID(nonce)) != nonce:
        raise ValueError("invalid capacity nonce")
    seconds = int((now or datetime.now(UTC)).timestamp())
    return _signed(
        {
            "aud": "yleum-api",
            "purpose": "generation_response.capacity",
            "identity": identity.to_wire(),
            "nonce": nonce,
            "iat": seconds,
            "exp": seconds + 60,
        },
        _capacity_secret(secret),
        _CAPACITY_DOMAIN,
    )


def verify_capacity_token(
    token: str, *, now: datetime | None = None, secret: str | None = None
) -> tuple[GenerationProviderIdentity, str]:
    from uuid import UUID

    claims = _verified(
        token,
        secret=_capacity_secret(secret),
        domain=_CAPACITY_DOMAIN,
        now=now or datetime.now(UTC),
    )
    if (
        set(claims) != {"aud", "purpose", "identity", "nonce", "iat", "exp"}
        or claims["aud"] != "yleum-api"
        or claims["purpose"] != "generation_response.capacity"
        or not isinstance(claims["nonce"], str)
    ):
        raise ValueError("invalid capacity assertion")
    try:
        if str(UUID(claims["nonce"])) != claims["nonce"]:
            raise ValueError
    except ValueError:
        raise ValueError("invalid capacity assertion") from None
    return GenerationProviderIdentity.from_wire(claims["identity"]), claims["nonce"]


_SNAPSHOT_DOMAIN = b"yleum:generation-response-capacity-snapshot:v1"


def sign_capacity_snapshot(
    values: dict[str, Any],
    identity: GenerationProviderIdentity,
    nonce: str,
    *,
    secret: str | None = None,
) -> str:
    return _signed(
        {
            "aud": "llm-gateway",
            "purpose": "generation_response.capacity.snapshot",
            "identity": identity.to_wire(),
            "nonce": nonce,
            "snapshot": values,
            "iat": values["measured_at"],
            "exp": values["measured_at"] + 15,
        },
        _capacity_secret(secret),
        _SNAPSHOT_DOMAIN,
    )


def verify_capacity_snapshot(
    token: str,
    identity: GenerationProviderIdentity,
    nonce: str,
    *,
    now: datetime | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    claims = _verified(
        token,
        secret=_capacity_secret(secret),
        domain=_SNAPSHOT_DOMAIN,
        now=now or datetime.now(UTC),
    )
    if (
        set(claims) != {"aud", "purpose", "identity", "nonce", "snapshot", "iat", "exp"}
        or claims["aud"] != "llm-gateway"
        or claims["purpose"] != "generation_response.capacity.snapshot"
        or claims["identity"] != identity.to_wire()
        or claims["nonce"] != nonce
        or claims["exp"] - claims["iat"] != 15
    ):
        raise ValueError("invalid capacity snapshot assertion")
    values = claims["snapshot"]
    if (
        not isinstance(values, dict)
        or set(values) != {"version", "bucket", "epoch", "quota_bytes", "free_bytes", "measured_at"}
        or type(values["version"]) is not int
        or values["version"] != 1
        or values["measured_at"] != claims["iat"]
    ):
        raise ValueError("invalid capacity snapshot assertion")
    from uuid import UUID

    if (
        type(values["quota_bytes"]) is not int
        or values["quota_bytes"] <= 0
        or type(values["free_bytes"]) is not int
        or values["free_bytes"] < 0
        or type(values["measured_at"]) is not int
        or not isinstance(values["bucket"], str)
        or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", values["bucket"]) is None
        or not isinstance(values["epoch"], str)
    ):
        raise ValueError("invalid capacity snapshot assertion")
    try:
        if str(UUID(values["epoch"])) != values["epoch"]:
            raise ValueError
    except ValueError:
        raise ValueError("invalid capacity snapshot assertion") from None
    return values


def record_headers(
    identity: GenerationProviderIdentity | None, headers: dict[str, str] | None = None
) -> dict[str, str] | None:
    from yleum_api.core.config import get_settings

    if identity is None or not getattr(
        get_settings(), "generation_response_retention_enabled", False
    ):
        return headers
    return {
        **(headers or {}),
        HEADER: mint_generation_response_token(identity, purpose="generation_response.record"),
    }
