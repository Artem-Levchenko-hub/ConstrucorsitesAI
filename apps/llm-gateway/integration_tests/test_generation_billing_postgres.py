"""Owned PostgreSQL generation billing fixtures; no provider or real wallet calls."""

from decimal import Decimal
from uuid import uuid4

import pytest
import test_billing_settlement_postgres as fixture_source
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_billing_settlement_postgres import _owner

from yleum_gateway.services import billing

database_url = fixture_source.database_url
pool = fixture_source.pool


async def test_paid_completed_call_then_failed_generation_has_zero_charge(pool, database_url):
    from yleum_api.services.generation_runs import (
        finalize_generation_run,
        record_generation_product_failure,
    )

    user, project, messages = await _owner(pool)
    run = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,"
            "idempotency_key,prompt_hash,status,response_mode) "
            "VALUES($1,$2,$3,$4,$5,'synthetic','running','build')",
            run,
            project,
            user,
            messages[0],
            str(run),
        )
    await billing.charge(
        user_id=user,
        project_id=project,
        message_id=messages[0],
        run_id=run,
        model_id="qa-no-provider",
        tokens_in=3,
        tokens_out=5,
        cost_rub=Decimal("12.5"),
        description="synthetic completed provider call",
        stage="native_agent",
        provider_request_id="failed-" + str(run),
    )
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await record_generation_product_failure(session, run, "verification_failed")
            await session.commit()
            assert await finalize_generation_run(run, session) == "failed"
    finally:
        await engine.dispose()
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT balance_rub FROM wallets WHERE user_id=$1", user
        ) == Decimal("100")
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", run) == 1


async def _run(pool, database_url, *, free=False):
    from yleum_api.services.generation_billing import admit_policy

    user, project, messages = await _owner(pool)
    run = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,"
            "prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'test','running','build')",
            run,
            project,
            user,
            messages[0],
            str(run),
        )
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await admit_policy(session, run, is_free=free)
        await session.commit()
    await engine.dispose()
    return user, project, messages, run


async def _provider(user, project, messages, run, key, amount="2", free=False):
    return await billing.charge(
        user_id=user,
        project_id=project,
        message_id=messages[0],
        run_id=run,
        model_id="synthetic-paid",
        tokens_in=3,
        tokens_out=5,
        cost_rub=Decimal(amount),
        description="No real provider",
        stage="native_agent",
        free=free,
        provider_request_id=key,
    )


async def _accepted(session, user, project, run, messages):
    from types import SimpleNamespace as NS

    from yleum_api.models.message import Message
    from yleum_api.services.generation_artifacts import create_generation_snapshot
    from yleum_api.services.generation_billing import record_accepted_candidate
    from yleum_api.services.promotion_permit import (
        canonical_files_digest,
        issue_promotion_permit,
        release_receipt_digest,
        release_receipt_ref,
    )

    files = {"src/app/page.tsx": "export default function Page(){return <p>fixture</p>}"}
    artifact = canonical_files_digest(files)
    key = "a" * 64
    workspace = uuid4()
    identity = NS(
        proof_key=key,
        workspace_revision="b" * 64,
        workspace_id=workspace,
        generation_run_id=run,
        fencing_epoch=1,
    )
    proof = NS(
        identity=identity,
        full_build=NS(
            outcome="green", workspace_id=workspace, artifact_ref="build/sha256/" + artifact
        ),
        runtime=NS(
            outcome="green", workspace_id=workspace, artifact_ref="verification/sha256/" + "c" * 64
        ),
        release=NS(
            outcome="green",
            workspace_id=workspace,
            redacted_detail="synthetic proof fixture",
            artifact_ref=release_receipt_ref(
                artifact_digest=artifact,
                receipt_digest=release_receipt_digest(
                    proof_key=key, artifact_digest=artifact, detail="synthetic proof fixture"
                ),
            ),
        ),
    )
    permit = issue_promotion_permit(proof)
    snapshot, _ = await create_generation_snapshot(
        session,
        project_id=project,
        run_id=run,
        parent_snapshot_id=None,
        commit_sha="d" * 40,
        prompt_text="synthetic fixture",
        model_id="synthetic-paid",
        changed_files=list(files),
    )
    message = await session.get(Message, messages[0])
    message.snapshot_id = snapshot.id
    await record_accepted_candidate(
        session, run_id=run, snapshot_id=snapshot.id, permit=permit, proof=proof
    )
    return snapshot


