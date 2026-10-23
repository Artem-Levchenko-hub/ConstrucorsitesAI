"""Real constraints, scoped receipt races and API classification on an owned DB."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4, uuid5

import asyncpg
import pytest
import test_billing_settlement_postgres as fixture_source
from sqlalchemy.dialects.postgresql.asyncpg import dialect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_generation_attempts_postgres import intent
from test_generation_response_retention_postgres import Objects
from yleum_api.services.generation.provider_attempts import require_closed_provider_parent
from yleum_api.services.usage_cost_summary import classify_cost, cost_classification

from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.services import generation_attempts as attempts
from yleum_gateway.services import provider_calls

database_url = fixture_source.database_url
pool = fixture_source.pool


async def test_same_upstream_id_is_unique_per_provider_even_during_race(pool):
    request_id = "scoped-" + uuid4().hex
    calls = [await provider_calls.start_call(route="/v1/messages", model="claude-sonnet-5.5",
                                            provider_scope=scope)
             for scope in ("llmgw", "aitunnel", "aitunnel")]
    receipts = await asyncio.gather(*(provider_calls.finish_call(
        call, actual_model="claude-sonnet-5.5", provider_request_id=request_id,
        provider_cost_rub=Decimal("0.3")) for call in calls), return_exceptions=True)
    assert receipts[0] is None
    assert sum(value is None for value in receipts) == 2
    assert sum(isinstance(value, BillingReconciliationRequiredError) for value in receipts) == 1
    async with pool.acquire() as connection:
        rows = await connection.fetch("SELECT provider_scope,status FROM provider_calls WHERE id=ANY($1)", calls)
        assert sorted((r["provider_scope"], r["status"]) for r in rows) == [
            ("aitunnel", "ambiguous"), ("aitunnel", "completed"), ("llmgw", "completed")]
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute("UPDATE provider_calls SET provider_scope='unknown' WHERE id=$1", calls[0])


async def test_ai_dispatch_and_refusal_require_durable_receipt(pool, monkeypatch):
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    identity = await intent(pool)
    admitted = await attempts.reserve_generation_attempt(identity)
    call = await attempts.mark_attempt_dispatching(identity.attempt_id,
        dispatch_token=admitted.dispatch_token, route="/v1/messages", model="claude-sonnet-5.5",
        run_id=identity.run_id, project_id=identity.project_id, user_id=identity.user_id,
        provider_scope="aitunnel")
    with pytest.raises(attempts.AttemptConflict):
        await attempts.close_attempt(call, reason="upstream_refused")
    await provider_calls.finish_call(call, status="failed", error_type="HTTPStatusError")
    await attempts.close_attempt(call, reason="upstream_refused")
    async with pool.acquire() as connection:
        row = await connection.fetchrow("SELECT a.phase,a.terminal_reason,c.provider_scope "
            "FROM generation_provider_attempts a JOIN provider_calls c ON c.id=a.provider_call_id "
            "WHERE a.attempt_id=$1", identity.attempt_id)
        assert tuple(row.values()) == ("closed", "upstream_refused", "aitunnel")
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute("UPDATE generation_provider_attempts SET terminal_reason='unknown' "
                                     "WHERE attempt_id=$1", identity.attempt_id)


@pytest.mark.parametrize("changes,expected", [
    ({}, "confirmed"),
    ({"calculated_cost_rub": "0.4"}, "confirmed"),
    ({"calculated_cost_rub": "NaN"}, "unknown"),
    ({"provider_scope": "llmgw"}, "unknown"),
    ({"tariff_source": "unknown"}, "unknown"),
    ({"basis": "provider_catalog_estimate", "reported_cost_rub": None,
      "calculated_cost_rub": "0.3"}, "estimated"),
    ({"basis": "provider_catalog_estimate", "reported_cost_rub": None}, "unknown"),
])
async def test_python_and_sql_classification_agree_for_provider_v2(pool, changes, expected):
    proof = {"schema_version": 2, "provider_scope": "aitunnel",
        "tariff_source": "aitunnel-public-catalog-2026-10-23", "basis": "provider_reported_rub",
        "effective_cost_rub": "0.3", "reported_cost_rub": "0.3",
        "calculated_cost_rub": None, **changes}
    assert classify_cost(proof, Decimal("0.3")) == expected
    compiled = cost_classification().compile(dialect=dialect(),
                                             compile_kwargs={"render_postcompile": True})
    parameters = [compiled.params[key] for key in compiled.positiontup]
    async with pool.acquire() as connection:
        result = await connection.fetchval("SELECT " + str(compiled) +
            f" FROM (SELECT ${len(parameters) + 1}::jsonb AS cost_provenance,"
            "0.3::numeric AS cost_rub) AS usage", *parameters, json.dumps(proof))
    assert result == expected


@pytest.mark.parametrize("scope", ["llmgw", "aitunnel"])
async def test_retained_refusal_cold_read_and_sql_child_authority(
    pool, database_url, monkeypatch, scope,
):
    from yleum_gateway.core import db
    from yleum_gateway.services import billing, text_failover
    from yleum_gateway.services import generation_response_ledger as ledger
    from yleum_gateway.services.generation_response_store import (
        ChunkWriter,
        ResponseIndeterminate,
        load_generation_response,
        reconcile_generation_response,
    )

    for module in (db, attempts, ledger, text_failover):
        monkeypatch.setattr(module, "get_pool", lambda: pool)
    monkeypatch.setattr(text_failover, "get_settings", lambda: SimpleNamespace(
        aitunnel_text_fallback_enabled=True, aitunnel_text_api_key="synthetic"))
    identity = await intent(pool)
    writer = await ledger.admit_response(identity, ledger.CapacitySnapshot(
        "refusal-" + uuid4().hex, 10_000_000, 10_000_000, uuid4(), uuid4(),
        expires_at=int(datetime.now(UTC).timestamp()) + 30))
    admitted = await attempts.reserve_generation_attempt(identity)
    call = await attempts.mark_attempt_dispatching(identity.attempt_id,
        dispatch_token=admitted.dispatch_token, route="/v1/messages", model="claude-sonnet-5.5",
        user_id=identity.user_id, run_id=identity.run_id, project_id=identity.project_id,
        provider_scope=scope)
    await attempts.finish(identity, call, status="failed", reason="upstream_refused",
                          error_type="HTTPStatusError")
    objects = Objects()
    payload = b'{"error":{"code":"api_error","retry_policy":"llmgw_overload_v1"}}'

    async def body():
        yield payload

    await ChunkWriter(identity.attempt_id, objects, writer, chunk_bytes=19).store(
        body(), finish_reason="error", route="/v1/messages")
    reads_after_write_verification = objects.reads
    async with pool.acquire() as conn:
        attempt = await conn.fetchrow("SELECT * FROM generation_provider_attempts WHERE attempt_id=$1",
                                      identity.attempt_id)
        assert await billing.confirmed_provider_accounting(conn, attempt) is (scope == "llmgw")
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", identity.run_id) == 0
        assert await conn.fetchval("SELECT usage_id FROM provider_calls WHERE id=$1", call) is None
    child = replace(identity, attempt_id=uuid5(identity.attempt_id, "definitive-refusal-retry-v1"))
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        if scope == "aitunnel":
            with pytest.raises(ResponseIndeterminate):
                await writer.reconciled()
            with pytest.raises(ResponseIndeterminate):
                await reconcile_generation_response(identity, objects=objects)
            # Even a cleared historical hold cannot promote an AI refusal.
            async with pool.acquire() as conn:
                await conn.execute("UPDATE generation_responses SET reconciliation_hold=false "
                                   "WHERE attempt_id=$1", identity.attempt_id)
            with pytest.raises(ResponseIndeterminate):
                await load_generation_response(identity, objects=objects)
            assert objects.reads == reads_after_write_verification
            async with async_sessionmaker(engine)() as session:
                with pytest.raises(ValueError, match="first primary refusal"):
                    await require_closed_provider_parent(session, identity, child)
            return

        await writer.reconciled()
        assert await load_generation_response(identity, objects=objects) == payload
        # Lost reconciliation acknowledgement: use only stored SQL + object bytes.
        async with pool.acquire() as conn:
            await conn.execute("UPDATE generation_responses SET reconciliation_hold=true "
                               "WHERE attempt_id=$1", identity.attempt_id)
        assert await reconcile_generation_response(identity, objects=objects)
        assert await load_generation_response(identity, objects=objects) == payload
        async with async_sessionmaker(engine)() as session:
            await require_closed_provider_parent(session, identity, child)
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO generation_provider_attempts "
                "(attempt_id,run_id,project_id,user_id,prompt_hash,request_sha256,dispatch_token,"
                "parent_attempt_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8)", child.attempt_id,
                child.run_id, child.project_id, child.user_id, child.prompt_hash,
                child.request_sha256, uuid4(), identity.attempt_id)
        assert await text_failover.select_scope(child, "claude-sonnet-5.5") == "aitunnel"
        assert await text_failover.select_scope(child, "claude-sonnet-5.5") == "aitunnel"
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM provider_calls WHERE run_id=$1",
                                      identity.run_id) == 1
    finally:
        await engine.dispose()


async def test_native_fallback_deferred_until_accepted_then_one_generation_charge(
    pool, database_url, monkeypatch, request,
):
    import httpx
    from fastapi import FastAPI
    from test_generation_billing_postgres import (
        _finish,
        _make_outbox_due,
        _prepare_accept,
        _reconcile,
        _run,
    )

    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.routers import messages_native

    for name, value in {"LLMGW_API_KEY": "synthetic-primary", "AITUNNEL_TEXT_API_KEY": "synthetic-backup",
                        "AITUNNEL_TEXT_FALLBACK_ENABLED": "true", "GENERATION_PROVIDER_ATTEMPTS_ENABLED": "false",
                        "GENERATION_RESPONSE_RETENTION_ENABLED": "false"}.items():
        monkeypatch.setenv(name, value)
    reset_settings_cache()
    request.addfinalizer(reset_settings_cache)
    owner = await _run(pool, database_url)
    user, project, messages, run = owner
    sent = []

    def post(url, payload, headers, **kwargs):
        sent.append((url, payload))
        assert len(sent) <= 2
        if len(sent) == 1:
            assert url == "https://api.llmgw.ru/v1/chat/completions"
            return httpx.Response(502, json={"error": "Bad gateway"}, request=httpx.Request("POST", url))
        assert url == "https://api.aitunnel.ru/v1/chat/completions" and kwargs["provider_scope"] == "aitunnel"
        assert headers["Authorization"] == "Bearer synthetic-backup"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "id": "qa-backup-" + str(run), "model": "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 413, "completion_tokens": 66, "cost_rub": 0.3, "cost": 0.3},
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "add-1", "type": "function", "function": {"name": "add", "arguments": '{"a":2,"b":3}'}}
            ]}}]})

    monkeypatch.setattr(messages_native, "_post_llmgw", post)
    monkeypatch.setattr(messages_native.file_logger, "log_request", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(messages_native.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qa-no-network") as client:
        response = await client.post("/v1/messages", json={"model": "claude-sonnet-5.5", "max_tokens": 128,
            "messages": [{"role": "user", "content": "Add 2 and 3 with the add tool"}],
            "tools": [{"name": "add", "input_schema": {"type": "object"}}], "tool_choice": {"type": "auto"},
            "metadata": {"user_id": str(user), "project_id": str(project), "message_id": str(messages[0]),
                         "run_id": str(run), "stage": "native_agent"}})
    assert response.status_code == 200 and response.json()["stop_reason"] == "tool_use"
    assert response.json()["content"][0]["input"] == {"a": 2, "b": 3}
    assert response.json()["metadata"]["provider_scope"] == "aitunnel"
    assert len(sent) == 2 and sent[1][1]["model"] == "claude-sonnet-5.5"
    assert {k: v for k, v in sent[0][1].items() if k != "model"} == {
        k: v for k, v in sent[1][1].items() if k != "model"}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT provider_scope,status,provider_cost_rub,provider_cost_usd,receipt_hash "
                                "FROM provider_calls WHERE run_id=$1 ORDER BY created_at", run)
        assert [(r["provider_scope"], r["status"], r["provider_cost_rub"], r["provider_cost_usd"]) for r in rows] == [
            ("llmgw", "failed", None, None), ("aitunnel", "completed", Decimal("0.3"), None)]
        assert all(r["receipt_hash"] for r in rows)
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", run) == 1
        assert await conn.fetchval("SELECT count(*) FROM usage_settlements WHERE user_id=$1 "
                                   "AND provider_scope='aitunnel' AND status='deferred'", user) == 1
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 0
    await _prepare_accept(database_url, owner)
    assert await _finish(database_url, run) == "completed"
    await _make_outbox_due(pool, run)
    await _reconcile(database_url)
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == Decimal("99.7")
        charges = await conn.fetch("SELECT amount_rub,external_ref FROM wallet_charges WHERE user_id=$1", user)
        assert [tuple(r.values()) for r in charges] == [(Decimal("-0.3"), "generation:" + str(run))]
        assert await conn.fetchval("SELECT settled_at IS NOT NULL FROM generation_billing_outbox WHERE run_id=$1", run)
