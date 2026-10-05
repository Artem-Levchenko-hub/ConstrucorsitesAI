"""Only newly owned local PostgreSQL: additive migration and exact paid receipt."""

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import test_billing_settlement_postgres as fixture_source
from alembic import command
from alembic.config import Config
from test_billing_settlement_postgres import ROOT, _owner
from test_generation_billing_postgres import _finish, _prepare_accept, _reconcile, _run

from yleum_gateway.services import billing, pricing

database_url = fixture_source.database_url
pool = fixture_source.pool
MODEL = "gemini-3.1-pro-preview-customtools"


@pytest.mark.parametrize("model,output_rate,writes,factor,expected", [
    (MODEL, "2099.4918", 0, None, ".3709"),
    ("claude-sonnet-5", "1749.5765", 200, Decimal("1.25"), ".3534"),
    ("claude-sonnet-5", "1749.5765", 200, Decimal("2"), ".4059"),
])
async def test_catalog_estimate_and_usd_survive_real_settlement(
    pool, monkeypatch, model, output_rate, writes, factor, expected,
):
    user, project, messages = await _owner(pool)
    monkeypatch.setattr(pricing, "_catalog_prices", AsyncMock(return_value={
        model: (Decimal("349.9153"), Decimal(output_rate), Decimal("34.9915"))}))
    cost, evidence = await pricing.resolve_request_cost(model, tokens_in=1000, tokens_out=100,
        cache_read_tokens=600, cache_write_tokens=writes, cache_write_factor=factor,
        reported=pricing.ReportedCost(cost_usd=Decimal(".0123"),
                                                           usd_source="usage:cost"))
    args = dict(user_id=user, project_id=project, message_id=messages[0], model_id=model,
        tokens_in=1000, tokens_out=100, cache_read_tokens=600,
        cache_write_tokens=writes, cost_rub=cost,
        provider_cost_usd=Decimal(".0123"), cost_provenance=evidence,
        provider_request_id="catalog-" + str(uuid4()), description="Synthetic catalog quote")
    first = await billing.charge(**args)
    assert await billing.charge(**args) == first
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT cost_rub,provider_cost_usd,cost_provenance,cache_write_tokens FROM usage WHERE user_id=$1", user)
        assert len(rows) == 1
        assert rows[0]["cost_rub"] == cost == Decimal(expected)
        assert rows[0]["cache_write_tokens"] == writes
        assert rows[0]["provider_cost_usd"] == Decimal(".0123")
        assert json.loads(rows[0]["cost_provenance"]) == evidence
        assert evidence["basis"] == "provider_catalog_estimate"
        assert evidence["reported_cost_rub"] is None
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 100 - cost
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 1


async def test_nullable_migration_preserves_old_usage_and_old_writer(pool):
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/migrations"))
    await asyncio.to_thread(command.downgrade, config, "0077_free_owner_allowance")
    user, project, messages = await _owner(pool)
    old_id, later_id = uuid4(), uuid4()
    old_insert = (
        "INSERT INTO usage(id,user_id,project_id,message_id,model_id,tokens_in,tokens_out,cost_rub) "
        "VALUES($1,$2,$3,$4,'legacy',3,5,1.2345)"
    )
    async with pool.acquire() as conn:
        await conn.execute(old_insert, old_id, user, project, messages[0])
        old = await conn.fetchrow("SELECT * FROM usage WHERE id=$1", old_id)
        assert "cost_provenance" not in old
    await asyncio.to_thread(command.upgrade, config, "head")
    async with pool.acquire() as conn:
        current = await conn.fetchrow("SELECT * FROM usage WHERE id=$1", old_id)
        assert "cost_provenance" in current  # RED until the additive migration exists.
        assert current["cost_provenance"] is None
        assert {k: v for k, v in dict(current).items() if k != "cost_provenance"} == dict(old)
        metadata = await conn.fetchrow(
            "SELECT data_type,is_nullable,column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='usage' AND column_name='cost_provenance'"
        )
        assert dict(metadata) == {"data_type": "jsonb", "is_nullable": "YES", "column_default": None}
        await conn.execute(old_insert, later_id, user, project, messages[0])
        assert await conn.fetchval("SELECT cost_provenance FROM usage WHERE id=$1", later_id) is None


async def test_paid_accepted_aggregate_retains_reported_and_fallback_provenance(pool, database_url):
    user, project, messages, run = await _run(pool, database_url)
    records = [
        pricing.cost_provenance(
            MODEL, Decimal("9"), pricing.read_reported_cost({}, {"x-llmgw-cost-rub": "2"}),
            tokens_in=3, tokens_out=5,
        ),
        pricing.cost_provenance(MODEL, Decimal("3"), tokens_in=3, tokens_out=5),
    ]
    for index, evidence in enumerate(records):
        key = "provenance-" + str(run) + "-" + str(index)
        args = dict(
            user_id=user, project_id=project, message_id=messages[0], run_id=run,
            model_id=MODEL, tokens_in=3, tokens_out=5,
            cost_rub=Decimal(evidence["effective_cost_rub"]), cost_provenance=evidence,
            description="Synthetic provider receipt", stage="native_agent", provider_request_id=key,
        )
        first = await billing.charge(**args)
        assert await billing.charge(**args) == first
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT cost_provenance FROM usage WHERE run_id=$1 ORDER BY cost_rub", run)
        assert [json.loads(row["cost_provenance"]) for row in rows] == records
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 100
    await _prepare_accept(database_url, (user, project, messages, run))
    assert await _finish(database_url, run) == "completed"
    await _reconcile(database_url)
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 95
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 1
        intent = await conn.fetchrow("SELECT outcome,amount_rub,usage_ids FROM generation_billing_intents WHERE run_id=$1", run)
        assert intent["outcome"] == "billable" and intent["amount_rub"] == 5
        assert len(json.loads(intent["usage_ids"])) == 2
