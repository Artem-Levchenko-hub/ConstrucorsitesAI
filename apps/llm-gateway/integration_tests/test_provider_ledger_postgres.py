"""Owned PostgreSQL acceptance; no real provider requests or customer data."""

import asyncio
import hashlib
import json
import os
import stat
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import test_billing_settlement_postgres as fixture_source
from yleum_api.services.provider_ledger import (
    import_statement,
    parse_statement,
    report_organization,
)

from yleum_gateway.services import billing, provider_calls

database_url = fixture_source.database_url
pool = fixture_source.pool
SOURCE = b"synthetic provider ledger; owned disposable database"
CLI_SCRIPT = Path(__file__).resolve().parents[2] / "api/scripts/reconcile_provider_ledger.py"


def statement(*rows, org="qa-org-a"):
    payload = {
        "schema_version": 1,
        "organization_id": org,
        "source_kind": "balance_ledger_export",
        "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "operations": list(rows),
    }
    return parse_statement(
        json.dumps(payload).encode(), expected_organization_id=org, source=SOURCE
    )


def operation(op="qa-op-1", ref="qa-request-1", delta=-2398, kind="usage"):
    return {
        "operation_id": op,
        "ref_id": ref,
        "kind": kind,
        "delta_kopecks": delta,
        "currency": "RUB",
    }


async def call(pool, monkeypatch, *, ref="qa-request-1", org="qa-org-a"):
    from types import SimpleNamespace

    monkeypatch.setattr(
        provider_calls, "get_settings", lambda: SimpleNamespace(llmgw_organization_id=org)
    )
    call_id = await provider_calls.start_call(route="/v1/messages", model="qa-no-provider")
    await provider_calls.finish_call(
        call_id,
        actual_model="qa-no-provider",
        provider_request_id=ref,
        calculated_cost_rub=Decimal("23.9806"),
    )
    return call_id


async def imported(pool, document):
    async with pool.acquire() as conn:
        return await import_statement(conn, document)


async def report(pool, org="qa-org-a"):
    async with pool.acquire() as conn:
        return await report_organization(conn, org)


async def test_real_id_match_keeps_integer_amount_and_original_receipt(pool, monkeypatch):
    org = "qa-exact-org-" + uuid4().hex
    call_id = await call(pool, monkeypatch, org=org)
    async with pool.acquire() as conn:
        before = dict(await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", call_id))
    result = await imported(pool, statement(operation(), org=org))
    row = next(r for r in result["operations"] if r["operation_id"] == "qa-op-1")
    assert row["state"] == "confirmed"
    assert row["call_id"] == str(call_id) and row["ref_id"] == "qa-request-1"
    assert result["confirmed_expense_kopecks"] == 2398
    assert Decimal(result["linked_estimate_rub"]) == Decimal("23.9806")
    assert Decimal(result["estimate_difference_rub"]) == Decimal("-0.0006")
    async with pool.acquire() as conn:
        assert (
            dict(await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", call_id)) == before
        )


async def test_replay_and_concurrent_import_do_not_duplicate_or_bill(pool, monkeypatch):
    user, _, _ = await fixture_source._owner(pool)
    await call(pool, monkeypatch, ref="qa-replay-ref")
    document = statement(operation("qa-replay-op", "qa-replay-ref", -580))
    before = await fixture_source._counts(pool, user)
    await asyncio.gather(*(imported(pool, document) for _ in range(4)))
    assert await fixture_source._counts(pool, user) == before == (Decimal("100"), 0, 0)
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_ledger_entries WHERE operation_id='qa-replay-op'"
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_ledger_confirmations "
                "WHERE operation_id='qa-replay-op'"
            )
            == 1
        )


async def test_amount_conflict_is_durable_visible_and_excluded_from_total(pool, monkeypatch):
    await call(pool, monkeypatch, ref="qa-conflict-ref")
    await imported(pool, statement(operation("qa-conflict-op", "qa-conflict-ref", -315)))
    document = statement(operation("qa-conflict-op", "qa-conflict-ref", -316))
    await imported(pool, document)
    await imported(pool, document)
    row = next(
        r for r in (await report(pool))["operations"] if r["operation_id"] == "qa-conflict-op"
    )
    assert row["state"] == "conflict" and row["delta_kopecks"] == -315
    assert row["conflicting_delta_kopecks"] == [-316]
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_ledger_conflicts WHERE operation_id='qa-conflict-op'"
            )
            == 1
        )


async def test_same_batch_conflict_never_gets_confirmation(pool, monkeypatch):
    await call(pool, monkeypatch, ref="qa-batch-ref")
    await imported(
        pool,
        statement(
            operation("qa-batch-op", "qa-batch-ref", -339),
            operation("qa-batch-op", "qa-batch-ref", -340),
        ),
    )
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_ledger_confirmations "
                "WHERE operation_id='qa-batch-op'"
            )
            == 0
        )


