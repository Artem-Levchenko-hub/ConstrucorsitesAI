"""An engine choice is authorized once and remains bound to its accepted run."""

from functools import partial
from inspect import isawaitable
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.schemas.message import PromptRequest
from yleum_api.services import agent_builder, generation_engines
from yleum_api.services.generation import acceptance
from yleum_api.services.generation.agent_generation import uses_native_protocol
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.generation_runs import (
    GenerationDispatch,
    load_generation_dispatch,
    store_generation_dispatch,
)

OMITTED = object()


def admission(engine=OMITTED):
    user_id, project_id = uuid4(), uuid4()
    owner = SimpleNamespace(id=user_id, role="admin")
    project = SimpleNamespace(id=project_id, owner_id=user_id, template="max_miniapp")
    return acceptance.PromptAcceptance(
        project_id=project_id,
        payload=(PromptRequest(prompt="Build a journal") if engine is OMITTED else
                 PromptRequest(prompt="Build a journal", generation_engine=engine)),
        session=SimpleNamespace(scalar=AsyncMock()),
        current_user=owner,
        project=project,
    )


@pytest.mark.parametrize("engine", ["gpt-6.2-sol", "openai/gpt-6.2-sol", "SOL", "", None])
def test_only_closed_engine_choices_are_accepted(engine):
    with pytest.raises(ValidationError):
        PromptRequest(prompt="Build", generation_engine=engine)


@pytest.mark.parametrize("engine", [OMITTED, "sol", "claude"])
async def test_normal_engine_choices_do_not_grant_or_require_private_qa_authority(
    monkeypatch, engine,
):
    allowed = AsyncMock(side_effect=AssertionError("ordinary client must remain unchanged"))
    monkeypatch.setattr(generation_engines, "can_choose_generation_engine", allowed)
    request = admission(engine)
    request.current_user.role = "user"
    request.project.template = "blank"
    await request.check_engine_access()
    allowed.assert_not_awaited()
    assert request.generation_engine == ("sol" if engine is OMITTED else engine)


@pytest.mark.parametrize("engine", ["sol", "claude"])
async def test_engine_choice_never_grants_project_ownership(engine):
    request = admission(engine)
    request.project.owner_id = uuid4()
    with pytest.raises(ApiError) as error:
        await request.check_engine_access()
    assert error.value.status_code == 403


def test_new_default_and_historical_missing_marker_have_distinct_meanings():
    assert PromptRequest(prompt="Build").generation_engine == "sol"
    assert "generation_engine" not in PromptRequest(prompt="Build").model_fields_set
    assert generation_engines.selected_engine({}) == "claude"
    assert generation_engines.selected_engine(None) == "claude"


@pytest.mark.parametrize("role", ["user", None, "ADMIN"])
async def test_ordinary_profile_does_not_query_private_qa_plan(monkeypatch, role):
    load = AsyncMock(side_effect=AssertionError("A non-admin cannot qualify for the QA plan"))
    monkeypatch.setattr(generation_engines, "load_plan_context", load)
    assert (
        await generation_engines.can_choose_generation_engine(
            SimpleNamespace(),
            SimpleNamespace(id=uuid4(), role=role),
        )
        is False
    )
    load.assert_not_awaited()


