from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import SecretStr
from yleum_gateway.core import provider_health_auth as auth
from yleum_gateway.services import provider_health as health

SECRET = "synthetic-health-signing-secret-32-bytes"
NOW = 1800000000
IDENTITY = {
    "run_id": str(UUID(int=1)),
    "project_id": str(UUID(int=2)),
    "user_id": str(UUID(int=3)),
    "dependency": "funds",
}
NONCE = str(UUID(int=4))


class Redis:
    def __init__(self):
        self.values = {}
        self.published = 0

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx, px):
        assert nx and px == 5000
        if key in self.values:
            return None
        self.values[key] = value
        return True

    async def eval(self, script, count, *args):
        lease = args[0]
        token = args[count]
        if self.values.get(lease) != token:
            return 0
        if count == 2:
            self.values[args[1]] = args[3]
            self.published += 1
        del self.values[lease]
        return 1


@pytest.fixture
def configured(monkeypatch):
    settings = SimpleNamespace(
        llmgw_api_key=SecretStr("private-test-key"),
        llmgw_base_url="https://synthetic.invalid/v1",
        llmgw_organization_id="private-test-org",
        generation_response_signing_secret=SecretStr(SECRET),
    )
    monkeypatch.setattr(health, "get_settings", lambda: settings)
    return settings


def test_auth_domains_nonce_and_current_identity():
    now = datetime.fromtimestamp(NOW, UTC)
    token = auth.mint_health_request(IDENTITY, NONCE, secret=SECRET, now=now)
    assert auth.verify_health_request(token, secret=SECRET, now=now) == (IDENTITY, NONCE)
    result = auth.unknown_snapshot("a" * 64, "unsupported", now=NOW)
    signed = auth.sign_health_response(result, IDENTITY, NONCE, secret=SECRET, now=now)
    assert auth.verify_health_response(signed, IDENTITY, NONCE, secret=SECRET, now=now) == result
    for identity, nonce in [
        (dict(IDENTITY, run_id=str(UUID(int=9))), NONCE),
        (IDENTITY, str(UUID(int=9))),
    ]:
        with pytest.raises(ValueError):
            auth.verify_health_response(signed, identity, nonce, secret=SECRET, now=now)
    with pytest.raises(ValueError):
        auth.verify_health_request(signed, secret=SECRET, now=now)
    with pytest.raises(ValueError):
        auth.verify_health_request(token, secret=SECRET, now=datetime.fromtimestamp(NOW + 15, UTC))


async def test_unsupported_performs_zero_upstream(configured):
    result = await health.ProviderHealth(Redis()).check()
    assert result.credential_status == result.funds_status == "unknown"
    assert result.reason == "unsupported"
    assert "private-test" not in result.model_dump_json()


async def test_hundred_waiters_share_one_bounded_probe(configured):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return health.FreeProbeResult(credential_status="available", funds_status="available")

    redis = Redis()
    service = health.ProviderHealth(redis, probe=probe)
    winner = asyncio.create_task(service.check())
    await entered.wait()
    losers = await asyncio.gather(
        *(health.ProviderHealth(redis, probe=probe).check() for _ in range(99))
    )
    assert all(row.reason == "checking" for row in losers)
    release.set()
    assert (await winner).funds_status == "available"
    assert (await service.check()).funds_status == "available"
    assert calls == 1


@pytest.mark.parametrize("fault", ["rotation", "expired_owner", "cancel"])
async def test_late_winner_cannot_publish_or_delete_foreign_lease(configured, fault):
    redis = Redis()
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe():
        entered.set()
        await release.wait()
        return health.FreeProbeResult(credential_status="available", funds_status="available")

    winner = asyncio.create_task(health.ProviderHealth(redis, probe=probe).check())
    await entered.wait()
    lease = next(iter(redis.values))
    redis.values[lease] = "new-owner"
    if fault == "rotation":
        configured.llmgw_api_key = SecretStr("rotated-key")
    if fault == "cancel":
        winner.cancel()
    release.set()
    if fault == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await winner
    else:
        assert (await winner).funds_status == "unknown"
    assert redis.values[lease] == "new-owner" and redis.published == 0


async def test_redis_fault_or_forged_cache_never_triggers_probe(configured):
    calls = []

    async def probe():
        calls.append(True)
        raise AssertionError

    redis = Redis()
    scope = health.current_scope()
    redis.values[f"provider-health:v1:{scope}:result"] = b'{"funds_status":"available"}'
    assert (await health.ProviderHealth(redis, probe=probe).check()).reason == "unavailable"
    assert calls == []

    async def fail(_):
        raise ConnectionError("private error")

    redis.get = fail
    assert (await health.ProviderHealth(redis, probe=probe).check()).reason == "unavailable"
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"purpose": "generation_response.read"},
        {"aud": "yleum-api"},
        {"nonce": "bad"},
        {"identity": dict(IDENTITY, extra="bad")},
        {"identity": dict(IDENTITY, user_id="bad")},
        {"identity": dict(IDENTITY, dependency="completion")},
        {"exp": NOW + 60},
    ],
)
def test_request_exact_shape_domain_and_lifetime(change):
    claims = {
        "aud": "llm-gateway",
        "purpose": "provider_health.read",
        "identity": IDENTITY,
        "nonce": NONCE,
        "iat": NOW,
        "exp": NOW + 15,
        **change,
    }
    token = auth._signed(claims, SECRET, auth._REQUEST)
    with pytest.raises(ValueError):
        auth.verify_health_request(token, secret=SECRET, now=datetime.fromtimestamp(NOW, UTC))


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"expires_at": NOW + 16},
        {"observed_at": True},
        {"credential_scope": "A" * 64},
        {"private": "secret"},
    ],
)
def test_signed_snapshot_rejects_malformed_fields(change):
    snapshot = auth.unknown_snapshot("a" * 64, "unsupported", now=NOW).model_dump()
    snapshot.update(change)
    token = auth._signed(
        {
            "aud": "yleum-api",
            "purpose": "provider_health.advisory",
            "identity": IDENTITY,
            "nonce": NONCE,
            "snapshot": snapshot,
            "iat": NOW,
            "exp": NOW + 15,
        },
        SECRET,
        auth._RESPONSE,
    )
    with pytest.raises(ValueError):
        auth.verify_health_response(
            token, IDENTITY, NONCE, secret=SECRET, now=datetime.fromtimestamp(NOW, UTC)
        )