@pytest.mark.parametrize(
    "call_org,state", [("qa-org-b", "organization_mismatch"), (None, "call_organization_unknown")]
)
async def test_matching_id_without_matching_call_scope_cannot_confirm(
    pool, monkeypatch, call_org, state
):
    ref = "qa-scope-" + uuid4().hex
    await call(pool, monkeypatch, ref=ref, org=call_org)
    result = await imported(pool, statement(operation("qa-scope-" + ref, ref)))
    own_row = next(r for r in result["operations"] if r["ref_id"] == ref)
    assert own_row["state"] == state
    assert all(r["state"] != "confirmed" for r in result["operations"] if r["ref_id"] == ref)


async def test_missing_ref_and_unknown_call_are_retained_separately(pool):
    result = await imported(
        pool, statement(operation("qa-no-ref", None), operation("qa-no-call", "qa-absent-request"))
    )
    states = {r["operation_id"]: r["state"] for r in result["operations"]}
    assert states["qa-no-ref"] == "missing_ref_id"
    assert states["qa-no-call"] == "call_not_found"


async def test_duplicate_usage_ref_is_ambiguous_even_after_first_confirmation(pool, monkeypatch):
    ref = "qa-duplicate-usage-ref"
    await call(pool, monkeypatch, ref=ref)
    await imported(pool, statement(operation("qa-duplicate-op-1", ref, -402)))
    result = await imported(pool, statement(operation("qa-duplicate-op-2", ref, -402)))
    rows = [r for r in result["operations"] if r["ref_id"] == ref]
    assert len(rows) == 2 and {r["state"] for r in rows} == {"ambiguous_ref_id"}
    assert all(r["confirmed_expense_kopecks"] == 0 for r in rows)


async def test_correction_has_own_confirmation_and_does_not_rewrite_charge(pool, monkeypatch):
    ref = "qa-correct-ref"
    await call(pool, monkeypatch, ref=ref)
    result = await imported(
        pool,
        statement(
            operation("qa-original", ref, -421), operation("qa-adjustment", ref, 21, "correction")
        ),
    )
    rows = [r for r in result["operations"] if r["ref_id"] == ref]
    assert {r["state"] for r in rows} == {"confirmed"}
    assert sum(r["confirmed_expense_kopecks"] for r in rows) == 400
    assert [r["delta_kopecks"] for r in rows if r["operation_id"] == "qa-original"] == [-421]


async def test_positive_usage_and_other_operations_do_not_become_cost(pool, monkeypatch):
    ref = "qa-other-ref"
    await call(pool, monkeypatch, ref=ref)
    result = await imported(
        pool,
        statement(operation("qa-positive", ref, 200), operation("qa-topup", ref, 1000, "other")),
    )
    states = {r["operation_id"]: r["state"] for r in result["operations"]}
    assert states["qa-positive"] == "invalid_usage_direction"
    assert states["qa-topup"] == "not_usage"


async def test_entries_conflicts_and_confirmations_are_database_immutable(pool, monkeypatch):
    ref = "qa-immutable-ref"
    await call(pool, monkeypatch, ref=ref)
    await imported(pool, statement(operation("qa-immutable", ref, -1)))
    await imported(pool, statement(operation("qa-immutable", ref, -2)))
    async with pool.acquire() as conn:
        for table in (
            "provider_ledger_entries",
            "provider_ledger_conflicts",
            "provider_ledger_confirmations",
        ):
            for sql in (
                f"DELETE FROM {table} WHERE operation_id='qa-immutable'",
                f"UPDATE {table} SET operation_id=operation_id WHERE operation_id='qa-immutable'",
            ):
                with pytest.raises(asyncpg.RaiseError, match="immutable_provider_ledger"):
                    await conn.execute(sql)


async def test_foreign_org_report_has_no_operations_from_this_org(pool):
    await imported(pool, statement(operation("qa-isolated-op", None), org="qa-isolated-org"))
    assert (await report(pool, "qa-other-org"))["operations"] == []


async def test_direct_confirmation_cannot_join_foreign_org_call(pool, monkeypatch):
    call_id = await call(pool, monkeypatch, ref="qa-direct-ref", org="qa-direct-org-b")
    await imported(pool, statement(operation("qa-direct-op", "qa-direct-ref")))
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="scope_mismatch"):
            await conn.execute(
                "INSERT INTO provider_ledger_confirmations (organization_id,operation_id,call_id,"
                "entry_receipt_hash,call_receipt_hash) SELECT e.organization_id,e.operation_id,"
                "c.id,e.receipt_hash,c.receipt_hash FROM provider_ledger_entries e,provider_calls c "
                "WHERE e.operation_id='qa-direct-op' AND c.id=$1",
                call_id,
            )


