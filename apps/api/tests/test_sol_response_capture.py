"""Standalone admitted Sol coding capture; PostgreSQL cases use owned local QA."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_free_chat_allowance import no_providers, owner_projects
from yleum_api.core import config
from yleum_api.core.deps import get_current_user
from yleum_api.main import app
from yleum_api.models.billing import BillingPlan
from yleum_api.models.generation_billing import GenerationBillingPolicy
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.user import User
from yleum_api.services import agent_builder, generation_engines, llm_client
from yleum_api.services.generation import provider_attempts, recovery_reconciliation
from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity,
    request_digest,
)
from yleum_api.services.generation.response_auth import HEADER, verify_generation_response_token
from yleum_api.services.generation.source_intent import READONLY_SOURCE_TURN_KEY
from yleum_api.services.generation_runs import load_generation_dispatch

MODEL = "gpt-6.2-sol"
ROUTE = "/v1/chat/completions"
SECRET = "synthetic-sol-capture-signing-secret-" * 2
ACTION = '<omnia:action name="read_file">{"path":"src/app/page.tsx"}</omnia:action>'


def capture_settings(monkeypatch):
    settings = config.get_settings().model_copy(update={
        "mock_llm": False,
        "llm_gateway_url": "https://gateway.test",
        "generation_provider_attempts_enabled": True,
        "generation_response_retention_enabled": True,
        "generation_response_signing_secret": SecretStr(SECRET),
        "generation_recovery_execution_enabled": False,
        "generation_recovery_mode": "off",
    })
    for module in (config, provider_attempts, llm_client):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    return settings


def completion(reason="stop"):
    return {"model": MODEL, "choices": [{
        "finish_reason": reason, "message": {"content": ACTION},
    }]}


def retained(identity, body=None):
    return {"version": 1, "identity": identity.to_wire(), "state": "stored", "response": {
        "format": "json", "status": 200, "body": completion() if body is None else body,
    }}


def install_gateway(monkeypatch, env, *, observation=None, before_post=None):
    original = httpx.AsyncClient

    class LostBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"choices":'
            raise httpx.ReadTimeout("Synthetic lost body acknowledgement")

    async def gateway(request):
        env.requests.append(request)
        if request.method == "POST" and request.url.path == ROUTE:
            body = json.loads(request.content)
            identity = GenerationProviderIdentity.from_wire(
                body["metadata"]["generation_provider_identity"],
            )
            assert verify_generation_response_token(
                request.headers[HEADER], purpose="generation_response.record", secret=SECRET,
            ) == identity
            assert identity.request_sha256 == request_digest(ROUTE, body)
            env.identity = identity
            if before_post is not None:
                await before_post(identity, body)
            return httpx.Response(200, stream=LostBody())
        assert request.method == "GET", "Recovery must not create another coding/reconcile POST"
        assert request.url.path == f"/_internal/generation-responses/{env.identity.attempt_id}"
        assert verify_generation_response_token(
            request.headers[HEADER], purpose="generation_response.read", secret=SECRET,
        ) == env.identity
        return httpx.Response(200, json=(
            observation(env.identity) if observation is not None else retained(env.identity)
        ))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: original(
        transport=httpx.MockTransport(gateway), **options,
    ))


@pytest.fixture
def pure_capture(monkeypatch):
    capture_settings(monkeypatch)
    env = SimpleNamespace(identity=None, requests=[])
    template = GenerationProviderIdentity(
        uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "b" * 64,
    )

    async def prepare(body, route, **options):
        assert route == ROUTE and not options
        env.identity = replace(template, request_sha256=request_digest(route, body))
        return env.identity

    env.prepare = AsyncMock(side_effect=prepare)
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", env.prepare)
    return env


async def test_lost_body_ack_reads_same_signed_attempt_and_uses_normal_action_parser(
    monkeypatch, pure_capture,
):
    install_gateway(monkeypatch, pure_capture)
    result = await llm_client.complete_chat([], MODEL, max_tokens=None, timeout_seconds=5)
    action = agent_builder.parse_action(result)
    assert action is not None and action.name == "read_file"
    assert action.args == {"path": "src/app/page.tsx"}
    assert [request.method for request in pure_capture.requests] == ["POST", "GET"]
    pure_capture.prepare.assert_awaited_once()


async def test_pending_signed_read_uses_original_clock_and_never_posts_again(
    monkeypatch, pure_capture,
):
    def pending(identity):
        return {"version": 1, "identity": identity.to_wire(), "state": "pending"}

    install_gateway(monkeypatch, pure_capture, observation=pending)
    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(llm_client.LLMError) as caught:
        await llm_client._chat_post(
            {"model": MODEL, "messages": [], "metadata": {}}, "https://gateway.test" + ROUTE, 0.05,
        )
    assert caught.value.code == "generation_response_pending"
    assert caught.value.provider_charge_ambiguous is True and caught.value.retry_safe is False
    assert loop.time() - start < 0.5
    assert [request.method for request in pure_capture.requests] == ["POST", "GET"]
    pure_capture.prepare.assert_awaited_once()


async def test_expired_capture_clock_performs_zero_reads(monkeypatch, pure_capture):
    install_gateway(monkeypatch, pure_capture)
    identity = GenerationProviderIdentity(uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "b" * 64)
    with pytest.raises(llm_client.LLMError) as caught:
        await llm_client._wait_for_captured_chat_response(
            identity, "https://gateway.test" + ROUTE,
            deadline=asyncio.get_running_loop().time() - 1,
        )
    assert caught.value.code == "generation_response_pending" and caught.value.retry_safe is False
    assert pure_capture.requests == []
    pure_capture.prepare.assert_not_awaited()


@pytest.mark.parametrize("fault,expected", [
    ("identity", "generation_response_indeterminate"),
    ("format", "generation_response_indeterminate"),
    ("choices", "generation_response_indeterminate"),
    ("settled_refusal", "wallet_empty"),
    ("length", "provider_output_truncated"),
    ("filtered", "provider_output_filtered"),
])
async def test_malformed_or_refused_retained_output_is_never_ready_or_a_new_post(
    monkeypatch, pure_capture, fault, expected,
):
    def observation(identity):
        value = retained(identity)
        if fault == "identity":
            value["identity"] = {**value["identity"], "attempt_id": str(uuid4())}
        elif fault == "format":
            value["response"] = {"format": "sse", "events": ["[DONE]"]}
        elif fault == "choices":
            value["response"]["body"] = {"choices": {"not": "a list"}}
        elif fault == "settled_refusal":
            value["response"].update(status=402, body={"detail": {"error": {
                "code": "wallet_empty", "message": "Synthetic settled refusal",
            }}})
        else:
            reason = "length" if fault == "length" else "content_filter"
            value["response"]["body"] = completion(reason)
        return value

    install_gateway(monkeypatch, pure_capture, observation=observation)
    with pytest.raises(llm_client.LLMError) as caught:
        await llm_client.complete_chat([], MODEL, max_tokens=None, timeout_seconds=5)
    assert caught.value.code == expected and caught.value.retry_safe is not True
    assert [request.method for request in pure_capture.requests] == ["POST", "GET"]
    pure_capture.prepare.assert_awaited_once()


async def policy_snapshot(session, run_id):
    policy = await session.get(GenerationBillingPolicy, run_id)
    holds = (await session.execute(text(
        "SELECT run_id,state,units FROM generation_credit_reservations WHERE run_id=:run",
    ), {"run": run_id})).all()
    return policy.version if policy else None, [tuple(row) for row in holds]


@pytest.fixture
async def admitted_sol(client, db_session, test_engine, monkeypatch, request):
    settings = capture_settings(monkeypatch)
    ordinary = getattr(request, "param", "qa") == "ordinary"
    if ordinary:
        owner, projects, _account = await owner_projects(db_session, count=1)
    else:
        plan = BillingPlan(
            id=uuid4(), code="qa_internal", version=1, name="QA capture",
            price_rub=Decimal(0), included_credit_rub=Decimal(0), billing_interval="month",
            is_active=False, entitlements={"qa_unlimited_generations": True},
        )
        db_session.add(plan)
        await db_session.commit()
        owner, projects, _account = await owner_projects(db_session, plan_id=plan.id, count=1)
        owner.role = "admin"
    projects[0].template = "max_miniapp"
    await db_session.commit()
    jobs = no_providers(monkeypatch, owner)
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: test_engine)
    try:
        response = await client.post(f"/api/projects/{projects[0].id}/prompt", json={
            "prompt": "Создай дневник растений", "skip_clarify": True,
            "idempotency_key": "SOL_CAPTURE_ONCE",
            **({} if ordinary else {"generation_engine": "sol"}),
        })
        assert response.status_code == 202, response.text
        run = await db_session.get(GenerationRun, UUID(response.json()["run_id"]))
        assert run is not None and len(jobs) == 1
        assert generation_engines.selected_engine(run.agent_state) == "sol"
        dispatch = load_generation_dispatch(run)
        assert dispatch.model_id == dispatch.force_model == MODEL
        assert "recovery_v1" not in run.agent_state
        run.status = "running"  # Fixture promotes the normally admitted job; no worker/provider.
        await db_session.commit()
        assert (await policy_snapshot(db_session, run.id))[0] == (
            "accepted-units-v2" if ordinary else "accepted-build-v1"
        )
        body = {"model": MODEL, "messages": [{"role": "user", "content": "Frozen coding task"}],
                "stream": False, "user": str(owner.id), "metadata": {
                    "run_id": str(run.id), "project_id": str(run.project_id),
                    "user_id": str(owner.id), "message_id": str(run.assistant_message_id),
                }}
        yield SimpleNamespace(
            run=run, body=body, owner=owner, project=projects[0], settings=settings,
            policy=await policy_snapshot(db_session, run.id), state=deepcopy(run.agent_state),
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_readonly_source_without_coding_dispatch_is_not_a_sol_coding_capture():
    state = {**generation_engines.engine_selection("sol"), READONLY_SOURCE_TURN_KEY: True}
    run = SimpleNamespace(agent_state=state, response_mode="clarify")
    session = SimpleNamespace(get=AsyncMock(return_value=run))
    assert not await provider_attempts._standalone_sol_capture(
        session, {"model": "claude-sonnet-5.5"}, ROUTE, run_id=uuid4(), agent_state=state,
    )


@pytest.mark.parametrize("mode, marker, dispatch", [
    ("build", True, None), ("edit", True, None), ("clarify", "true", None),
    ("clarify", True, {}), ("clarify", False, None),
])
async def test_readonly_exception_never_hides_missing_or_corrupt_coding_dispatch(
    mode, marker, dispatch,
):
    state = {**generation_engines.engine_selection("sol"), READONLY_SOURCE_TURN_KEY: marker}
    if dispatch is not None:
        state["dispatch"] = dispatch
    run = SimpleNamespace(agent_state=state, response_mode=mode)
    with pytest.raises(ValueError):
        await provider_attempts._standalone_sol_capture(
            SimpleNamespace(get=AsyncMock(return_value=run)), {"model": MODEL}, ROUTE,
            run_id=uuid4(), agent_state=state,
        )


@pytest.mark.parametrize("admitted_sol", ["ordinary"], indirect=True)
async def test_postgres_ordinary_default_sol_retains_owned_unit_call_without_v3(
    admitted_sol, db_session,
):
    case = admitted_sol
    assert case.owner.role != "admin" and "recovery_v1" not in case.state
    before = await policy_snapshot(db_session, case.run.id)
    identity = await provider_attempts.prepare_generation_attempt(deepcopy(case.body), ROUTE)
    assert identity is not None
    assert (identity.run_id, identity.project_id, identity.user_id) == (
        case.run.id, case.project.id, case.owner.id,
    )
    assert await policy_snapshot(db_session, case.run.id) == before
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 1
    assert await db_session.scalar(select(func.count()).select_from(GenerationRecoveryEffect)) == 0


async def test_postgres_http_admission_captures_distinct_identical_logical_calls_without_v2(
    admitted_sol, db_session,
):
    case = admitted_sol
    assert not recovery_reconciliation.has_provider_effect_context()
    first = await provider_attempts.prepare_generation_attempt(deepcopy(case.body), ROUTE)
    second = await provider_attempts.prepare_generation_attempt(deepcopy(case.body), ROUTE)
    assert first is not None and second is not None and first.attempt_id != second.attempt_id
    assert first.request_sha256 == second.request_sha256 == request_digest(ROUTE, case.body)
    assert (first.run_id, first.project_id, first.user_id) == (
        case.run.id, case.run.project_id, case.run.user_id,
    )
    await db_session.refresh(case.run)
    assert case.run.agent_state == case.state and case.run.status == "running"
    assert await policy_snapshot(db_session, case.run.id) == case.policy
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 2
    assert await db_session.scalar(select(func.count()).select_from(GenerationRecoveryEffect)) == 0


@pytest.mark.parametrize("fault", [
    "ordinary", "corrupt_marker", "force", "model", "owner", "body_owner", "route",
    "not_running", "attempts_off", "retention_off",
])
async def test_postgres_standalone_capture_refuses_unowned_or_changed_admission(
    admitted_sol, db_session, monkeypatch, fault,
):
    case = admitted_sol
    body, route = deepcopy(case.body), ROUTE
    state = deepcopy(case.run.agent_state)
    if fault == "ordinary":
        state.pop(generation_engines.ENGINE_SELECTION_KEY)
    elif fault == "corrupt_marker":
        state[generation_engines.ENGINE_SELECTION_KEY]["version"] = 999
    elif fault == "force":
        state["dispatch"]["force_model"] = "claude-sonnet-5.5"
    elif fault == "model":
        body["model"] = "claude-sonnet-5.5"
    elif fault == "owner":
        other = User(email=f"capture-other-{uuid4().hex}@example.com", password_hash="synthetic")
        db_session.add(other)
        await db_session.flush()
        case.project.owner_id = other.id
    elif fault == "body_owner":
        body["user"] = str(uuid4())
    elif fault == "route":
        route = "/v1/messages"
    elif fault == "not_running":
        case.run.status = "completed"
    else:
        settings = case.settings.model_copy(update={
            "generation_provider_attempts_enabled" if fault == "attempts_off"
            else "generation_response_retention_enabled": False,
        })
        monkeypatch.setattr(provider_attempts, "get_settings", lambda: settings)
    case.run.agent_state = state
    await db_session.commit()
    if fault == "ordinary":
        assert await provider_attempts.prepare_generation_attempt(body, route) is None
    else:
        with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
            await provider_attempts.prepare_generation_attempt(body, route)
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 0
    assert await policy_snapshot(db_session, case.run.id) == case.policy


async def test_postgres_committed_attempt_and_record_header_precede_one_coding_post(
    admitted_sol, db_session, test_engine, monkeypatch,
):
    case = admitted_sol
    env = SimpleNamespace(identity=None, requests=[])

    async def before_post(identity, body):
        async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
            row = await session.get(GenerationProviderAttempt, identity.attempt_id)
            assert row is not None, "HTTP must never precede the durable identity commit"
            actual = (
                row.run_id, row.project_id, row.user_id, row.prompt_hash, row.request_sha256,
            )
            assert actual == (
                case.run.id, case.run.project_id, case.run.user_id, case.run.prompt_hash,
                request_digest(ROUTE, body),
            )

    install_gateway(monkeypatch, env, before_post=before_post)
    token = llm_client._generation_billing_context.set((
        str(case.run.id), str(case.run.user_id), str(case.run.project_id),
    ))
    try:
        result = await llm_client.complete_chat(
            [], MODEL, user_id=str(case.run.user_id), project_id=str(case.run.project_id),
            max_tokens=None, timeout_seconds=5,
        )
    finally:
        llm_client._generation_billing_context.reset(token)
    assert agent_builder.parse_action(result).name == "read_file"
    assert [request.method for request in env.requests] == ["POST", "GET"]
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 1
    assert await policy_snapshot(db_session, case.run.id) == case.policy
    await db_session.refresh(case.run)
    assert case.run.agent_state == case.state and "recovery_v1" not in case.run.agent_state