async def test_final_release_io_expiry_cannot_return_available(configured, monkeypatch):
    redis = Redis()
    clock = [NOW]
    monkeypatch.setattr(health.time, "time", lambda: clock[0])
    original = redis.eval

    async def delayed(script, count, *args):
        result = await original(script, count, *args)
        if count == 1:
            clock[0] += 16
        return result

    redis.eval = delayed

    async def probe():
        return health.FreeProbeResult(credential_status="available", funds_status="available")

    assert (await health.ProviderHealth(redis, probe=probe).check()).funds_status == "unknown"


async def test_cancelled_winner_release_failure_is_bounded(configured):
    redis = Redis()
    entered = asyncio.Event()

    async def probe():
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError

    original = redis.eval

    async def stuck_release(script, count, *args):
        if count == 1:
            await asyncio.Event().wait()
        return await original(script, count, *args)

    redis.eval = stuck_release
    winner = asyncio.create_task(health.ProviderHealth(redis, probe=probe).check())
    await entered.wait()
    winner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(winner, timeout=1)
    assert redis.published == 0


def test_actual_credential_base_and_organization_rotation_separate_scope(configured):
    initial = health.current_scope()
    for field, value in [
        ("llmgw_api_key", SecretStr("rotated")),
        ("llmgw_base_url", "https://other.invalid"),
        ("llmgw_organization_id", "another"),
    ]:
        old = getattr(configured, field)
        setattr(configured, field, value)
        assert health.current_scope() != initial
        setattr(configured, field, old)
    assert health.current_scope() == initial


async def test_current_owner_query_is_readonly_and_never_creates_attempt(monkeypatch):
    from contextlib import asynccontextmanager

    from yleum_gateway.routers import provider_health as route

    calls = []

    class Connection:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            assert kwargs == {"readonly": True}
            yield

        async def fetchval(self, sql, *args):
            calls.append((sql, args))
            return True

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Connection()

    monkeypatch.setattr(route, "get_pool", lambda: Pool())
    assert await route.current_owner(IDENTITY)
    sql, args = calls.pop()
    assert (
        sql.startswith("SELECT EXISTS") and "p.owner_id=$3" in sql and "r.status='running'" in sql
    )
    assert args == tuple(UUID(IDENTITY[key]) for key in ("run_id", "project_id", "user_id"))
    assert "generation_provider_attempts" not in sql


@pytest.mark.parametrize(
    "edge",
    ["valid", "foreign_run", "duplicate", "wrong_purpose", "foreign_owner", "cancel_during_check"],
)
async def test_http_route_rechecks_ownership_and_returns_only_signed_advisory(
    configured, monkeypatch, edge
):
    from unittest.mock import AsyncMock

    import httpx
    from yleum_gateway.core import generation_response_auth
    from yleum_gateway.main import create_app
    from yleum_gateway.routers import provider_health as route

    monkeypatch.setattr(generation_response_auth, "_secret", lambda _: SECRET)
    monkeypatch.setattr(auth, "_secret", lambda _: SECRET)
    owner = AsyncMock(
        side_effect=[True, False] if edge == "cancel_during_check" else None,
        return_value=edge != "foreign_owner",
    )
    monkeypatch.setattr(route, "current_owner", owner)
    monkeypatch.setattr(route, "get_redis", lambda: Redis())
    app = create_app()
    token = auth.mint_health_request(IDENTITY, NONCE, secret=SECRET)
    if edge == "wrong_purpose":
        token = auth.sign_health_response(
            auth.unknown_snapshot("a" * 64, "unsupported"), IDENTITY, NONCE, secret=SECRET
        )
    headers = [(auth.HEADER, token)] * (2 if edge == "duplicate" else 1)
    run = str(UUID(int=99)) if edge == "foreign_run" else IDENTITY["run_id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit"
    ) as client:
        response = await client.get(f"/_internal/provider-health/{run}", headers=headers)
    if edge == "valid":
        assert response.status_code == 200 and set(response.json()) == {"assertion"}
        result = auth.verify_health_response(
            response.json()["assertion"], IDENTITY, NONCE, secret=SECRET
        )
        assert result.reason == "unsupported" and owner.await_count == 2
    else:
        assert response.status_code in {403, 503}
    assert "private-test" not in response.text