async def _finish(database_url, run, *, failed=False):
    from yleum_api.services.generation_runs import (
        finalize_generation_run,
        record_generation_product_failure,
    )

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            if failed:
                await record_generation_product_failure(session, run, "verification_failed")
                await session.commit()
            return await finalize_generation_run(run, session)
    finally:
        await engine.dispose()


async def _prepare_accept(database_url, data):
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user, project, messages, run = data
            await _accepted(session, user, project, run, messages)
            await session.commit()
    finally:
        await engine.dispose()


async def _reconcile(database_url):
    from yleum_api.services.generation_billing import reconcile_generation_billing

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await reconcile_generation_billing(session)
            await session.commit()
    finally:
        await engine.dispose()


async def test_prospective_paid_call_no_early_debit_failed_seals_zero(pool, database_url):
    data = await _run(pool, database_url)
    u, p, m, r = data
    await _provider(u, p, m, r, "prospective-" + str(r), free=True)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
        assert (
            await conn.fetchval(
                "SELECT is_free FROM generation_billing_policies WHERE run_id=$1", r
            )
            is False
        )
    assert await _finish(database_url, r, failed=True) == "failed"
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT amount_rub FROM generation_billing_intents WHERE run_id=$1", r
            )
            == 0
        )
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 0


async def test_accepted_paid_aggregate_exactly_once_two_terminal_workers(pool, database_url):
    import asyncio

    data = await _run(pool, database_url)
    u, p, m, r = data
    await _provider(u, p, m, r, "one-" + str(r), "2")
    await _provider(u, p, m, r, "two-" + str(r), "3")
    await _prepare_accept(database_url, data)
    assert await asyncio.gather(_finish(database_url, r), _finish(database_url, r)) == [
        "completed",
        "completed",
    ]
    await asyncio.gather(_reconcile(database_url), _reconcile(database_url))
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 95
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 1
        row = await conn.fetchrow(
            "SELECT amount_rub,usage_ids,outcome FROM generation_billing_intents WHERE run_id=$1", r
        )
        assert row["amount_rub"] == 5 and row["outcome"] == "billable"
        import json

        assert len(json.loads(row["usage_ids"])) == 2


async def test_late_provider_serializes_behind_seal_and_never_reprices(pool, database_url):
    import asyncio

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_billing import seal_terminal_locked

    data = await _run(pool, database_url)
    u, p, m, r = data
    await _provider(u, p, m, r, "known-" + str(r), "2")
    await _prepare_accept(database_url, data)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(GenerationRun, r, with_for_update=True)
            row.status = "completed"
            await seal_terminal_locked(session, row)
            late = asyncio.create_task(_provider(u, p, m, r, "late-" + str(r), "99"))
            await asyncio.sleep(0.03)
            assert not late.done()
            await session.commit()
            await late
    finally:
        await engine.dispose()
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 98
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", r) == 2
        assert (
            await conn.fetchval(
                "SELECT amount_rub FROM generation_billing_intents WHERE run_id=$1", r
            )
            == 2
        )


async def test_insufficient_accepted_build_pending_retries_frozen_amount(pool, database_url):
    data = await _run(pool, database_url)
    u, p, m, r = data
    await _provider(u, p, m, r, "cost-" + str(r), "101")
    await _prepare_accept(database_url, data)
    assert await _finish(database_url, r) == "completed"
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
        assert (
            await conn.fetchval("SELECT outcome FROM generation_billing_intents WHERE run_id=$1", r)
            == "billable"
        )
        await conn.execute("UPDATE wallets SET balance_rub=200 WHERE user_id=$1", u)
        await conn.execute(
            "UPDATE generation_billing_outbox SET next_attempt_at=now() WHERE run_id=$1", r
        )
    await _reconcile(database_url)
    await _provider(u, p, m, r, "latecost-" + str(r), "99")
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 99
        assert (
            await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r) == "completed"
        )
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 1


