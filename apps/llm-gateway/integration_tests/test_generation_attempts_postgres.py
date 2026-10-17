"""Owned PostgreSQL dispatch races; no provider/network side effects."""

import asyncio
import importlib
from pathlib import Path
from uuid import uuid4

import pytest
import test_billing_settlement_postgres as fixture_source
from test_billing_settlement_postgres import _owner

_REPO_APPS = Path(__file__).resolve().parents[2]

database_url = fixture_source.database_url
pool = fixture_source.pool


def service():
    name = "yleum_gateway.services.generation_attempts"
    assert importlib.util.find_spec(name), "missing durable attempt admission"
    return importlib.import_module(name)


async def intent(pool):
    from yleum_gateway.services.generation_identity import GenerationProviderIdentity

    user, project, _ = await _owner(pool)
    run, attempt = uuid4(), uuid4()
    identity = GenerationProviderIdentity(run, project, user, attempt, "a" * 64, "b" * 64)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,idempotency_key,prompt_hash,status) "
            "VALUES($1,$2,$3,$4,$5,'running')",
            run,
            project,
            user,
            str(run),
            identity.prompt_hash,
        )
        await conn.execute(
            "INSERT INTO generation_provider_attempts "
            "(attempt_id,run_id,project_id,user_id,prompt_hash,request_sha256,dispatch_token) "
            "VALUES($1,$2,$3,$4,$5,$6,$7)",
            attempt,
            run,
            project,
            user,
            identity.prompt_hash,
            identity.request_sha256,
            uuid4(),
        )
    return identity


async def test_equal_admissions_race_to_one_committed_journal_and_physical_send(pool, monkeypatch):
    s = service()
    monkeypatch.setattr(s, "get_pool", lambda: pool)
    identity = await intent(pool)
    posts = []

    async def caller():
        admission = await s.reserve_generation_attempt(identity)
        if admission.action != "dispatch":
            return
        try:
            call = await s.mark_attempt_dispatching(
                identity.attempt_id,
                dispatch_token=admission.dispatch_token,
                route="/v1/messages",
                model="claude-sonnet-5",
                user_id=identity.user_id,
                project_id=identity.project_id,
                run_id=identity.run_id,
            )
        except s.AttemptConflict:
            return
        posts.append(call)  # physical transport stand-in, ONLY after committed CAS

    await asyncio.gather(*(caller() for _ in range(8)))
    assert len(posts) == 1
    assert (await s.reserve_generation_attempt(identity)).action == "pending"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT calculated_cost_rub FROM provider_calls WHERE id=$1",
                posts[0],
            )
            is None
        )


async def test_rotation_revokes_old_reserved_token_and_never_rotates_dispatching(pool, monkeypatch):
    s = service()
    monkeypatch.setattr(s, "get_pool", lambda: pool)
    identity = await intent(pool)
    old = await s.reserve_generation_attempt(identity)
    current = await s.reserve_generation_attempt(identity)
    with pytest.raises(s.AttemptConflict):
        await s.mark_attempt_dispatching(
            identity.attempt_id,
            dispatch_token=old.dispatch_token,
            route="/v1/messages",
            model="claude-sonnet-5",
            user_id=identity.user_id,
            project_id=identity.project_id,
            run_id=identity.run_id,
        )
    assert current.dispatch_token != old.dispatch_token
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
            )
            == 0
        )


