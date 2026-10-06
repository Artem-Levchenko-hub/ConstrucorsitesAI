"""Physical provider journal checks against the owned disposable local database."""

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
import test_billing_settlement_postgres as fixture_source
from test_billing_settlement_postgres import _owner

from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.services import billing, pricing, provider_calls

database_url = fixture_source.database_url
pool = fixture_source.pool
MODEL = "gemini-3.1-pro-preview-customtools"


def _receipt(key, amount="2"):
    cost = Decimal(amount)
    reported = pricing.ReportedCost(
        cost_rub=cost, cost_usd=Decimal(".0123"),
        rub_source="header:x-llmgw-cost-rub", usd_source="usage:cost_usd",
    )
    provenance = pricing.cost_provenance(
        MODEL, Decimal("3"), reported, tokens_in=10, tokens_out=5,
        cache_read_tokens=4, cache_write_tokens=2,
    )
    return dict(actual_model=MODEL, provider_request_id=key, tokens_in=10, tokens_out=5,
                cache_read_tokens=4, cache_write_tokens=2, calculated_cost_rub=Decimal("3"),
                provider_cost_rub=cost, provider_cost_usd=Decimal(".0123"),
                cost_provenance=provenance)


async def _start(**kwargs):
    return await provider_calls.start_call(route="/v1/messages", model=MODEL, **kwargs)


async def _bill(user, project, key, provenance, amount="2"):
    return await billing.charge(
        user_id=user, project_id=project, message_id=None, model_id=MODEL,
        tokens_in=10, tokens_out=5, cache_read_tokens=4, cache_write_tokens=2,
        cost_rub=Decimal(amount), provider_cost_usd=Decimal(".0123"),
        provider_request_id=key, cost_provenance=provenance,
        description="Synthetic provider journal integration receipt", stage="runtime_ai",
    )


async def test_platform_receipt_is_durable_without_usage_or_wallet(pool):
    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM usage) AS usages, "
            "(SELECT count(*) FROM wallet_charges) AS charges, "
            "(SELECT coalesce(sum(balance_rub),0) FROM wallets) AS balance"
        )
    call = await _start(free=True, stage="product_advisor")
    receipt = _receipt("platform-" + str(uuid4()), "0")
    await provider_calls.finish_call(call, **receipt)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", call)
        assert row["status"] == "completed" and row["expense_owner"] == "platform"
        assert row["user_id"] is None and row["usage_id"] is None
        assert row["provider_cost_rub"] == 0 and row["provider_cost_usd"] == Decimal(".0123")
        assert json.loads(row["cost_provenance"]) == receipt["cost_provenance"]
        assert row["finished_at"] >= row["created_at"]
        after = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM usage) AS usages, "
            "(SELECT count(*) FROM wallet_charges) AS charges, "
            "(SELECT coalesce(sum(balance_rub),0) FROM wallets) AS balance"
        )
        assert dict(after) == dict(before)


async def test_paid_receipt_precedes_unchanged_customer_billing(pool):
    user, project, _ = await _owner(pool)
    key = "paid-journal-" + str(uuid4())
    call = await _start(user_id=user, project_id=project, stage="runtime_ai")
    receipt = _receipt(key)
    await provider_calls.finish_call(call, **receipt)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 100
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE user_id=$1", user) == 0
    first = await _bill(user, project, key, receipt["cost_provenance"])
    assert await _bill(user, project, key, receipt["cost_provenance"]) == first
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 98
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 1
        usage = await conn.fetchrow("SELECT * FROM usage WHERE user_id=$1", user)
        assert usage["cost_rub"] == 2
    await provider_calls.finish_call(call, **receipt, usage_id=usage["id"])
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT usage_id FROM provider_calls WHERE id=$1", call) == usage["id"]