async def test_foreign_run_owner_rejected_and_runtime_ai_still_debits(pool, database_url):
    import pytest

    from yleum_gateway.core.errors import BillingReconciliationRequiredError

    data = await _run(pool, database_url)
    u, p, m, r = data
    foreign, fp, fm = await _owner(pool)
    with pytest.raises(BillingReconciliationRequiredError):
        await _provider(foreign, fp, fm, r, "foreign-" + str(r))
    await billing.charge(
        user_id=foreign,
        project_id=fp,
        message_id=fm[0],
        model_id="test-runtime",
        tokens_in=1,
        tokens_out=1,
        cost_rub=Decimal("4"),
        description="runtime AI",
        stage="runtime_ai",
        provider_request_id="runtime-" + str(r),
    )
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", foreign) == 96
        )
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", r) == 0


async def test_free_accepted_never_charges_and_legacy_success_not_retrocharged(pool, database_url):
    data = await _run(pool, database_url, free=True)
    u, p, m, r = data
    await _provider(u, p, m, r, "free-" + str(r), "5", free=False)
    await _prepare_accept(database_url, data)
    assert await _finish(database_url, r) == "completed"
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
        assert (
            await conn.fetchval("SELECT outcome FROM generation_billing_intents WHERE run_id=$1", r)
            == "waived"
        )


async def _historical_debit(pool, user, project, messages, run, key, amount):
    # Exact pre-cutover negative entry and usage link; never exercise new code
    # to fabricate a historical early debit that the new contract forbids.
    usage, charge = uuid4(), uuid4()
    async with pool.acquire() as conn, conn.transaction():
        account = await conn.fetchval(
            "SELECT billing_account_id FROM wallets WHERE user_id=$1", user
        )
        balance = await conn.fetchval(
            "UPDATE wallets SET balance_rub=balance_rub-$2 WHERE user_id=$1 RETURNING balance_rub",
            user,
            Decimal(amount),
        )
        await conn.execute(
            "INSERT INTO usage(id,user_id,project_id,message_id,run_id,model_id,tokens_in,tokens_out,cost_rub,stage,provider_request_id) VALUES($1,$2,$3,$4,$5,'historical',1,1,$6,'native_agent',$7)",
            usage,
            user,
            project,
            messages[0],
            run,
            Decimal(amount),
            key,
        )
        await conn.execute(
            "INSERT INTO wallet_charges(id,billing_account_id,user_id,message_id,entry_type,amount_rub,balance_after_rub,external_ref,description) VALUES($1,$2,$3,$4,'usage',$5,$6,$7,'pre-cutover fixture')",
            charge,
            account,
            user,
            messages[0],
            -Decimal(amount),
            balance,
            "usage:" + str(usage),
        )


async def test_legacy_refund_double_worker_restart_and_insert_failure_rolls_back(
    pool, database_url
):
    import asyncio

    import pytest

    u, p, m = await _owner(pool)
    r = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'legacy','running','build')",
            r,
            p,
            u,
            m[0],
            str(r),
        )
    await _historical_debit(pool, u, p, m, r, "legacy1-" + str(r), "2")
    await _historical_debit(pool, u, p, m, r, "legacy2-" + str(r), "3")
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE FUNCTION qa_fail_refund() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.entry_type='refund' THEN RAISE EXCEPTION 'synthetic refund failure'; END IF; RETURN NEW; END $$"
        )
        await conn.execute(
            "CREATE TRIGGER qa_fail_refund BEFORE INSERT ON wallet_charges FOR EACH ROW EXECUTE FUNCTION qa_fail_refund()"
        )
    try:
        assert await _finish(database_url, r, failed=True) == "failed"
        with pytest.raises(Exception, match="synthetic refund failure"):
            await _reconcile(database_url)
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 95
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM wallet_charges WHERE user_id=$1 AND entry_type='refund'",
                    u,
                )
                == 0
            )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DROP TRIGGER qa_fail_refund ON wallet_charges")
            await conn.execute("DROP FUNCTION qa_fail_refund()")
    await asyncio.gather(_reconcile(database_url), _reconcile(database_url))
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wallet_charges WHERE user_id=$1 AND entry_type='refund'", u
            )
            == 2
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wallet_charges WHERE user_id=$1 AND amount_rub<0", u
            )
            == 2
        )
    # Late provider expense cannot debit even a historical failed run.
    await _provider(u, p, m, r, "legacylate-" + str(r), "99")
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100


