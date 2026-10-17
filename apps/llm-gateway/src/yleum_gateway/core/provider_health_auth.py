"""Free health advisory wire. Never dispatch, settlement or replay authority."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yleum_gateway.core.generation_response_auth import _secret, _signed, _verified

HEADER = "x-provider-health-authorization"
_REQUEST = b"yleum:provider-health:request:v1"
_RESPONSE = b"yleum:provider-health:response:v1"
_CACHE = b"yleum:provider-health:cache:v1"
Dependency = Literal["credentials", "funds", "provider"]
Status = Literal["unknown", "available", "unavailable"]
Reason = Literal["unsupported", "checking", "observed", "unavailable"]


class ProviderHealthAdvisory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    credential_scope: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    credential_status: Status
    funds_status: Status
    observed_at: int
    expires_at: int
    reason: Reason

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if isinstance(value, dict) and "version" in value and type(value["version"]) is not int:
            raise ValueError("invalid health version")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> ProviderHealthAdvisory:
        if self.expires_at - self.observed_at != 15 or self.observed_at < 0:
            raise ValueError("invalid health interval")
        if self.reason != "observed" and (
            self.credential_status != "unknown" or self.funds_status != "unknown"
        ):
            raise ValueError("unsupported health authority")
        return self

    def available_for(self, dependency: Dependency) -> bool:
        # Even TRUE is advisory; admission and immutable attempt deadlines stay authoritative.
        return (
            dependency in {"credentials", "funds", "provider"}
            and self.credential_scope is not None
            and self.observed_at <= time.time() < self.expires_at
            and self.credential_status == self.funds_status == "available"
        )


def unknown_snapshot(
    scope: str | None, reason: Reason, *, now: int | None = None
) -> ProviderHealthAdvisory:
    observed = int(time.time()) if now is None else now
    return ProviderHealthAdvisory(
        credential_scope=scope,
        credential_status="unknown",
        funds_status="unknown",
        observed_at=observed,
        expires_at=observed + 15,
        reason=reason,
    )


def _identity(value: Any, nonce: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "run_id",
        "project_id",
        "user_id",
        "dependency",
    }:
        raise ValueError("invalid health identity")
    for item in (value["run_id"], value["project_id"], value["user_id"], nonce):
        if not isinstance(item, str) or str(UUID(item)) != item:
            raise ValueError("invalid health identity")
    if value["dependency"] not in {"credentials", "funds", "provider"}:
        raise ValueError("invalid health dependency")
    return value


def mint_health_request(
    identity: dict[str, str], nonce: str, *, secret: str | None = None, now: datetime | None = None
) -> str:
    seconds = int((now or datetime.now(UTC)).timestamp())
    return _signed(
        {
            "aud": "llm-gateway",
            "purpose": "provider_health.read",
            "identity": _identity(identity, nonce),
            "nonce": nonce,
            "iat": seconds,
            "exp": seconds + 15,
        },
        _secret(secret),
        _REQUEST,
    )


def verify_health_request(
    token: str, *, secret: str | None = None, now: datetime | None = None
) -> tuple[dict[str, str], str]:
    claims = _verified(token, secret=_secret(secret), domain=_REQUEST, now=now or datetime.now(UTC))
    if (
        set(claims) != {"aud", "purpose", "identity", "nonce", "iat", "exp"}
        or claims["aud"] != "llm-gateway"
        or claims["purpose"] != "provider_health.read"
        or claims["exp"] - claims["iat"] != 15
    ):
        raise ValueError("invalid health request")
    return _identity(claims["identity"], claims["nonce"]), claims["nonce"]


def sign_health_response(
    snapshot: ProviderHealthAdvisory,
    identity: dict[str, str],
    nonce: str,
    *,
    secret: str | None = None,
    now: datetime | None = None,
) -> str:
    seconds = int((now or datetime.now(UTC)).timestamp())
    return _signed(
        {
            "aud": "yleum-api",
            "purpose": "provider_health.advisory",
            "identity": _identity(identity, nonce),
            "nonce": nonce,
            "snapshot": snapshot.model_dump(),
            "iat": seconds,
            "exp": seconds + 15,
        },
        _secret(secret),
        _RESPONSE,
    )


def _snapshot(value: Any, *, now: datetime) -> ProviderHealthAdvisory:
    result = ProviderHealthAdvisory.model_validate(value)
    if (
        result.credential_scope is None
        or re.fullmatch(r"[a-f0-9]{64}", result.credential_scope) is None
        or not result.observed_at <= now.timestamp() < result.expires_at
    ):
        raise ValueError("stale health advisory")
    return result


def verify_health_response(
    token: str,
    identity: dict[str, str],
    nonce: str,
    *,
    secret: str | None = None,
    now: datetime | None = None,
) -> ProviderHealthAdvisory:
    current = now or datetime.now(UTC)
    claims = _verified(token, secret=_secret(secret), domain=_RESPONSE, now=current)
    if (
        set(claims) != {"aud", "purpose", "identity", "nonce", "snapshot", "iat", "exp"}
        or claims["aud"] != "yleum-api"
        or claims["purpose"] != "provider_health.advisory"
        or claims["identity"] != _identity(identity, nonce)
        or claims["nonce"] != nonce
        or claims["exp"] - claims["iat"] != 15
    ):
        raise ValueError("invalid health response")
    return _snapshot(claims["snapshot"], now=current)


def sign_health_cache(snapshot: ProviderHealthAdvisory, *, secret: str) -> str:
    return _signed(
        {
            "aud": "llm-gateway",
            "purpose": "provider_health.cache",
            "snapshot": snapshot.model_dump(),
            "iat": snapshot.observed_at,
            "exp": snapshot.expires_at,
        },
        _secret(secret),
        _CACHE,
    )


def verify_health_cache(
    token: str, scope: str, *, secret: str, now: datetime | None = None
) -> ProviderHealthAdvisory:
    current = now or datetime.now(UTC)
    claims = _verified(token, secret=_secret(secret), domain=_CACHE, now=current)
    if (
        set(claims) != {"aud", "purpose", "snapshot", "iat", "exp"}
        or claims["aud"] != "llm-gateway"
        or claims["purpose"] != "provider_health.cache"
    ):
        raise ValueError("invalid health cache")
    result = _snapshot(claims["snapshot"], now=current)
    if (
        result.credential_scope != scope
        or claims["iat"] != result.observed_at
        or claims["exp"] != result.expires_at
    ):
        raise ValueError("foreign health cache")
    return result