@pytest.mark.parametrize("edge", ["native", "chat", "collected", "sse"])
@pytest.mark.parametrize("failure", ["read", "preconnect"])
async def test_real_route_commits_before_physical_post_and_replay_never_posts(
    pool, monkeypatch, edge, failure
):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI

    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.routers import chat, messages_native
    from yleum_gateway.services import cache, file_logger
    from yleum_gateway.services.generation_identity import request_digest

    s = service()
    monkeypatch.setattr(s, "get_pool", lambda: pool)
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    reset_settings_cache()
    monkeypatch.setattr(
        cache, "get", AsyncMock(side_effect=AssertionError("durable identity used Redis cache"))
    )
    monkeypatch.setattr(
        cache, "set", AsyncMock(side_effect=AssertionError("durable identity retained in Redis"))
    )
    monkeypatch.setattr(file_logger, "log_request", lambda value: None)
    route = "/v1/messages" if edge == "native" else "/v1/chat/completions"
    body = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "stream": edge == "sse",
    }
    if edge == "chat":
        body["max_tokens"] = 32
    identity = await intent(pool)
    identity = replace(identity, request_sha256=request_digest(route, body))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_provider_attempts SET request_sha256=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            identity.request_sha256,
        )
    body["metadata"] = {
        "run_id": str(identity.run_id),
        "project_id": str(identity.project_id),
        "generation_provider_identity": identity.to_wire(),
    }
    posts, observed = [], []
    loop = asyncio.get_running_loop()

    async def committed():
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT a.phase,c.status FROM generation_provider_attempts a JOIN provider_calls c ON c.id=a.provider_call_id WHERE a.attempt_id=$1",
                identity.attempt_id,
            )
            observed.append(tuple(row.values()))

    def reply(request):
        posts.append(request)
        asyncio.run_coroutine_threadsafe(committed(), loop).result(timeout=3)
        raise (httpx.ConnectError if failure == "preconnect" else httpx.ReadError)(
            "synthetic", request=request
        )

    async def async_reply(request):
        posts.append(request)
        await committed()
        raise (httpx.ConnectError if failure == "preconnect" else httpx.ReadError)(
            "synthetic", request=request
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(async_reply))
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(messages_native.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app)) as client:
        first = await client.post("http://gateway.test" + route, json=body)
        second = await client.post("http://gateway.test" + route, json=body)
    assert first.status_code in {200, 502, 503}
    assert second.status_code in {409, 503}
    assert len(posts) == 1
    assert observed == [("dispatching", "started")]
    if failure == "preconnect":
        assert "llmgw_preconnect_v1" in first.text
    async with pool.acquire() as conn:
        phase = await conn.fetchval(
            "SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1",
            identity.attempt_id,
        )
        assert phase == ("closed" if failure == "preconnect" else "indeterminate")
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT calculated_cost_rub FROM provider_calls WHERE run_id=$1", identity.run_id
            )
            is None
        )


@pytest.mark.parametrize(
    "mutation", ["owner", "run_cancelled", "prompt", "request", "project", "user"]
)
async def test_current_binding_changes_never_admit(pool, monkeypatch, mutation):
    from dataclasses import replace

    s = service()
    monkeypatch.setattr(s, "get_pool", lambda: pool)
    identity = await intent(pool)
    async with pool.acquire() as conn:
        if mutation == "owner":
            other, _, _ = await _owner(pool)
            await conn.execute(
                "UPDATE projects SET owner_id=$2 WHERE id=$1", identity.project_id, other
            )
        elif mutation == "run_cancelled":
            await conn.execute(
                "UPDATE generation_runs SET status='cancelled' WHERE id=$1", identity.run_id
            )
        elif mutation == "prompt":
            await conn.execute(
                "UPDATE generation_runs SET prompt_hash=$2 WHERE id=$1", identity.run_id, "c" * 64
            )
        else:
            field = {"request": "request_sha256", "project": "project_id", "user": "user_id"}[
                mutation
            ]
            identity = replace(identity, **{field: "c" * 64 if mutation == "request" else uuid4()})
    assert (await s.reserve_generation_attempt(identity)).action == "conflict"


@pytest.mark.parametrize("committed", [False, True])
async def test_unknown_dispatch_commit_grants_no_post_and_no_orphan_journal(
    pool, monkeypatch, committed
):
    from contextlib import asynccontextmanager

    s = service()
    monkeypatch.setattr(s, "get_pool", lambda: pool)
    identity = await intent(pool)
    admission = await s.reserve_generation_attempt(identity)

    class BrokenCommit:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        @asynccontextmanager
        async def transaction(self):
            async with self.conn.transaction():
                yield
                if not committed:
                    raise ConnectionError("commit outcome unknown")
            raise ConnectionError("ack lost after commit")

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as conn:
                yield BrokenCommit(conn)

    monkeypatch.setattr(s, "get_pool", lambda: Pool())
    posts = []
    with pytest.raises(s.AttemptConflict):
        call = await s.mark_attempt_dispatching(
            identity.attempt_id,
            dispatch_token=admission.dispatch_token,
            route="/v1/messages",
            model="claude-sonnet-5.5",
            run_id=identity.run_id,
            project_id=identity.project_id,
        )
        posts.append(call)
    assert posts == []
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
        ) == int(committed)
        assert await conn.fetchval(
            "SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1",
            identity.attempt_id,
        ) == ("dispatching" if committed else "reserved")