async def test_billable_insert_failure_rolls_back_wallet_and_terminal_intent(pool, database_url):
    import pytest

    data = await _run(pool, database_url)
    u, p, m, r = data
    await _provider(u, p, m, r, "atomic-" + str(r), "7")
    await _prepare_accept(database_url, data)
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE FUNCTION qa_fail_generation_debit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.external_ref LIKE 'generation:%' THEN RAISE EXCEPTION 'synthetic debit failure'; END IF; RETURN NEW; END $$"
        )
        await conn.execute(
            "CREATE TRIGGER qa_fail_generation_debit BEFORE INSERT ON wallet_charges FOR EACH ROW EXECUTE FUNCTION qa_fail_generation_debit()"
        )
    try:
        assert await _finish(database_url, r) == "completed"
        with pytest.raises(Exception, match="synthetic debit failure"):
            await _reconcile(database_url)
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM generation_billing_intents WHERE run_id=$1", r
                )
                == 1
            )
            assert (
                await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r)
                == "completed"
            )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DROP TRIGGER qa_fail_generation_debit ON wallet_charges")
            await conn.execute("DROP FUNCTION qa_fail_generation_debit()")
    await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 93


async def test_missing_marker_cancelled_failed_or_partial_snapshot_never_charge(pool, database_url):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_runs import terminalize_generation_run_locked

    for state in ["failed", "cancelled", "completed"]:
        data = await _run(pool, database_url)
        u, p, m, r = data
        await _provider(u, p, m, r, "no-marker-" + str(r), "8")
        engine = create_async_engine(
            database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                row = await session.get(GenerationRun, r, with_for_update=True)
                await terminalize_generation_run_locked(session, row, status=state)
                await session.commit()
        finally:
            await engine.dispose()
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
            assert (
                await conn.fetchval(
                    "SELECT outcome FROM generation_billing_intents WHERE run_id=$1", r
                )
                == "waived"
            )


async def test_historical_successful_charges_not_refunded_or_recharged(pool, database_url):
    u, p, m = await _owner(pool)
    r = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'legacy','running','build')",
            r,
            p,
            u,
            m[0],
            str(r),
        )
    await _historical_debit(pool, u, p, m, r, "historical-success-" + str(r), "2")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_runs SET status='completed',finished_at=now() WHERE id=$1", r
        )
    await _reconcile(database_url)
    await _provider(u, p, m, r, "late-historical-success-" + str(r), "99")
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 98
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM generation_billing_intents WHERE run_id=$1", r
            )
            == 0
        )


async def test_active_build_without_policy_never_debits_or_retrocharges(pool, database_url):
    for terminal in ["failed", "completed"]:
        u, p, m = await _owner(pool)
        r = uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'no-policy','running','build')",
                r,
                p,
                u,
                m[0],
                str(r),
            )
        await _provider(u, p, m, r, "missing-policy-" + str(r), "8")
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
            await conn.execute(
                "UPDATE generation_runs SET status=$2,finished_at=now() WHERE id=$1", r, terminal
            )
        await _reconcile(database_url)
        await _provider(u, p, m, r, "late-missing-policy-" + str(r), "99")
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
            assert (
                await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM generation_billing_intents WHERE run_id=$1", r
                )
                == 0
            )