async def test_historical_unknown_organization_cannot_be_silently_backfilled(pool, monkeypatch):
    call_id = await call(pool, monkeypatch, ref="qa-no-backfill-ref", org=None)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="immutable_provider_call_organization"):
            await conn.execute(
                "UPDATE provider_calls SET provider_organization_id='qa-org-a' WHERE id=$1", call_id
            )


async def test_truncate_cannot_erase_confirmations(pool):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="immutable_provider_ledger"):
            await conn.execute("TRUNCATE provider_ledger_confirmations")


async def test_correction_only_does_not_compare_refund_with_full_call_estimate(pool, monkeypatch):
    org = "qa-refund-only-" + uuid4().hex
    await call(pool, monkeypatch, ref="qa-refund-only-ref", org=org)
    result = await imported(
        pool,
        statement(operation("qa-refund-only", "qa-refund-only-ref", 21, "correction"), org=org),
    )
    assert result["confirmed_expense_kopecks"] == -21
    assert result["linked_estimate_rub"] is None
    assert result["estimate_difference_rub"] is None


async def test_import_cannot_reprice_or_debit_an_already_settled_customer(pool, monkeypatch):
    from types import SimpleNamespace

    user, project, messages = await fixture_source._owner(pool)
    org, ref = "qa-paid-org-" + uuid4().hex, "qa-paid-import-ref"
    monkeypatch.setattr(
        provider_calls, "get_settings", lambda: SimpleNamespace(llmgw_organization_id=org)
    )
    call_id = await provider_calls.start_call(
        route="/v1/messages",
        model="qa-no-provider",
        user_id=user,
        project_id=project,
        message_id=messages[0],
    )
    await provider_calls.finish_call(
        call_id,
        actual_model="qa-no-provider",
        provider_request_id=ref,
        calculated_cost_rub=Decimal("12.50"),
    )
    settlement_id = await billing.charge(
        user_id=user,
        project_id=project,
        message_id=messages[0],
        model_id="qa-no-provider",
        tokens_in=3,
        tokens_out=5,
        cost_rub=Decimal("12.50"),
        description="synthetic already settled customer",
        provider_request_id=ref,
    )
    async with pool.acquire() as conn:
        usage_id = await conn.fetchval(
            "SELECT usage_id FROM usage_settlements WHERE id=$1",
            settlement_id,
        )
    assert usage_id is not None
    await provider_calls.finish_call(
        call_id,
        actual_model="qa-no-provider",
        provider_request_id=ref,
        calculated_cost_rub=Decimal("12.50"),
        usage_id=usage_id,
    )
    before = await fixture_source._counts(pool, user)
    assert before == (Decimal("87.5"), 1, 1)
    async with pool.acquire() as conn:
        receipt_before = dict(await conn.fetchrow("SELECT * FROM usage WHERE id=$1", usage_id))
        settlement_before = dict(
            await conn.fetchrow("SELECT * FROM usage_settlements WHERE usage_id=$1", usage_id)
        )
    document = statement(operation("qa-paid-import-op", ref, -1251), org=org)
    result = await imported(pool, document)
    await imported(pool, document)
    assert result["confirmed_expense_kopecks"] == 1251
    assert Decimal(result["estimate_difference_rub"]) == Decimal("0.01")
    assert await fixture_source._counts(pool, user) == before
    async with pool.acquire() as conn:
        assert (
            dict(await conn.fetchrow("SELECT * FROM usage WHERE id=$1", usage_id)) == receipt_before
        )
        assert (
            dict(await conn.fetchrow("SELECT * FROM usage_settlements WHERE usage_id=$1", usage_id))
            == settlement_before
        )


async def test_operator_cli_real_database_import_and_replay_are_private_and_idempotent(
    pool,
    database_url,
    monkeypatch,
    tmp_path,
):
    org, ref = "qa-cli-org-" + uuid4().hex, "qa-cli-private-request"
    await call(pool, monkeypatch, org=org, ref=ref)
    source = tmp_path / "source.bin"
    source.write_bytes(SOURCE)
    normalized = tmp_path / "normalized.json"
    normalized.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "organization_id": org,
                "source_kind": "balance_ledger_export",
                "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
                "operations": [operation("qa-cli-op", ref, -2398)],
            }
        )
    )
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    for index in range(2):
        report_path = tmp_path / f"report-{index}.json"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(CLI_SCRIPT),
            "--expected-organization-id",
            org,
            "--normalized-statement",
            str(normalized),
            "--source",
            str(source),
            "--report-file",
            str(report_path),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0
        assert ref.encode() not in stdout + stderr and org.encode() not in stdout + stderr
        assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
        report_data = json.loads(report_path.read_bytes())
        assert report_data["operations"][0]["ref_id"] == ref
        assert report_data["confirmed_expense_kopecks"] == 2398
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_ledger_confirmations WHERE organization_id=$1", org
            )
            == 1
        )