@pytest.fixture
async def api_writer(pool, database_url, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import create_async_engine
    from yleum_api.services.generation import provider_attempts

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: engine)
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(generation_provider_attempts_enabled=True),
    )
    monkeypatch.setattr(service(), "get_pool", lambda: pool)
    try:
        yield provider_attempts
    finally:
        await engine.dispose()


def request_body(identity):
    return {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "metadata": {
            "run_id": str(identity.run_id),
            "project_id": str(identity.project_id),
            "user_id": str(identity.user_id),
        },
    }


async def test_api_intent_and_gateway_dispatch_do_not_wait_on_callers_project_run_locks(
    pool, api_writer
):
    prior = await intent(pool)
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchrow("SELECT id FROM projects WHERE id=$1 FOR UPDATE", prior.project_id)
        await conn.fetchrow("SELECT id FROM generation_runs WHERE id=$1 FOR UPDATE", prior.run_id)
        async with asyncio.timeout(3):
            identity = await api_writer.prepare_generation_attempt(
                request_body(prior), "/v1/messages"
            )
            assert identity is not None
            call = await service().dispatch(
                identity,
                route="/v1/messages",
                model="claude-sonnet-5.5",
                run_id=identity.run_id,
                project_id=identity.project_id,
                user_id=identity.user_id,
            )
        assert (
            await conn.fetchval(
                "SELECT provider_call_id FROM generation_provider_attempts WHERE attempt_id=$1",
                identity.attempt_id,
            )
            == call
        )


@pytest.mark.parametrize("reason", ["preconnect", "rate_limit_refused", None])
async def test_new_attempt_requires_affirmative_committed_parent_and_prevents_fanout(
    pool, api_writer, reason
):
    prior = await intent(pool)
    body = request_body(prior)
    identity = await api_writer.prepare_generation_attempt(body, "/v1/messages")
    s = service()
    call = await s.dispatch(
        identity,
        route="/v1/messages",
        model="claude-sonnet-5.5",
        run_id=identity.run_id,
        project_id=identity.project_id,
        user_id=identity.user_id,
    )
    # Neither a started journal nor a failed receipt alone permits a second attempt.
    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
        await api_writer.prepare_generation_attempt(body, "/v1/messages", parent=identity)
    from yleum_gateway.services import provider_calls

    await provider_calls.finish_call(call, status="failed", error_type="ConnectError")
    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
        await api_writer.prepare_generation_attempt(body, "/v1/messages", parent=identity)
    await s.close_attempt(call, reason=reason)
    if reason is None:
        with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
            await api_writer.prepare_generation_attempt(body, "/v1/messages", parent=identity)
        return
    attempts = await asyncio.gather(
        *(
            api_writer.prepare_generation_attempt(body, "/v1/messages", parent=identity)
            for _ in range(4)
        ),
        return_exceptions=True,
    )
    children = [a for a in attempts if not isinstance(a, BaseException)]
    assert len(children) == 1
    assert children[0].attempt_id != identity.attempt_id
    assert (await s.reserve_generation_attempt(identity)).action == "pending"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM generation_provider_attempts WHERE parent_attempt_id=$1",
                identity.attempt_id,
            )
            == 1
        )
        assert (
            await conn.fetchval("SELECT provider_cost_rub FROM provider_calls WHERE id=$1", call)
            is None
        )