async def test_chat_and_stream_missing_run_id_bind_real_owner_message_without_early_debit(
    pool, database_url, monkeypatch
):
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI
    from yleum_api.services import llm_client

    from yleum_gateway.routers import chat
    from yleum_gateway.services import streaming

    data = await _run(pool, database_url)
    u, p, m, r = data
    model = "gemini-3.1-pro-preview-customtools"

    async def fake_provider(**kwargs):
        return {
            # Each real upstream call has a distinct receipt, including the
            # platform-owned preliminary after the customer's chat call.
            "id": "qa-chat-" + str(uuid4()),
            "model": model,
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost_rub": "2"},
        }

    async def fake_stream(*args, **kwargs):
        kwargs["receipt"].update(
            {
                "id": "qa-stream-" + str(r),
                "model": model,
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost_rub": "3"},
                "complete": True,
            }
        )
        yield "synthetic", model

    monkeypatch.setattr(chat.router_module, "acompletion", fake_provider)
    monkeypatch.setattr(streaming.llmgw, "astream", fake_stream)
    monkeypatch.setattr(chat.cache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.cache, "set", AsyncMock())
    monkeypatch.setattr(chat.file_logger, "log_request", lambda *args: None)
    monkeypatch.setattr(streaming.file_logger, "log_request", lambda *args: None)
    # Real HTTP handlers + real billing/pool; only provider/cache/files stubbed.
    app = FastAPI()
    app.include_router(chat.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://qa-no-network"
    ) as client:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "synthetic"}],
            "user": str(u),
            "metadata": {"project_id": str(p), "message_id": str(m[0]), "free": True},
        }
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 200
        body["stream"] = True
        response = await client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200 and "[DONE]" in response.text
        # Platform-owned service preliminaries stay user=None even in a run context.
        llm_client.set_generation_billing_context(r, u, p)
        body.pop("user")
        body["stream"] = False
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 200
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", r) == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM provider_calls WHERE project_id=$1 AND status='completed'",
            p,
        ) == 3
        assert await conn.fetchval(
            "SELECT count(*) FROM provider_calls WHERE project_id=$1 "
            "AND expense_owner='platform' AND user_id IS NULL",
            p,
        ) == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM usage_settlements WHERE user_id=$1 AND status='deferred' AND wallet_charge_id IS NULL",
                u,
            )
            == 2
        )
    assert await _finish(database_url, r, failed=True) == "failed"
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100


async def test_missing_run_foreign_message_and_explicit_runtime_spoof_cannot_debit(
    pool, database_url
):
    import pytest

    from yleum_gateway.core.errors import BillingReconciliationRequiredError

    u, p, m, r = await _run(pool, database_url)
    other, op, om = await _owner(pool)
    with pytest.raises(BillingReconciliationRequiredError):
        await billing.charge(
            user_id=other,
            project_id=op,
            message_id=m[0],
            model_id="qa",
            tokens_in=1,
            tokens_out=1,
            cost_rub=Decimal("4"),
            description="wrong message owner",
        )
    with pytest.raises(BillingReconciliationRequiredError):
        await billing.charge(
            user_id=u,
            project_id=p,
            message_id=m[0],
            run_id=r,
            stage="runtime_ai",
            model_id="qa",
            tokens_in=1,
            tokens_out=1,
            cost_rub=Decimal("4"),
            description="runtime spoof",
        )
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", other) == 100
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", r) == 0


async def test_legacy_null_run_only_unique_owned_assistant_failed_debit_refunded(
    pool, database_url
):
    for mode in ["owned", "ambiguous", "foreign", "runtime", "successful", "free"]:
        u, p, m = await _owner(pool)
        r = uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'legacy','running','build')",
                r,
                p,
                u,
                m[0],
                str(r),
            )
            if mode == "ambiguous":
                second = uuid4()
                await conn.execute(
                    "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'legacy','failed','build')",
                    second,
                    p,
                    u,
                    m[0],
                    str(second),
                )
        expense_user, expense_project, expense_messages = u, p, m
        if mode == "foreign":
            expense_user, expense_project, expense_messages = await _owner(pool)
            expense_messages[0] = m[0]
        if mode != "free":
            await _historical_debit(
                pool,
                expense_user,
                expense_project,
                expense_messages,
                None,
                "legacy-null-" + str(r),
                "4",
            )
        async with pool.acquire() as conn:
            if mode == "runtime":
                await conn.execute("UPDATE usage SET stage='runtime_ai' WHERE user_id=$1", u)
            await conn.execute(
                "UPDATE generation_runs SET status=$2,finished_at=now() WHERE id=$1",
                r,
                "completed" if mode in {"successful", "free"} else "failed",
            )
        await _reconcile(database_url)
        await _reconcile(database_url)
        async with pool.acquire() as conn:
            expected = 100 if mode in {"owned", "free"} else 96
            assert (
                await conn.fetchval(
                    "SELECT balance_rub FROM wallets WHERE user_id=$1", expense_user
                )
                == expected
            )
            assert await conn.fetchval(
                "SELECT count(*) FROM wallet_charges WHERE user_id=$1 AND entry_type='refund'",
                expense_user,
            ) == (1 if mode == "owned" else 0)
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM generation_billing_intents WHERE run_id=$1", r
                )
                == 0
            )


