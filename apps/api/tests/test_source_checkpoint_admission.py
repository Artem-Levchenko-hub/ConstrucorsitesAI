"""A real capacity CAS must precede private source recovery and the first model."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_runs import _owner_and_project
from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation import agent_pipeline, agent_runtime, recovery_execution
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)
from yleum_api.services.generation.source_checkpoint import persist_checkpoint
from yleum_api.services.generation_runs import (
    GenerationDispatch,
    promote_generation_after_admission,
    prompt_hash,
    store_generation_dispatch,
    write_capacity_dispatch_claim,
)

BASE = "a" * 40
TASK = "Original saved product with orders"
EXPANDED = "Expanded discovery request: create the complete application again."
BASE_FILES = {"src/app/page.tsx": "accepted page", "src/app/old.ts": "accepted old file"}
SAVED_FILES = {"src/app/page.tsx": "saved unfinished feature", "src/app/empty.ts": ""}


@pytest.fixture
async def admitted_checkpoint_case(db_session, test_engine, monkeypatch):
    """Real PG rows + isolated immutable object storage, no model/runtime network."""
    owner, project = await _owner_and_project(db_session)
    project.template = "max_miniapp"
    snapshot = Snapshot(project_id=project.id, commit_sha=BASE, prompt_text="Accepted product")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    baseline = SourceBaseline(snapshot.id, BASE, dict(BASE_FILES))

    async def add_run(prompt, status, at):
        request = Message(project_id=project.id, role="user", content=prompt)
        assistant = Message(project_id=project.id, role="assistant", content="")
        db_session.add_all([request, assistant])
        await db_session.flush()
        run = GenerationRun(
            project_id=project.id, user_id=owner.id, user_message_id=request.id,
            assistant_message_id=assistant.id, status=status, response_mode="edit",
            idempotency_key=str(uuid4()), prompt_hash=prompt_hash(prompt), created_at=at,
        )
        store_generation_dispatch(run, GenerationDispatch(
            schema_version=1, project_id=project.id, user_id=owner.id,
            user_message_id=request.id, assistant_message_id=assistant.id,
            current_snapshot_id=snapshot.id, prompt_text=EXPANDED, model_id="test-no-provider",
            force_model=None, is_free=False, orchestrate=False, selected_elements=None,
        ))
        db_session.add(run)
        await db_session.commit()
        return run

    now = datetime.now(UTC)
    previous = await add_run(TASK, "running", now - timedelta(minutes=2))
    await persist_checkpoint(
        factory=factory, ids=GenerationIds(previous.id, project.id, owner.id,
            previous.user_message_id, previous.assistant_message_id),
        baseline=baseline, files=SAVED_FILES, original_prompt=TASK,
    )
    previous.status = "failed"
    previous.finished_at = now - timedelta(minutes=1)
    await db_session.commit()
    current = await add_run("Попробуй ещё раз", "pending", now)
    ids = GenerationIds(current.id, project.id, owner.id,
                        current.user_message_id, current.assistant_message_id)
    facts = ProjectGenerationFacts("max_miniapp", project.slug, "Saved product", None,
                                   None, False, "ru", False, "", "")
    settings = get_settings()
    monkeypatch.setattr(settings, "use_design_intelligence_plugin", False)
    monkeypatch.setattr(settings, "use_max_finalization_coordinator", False)
    monkeypatch.setattr(recovery_execution, "execution_enabled", lambda _project: False)
    return SimpleNamespace(factory=factory, session=db_session, current=current, previous=previous,
        project=project, snapshot=snapshot, ids=ids, facts=facts, baseline=baseline)


class ReachedFirstModel(Exception):
    pass


class AdmissionStopped(Exception):
    pass


async def run_until_first_model(case, monkeypatch, *, outcome="admitted", design=False):
    """Keep the real pipeline/runtime builder/CAS/loader/source-patch machinery."""
    records = []
    files = dict(BASE_FILES)
    events = []
    runtime_bindings = []
    stack_prompts = []
    final_vision = f"Restored design: {TASK}" if design else TASK
    design_contract = None
    if design:
        from yleum_api.services import design_plugin

        design_contract = SimpleNamespace(
            vision_context=final_vision, version=1, archetype="synthetic", preset_id="synthetic",
        )

        def build_design_contract(**kwargs):
            assert kwargs["brief"] == TASK
            return design_contract

        monkeypatch.setattr(get_settings(), "use_design_intelligence_plugin", True)
        monkeypatch.setattr(design_plugin, "build_design_contract", build_design_contract)
    model = AsyncMock()
    token = uuid4()
    if case.current.status == "queued_for_capacity":
        write_capacity_dispatch_claim(case.current, token=token, lease_seconds=60)
        await case.session.commit()
    dispatched_token = uuid4() if outcome == "wrong_token" else token

    async def stage_patch(writes, deletes):
        records.append("apply")
        assert writes["src/app/empty.ts"] == ""
        assert deletes == ("src/app/old.ts",)
        for path in deletes:
            files.pop(path, None)
        files.update(writes)

    handle = SimpleNamespace(
        stage_patch=stage_patch, snapshot_files=AsyncMock(side_effect=lambda: dict(files)),
        execute=AsyncMock(side_effect=AssertionError("unexpected model/tool execution")),
        sync_preview=AsyncMock(side_effect=AssertionError("draft must not publish preview")),
        is_portable=lambda: False, capabilities={},
    )

    async def physical_runtime(**kwargs):
        records.append("runtime")
        assert kwargs["capacity_dispatch_token"] == dispatched_token
        assert kwargs["vision_context"] == EXPANDED
        assert case.current.status in {"pending", "queued_for_capacity"}
        assert "source_checkpoint_resume" not in (case.current.agent_state or {})
        if outcome == "cancel":
            case.current.status = "cancel_requested"
            await case.session.commit()
        actual = await promote_generation_after_admission(
            case.factory, run_id=case.current.id, dispatch_token=dispatched_token,
        )
        await case.session.refresh(case.current)
        if actual != "admitted":
            raise AdmissionStopped(actual)
        assert case.current.status == "running"
        records.append("admitted")
        return dict(project_cell_handle=handle, base_agent_executor=handle.execute,
            max_shell_enabled=False, active_max_locked_files=frozenset(), agent_result=None)

    async def emit(kind, value):
        events.append((kind, value))

    async def stack(**kwargs):
        records.append("stack")
        stack_prompts.append(kwargs["prompt_text"])
        assert kwargs["_design_contract"] is design_contract
        return StackPrompt("", None, None, None, "synthetic system", False)

    async def first_model(**kwargs):
        records.append("model")
        await model()
        async with case.factory() as session:
            committed = await session.get(GenerationRun, case.current.id)
            marker = committed.agent_state.get("source_checkpoint_resume")
            assert marker is not None, "admitted retry lost source checkpoint provenance"
            assert marker["previous_run_id"] == marker["source_run_id"] == str(case.previous.id)
            assert marker["baseline_sha"] == BASE
        assert files == SAVED_FILES
        assert kwargs["workspace_context_files"] == SAVED_FILES
        assert kwargs["prompt_text"] == TASK
        assert TASK in kwargs["plan"].user
        assert stack_prompts == [TASK]
        assert runtime_bindings[0].vision_context == final_vision
        assert any(kind == "agent.continuation" for kind, _ in events)
        assert records.index("admitted") < records.index("apply") < records.index("model")
        raise ReachedFirstModel

    monkeypatch.setattr(agent_runtime, "_prepare_max_runtime_context", physical_runtime)
    actual_prepare = agent_pipeline.prepare_agent_runtime

    async def observe_prepare(**kwargs):
        result = await actual_prepare(**kwargs)
        runtime_bindings.append(result[0])
        return result

    monkeypatch.setattr(agent_pipeline, "prepare_agent_runtime", observe_prepare)
    monkeypatch.setattr(agent_pipeline, "require_named_behavior_readiness", AsyncMock())
    monkeypatch.setattr(agent_pipeline, "prepare_stack_prompt", stack)
    monkeypatch.setattr(agent_pipeline, "execute_agent_turn", first_model)
    kwargs = dict(
        _consume_free_generation=AsyncMock(), baseline=case.baseline,
        capacity_dispatch_token=dispatched_token, factory=case.factory,
        force_model="synthetic-test", ids=case.ids, is_free=False, model_id="test-no-provider",
        orchestrate=design, progress=SimpleNamespace(emit_agent_event=emit),
        project_info=case.facts, prompt_text=EXPANDED, runtime=GenerationRuntime(),
        selected_elements=None,
    )
    return SimpleNamespace(invoke=lambda: agent_pipeline.run_agent_generation(**kwargs),
        records=records, files=files, model=model, kwargs=kwargs)


@pytest.mark.parametrize("initial_status", ["pending", "queued_for_capacity"])
async def test_real_admission_recovers_source_before_first_model(
    admitted_checkpoint_case, monkeypatch, initial_status,
):
    case = admitted_checkpoint_case
    case.current.status = initial_status
    await case.session.commit()
    run = await run_until_first_model(case, monkeypatch)
    with pytest.raises(ReachedFirstModel):
        await run.invoke()
    run.model.assert_awaited_once()
    assert run.records == ["runtime", "admitted", "stack", "apply", "model"]


async def test_restored_design_context_is_final_before_model(admitted_checkpoint_case, monkeypatch):
    run = await run_until_first_model(admitted_checkpoint_case, monkeypatch, design=True)
    with pytest.raises(ReachedFirstModel):
        await run.invoke()
    run.model.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["wrong_token", "cancel"])
async def test_unadmitted_retry_never_restores_source_or_calls_model(
    admitted_checkpoint_case, monkeypatch, outcome,
):
    case = admitted_checkpoint_case
    case.current.status = "queued_for_capacity"
    await case.session.commit()
    run = await run_until_first_model(case, monkeypatch, outcome=outcome)
    with pytest.raises(AdmissionStopped, match="lost" if outcome == "wrong_token" else "cancelled"):
        await run.invoke()
    run.model.assert_not_awaited()
    assert run.files == BASE_FILES
    assert "apply" not in run.records
    await case.session.refresh(case.current)
    assert "source_checkpoint_resume" not in (case.current.agent_state or {})
    assert case.current.status == ("queued_for_capacity" if outcome == "wrong_token"
                                   else "cancel_requested")