@pytest.mark.parametrize("committed", [False, True])
async def test_api_unknown_intent_commit_stops_before_http(
    pool, api_writer, monkeypatch, committed
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    prior = await intent(pool)
    engine = api_writer.get_engine()

    class BrokenCommit(AsyncSession):
        async def commit(self):
            if committed:
                await super().commit()
            raise ConnectionError("commit acknowledgment uncertain")

    monkeypatch.setattr(
        api_writer,
        "async_sessionmaker",
        lambda *args, **kw: async_sessionmaker(engine, class_=BrokenCommit, **kw),
    )
    sent = []
    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
        identity = await api_writer.prepare_generation_attempt(request_body(prior), "/v1/messages")
        sent.append(identity)
    assert sent == []
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM generation_provider_attempts WHERE run_id=$1", prior.run_id
        ) == 1 + int(committed)
        assert (
            await conn.fetchval("SELECT count(*) FROM provider_calls WHERE run_id=$1", prior.run_id)
            == 0
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("messages", [{"role": "user", "content": "different"}]),
        ("model", "other"),
        ("temperature", None),
        ("stream", False),
        ("max_tokens", None),
        ("tools", []),
        ("tool_choice", {"type": "auto"}),
    ],
)
def test_api_gateway_semantic_vectors_match_and_bind_full_ingress(field, value):
    from yleum_api.services.generation import provider_identity as api

    from yleum_gateway.services import generation_identity as gateway

    route = "/v1/messages"
    original = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "Привет"}],
        "tools": [
            {
                "name": "read",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ],
    }
    changed = {**original, field: value}
    assert api.canonical_request(route, original) == gateway.canonical_request(route, original)
    assert api.request_digest(route, changed) == gateway.request_digest(route, changed)
    assert api.request_digest(route, changed) != api.request_digest(route, original)
    reordered = dict(reversed(list(original.items())))
    assert api.request_digest(route, reordered) == gateway.request_digest(route, original)


async def test_platform_runner_keeps_real_jti_fence_and_cannot_replay_dispatch(pool, monkeypatch):
    import base64
    import hashlib
    import hmac
    import json
    import os
    import time
    from dataclasses import replace
    from urllib.parse import urlparse

    import httpx
    from fastapi import FastAPI
    from redis.asyncio import Redis

    from yleum_gateway.core import runner_auth
    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.routers import messages_native
    from yleum_gateway.services.generation_identity import request_digest

    redis_url = os.environ.get("QA_GENERATION_REDIS_URL")
    if not redis_url:
        pytest.skip("requires owned local QA_GENERATION_REDIS_URL")
    if urlparse(redis_url).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("runner replay test requires isolated loopback Redis")
    redis = Redis.from_url(redis_url)
    assert await redis.ping()
    s = service()
    monkeypatch.setattr(s, "get_pool", lambda: pool)
    monkeypatch.setattr(runner_auth, "get_redis", lambda: redis)
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    monkeypatch.setenv("RUNNER_AUTH_SECRET", "task7-runner-synthetic")
    monkeypatch.setenv("RUNNER_AUTH_ISSUER", "omnia-agent-runner")
    reset_settings_cache()
    identity = await intent(pool)
    route = "/v1/project-cell/messages"
    body = request_body(identity)
    identity = replace(identity, request_sha256=request_digest(route, body))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_provider_attempts SET request_sha256=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            identity.request_sha256,
        )
    body["metadata"]["generation_provider_identity"] = identity.to_wire()
    posts = []

    def reply(request):
        posts.append(request)
        raise httpx.ReadError("unknown outcome", request=request)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(reply))
    app = FastAPI()
    app.include_router(messages_native.router)
    jtis = []

    def authorize():
        jti = str(uuid4())
        jtis.append(jti)
        now = int(time.time())
        claims = {
            "iss": "omnia-agent-runner",
            "aud": "omnia-project-cell-runner",
            "jti": jti,
            "project_id": str(identity.project_id),
            "run_id": str(identity.run_id),
            "session_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "fencing_epoch": 4,
            "cancel_epoch": 0,
            "nbf": now - 1,
            "exp": now + 60,
        }
        body["metadata"].update(
            {
                k: claims[k]
                for k in (
                    "project_id",
                    "run_id",
                    "session_id",
                    "workspace_id",
                    "fencing_epoch",
                    "cancel_epoch",
                )
            }
        )
        body["metadata"]["message_id"] = jti

        def encode(data):
            return (
                base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode())
                .decode()
                .rstrip("=")
            )

        unsigned = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode(claims)
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(b"task7-runner-synthetic", unsigned.encode(), hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        return {"authorization": "Bearer " + unsigned + "." + signature}

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app)) as client:
            headers = authorize()
            body["metadata"]["fencing_epoch"] = 5
            wrong_fence = await client.post(
                "http://gateway.test" + route, json=body, headers=headers
            )
            assert wrong_fence.status_code == 401 and posts == []
            body["metadata"]["fencing_epoch"] = 4
            first = await client.post("http://gateway.test" + route, json=body, headers=headers)
            replay = await client.post("http://gateway.test" + route, json=body, headers=headers)
            next_token = await client.post(
                "http://gateway.test" + route, json=body, headers=authorize()
            )
        assert (
            first.status_code == 503 and replay.status_code == 401 and next_token.status_code == 503
        )
        assert len(posts) == 1
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expense_owner,user_id,free,status FROM provider_calls WHERE run_id=$1",
                identity.run_id,
            )
            assert tuple(row.values()) == ("platform", None, True, "ambiguous")
    finally:
        # Delete only exact synthetic JTIs created by this test; never flush a DB.
        for jti in jtis:
            await redis.delete("omnia:project-cell:runner-jti:" + jti)
        await redis.aclose()