async def test_api_llm_client_paid_run_and_platform_preliminary_attribution(
    pool, database_url, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI
    from yleum_api.services import llm_client

    from yleum_gateway.routers import chat
    from yleum_gateway.services import streaming

    u, p, m, r = await _run(pool, database_url)
    model = "gemini-3.1-pro-preview-customtools"
    seen = []

    async def provider(**kwargs):
        seen.append(kwargs.get("user"))
        return {
            "id": "client-" + str(len(seen)) + "-" + str(r),
            "model": model,
            "choices": [{"message": {"content": "synthetic"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost_rub": "2"},
        }

    async def provider_stream(*args, **kwargs):
        kwargs["receipt"].update(
            {
                "id": "client-stream-" + str(r),
                "model": model,
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost_rub": "3"},
                "complete": True,
            }
        )
        yield "synthetic", model

    monkeypatch.setattr(chat.router_module, "acompletion", provider)
    monkeypatch.setattr(streaming.llmgw, "astream", provider_stream)
    monkeypatch.setattr(chat.cache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(chat.cache, "set", AsyncMock())
    monkeypatch.setattr(chat.file_logger, "log_request", lambda *args: None)
    app = FastAPI()
    app.include_router(chat.router)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.ASGITransport(app=app), **kwargs),
    )
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="http://qa-no-network"),
    )
    llm_client.set_generation_billing_context(r, u, p)
    assert (
        await llm_client.complete_chat(
            [{"role": "user", "content": "synthetic"}],
            model,
            user_id=str(u),
            project_id=str(p),
            free=True,
        )
        == "synthetic"
    )
    assert (
        await llm_client.complete_chat(
            [{"role": "user", "content": "synthetic"}], model, project_id=str(p)
        )
        == "synthetic"
    )
    events = [
        event
        async for event in llm_client.stream_chat_completion(
            [{"role": "user", "content": "synthetic"}], model, str(u), str(p), str(m[0])
        )
    ]
    assert any("usage" in event for event in events)
    assert seen == [str(u), None]
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM usage WHERE run_id=$1", r) == 2
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100


async def test_free_waived_intent_without_wallet_and_policy_cannot_be_rewritten(pool, database_url):
    import pytest
    from yleum_api.services.generation_billing import admit_policy

    u, p, m, r = await _run(pool, database_url, free=True)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM wallets WHERE user_id=$1", u)
        await conn.execute("DELETE FROM billing_accounts WHERE personal_user_id=$1", u)
    await _provider(u, p, m, r, "free-no-wallet-" + str(r), "5", free=False)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine) as session:
            with pytest.raises(ValueError, match="immutable"):
                await admit_policy(session, r, is_free=False)
            await session.rollback()
    finally:
        await engine.dispose()
    assert await _finish(database_url, r, failed=True) == "failed"
    async with pool.acquire() as conn:
        intent = await conn.fetchrow(
            "SELECT outcome,amount_rub,billing_account_id FROM generation_billing_intents WHERE run_id=$1",
            r,
        )
        assert (
            intent["outcome"] == "waived"
            and intent["amount_rub"] == 0
            and intent["billing_account_id"] is None
        )


async def _actual_tick(database_url):
    from yleum_api.services.billing_cycle import run_billing_tick

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await run_billing_tick(session)
    finally:
        await engine.dispose()