@pytest.mark.parametrize("requested, stored", [("sol", "claude"), ("claude", "sol")])
async def test_same_key_cannot_change_engine_before_any_upload_or_reservation(
    monkeypatch, requested, stored
):
    request = admission(requested)
    request.session.scalar.return_value = SimpleNamespace(
        agent_state=generation_engines.engine_selection(stored)
    )
    monkeypatch.setattr(acceptance, "_try_prompt_project_lock", AsyncMock())
    with pytest.raises(ApiError) as error:
        await request.reserve_or_replay()
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize("stored", ["claude", "sol"])
async def test_omitted_same_key_replay_adopts_frozen_engine_under_project_lock(monkeypatch, stored):
    from yleum_api.schemas.message import PromptResponse

    request = admission()
    run_id, message_id = uuid4(), uuid4()
    existing = SimpleNamespace(
        agent_state=generation_engines.engine_selection(stored), user_message_id=uuid4(),
        user_id=request.current_user.id, assistant_message_id=message_id, status="running",
        response_payload={"message_id": str(message_id), "run_id": str(run_id), "mode": "build"},
    )
    request.session.scalar.return_value = existing
    request.session.get = AsyncMock(return_value=SimpleNamespace(
        reference_images=[], font_selections=[], resolved_fonts=[], role="user",
        project_id=request.project_id,
    ))
    lock = AsyncMock()
    reserve = AsyncMock(return_value=(existing, True))
    monkeypatch.setattr(acceptance, "_try_prompt_project_lock", lock)
    monkeypatch.setattr(acceptance, "reserve_generation_run", reserve)
    response = await request.reserve_or_replay()
    lock.assert_awaited_once_with(request.session, request.project_id)
    assert request.generation_engine == stored
    assert request.payload.generation_engine == "sol"  # Incoming omission was not rewritten.
    assert isinstance(response, PromptResponse) and response.replayed is True
    assert response.run_id == run_id
    assert existing.agent_state == generation_engines.engine_selection(stored)
    reserve.assert_awaited_once()


@pytest.mark.parametrize(
    "engine, override, expected",
    [
        ("sol", "other", "gpt-6.2-sol"),
        ("claude", "", None),
        ("claude", "gpt-6.2-sol", None),
        ("claude", "openai/gpt-6.2-sol", None),
    ],
)
async def test_qa_choice_becomes_dispatch_model_without_global_override(engine, override, expected):
    request = admission(engine)
    request.settings = get_settings().model_copy(update={"force_model": override})
    request.effective_prompt = request.payload.prompt
    request.is_first_build = True
    request.selected_dump = None
    request.credential_redirect = request.explain_failed_build = False
    request.font_selections = request.resolved_fonts = []
    await request.select_intent_and_model()
    assert request.force_model == expected
    if engine == "sol":
        assert request.routing_model == "gpt-6.2-sol"


async def test_readonly_source_keeps_shared_role_routing_instead_of_the_coding_default():
    request = admission()
    request.settings = get_settings().model_copy(update={"force_model": ""})
    request.effective_prompt = "Объясни текущие исходники"
    request.is_first_build = False
    request.selected_dump = None
    request.credential_redirect = request.explain_failed_build = False
    request.font_selections = request.resolved_fonts = []
    request.readonly_source = True
    await request.select_intent_and_model()
    assert request.force_model is None
    assert request.routing_model == acceptance.model_for_role("edit")


@pytest.mark.parametrize("engine", ["claude", "sol"])
def test_cold_dispatch_load_retains_model_and_rejects_mismatched_engine(engine):
    owner, project = uuid4(), uuid4()
    run = SimpleNamespace(
        user_id=owner, project_id=project, agent_state=generation_engines.engine_selection(engine)
    )
    dispatch = GenerationDispatch(
        1,
        project,
        owner,
        uuid4(),
        uuid4(),
        None,
        "Build",
        "gpt-6.2-sol" if engine == "sol" else "claude-sonnet-5.5",
        "gpt-6.2-sol" if engine == "sol" else None,
        True,
        True,
        None,
    )
    store_generation_dispatch(run, dispatch)
    assert load_generation_dispatch(run) == dispatch
    run.agent_state["dispatch"]["force_model"] = (
        "claude-sonnet-5.5" if engine == "sol" else "gpt-6.2-sol"
    )
    with pytest.raises(ValueError, match="engine binding"):
        load_generation_dispatch(run)


@pytest.mark.parametrize(
    "configured,directed", [(False, False), (True, False), (False, True), (True, True)]
)
def test_sol_always_uses_text_protocol_including_directed_repair(configured, directed):
    assert not uses_native_protocol(
        "gpt-6.2-sol", None, configured=configured, directed_repair=directed
    )
    assert not uses_native_protocol(
        "gpt-6.2-sol", {"kind": "legacy"}, configured=configured, directed_repair=directed
    )
    with pytest.raises(RecoveryContractError):
        uses_native_protocol(
            "gpt-6.2-sol", {"kind": "native"}, configured=configured, directed_repair=directed
        )


