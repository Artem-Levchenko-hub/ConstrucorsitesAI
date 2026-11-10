"""Real SQL admission/receipt authority for one paused, non-V3 provider step.

Only HTTP and the passing of wait time are synthetic. The normal accepted-unit
hold, deterministic identities, parent receipts and child authorization are real
transactions against the repository's disposable PostgreSQL test fixture.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4, uuid5

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.models.generation_billing import GenerationBillingPolicy
from yleum_api.models.generation_credits import GenerationCreditReservation
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services import generation_credits, llm_client
from yleum_api.services.generation import provider_attempts
from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity,
    request_digest,
)

from .test_generation_credit_units import owner, run_for

URL = "https://gateway.test/v1/chat/completions"
ROUTE = "/v1/chat/completions"
MODEL = "claude-sonnet-5.5"


def refusal(*, transient=True):
    details = {
        "upstream_http_status": 402,
        "provider_scope": "llmgw",
        "provider_refusal_category": "rate_limit" if transient else "funds",
        "provider_charge_ambiguous": False,
        "retry_safe": transient,
    }
    if transient:
        details.update(retry_policy="llmgw_inflight_budget_v1", retry_after_seconds=120)
    return httpx.Response(502, json={"detail": {"error": {
        "code": "model_unavailable", "details": details,
    }}})


async def admitted_payload(db_session, test_engine, monkeypatch):
    user, _account = await owner(db_session)
    run, project = await run_for(db_session, user)
    await generation_credits.reserve_generation_units(db_session, run)
    db_session.add(GenerationBillingPolicy(
        run_id=run.id, is_free=True, version="accepted-units-v2",
    ))
    await db_session.commit()
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: test_engine)
    monkeypatch.setattr(provider_attempts, "get_settings", lambda: SimpleNamespace(
        generation_provider_attempts_enabled=True,
        generation_response_retention_enabled=False,
    ))
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Continue the saved synthetic draft."}],
        "stream": False,
        "temperature": 0.0,
        "metadata": {
            "run_id": str(run.id), "project_id": str(project.id), "user_id": str(user.id),
        },
    }
    return run, payload, async_sessionmaker(test_engine, expire_on_commit=False)


async def journal_refusal(factory, identity, *, status="failed", committed=True):
    """Stand in for Gateway settlement, not for API child authorization."""
    async with factory() as session:
        attempt = await session.get(GenerationProviderAttempt, identity.attempt_id)
        assert attempt is not None and attempt.phase == "reserved"
        assert attempt.provider_call_id is None  # A closed attempt is never sent twice.
        call = ProviderCall(
            id=uuid4(), provider_scope="llmgw", route=ROUTE, requested_model=MODEL,
            expense_owner="user", user_id=identity.user_id, project_id=identity.project_id,
            run_id=identity.run_id, stage="agent", free=True, status=status,
            error_type="HTTPStatusError", receipt_hash="f" * 64,
            finished_at=datetime.now(UTC),
        )
        session.add(call)
        await session.flush()
        attempt.provider_call_id = call.id
        attempt.phase = "closed"
        attempt.terminal_reason = "rate_limit_refused"
        if committed:
            await session.commit()
        else:
            await session.rollback()  # An HTTP assertion cannot replace durable evidence.


def install_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: real_client(
        transport=httpx.MockTransport(handler), **options,
    ))


async def attempts_for(factory, run_id):
    async with factory() as session:
        return list((await session.scalars(select(GenerationProviderAttempt).where(
            GenerationProviderAttempt.run_id == run_id,
        ))).all())


@pytest.mark.asyncio
async def test_two_inflight_refusals_use_real_committed_children_without_restarting_run(
    db_session, test_engine, monkeypatch,
):
    run, payload, factory = await admitted_payload(db_session, test_engine, monkeypatch)
    digest = request_digest(ROUTE, payload)
    sent, sleeps = [], []

    async def handler(request):
        assert str(request.url) == URL  # The client never dispatches a backup endpoint.
        body = json.loads(request.content)
        identity = GenerationProviderIdentity.from_wire(
            body["metadata"]["generation_provider_identity"],
        )
        assert identity.request_sha256 == request_digest(ROUTE, body) == digest
        assert body["model"] == MODEL and identity.run_id == run.id
        assert body["messages"] == payload["messages"]
        assert identity.attempt_id not in [item.attempt_id for item in sent]
        async with factory() as session:
            current = await session.get(GenerationRun, run.id)
            assert current.status == "running" and "recovery_v1" not in current.agent_state
        sent.append(identity)
        if len(sent) <= 2:
            await journal_refusal(factory, identity)
            return refusal()
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "saved-draft-complete"}, "finish_reason": "stop",
        }]})

    async def sleep(delay):
        sleeps.append(delay)
        rows = await attempts_for(factory, run.id)
        # The child does not exist until the full provider-requested wait passed.
        assert len(rows) == len(sleeps)
        assert all(row.phase == "closed" for row in rows)

    install_http(monkeypatch, handler)
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    result = await llm_client._chat_post(payload, URL, 600)

    assert result.json()["choices"][0]["message"]["content"] == "saved-draft-complete"
    assert sleeps == [120, 120]
    assert len(sent) == 3
    assert sent[0].attempt_id == uuid5(run.id, "unit-request-v2:" + ROUTE + ":" + digest)
    assert sent[1].attempt_id == uuid5(sent[0].attempt_id, "definitive-refusal-retry-v1")
    assert sent[2].attempt_id == uuid5(sent[1].attempt_id, "definitive-refusal-retry-v1")
    rows = {row.attempt_id: row for row in await attempts_for(factory, run.id)}
    assert rows[sent[0].attempt_id].parent_attempt_id is None
    assert rows[sent[1].attempt_id].parent_attempt_id == sent[0].attempt_id
    assert rows[sent[2].attempt_id].parent_attempt_id == sent[1].attempt_id
    async with factory() as session:
        calls = list((await session.scalars(select(ProviderCall).where(
            ProviderCall.run_id == run.id,
        ))).all())
        assert len(calls) == 2 and all(call.provider_scope == "llmgw" for call in calls)
        current = await session.get(GenerationRun, run.id)
        hold = await session.get(GenerationCreditReservation, run.id)
        assert current.status == "running" and hold.state == "held"
        assert "recovery_v1" not in current.agent_state


@pytest.mark.asyncio
@pytest.mark.parametrize("committed,status", [(False, "failed"), (True, "ambiguous")])
async def test_inflight_http_assertion_cannot_replace_confirmed_failed_parent(
    db_session, test_engine, monkeypatch, committed, status,
):
    run, payload, factory = await admitted_payload(db_session, test_engine, monkeypatch)
    sent, sleeps = [], []

    async def handler(request):
        sent.append(json.loads(request.content))
        identity = GenerationProviderIdentity.from_wire(
            sent[-1]["metadata"]["generation_provider_identity"],
        )
        await journal_refusal(factory, identity, status=status, committed=committed)
        return refusal()

    async def sleep(delay):
        sleeps.append(delay)

    install_http(monkeypatch, handler)
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
        await llm_client._chat_post(payload, URL, 600)
    assert len(sent) == 1 and sleeps == [120]
    rows = await attempts_for(factory, run.id)
    assert len(rows) == 1 and rows[0].parent_attempt_id is None


@pytest.mark.asyncio
async def test_ordinary_balance_402_does_not_wait_or_acquire_child(
    db_session, test_engine, monkeypatch,
):
    run, payload, factory = await admitted_payload(db_session, test_engine, monkeypatch)
    sent, sleeps = [], []

    async def handler(request):
        sent.append(json.loads(request.content))
        return refusal(transient=False)

    async def sleep(delay):
        sleeps.append(delay)

    install_http(monkeypatch, handler)
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    with pytest.raises(llm_client.LLMError) as stopped:
        await llm_client._chat_post(payload, URL, 600)
    assert stopped.value.upstream_http_status == 402
    assert stopped.value.retry_safe is False
    assert len(sent) == 1 and sleeps == []
    assert len(await attempts_for(factory, run.id)) == 1