async def test_actual_quiet_billing_tick_commits_debit_refund_and_outbox(pool, database_url):
    data = await _run(pool, database_url)
    u, p, m, r = data
    await _provider(u, p, m, r, "quiet-" + str(r), "2")
    await _prepare_accept(database_url, data)
    assert await _finish(database_url, r) == "completed"
    old, op, om = await _owner(pool)
    oldrun = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO generation_runs(id,project_id,user_id,assistant_message_id,idempotency_key,prompt_hash,status,response_mode) VALUES($1,$2,$3,$4,$5,'legacy','running','build')",
            oldrun,
            op,
            old,
            om[0],
            str(oldrun),
        )
    await _historical_debit(pool, old, op, om, oldrun, "quiet-old-" + str(oldrun), "4")
    assert await _finish(database_url, oldrun, failed=True) == "failed"
    assert await _actual_tick(database_url) == (0, 0)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 98
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", old) == 100
        assert (
            await conn.fetchval(
                "SELECT settled_at FROM generation_billing_outbox WHERE run_id=$1", r
            )
            is not None
        )
    assert await _actual_tick(database_url) == (0, 0)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wallet_charges WHERE user_id=$1 AND entry_type='refund'", old
            )
            == 1
        )


async def test_actual_tick_downstream_failure_cannot_rollback_generation_payment(
    pool, database_url, monkeypatch
):
    import pytest
    from yleum_api.services import billing_cycle

    u, p, m, r = await _run(pool, database_url)
    await _provider(u, p, m, r, "downstream-" + str(r), "3")
    await _prepare_accept(database_url, (u, p, m, r))
    assert await _finish(database_url, r) == "completed"

    async def fail_downstream(*args, **kwargs):
        raise RuntimeError("synthetic downstream provider failure")

    original = billing_cycle.process_subscription_cycle
    monkeypatch.setattr(billing_cycle, "process_subscription_cycle", fail_downstream)
    with pytest.raises(RuntimeError, match="synthetic downstream"):
        await _actual_tick(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 97
        assert (
            await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r) == "completed"
        )
    monkeypatch.setattr(billing_cycle, "process_subscription_cycle", original)
    assert await _actual_tick(database_url) == (0, 0)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 1


async def test_real_status_setter_cannot_overwrite_locked_completed_intent(
    pool, database_url, monkeypatch
):
    import asyncio

    from yleum_api.core import db
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_runs import (
        set_generation_run_status,
        terminalize_generation_run_locked,
    )

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    try:
        for wanted in ["failed", "cancelled"]:
            u, p, m, r = await _run(pool, database_url)
            await _provider(u, p, m, r, "setter-race-" + str(r), "2")
            await _prepare_accept(database_url, (u, p, m, r))
            async with AsyncSession(engine, expire_on_commit=False) as session:
                row = await session.get(GenerationRun, r, with_for_update=True)
                await terminalize_generation_run_locked(session, row, status="completed")
                setter = asyncio.create_task(set_generation_run_status(r, wanted))
                await asyncio.sleep(0.05)
                assert not setter.done()
                await session.commit()
                await setter
            async with pool.acquire() as conn:
                assert (
                    await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r)
                    == "completed"
                )
                assert (
                    await conn.fetchval(
                        "SELECT outcome FROM generation_billing_intents WHERE run_id=$1", r
                    )
                    == "billable"
                )
    finally:
        await engine.dispose()


async def test_status_setter_does_not_erase_durable_manual_cancel(pool, database_url, monkeypatch):
    from yleum_api.core import db
    from yleum_api.services.generation_runs import set_generation_run_status

    u, p, m, r = await _run(pool, database_url)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE generation_runs SET status='cancel_requested' WHERE id=$1", r)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    try:
        await set_generation_run_status(r, "running")
    finally:
        await engine.dispose()
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r)
            == "cancel_requested"
        )


async def test_contradictory_billable_intent_stops_without_debit_or_waiver(pool, database_url):
    import pytest

    u, p, m, r = await _run(pool, database_url)
    await _provider(u, p, m, r, "contradiction-" + str(r), "2")
    await _prepare_accept(database_url, (u, p, m, r))
    assert await _finish(database_url, r) == "completed"
    async with pool.acquire() as conn:
        await conn.execute("UPDATE generation_runs SET status='failed' WHERE id=$1", r)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await _reconcile(database_url)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100
        assert (
            await conn.fetchval("SELECT outcome FROM generation_billing_intents WHERE run_id=$1", r)
            == "billable"
        )
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", u) == 0

    # Restore only this intentionally corrupted synthetic row, so the session
    # fixture's subsequent real-worker tests do not inherit its blocked intent.
    async with pool.acquire() as conn:
        await conn.execute("UPDATE generation_runs SET status='completed' WHERE id=$1", r)


