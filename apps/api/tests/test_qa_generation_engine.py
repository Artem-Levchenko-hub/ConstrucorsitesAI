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


def admission(engine="claude"):
    user_id, project_id = uuid4(), uuid4()
    owner = SimpleNamespace(id=user_id, role="admin")
    project = SimpleNamespace(id=project_id, owner_id=user_id, template="max_miniapp")
    return acceptance.PromptAcceptance(
        project_id=project_id,
        payload=PromptRequest(prompt="Build a journal", generation_engine=engine),
        session=SimpleNamespace(scalar=AsyncMock()),
        current_user=owner,
        project=project,
    )


@pytest.mark.parametrize("engine", ["gpt-6.2-sol", "openai/gpt-6.2-sol", "SOL", "", None])
def test_only_closed_engine_choices_are_accepted(engine):
    with pytest.raises(ValidationError):
        PromptRequest(prompt="Build", generation_engine=engine)


@pytest.mark.parametrize("case", ["qa", "ordinary", "wrong_owner", "wrong_template"])
async def test_sol_requires_owned_max_project_and_server_qa_authority(monkeypatch, case):
    request = admission("sol")
    allowed = AsyncMock(return_value=case != "ordinary")
    monkeypatch.setattr(acceptance, "can_choose_generation_engine", allowed)
    if case == "wrong_owner":
        request.project.owner_id = uuid4()
    if case == "wrong_template":
        request.project.template = "blank"
    if case == "qa":
        await request.check_engine_access()
        allowed.assert_awaited_once_with(request.session, request.current_user)
    else:
        with pytest.raises(ApiError) as error:
            await request.check_engine_access()
        assert error.value.status_code == 403


async def test_default_claude_needs_no_pilot_authorization(monkeypatch):
    allowed = AsyncMock(side_effect=AssertionError("ordinary client must remain unchanged"))
    monkeypatch.setattr(acceptance, "can_choose_generation_engine", allowed)
    await admission().check_engine_access()
    allowed.assert_not_awaited()


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


def test_cold_dispatch_load_retains_model_and_rejects_mismatched_engine():
    owner, project = uuid4(), uuid4()
    run = SimpleNamespace(
        user_id=owner, project_id=project, agent_state=generation_engines.engine_selection("sol")
    )
    dispatch = GenerationDispatch(
        1,
        project,
        owner,
        uuid4(),
        uuid4(),
        None,
        "Build",
        "gpt-6.2-sol",
        "gpt-6.2-sol",
        True,
        True,
        None,
    )
    store_generation_dispatch(run, dispatch)
    assert load_generation_dispatch(run) == dispatch
    run.agent_state["dispatch"]["force_model"] = "claude-sonnet-5.5"
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


async def test_http_qa_choice_is_frozen_replayed_and_revocable(client, db_session, monkeypatch):
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
        denied = await client.post(
            url, json={**payload, "idempotency_key": "QA_SOL_REVOKED_SUBMIT"}
        )
        assert denied.status_code == 403 and len(jobs) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