async def test_receipt_survives_real_billing_transaction_failure(pool):
    user, project, _ = await _owner(pool)
    key = "billing-failure-" + str(uuid4())
    call = await _start(user_id=user, project_id=project)
    receipt = _receipt(key)
    await provider_calls.finish_call(call, **receipt)
    # A nonexistent project makes the usage INSERT fail after the attempted wallet
    # debit. PostgreSQL must roll that debit back without losing the provider row.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _bill(user, uuid4(), key, receipt["cost_provenance"])
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM provider_calls WHERE id=$1", call) == "completed"
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 100
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE user_id=$1", user) == 0
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 0


async def test_replay_and_conflicting_receipt_cannot_mutate_committed_evidence(pool):
    call = await _start()
    key = "replay-journal-" + str(uuid4())
    receipt = _receipt(key)
    await provider_calls.finish_call(call, **receipt)
    async with pool.acquire() as conn:
        original = dict(await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", call))
    await provider_calls.finish_call(call, **receipt)
    with pytest.raises(BillingReconciliationRequiredError):
        await provider_calls.finish_call(call, **_receipt(key, "7"))
    async with pool.acquire() as conn:
        assert dict(await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", call)) == original


async def test_concurrent_provider_id_has_one_receipt_and_one_customer_debit(pool):
    user, project, _ = await _owner(pool)
    key = "concurrent-journal-" + str(uuid4())
    first, second = await _start(user_id=user), await _start(user_id=user)
    receipt = _receipt(key)

    async def settle(call):
        await provider_calls.finish_call(call, **receipt)
        return await _bill(user, project, key, receipt["cost_provenance"])

    outcomes = await asyncio.gather(settle(first), settle(second), return_exceptions=True)
    assert sum(isinstance(value, BillingReconciliationRequiredError) for value in outcomes) == 1
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM provider_calls WHERE id=ANY($1::uuid[])", [first, second])
        assert sorted(row["status"] for row in rows) == ["ambiguous", "completed"]
        assert sum(row["provider_request_id"] == key for row in rows) == 1
        duplicate = next(row for row in rows if row["status"] == "ambiguous")
        assert duplicate["error_type"] == "duplicate_provider_receipt"
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 98
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE user_id=$1", user) == 1
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 1


@pytest.mark.parametrize("status", ["failed", "ambiguous"])
async def test_unknown_failure_cost_is_null_and_reported_zero_is_preserved(pool, status):
    unknown, zero = await _start(), await _start()
    await provider_calls.finish_call(unknown, status=status, error_type="ReadTimeout")
    await provider_calls.finish_call(zero, status=status, provider_cost_rub=Decimal(0))
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", unknown)
        assert row["status"] == status
        assert all(row[k] is None for k in ("provider_cost_rub", "calculated_cost_rub", "provider_cost_usd"))
        assert await conn.fetchval("SELECT provider_cost_rub FROM provider_calls WHERE id=$1", zero) == 0


async def test_new_journal_preserves_legacy_usage_and_old_insert_shape(pool):
    # The shared cost-provenance migration test already upgrades old Usage to
    # current head. Do not downgrade this shared session database again here.
    user, project, _ = await _owner(pool)
    old_id, later_id = uuid4(), uuid4()
    insert = ("INSERT INTO usage(id,user_id,project_id,model_id,tokens_in,tokens_out,cost_rub) "
              "VALUES($1,$2,$3,'legacy',3,5,1.2345)")
    async with pool.acquire() as conn:
        await conn.execute(insert, old_id, user, project)
        original = dict(await conn.fetchrow("SELECT * FROM usage WHERE id=$1", old_id))
    call = await _start()
    await provider_calls.finish_call(call, **_receipt("legacy-independent-" + str(uuid4())))
    async with pool.acquire() as conn:
        assert dict(await conn.fetchrow("SELECT * FROM usage WHERE id=$1", old_id)) == original
        await conn.execute(insert, later_id, user, project)
        assert await conn.fetchval("SELECT cost_provenance FROM usage WHERE id=$1", later_id) is None
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE user_id=$1", user) == 2
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 0