async def test_real_manual_cancel_cannot_replace_locked_accepted_completion(
    pool, database_url, monkeypatch
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest
    from yleum_api.core.errors import ApiError
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.routers import messages
    from yleum_api.services.generation_runs import terminalize_generation_run_locked

    u, p, m, r = await _run(pool, database_url)
    await _provider(u, p, m, r, "manual-race-" + str(r), "2")
    await _prepare_accept(database_url, (u, p, m, r))
    signal = AsyncMock()
    event = AsyncMock()
    monkeypatch.setattr(messages, "request_generation_cancel", signal)
    monkeypatch.setattr(messages, "publish_event", event)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))

    async def cancel():
        async with AsyncSession(engine) as other:
            return await messages.cancel_active_generation(p, other, SimpleNamespace(id=u))

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(GenerationRun, r, with_for_update=True)
            await terminalize_generation_run_locked(session, row, status="completed")
            pending = asyncio.create_task(cancel())
            await asyncio.sleep(0.05)
            assert not pending.done()
            await session.commit()
            with pytest.raises(ApiError, match="no active generation"):
                await pending
    finally:
        await engine.dispose()
    signal.assert_not_awaited()
    event.assert_not_awaited()
    await _actual_tick(database_url)
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r) == "completed"
        )
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 98


async def test_actual_tick_refuses_foreign_transaction_without_committing_it(pool, database_url):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import InvalidRequestError
    from yleum_api.services.billing_cycle import run_billing_tick

    u, p, m = await _owner(pool)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine) as session:
            await session.execute(
                text("UPDATE wallets SET balance_rub=91 WHERE user_id=:user"), {"user": u}
            )
            with pytest.raises(InvalidRequestError, match="already begun"):
                await run_billing_tick(session)
    finally:
        await engine.dispose()
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", u) == 100


@pytest.mark.parametrize("free", [False, True])
async def test_completed_reconciler_refreshes_stale_source_and_preserves_sealed_acceptance(
    pool, database_url, free
):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.message import Message
    from yleum_api.services.generation_runs import reconcile_completed_build_runs

    u, p, m, r = await _run(pool, database_url, free=free)
    await _provider(u, p, m, r, "stale-reconcile-" + str(r), "2")
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as stale:
            oldrun = await stale.get(GenerationRun, r)
            oldmessage = await stale.get(Message, m[0])
            assert oldrun.status == "running" and oldmessage.snapshot_id is None
            await _prepare_accept(database_url, (u, p, m, r))
            assert await _finish(database_url, r) == "completed"
            await reconcile_completed_build_runs(stale)
            assert oldrun.status == "completed"
            if free:
                assert oldmessage.snapshot_id is not None
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r)
                == "completed"
            )
            assert await conn.fetchval(
                "SELECT outcome FROM generation_billing_intents WHERE run_id=$1", r
            ) == ("waived" if free else "billable")
        if not free:
            # Historical chat/snapshot deletion is not a failed original build.
            async with pool.acquire() as conn:
                await conn.execute("UPDATE messages SET snapshot_id=NULL WHERE id=$1", m[0])
            async with AsyncSession(engine) as fresh:
                await reconcile_completed_build_runs(fresh)
            async with pool.acquire() as conn:
                assert (
                    await conn.fetchval("SELECT status FROM generation_runs WHERE id=$1", r)
                    == "completed"
                )
                # Restore only this deliberate synthetic deletion.
                await conn.execute(
                    "UPDATE messages SET snapshot_id=(SELECT accepted_snapshot_id FROM generation_billing_intents WHERE run_id=$2) WHERE id=$1",
                    m[0],
                    r,
                )
    finally:
        await engine.dispose()
