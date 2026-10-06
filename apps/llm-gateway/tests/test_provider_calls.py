from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.services import provider_calls


class Context:
    def __init__(self, value, rollback=False):
        self.value, self.rollback = value, rollback

    async def __aenter__(self):
        self.snapshot = deepcopy(self.value.rows) if self.rollback else None
        return self.value

    async def __aexit__(self, typ, *_):
        if typ and self.rollback:
            self.value.rows = self.snapshot


class Connection:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def transaction(self):
        return Context(self, True)

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        if sql.startswith("INSERT INTO provider_calls"):
            keys = ("id", "route", "requested_model", "expense_owner", "user_id",
                    "project_id", "run_id", "message_id", "stage", "free")
            self.rows[args[0]] = dict(zip(keys, args, strict=True)) | {
                "status": "started", "receipt_hash": None, "provider_request_id": None,
                "usage_id": None,
            }
        elif sql.startswith("UPDATE provider_calls SET status='ambiguous'"):
            self.rows[args[0]].update(status="ambiguous", error_type="duplicate_provider_receipt")
        elif sql.startswith("UPDATE provider_calls SET status=$2"):
            keys = ("id", "status", "actual_model", "provider_request_id", "tokens_in",
                    "tokens_out", "cache_read_tokens", "cache_write_tokens", "calculated_cost_rub",
                    "provider_cost_rub", "provider_cost_usd", "cost_provenance", "error_type",
                    "usage_id", "receipt_hash")
            self.rows[args[0]].update(dict(zip(keys, args, strict=True)))
        elif sql.startswith("UPDATE provider_calls SET usage_id"):
            self.rows[args[0]]["usage_id"] = args[1]
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        if "WHERE id=$1" in sql:
            return self.rows.get(args[0])
        return next((r for r in self.rows.values()
                     if r["provider_request_id"] == args[0] and r["id"] != args[1]), None)


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return Context(self.connection)


@pytest.fixture
def connection(monkeypatch):
    conn = Connection()
    monkeypatch.setattr(provider_calls, "get_pool", lambda: Pool(conn))
    return conn


async def start(**kwargs):
    return await provider_calls.start_call(route="/v1/messages", model="claude-sonnet-5", **kwargs)


async def finish(call_id, **kwargs):
    values = {"actual_model": "claude-sonnet-5", "provider_request_id": "provider-1",
              "tokens_in": 5, "tokens_out": 2, "calculated_cost_rub": Decimal(".5")}
    values.update(kwargs)
    await provider_calls.finish_call(call_id, **values)


async def test_platform_receipt_zero_and_unknown_cost_stay_distinct(connection):
    call_id = await start(stage="arbitrary unsafe stage", free=True)
    assert connection.rows[call_id]["expense_owner"] == "platform"
    assert connection.rows[call_id]["stage"] == "unknown"
    assert connection.rows[call_id]["status"] == "started"
    await finish(call_id, provider_cost_rub=Decimal(0))
    row = connection.rows[call_id]
    assert row["status"] == "completed"
    assert row["provider_cost_rub"] == 0 and row["provider_cost_usd"] is None
    assert all("wallet" not in sql and "INSERT INTO usage" not in sql for sql, _ in connection.writes)


async def test_user_attribution_does_not_charge_and_replay_is_idempotent(connection):
    user, run = uuid4(), uuid4()
    call_id = await start(user_id=user, run_id=run)
    await finish(call_id)
    before = deepcopy(connection.rows)
    await finish(call_id)
    assert connection.rows == before
    assert connection.rows[call_id]["user_id"] == user
    assert connection.rows[call_id]["expense_owner"] == "user"
    with pytest.raises(BillingReconciliationRequiredError):
        await finish(call_id, calculated_cost_rub=Decimal(".7"))
    assert connection.rows == before


async def test_duplicate_provider_receipt_blocks_before_customer_billing(connection):
    first, second = await start(), await start()
    await finish(first)
    with pytest.raises(BillingReconciliationRequiredError):
        await finish(second)
    assert connection.rows[first]["status"] == "completed"
    assert connection.rows[second]["status"] == "ambiguous"
    assert connection.rows[second]["provider_request_id"] is None


@pytest.mark.parametrize("field,value", [("tokens_in", True), ("tokens_out", -1),
    ("cache_read_tokens", 1.5), ("provider_cost_rub", Decimal("NaN")),
    ("provider_cost_usd", Decimal("-1")), ("calculated_cost_rub", True),
    ("cost_provenance", {"prompt": "must not be stored"})])
async def test_invalid_receipt_never_writes_unsafe_data(connection, field, value):
    call_id = await start()
    with pytest.raises(BillingReconciliationRequiredError):
        await finish(call_id, **{field: value})
    assert connection.rows[call_id]["status"] == "started"


async def test_missing_receipt_and_unknown_attempt_are_nonreplayable(connection):
    call_id = await start()
    with pytest.raises(BillingReconciliationRequiredError):
        await provider_calls.finish_call(call_id)
    with pytest.raises(BillingReconciliationRequiredError):
        await finish(uuid4())
    await provider_calls.finish_call(call_id, status="ambiguous", error_type="ReadTimeout")
    assert connection.rows[call_id]["provider_cost_rub"] is None


async def test_database_failure_is_safe_and_start_fails_closed(monkeypatch):
    def unavailable():
        raise RuntimeError("private database connection detail")
    monkeypatch.setattr(provider_calls, "get_pool", unavailable)
    with pytest.raises(BillingReconciliationRequiredError) as raised:
        await start()
    assert "private" not in str(raised.value)
    with pytest.raises(BillingReconciliationRequiredError) as raised:
        await finish(uuid4())
    assert raised.value.details["provider_charge_ambiguous"] is True


async def test_usage_link_can_be_added_but_never_rebound(connection):
    call_id = await start(user_id=uuid4())
    await finish(call_id)
    usage = uuid4()
    await finish(call_id, usage_id=usage)
    assert connection.rows[call_id]["usage_id"] == usage
    with pytest.raises(BillingReconciliationRequiredError):
        await finish(call_id, usage_id=uuid4())
    assert connection.rows[call_id]["usage_id"] == usage


async def test_zero_cost_completed_call_without_provider_id_is_recorded(connection):
    call_id = await start()
    await finish(call_id, provider_request_id=None, calculated_cost_rub=Decimal(0))
    assert connection.rows[call_id]["status"] == "completed"
    assert connection.rows[call_id]["calculated_cost_rub"] == 0


async def test_database_error_after_start_preserves_started_attempt(connection, monkeypatch):
    call_id = await start()
    async def failure(*args):
        raise RuntimeError("private database error")
    monkeypatch.setattr(connection, "execute", failure)
    with pytest.raises(BillingReconciliationRequiredError) as raised:
        await finish(call_id)
    assert "private" not in str(raised.value)
    assert connection.rows[call_id]["status"] == "started"