async def test_sol_multi_turn_actions_keep_model_observation_and_provider_output_default():
    calls, executed = [], []

    async def complete(messages, model, **options):
        calls.append((messages, model, options))
        if len(calls) == 1:
            return '<omnia:action name="read_file">{"path":"src/app/page.tsx"}</omnia:action>'
        assert "unique existing source" in str(messages)
        return '<omnia:action name="done">{"summary":"Ready"}</omnia:action>'

    async def execute(action):
        executed.append(action.name)
        return {"ok": True, "content": "unique existing source"}

    result = await agent_builder.run_agent_build(
        system_prompt="Use the shared action protocol",
        user_prompt="Inspect the application",
        model="gpt-6.2-sol",
        escalate_model="gpt-6.2-sol",
        complete=complete,
        execute=execute,
        max_steps=2,
    )
    assert result.done and executed == ["read_file"]
    assert len(calls) == 2
    assert all(
        model == "gpt-6.2-sol" and options["max_tokens"] is None for _, model, options in calls
    )


@pytest.mark.parametrize("checkpoint_fails", [False, True])
async def test_sol_finalization_repair_uses_same_model_and_authorized_executor(
    monkeypatch,
    checkpoint_fails,
):
    from yleum_api.services import agent_native
    from yleum_api.services.generation import agent_finalization, agent_runtime
    from yleum_api.services.generation.contracts import GenerationIds
    from yleum_api.services.generation.source_error_classification import SourceFailure

    baseline = {"src/app/page.tsx": "current draft"}
    execute = AsyncMock(return_value={"ok": True})
    snapshot = AsyncMock(return_value=baseline)
    result = agent_builder.AgentResult(done=True, files=baseline, steps=1, summary="Fixed")
    if checkpoint_fails:
        result.stop_reason = "checkpoint_error"
    legacy = AsyncMock(return_value=result)
    native = AsyncMock(side_effect=AssertionError("Sol must not use Anthropic tools"))

    async def guard(runtime, ids, execute, completion):
        return execute, completion

    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", guard)
    monkeypatch.setattr(
        agent_finalization, "uses_autonomous_max_source_repair", lambda runtime: True
    )
    monkeypatch.setattr(agent_builder, "run_agent_build", legacy)
    monkeypatch.setattr(agent_native, "run_native_build", native)
    repair = partial(
        agent_finalization.run_native_source_repair,
        task="Fix compiler diagnostic",
        prompt_text="Build a journal",
        baseline=baseline,
        runtime=SimpleNamespace(handle=SimpleNamespace(capabilities={}), coordinator=None),
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        plan=SimpleNamespace(model="gpt-6.2-sol", system="Shared text action protocol", steps=10),
        operations=SimpleNamespace(execute=execute, snapshot_files=snapshot, emit=AsyncMock()),
        is_free=True,
        shell_enabled=False,
        edit_deadline=None,
        source_failure=SourceFailure(kind="type_error", allowed_paths=("src/app/page.tsx",)),
    )
    if checkpoint_fails:
        with pytest.raises(RuntimeError, match="Не удалось сохранить черновик"):
            await repair()
        native.assert_not_awaited()
        assert not result.done
        return
    assert await repair() is result
    native.assert_not_awaited()
    kwargs = legacy.await_args.kwargs
    assert kwargs["model"] == kwargs["escalate_model"] == "gpt-6.2-sol"
    assert kwargs["bounded_repair"] is True and kwargs["ship_green_on_abort"] is False
    assert not isawaitable(kwargs["completion_check"](baseline, {}))
    refused = await kwargs["execute"](
        agent_builder.Action("write_file", {"path": "private.ts", "content": "x"})
    )
    assert refused["ok"] is False
    execute.assert_not_awaited()


