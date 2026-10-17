"""Response-purpose assertions never authorize another attempt or operation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from yleum_gateway.core.generation_response_auth import verify_generation_response_token
from yleum_gateway.services.generation_identity import GenerationProviderIdentity

SECRET = "synthetic-response-auth-secret-only-for-tests"
NOW = datetime(2026, 10, 17, tzinfo=UTC)
IDENTITY = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)


def token(**changes):
    claims = {
        "aud": "llm-gateway",
        "purpose": "generation_response.read",
        "identity": IDENTITY.to_wire(),
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 60,
        **changes,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    ).rstrip(b"=")
    signed = b"v1." + payload
    key = hmac.digest(SECRET.encode(), b"yleum:generation-response:v1", "sha256")
    return signed.decode() + "." + hmac.new(key, signed, hashlib.sha256).hexdigest()


def test_valid_read_returns_exact_bound_identity():
    assert (
        verify_generation_response_token(
            token(), purpose="generation_response.read", secret=SECRET, now=NOW
        )
        == IDENTITY
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": "generation_response.record"},
        {"aud": "yleum-api"},
        {"exp": int(NOW.timestamp())},
        {"iat": int(NOW.timestamp()) + 1},
        {"exp": int(NOW.timestamp()) + 61},
        {"iat": True},
        {"extra": "ignored?"},
        {"identity": {**IDENTITY.to_wire(), "run_id": "bad"}},
    ],
)
def test_invalid_assertions_fail_closed(changes):
    with pytest.raises(ValueError):
        verify_generation_response_token(
            token(**changes), purpose="generation_response.read", secret=SECRET, now=NOW
        )


def test_wrong_secret_and_malformed_assertions_fail_closed():
    for value in (token() + "x", "", "v1.bad.bad", "x" * 4097):
        with pytest.raises(ValueError):
            verify_generation_response_token(
                value, purpose="generation_response.read", secret=SECRET, now=NOW
            )
    with pytest.raises(ValueError):
        verify_generation_response_token(
            token(), purpose="generation_response.read", secret="foreign", now=NOW
        )


def test_api_mint_matches_local_golden_without_cross_service_runtime_import(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "api/src"))
    from yleum_api.services.generation.provider_identity import (
        GenerationProviderIdentity as APIIdentity,
    )
    from yleum_api.services.generation.response_auth import mint_generation_response_token

    minted = mint_generation_response_token(
        APIIdentity.from_wire(IDENTITY.to_wire()),
        purpose="generation_response.read",
        now=NOW,
        secret=SECRET,
    )
    assert minted == token()


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"metadata":{}}',
        b'{"metadata":{"generation_provider_identity":null},"metadata":{}}',
        b'{"metadata":{},"metadata":{}}',
    ],
)
def test_reserved_auth_header_forces_identity_even_when_marker_missing_or_hidden(raw, monkeypatch):
    from yleum_gateway.services import generation_attempts

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    with pytest.raises(generation_attempts.AttemptConflict):
        generation_attempts.parse_attempt(raw, "/v1/messages", require_identity=True)


def snapshot(**changes):
    return {
        "version": 1,
        "bucket": "private-" + UUID(int=9).hex,
        "epoch": str(UUID(int=9)),
        "quota_bytes": 10000000,
        "free_bytes": 20000000,
        "measured_at": int(NOW.timestamp()),
        **changes,
    }


def test_capacity_request_response_domains_and_exact_binding():
    from datetime import timedelta

    from yleum_gateway.core import generation_response_auth as auth

    nonce = str(UUID(int=10))
    request = auth.mint_capacity_token(IDENTITY, nonce, now=NOW, secret=SECRET)
    assert auth.verify_capacity_token(request, now=NOW, secret=SECRET) == (IDENTITY, nonce)
    response = auth.sign_capacity_snapshot(snapshot(), IDENTITY, nonce, secret=SECRET)
    assert (
        auth.verify_capacity_snapshot(response, IDENTITY, nonce, now=NOW, secret=SECRET)
        == snapshot()
    )
    for value, bound_nonce, moment in (
        (request, nonce, NOW),
        (response, str(UUID(int=11)), NOW),
        (response, nonce, NOW + timedelta(seconds=15)),
        (response, nonce, NOW - timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError):
            auth.verify_capacity_snapshot(value, IDENTITY, bound_nonce, now=moment, secret=SECRET)
    with pytest.raises(ValueError):
        auth.verify_generation_response_token(
            request, purpose="generation_response.record", now=NOW, secret=SECRET
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"quota_bytes": True},
        {"quota_bytes": 0},
        {"free_bytes": -1},
        {"free_bytes": "999"},
        {"bucket": ""},
        {"epoch": "bad"},
        {"version": True},
        {"unexpected": 1},
    ],
)
def test_capacity_snapshot_invalid_signed_values_are_not_authority(changes):
    from yleum_gateway.core import generation_response_auth as auth

    nonce = str(UUID(int=10))
    response = auth.sign_capacity_snapshot(snapshot(**changes), IDENTITY, nonce, secret=SECRET)
    with pytest.raises(ValueError):
        auth.verify_capacity_snapshot(response, IDENTITY, nonce, now=NOW, secret=SECRET)
