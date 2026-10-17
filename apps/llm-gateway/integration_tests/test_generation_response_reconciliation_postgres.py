"""Cold financial linkage and reconciliation using owned PostgreSQL receipts."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
import test_billing_settlement_postgres as fixture_source
from test_generation_attempts_postgres import intent

database_url = fixture_source.database_url
pool = fixture_source.pool


@pytest.fixture
async def private_storage(pool, database_url, monkeypatch):
    # Reuse the exact-image, positively owned Task8 SDK/IAM fixture. The physical
    # runner supplies the endpoint; the normal PG suite does not run this test.
    from test_generation_response_storage_postgres import private_storage as fixture

    generator = fixture.__wrapped__(pool, database_url, monkeypatch)
    async for value in generator:
        yield value


async def finished(pool, monkeypatch, *, provider_request_id=None, ambiguous=False, before=None,
                   provenance=None, free=False):
    from yleum_gateway.services import generation_attempts as attempts
    from yleum_gateway.services.billing import ProviderUsageBinding

    monkeypatch.setattr(attempts, 'get_pool', lambda: pool)
    identity = await intent(pool)
    if before is not None:
        await before(identity)
    call = await attempts.dispatch(
        identity, route='/v1/messages', model='claude-sonnet-5.5',
        user_id=identity.user_id, project_id=identity.project_id, run_id=identity.run_id,
        stage='native_agent', free=free,
    )
    await attempts.finish(
        identity, call, status='ambiguous' if ambiguous else 'completed',
        actual_model='claude-sonnet-5.5', provider_request_id=provider_request_id,
        tokens_in=2, tokens_out=3, cache_read_tokens=0, cache_write_tokens=0,
        calculated_cost_rub=Decimal(provenance['calculated_cost_rub']) if provenance else Decimal('1'),
        provider_cost_rub=Decimal(provenance['reported_cost_rub']) if provenance else None,
        provider_cost_usd=Decimal(provenance['provider_cost_usd']) if provenance else None,
        cost_provenance=provenance,
    )
    return identity, call, dict(
        user_id=identity.user_id, project_id=identity.project_id, run_id=identity.run_id,
        message_id=None, model_id='claude-sonnet-5.5', tokens_in=2, tokens_out=3,
        cost_rub=Decimal(provenance['effective_cost_rub']) if provenance else Decimal('1'),
        provider_cost_usd=Decimal(provenance['provider_cost_usd']) if provenance else None,
        cost_provenance=provenance,
        free=free,
        description='synthetic Task9 receipt',
        stage='native_agent', provider_request_id=provider_request_id,
        provider_binding=ProviderUsageBinding(identity, call),
    )


@pytest.mark.parametrize('prior_receipt', ['none', 'linked', 'upstream_legacy'])
async def test_original_precise_provenance_survives_sql_projections_and_cold_read(
    pool, monkeypatch, prior_receipt,
):
    from yleum_gateway.services import billing, pricing

    rub, usd = Decimal('1.234567'), Decimal('0.123456789')
    evidence = pricing.cost_provenance(
        'claude-sonnet-5.5', Decimal('1'),
        pricing.ReportedCost(rub, usd, 'header:x-llmgw-cost-rub', 'usage:cost_usd'),
        tokens_in=2, tokens_out=3,
    )
    identity, call, kwargs = await finished(
        pool, monkeypatch, provider_request_id=uuid4().hex if prior_receipt == 'upstream_legacy' else None,
        provenance=evidence,
    )
    async with pool.acquire() as conn:
        original_hash = await conn.fetchval('SELECT receipt_hash FROM provider_calls WHERE id=$1', call)
        assert await conn.fetchval('SELECT provider_cost_usd FROM provider_calls WHERE id=$1', call) == Decimal('0.12345679')
    if prior_receipt == 'upstream_legacy':
        await billing.charge(**{key: value for key, value in kwargs.items() if key != 'provider_binding'})
    elif prior_receipt == 'linked':
        await billing.charge(**kwargs)
    receipt = await billing.charge(**kwargs)
    assert await billing.charge(**kwargs) == receipt
    async with pool.acquire() as conn:
        attempt = await conn.fetchrow('SELECT * FROM generation_provider_attempts WHERE attempt_id=$1', identity.attempt_id)
        assert await billing.confirmed_provider_accounting(conn, attempt)
        assert await conn.fetchval('SELECT receipt_hash FROM provider_calls WHERE id=$1', call) == original_hash
        usage = await conn.fetchrow('SELECT u.* FROM usage u JOIN provider_calls c ON c.usage_id=u.id WHERE c.id=$1', call)
        assert usage['cost_rub'] == Decimal('1.2346')
        assert usage['provider_cost_usd'] == Decimal('0.12345679')
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 1


@pytest.mark.parametrize('provider_request_id', [None, 'synthetic-receipt'])
async def test_same_call_links_exact_usage_and_never_recharges_even_without_upstream_id(
    pool, monkeypatch, provider_request_id,
):
    from yleum_gateway.services import billing

    identity, call, kwargs = await finished(pool, monkeypatch, provider_request_id=(
        provider_request_id + uuid4().hex if provider_request_id else None
    ))
    results = await asyncio.gather(*(billing.charge(**kwargs) for _ in range(3)))
    assert len(set(results)) == 1
    async with pool.acquire() as conn:
        receipt = await conn.fetchrow(
            'SELECT c.usage_id,s.id,s.status FROM provider_calls c '
            'JOIN usage_settlements s ON s.usage_id=c.usage_id WHERE c.id=$1', call,
        )
        assert receipt['status'] == 'deferred'
        assert results[0] == receipt['id'] and results[0] != receipt['usage_id']
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 1
        assert await conn.fetchval('SELECT count(*) FROM wallet_charges WHERE user_id=$1', identity.user_id) == 0


async def test_existing_upstream_settlement_early_return_links_in_same_transaction(pool, monkeypatch):
    from yleum_gateway.services import billing

    identity, call, kwargs = await finished(pool, monkeypatch, provider_request_id=uuid4().hex)
    legacy = {key: value for key, value in kwargs.items() if key != 'provider_binding'}
    first = await billing.charge(**legacy)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT usage_id FROM provider_calls WHERE id=$1', call) is None
    assert await billing.charge(**kwargs) == first
    assert await billing.charge(**kwargs) == first
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT usage_id FROM provider_calls WHERE id=$1', call) is not None
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 1


async def test_link_failure_rolls_back_usage_and_settlement(pool, monkeypatch):
    from yleum_gateway.services import billing

    identity, call, kwargs = await finished(pool, monkeypatch)

    async def fail(*args):
        raise RuntimeError('synthetic link write failure')

    monkeypatch.setattr(billing, '_link_usage', fail)
    with pytest.raises(RuntimeError, match='link write failure'):
        await billing.charge(**kwargs)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 0
        assert await conn.fetchval('SELECT usage_id FROM provider_calls WHERE id=$1', call) is None


async def test_unknown_commit_reloads_link_without_second_usage(pool, monkeypatch):
    from yleum_gateway.services import billing

    identity, call, kwargs = await finished(pool, monkeypatch)

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, key):
            return getattr(self.conn, key)

        @asynccontextmanager
        async def transaction(self):
            async with self.conn.transaction():
                yield
            raise RuntimeError('synthetic commit acknowledgement lost')

    class UnknownCommitPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as conn:
                yield Connection(conn)

    monkeypatch.setattr(billing, 'get_pool', lambda: UnknownCommitPool())
    with pytest.raises(RuntimeError, match='acknowledgement lost'):
        await billing.charge(**kwargs)
    monkeypatch.setattr(billing, 'get_pool', lambda: pool)
    await billing.charge(**kwargs)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 1
        assert await conn.fetchval('SELECT usage_id FROM provider_calls WHERE id=$1', call) is not None


@pytest.mark.parametrize('boundary', ['cancel_requested', 'completed', 'ambiguous'])
async def test_late_or_ambiguous_expense_is_linked_without_granting_execution(pool, monkeypatch, boundary):
    from yleum_gateway.services import billing

    identity, call, kwargs = await finished(pool, monkeypatch, ambiguous=boundary == 'ambiguous')
    if boundary != 'ambiguous':
        async with pool.acquire() as conn:
            await conn.execute('UPDATE generation_runs SET status=$2 WHERE id=$1', identity.run_id, boundary)
    await billing.charge(**kwargs)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT usage_id FROM provider_calls WHERE id=$1', call) is not None
        assert await conn.fetchval('SELECT status FROM generation_runs WHERE id=$1', identity.run_id) == (
            'running' if boundary == 'ambiguous' else boundary
        )
        if boundary == 'ambiguous':
            assert await conn.fetchval('SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1', identity.attempt_id) == 'indeterminate'


@pytest.mark.parametrize('format_', ['json', 'sse'])
@pytest.mark.parametrize('failure', [None, 'missing_link', 'unpaid', 'usage_tampered', 'body_tampered', 'wrong_purpose', 'expired', 'cancelled', 'ambiguous'])
async def test_cold_authenticated_reconcile_reads_paid_body_without_new_usage_or_dispatch(pool, monkeypatch, format_, failure):
    import json
    from datetime import UTC, datetime

    import httpx
    from fastapi import FastAPI
    from test_generation_response_retention_postgres import Objects

    from yleum_gateway.core import db
    from yleum_gateway.core import generation_response_auth as auth
    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.routers import generation_responses as route
    from yleum_gateway.services import billing, generation_response_objects
    from yleum_gateway.services import generation_response_ledger as ledger
    from yleum_gateway.services.generation_response_store import ChunkWriter

    monkeypatch.setattr(db, 'get_pool', lambda: pool)
    monkeypatch.setattr(ledger, 'get_pool', lambda: pool)
    monkeypatch.setenv('GENERATION_RESPONSE_RETENTION_ENABLED', 'true')
    secret = 'synthetic-task9-response-signing-secret'
    monkeypatch.setenv('GENERATION_RESPONSE_SIGNING_SECRET', secret)
    reset_settings_cache()

    writers = []

    async def admit(identity):
        writers.append(await ledger.admit_response(identity, ledger.CapacitySnapshot(
            'cold-' + uuid4().hex, 10_000_000, 10_000_000, uuid4(), uuid4(),
            int(datetime.now(UTC).timestamp()) + 15,
        )))

    identity, call, kwargs = await finished(pool, monkeypatch, before=admit, ambiguous=failure == 'ambiguous')
    await billing.charge(**kwargs)
    objects = Objects()
    monkeypatch.setattr(generation_response_objects, 'PrivateObjects', lambda: objects)
    tool = {'id': 'tool-exact', 'name': 'write_file', 'input': {'path': 'src/a.ts', 'content': 'kept'}}
    first = {'version': 1, 'identity': identity.to_wire(), 'format': format_}
    if format_ == 'json':
        first.update(status=200, body={'content': [tool], 'stop_reason': 'tool_use', 'usage': {'input_tokens': 2, 'output_tokens': 3}})
        wire = json.dumps(first).encode()
    else:
        event = json.dumps({'content': [tool], 'finish_reason': 'tool_calls', 'usage': {'prompt_tokens': 2, 'completion_tokens': 3}})
        wire = (json.dumps(first) + '\n' + json.dumps(event) + '\n' + json.dumps('[DONE]') + '\n').encode()

    async def body():
        yield wire

    await ChunkWriter(identity.attempt_id, objects, writers[0], chunk_bytes=32).store(
        body(), finish_reason='tool_use', route='/v1/messages',
    )
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT reconciliation_hold FROM generation_responses WHERE attempt_id=$1', identity.attempt_id)
        if failure == 'missing_link':
            await conn.execute('UPDATE provider_calls SET usage_id=NULL WHERE id=$1', call)
        elif failure == 'unpaid':
            await conn.execute("UPDATE usage_settlements SET status='unpaid' WHERE usage_id=(SELECT usage_id FROM provider_calls WHERE id=$1)", call)
        elif failure == 'usage_tampered':
            await conn.execute('UPDATE usage SET tokens_out=tokens_out+1 WHERE id=(SELECT usage_id FROM provider_calls WHERE id=$1)', call)
        elif failure == 'cancelled':
            await conn.execute("UPDATE generation_runs SET status='cancel_requested' WHERE id=$1", identity.run_id)
    if failure == 'body_tampered':
        key = next(key for key in objects.data if '/chunks/' in key)
        objects.data[key] += b'synthetic-corruption'
    # New request has no capture object, accounting marker or writer token.
    # Construct the future exact purpose independently to prove the actual route.
    now = int(datetime.now(UTC).timestamp())
    if failure == 'expired':
        now -= 61
    purpose = 'generation_response.read' if failure == 'wrong_purpose' else 'generation_response.reconcile'
    token = auth._signed({'aud': 'llm-gateway', 'purpose': purpose,
                          'identity': identity.to_wire(), 'iat': now, 'exp': now + 60}, secret, auth._DOMAIN)
    app = FastAPI()
    app.include_router(route.router)
    path = '/_internal/generation-responses/' + str(identity.attempt_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://fixture') as client:
        denied = await client.get(path, headers={auth.HEADER: auth.mint_generation_response_token(identity, purpose='generation_response.read')})
        assert denied.status_code == 409
        reconciled = await client.post(path + '/reconcile', headers={auth.HEADER: token})
        expected = 200 if failure is None else 403 if failure in {'expired', 'wrong_purpose'} else 409
        assert reconciled.status_code == expected, reconciled.text
        assert (await client.post(path + '/reconcile', headers={auth.HEADER: token})).status_code == expected
        read = await client.get(path, headers={auth.HEADER: auth.mint_generation_response_token(identity, purpose='generation_response.read')})
        if failure is None:
            assert read.status_code == 200 and read.json()['state'] == 'stored'
            response = read.json()['response']
            recovered = response['body'] if format_ == 'json' else json.loads(response['events'][0])
            assert recovered['content'] == [tool]
        else:
            assert read.status_code == 409
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT reconciliation_hold FROM generation_responses WHERE attempt_id=$1', identity.attempt_id) is (failure is not None)
        assert await conn.fetchval('SELECT count(*) FROM provider_calls WHERE run_id=$1', identity.run_id) == 1
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 1
        assert await conn.fetchval('SELECT count(*) FROM wallet_charges WHERE user_id=$1', identity.user_id) == 0
    if failure is not None:
        from yleum_gateway.services import generation_response_cleanup as cleanup

        monkeypatch.setattr(cleanup, 'get_pool', lambda: pool)
        monkeypatch.setenv('GENERATION_RESPONSE_CLEANUP_ENABLED', 'true')
        reset_settings_cache()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE generation_runs SET status='completed',finished_at=now()-interval '8 days' WHERE id=$1", identity.run_id)
            await conn.execute("UPDATE generation_responses SET reconciled_at=now()-interval '8 days' WHERE attempt_id=$1", identity.attempt_id)
        assert await cleanup.delete_expired_generation_responses(datetime.now(UTC), objects=objects) == 0
        assert objects.data
    reset_settings_cache()


@pytest.mark.parametrize('format_', ['chat', 'native', 'sse'])
async def test_physical_cold_reconcile_exact_body_without_capture_authority(
    pool, monkeypatch, private_storage, format_,
):
    import json

    import httpx

    from yleum_gateway.core.generation_response_auth import HEADER, mint_generation_response_token
    from yleum_gateway.services import billing
    from yleum_gateway.services.generation_response_capture import preflight_response_store
    from yleum_gateway.services.generation_response_store import ChunkWriter, canonical

    captured = []

    async def admit(identity):
        captured.append(await preflight_response_store(identity))

    identity, call, kwargs = await finished(pool, monkeypatch, before=admit)
    await billing.charge(**kwargs)
    objects, ledger = captured.pop()
    native = {'id': 'msg-exact', 'content': [
        {'type': 'text', 'text': 'kept'},
        {'type': 'tool_use', 'id': 'tool-exact', 'name': 'write_file',
         'input': {'path': 'src/a.ts', 'content': '\u03b1\nfull arguments'}},
    ], 'stop_reason': 'tool_use', 'usage': {'input_tokens': 2, 'output_tokens': 3}}
    chat = {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'content': 'kept', 'tool_calls': [{
            'id': 'tool-exact', 'type': 'function', 'function': {
                'name': 'write_file', 'arguments': json.dumps(native['content'][1]['input']),
            },
        }],
    }}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 3}}
    envelope = {'version': 1, 'identity': identity.to_wire(),
                'format': 'sse' if format_ == 'sse' else 'json'}
    payload = native if format_ == 'native' else chat
    if format_ == 'sse':
        events = [json.dumps(payload), '[DONE]']
        wire = canonical(envelope) + b'\n' + b''.join(canonical(event) + b'\n' for event in events)
    else:
        wire = canonical({**envelope, 'status': 200, 'body': payload})

    async def chunks():
        for offset in range(0, len(wire), 7):
            yield wire[offset:offset + 7]

    await ChunkWriter(identity.attempt_id, objects, ledger, chunk_bytes=64).store(
        chunks(), finish_reason='tool_use', route='/v1/messages',
    )
    # Simulate gateway death after durable body completion and committed billing,
    # before the process-local accounting marker can release the hold.
    del objects, ledger
    path = 'http://gateway.test/_internal/generation-responses/' + str(identity.attempt_id)
    read_headers = {HEADER: mint_generation_response_token(identity, purpose='generation_response.read')}
    reconcile_headers = {HEADER: mint_generation_response_token(identity, purpose='generation_response.reconcile')}
    async with httpx.AsyncClient() as client:
        assert (await client.get(path, headers=read_headers)).status_code == 409
        for _ in range(2):
            result = await client.post(path + '/reconcile', headers=reconcile_headers)
            assert result.status_code == 200, result.text
        result = await client.get(path, headers=read_headers)
        assert result.status_code == 200, result.text
        retained = result.json()['response']
        assert (retained['events'] if format_ == 'sse' else retained['body']) == (
            events if format_ == 'sse' else payload
        )
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM provider_calls WHERE run_id=$1', identity.run_id) == 1
        assert await conn.fetchval('SELECT count(*) FROM usage WHERE run_id=$1', identity.run_id) == 1
        assert await conn.fetchval('SELECT count(*) FROM wallet_charges WHERE user_id=$1', identity.user_id) == 0
        assert await conn.fetchval('SELECT usage_id FROM provider_calls WHERE id=$1', call) is not None


@pytest.mark.parametrize("outcome", ["historical_settled", "historical_free", "preconnect", "rate_limit_refused", "started"])
async def test_cold_reader_financial_outcome_matrix(pool, monkeypatch, outcome):
    from datetime import UTC, datetime

    from test_generation_response_retention_postgres import Objects

    from yleum_gateway.core import db
    from yleum_gateway.services import billing
    from yleum_gateway.services import generation_attempts as attempts
    from yleum_gateway.services import generation_response_ledger as ledger
    from yleum_gateway.services.generation_response_store import (
        ChunkWriter,
        ResponseIndeterminate,
        canonical,
        load_generation_response,
        reconcile_generation_response,
    )

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(ledger, "get_pool", lambda: pool)
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    writers = []

    async def admit(identity):
        writers.append(await ledger.admit_response(identity, ledger.CapacitySnapshot(
            "matrix-" + uuid4().hex, 10_000_000, 10_000_000, uuid4(), uuid4(),
            int(datetime.now(UTC).timestamp()) + 15,
        )))

    historical = outcome.startswith("historical_")
    if historical:
        identity, call, kwargs = await finished(
            pool, monkeypatch, before=admit, free=outcome == "historical_free",
        )
        await billing.charge(**kwargs)
        # Compatibility fixture for an immutable historical receipt chain.
        # The CURRENT generation writer always produces deferred, never these
        # settled/free states. Do not change policy just to reach reader branches.
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT s.*,u.cost_rub FROM usage_settlements s JOIN usage u ON u.id=s.usage_id "
                "JOIN provider_calls c ON c.usage_id=u.id WHERE c.id=$1", call,
            )
            if outcome == "historical_settled":
                charge_id = uuid4()
                balance = await conn.fetchval(
                    "UPDATE wallets SET balance_rub=balance_rub-$2 WHERE billing_account_id=$1 RETURNING balance_rub",
                    row["billing_account_id"], row["cost_rub"],
                )
                await conn.execute(
                    "INSERT INTO wallet_charges(id,billing_account_id,user_id,entry_type,amount_rub,balance_after_rub,external_ref,description) "
                    "VALUES($1,$2,$3,'usage',$4,$5,$6,'synthetic historical reader fixture')",
                    charge_id, row["billing_account_id"], identity.user_id, -row["cost_rub"], balance,
                    "usage:" + str(row["usage_id"]),
                )
                await conn.execute("UPDATE usage_settlements SET status='settled',wallet_charge_id=$2 WHERE id=$1", row["id"], charge_id)
            else:
                await conn.execute("UPDATE usage_settlements SET status='free' WHERE id=$1", row["id"])
    else:
        identity = await intent(pool)
        await admit(identity)
        call = await attempts.dispatch(
            identity, route="/v1/messages", model="claude-sonnet-5.5",
            user_id=identity.user_id, project_id=identity.project_id, run_id=identity.run_id,
        )
        if outcome != "started":
            await attempts.finish(identity, call, status="failed", reason=outcome, error_type="ConnectError" if outcome == "preconnect" else "HTTPStatusError")

    objects = Objects()
    envelope = canonical({"version": 1, "identity": identity.to_wire(), "format": "json",
                          "status": 200 if historical else 502, "body": {"synthetic": outcome}})

    async def body():
        yield envelope

    await ChunkWriter(identity.attempt_id, objects, writers[0]).store(body(), finish_reason="complete_json", route="/v1/messages")
    async with pool.acquire() as conn:
        before = tuple([await conn.fetchval("SELECT count(*) FROM " + table + " WHERE user_id=$1", identity.user_id)
                        for table in ("provider_calls", "usage", "wallet_charges")])
    if outcome == "started":
        with pytest.raises(ResponseIndeterminate):
            await reconcile_generation_response(identity, objects=objects)
    else:
        assert await reconcile_generation_response(identity, objects=objects)
        assert await reconcile_generation_response(identity, objects=objects)
        assert await load_generation_response(identity, objects=objects) == envelope
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT reconciliation_hold FROM generation_responses WHERE attempt_id=$1", identity.attempt_id) is (outcome == "started")
        after = tuple([await conn.fetchval("SELECT count(*) FROM " + table + " WHERE user_id=$1", identity.user_id)
                       for table in ("provider_calls", "usage", "wallet_charges")])
        assert after == before