async def test_http_qa_selector_capability_is_revocable_without_changing_accepted_sol(
    client, db_session, monkeypatch,
):
    from decimal import Decimal
    from uuid import UUID

    from sqlalchemy import select

    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.billing import BillingPlan, Subscription
    from yleum_api.models.generation_run import GenerationRun

    plan = BillingPlan(
        id=uuid4(),
        code="qa_internal",
        version=1,
        name="QA Sol",
        price_rub=Decimal(0),
        included_credit_rub=Decimal(0),
        billing_interval="month",
        is_active=False,
        entitlements={"qa_unlimited_generations": True},
    )
    db_session.add(plan)
    await db_session.commit()
    owner, projects, account = await owner_projects(db_session, plan_id=plan.id, count=1)
    owner.role = "admin"
    projects[0].template = "max_miniapp"
    await db_session.commit()
    jobs = no_providers(monkeypatch, owner)
    try:
        assert (await client.get("/api/auth/me")).json()["can_choose_generation_engine"] is True
        url = f"/api/projects/{projects[0].id}/prompt"
        payload = {
            "prompt": "Создай дневник растений",
            "skip_clarify": True,
            "generation_engine": "sol",
            "idempotency_key": "QA_SOL_STABLE_SUBMIT",
        }
        first = await client.post(url, json=payload)
        assert first.status_code == 202, first.text
        run = await db_session.get(GenerationRun, UUID(first.json()["run_id"]))
        assert generation_engines.selected_engine(run.agent_state) == "sol"
        assert load_generation_dispatch(run).force_model == "gpt-6.2-sol"
        assert jobs[-1]["force_model"] == "gpt-6.2-sol"
        replay = await client.post(url, json=payload)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert replay.json()["run_id"] == first.json()["run_id"] and len(jobs) == 1
        conflict = await client.post(url, json={**payload, "generation_engine": "claude"})
        assert conflict.status_code == 409
        subscription = await db_session.scalar(
            select(Subscription).where(
                Subscription.billing_account_id == account.id,
            )
        )
        subscription.status = "paused"
        await db_session.commit()
        assert (await client.get("/api/auth/me")).json()["can_choose_generation_engine"] is False
        replay_after_revocation = await client.post(url, json=payload)
        assert replay_after_revocation.status_code == 202
        assert replay_after_revocation.json()["replayed"] is True and len(jobs) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("engine", [None, "sol", "claude"])
async def test_http_normal_default_and_explicit_choices_preserve_replay_and_budget(
    client, db_session, monkeypatch, engine,
):
    from uuid import UUID

    from tests.test_free_chat_allowance import no_providers, owner_projects
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_run import GenerationRun

    owner, projects, _ = await owner_projects(db_session)
    jobs = no_providers(monkeypatch, owner)
    payload = {"prompt": "Build a nutrition journal", "skip_clarify": True,
               "idempotency_key": "NORMAL_ENGINE_DEFAULT"}
    if engine is not None:
        payload["generation_engine"] = engine
    expected = engine or "sol"
    try:
        assert (await client.get("/api/auth/me")).json()["can_choose_generation_engine"] is False
        url = f"/api/projects/{projects[0].id}/prompt"
        first = await client.post(url, json=payload)
        assert first.status_code == 202, first.text
        run = await db_session.get(GenerationRun, UUID(first.json()["run_id"]))
        assert generation_engines.selected_engine(run.agent_state) == expected
        assert "recovery_v1" not in run.agent_state
        assert (load_generation_dispatch(run).force_model == "gpt-6.2-sol") is (expected == "sol")
        # A pre-default client may omit its old choice while replaying a stable key.
        replay = await client.post(url, json={
            k: v for k, v in payload.items() if k != "generation_engine"
        })
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert replay.json()["run_id"] == first.json()["run_id"] and len(jobs) == 1
        conflict = await client.post(url, json={
            **payload, "generation_engine": "claude" if expected == "sol" else "sol",
        })
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_conflict"
        exhausted = await client.post(f"/api/projects/{projects[1].id}/prompt", json={
            **payload, "idempotency_key": "NORMAL_ENGINE_SECOND",
        })
        assert exhausted.status_code == 402
        assert exhausted.json()["error"]["code"] == "generation_credits_exhausted"
        assert len(jobs) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