@pytest.mark.parametrize("mode", ["empty", "populated", "concurrent"])
async def test_attempt_downgrade_never_removes_committed_dispatch_evidence(database_url, mode):

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    path = (
        _REPO_APPS
        / "api/migrations/versions/0086_generation_provider_attempts.py"
    )
    spec = importlib.util.spec_from_file_location("migration_task7", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = "task7_downgrade_" + uuid4().hex
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    pending = None

    def upgrade(conn):
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()

    def downgrade(conn):
        migration.op = Operations(MigrationContext.configure(conn))
        migration.downgrade()

    async def insert(conn):
        await conn.execute(
            text(
                f'INSERT INTO "{schema}".generation_provider_attempts (attempt_id,run_id,project_id,user_id,dispatch_token,prompt_hash,request_sha256) VALUES (:id,:run,:project,:user,:token,:prompt,:digest)'
            ),
            {
                "id": uuid4(),
                "run": uuid4(),
                "project": uuid4(),
                "user": uuid4(),
                "token": uuid4(),
                "prompt": "a" * 64,
                "digest": "b" * 64,
            },
        )

    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
            await conn.run_sync(upgrade)
            if mode == "populated":
                await insert(conn)
        async with engine.connect() as writer, engine.connect() as reader:
            if mode == "concurrent":
                await writer.begin()
                await insert(writer)
            await reader.begin()
            await reader.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
            pid = await reader.scalar(text("SELECT pg_backend_pid()"))
            pending = asyncio.create_task(reader.run_sync(downgrade))
            if mode == "concurrent":
                async with engine.connect() as observer:
                    for _ in range(100):
                        blocked = await observer.scalar(
                            text(
                                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"
                            ),
                            {"pid": pid},
                        )
                        await observer.commit()
                        if blocked:
                            break
                        await asyncio.sleep(0.02)
                    else:
                        pytest.fail("actual downgrade never waited on concurrent INSERT")
                await writer.commit()
            if mode == "empty":
                await pending
                await reader.commit()
            else:
                with pytest.raises(RuntimeError, match="evidence prevents downgrade"):
                    await asyncio.wait_for(pending, 5)
                await reader.rollback()
        async with engine.begin() as verify:
            exists = await verify.scalar(
                text("SELECT to_regclass(:name)"),
                {"name": schema + ".generation_provider_attempts"},
            )
            if mode == "empty":
                assert exists is None
            else:
                assert exists is not None
                assert (
                    await verify.scalar(
                        text(f'SELECT count(*) FROM "{schema}".generation_provider_attempts')
                    )
                    == 1
                )
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
