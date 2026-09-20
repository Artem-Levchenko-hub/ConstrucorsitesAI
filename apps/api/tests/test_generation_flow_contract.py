"""Whole process/tracker contracts: real PostgreSQL/Git, deterministic external I/O.

These tests intentionally execute normal terminal paths, not extracted source or
an early-stop exception. The provider/runtime doubles are not product acceptance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from omnia_api.core import config, db
from omnia_api.models.generation_event import GenerationEvent
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services import (
    agent_builder,
    agent_native,
    llm_client,
    orchestrator_client,
    project_cell_executor,
    repo,
)
from omnia_api.services.generation import (
    acceptance,
    agent_generation,
    agent_pipeline,
    agent_preparation,
    agent_prompt,
    agent_publication,
    agent_recovery,
    agent_runtime,
    agent_verification,
    lifecycle,
    lightweight_turns,
    onboarding,
    progress,
    supervisor,
)
from omnia_api.services.generation import lifecycle as messages
from omnia_api.services.generation_runs import GenerationDispatch, store_generation_dispatch
from omnia_api.workers import generation


def set_generation_settings(monkeypatch, settings):
    for owner in (
        acceptance,
        agent_generation,
        agent_pipeline,
        agent_preparation,
        agent_prompt,
        agent_publication,
        agent_recovery,
        agent_runtime,
        agent_verification,
        lifecycle,
        onboarding,
    ):
        monkeypatch.setattr(owner, "get_settings", lambda: settings)


@dataclass
class Flow:
    engine: AsyncEngine
    owner_id: UUID
    project_id: UUID
    user_message_id: UUID
    assistant_id: UUID
    run_id: UUID
    parent_id: UUID
    parent_sha: str
    events: list[tuple[str, dict]] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    model_free: list[bool] = field(default_factory=list)
    free: bool = True
    visible_publications: list[str] = field(default_factory=list)

    async def run(self, *, free=True):
        self.free = free
        await asyncio.wait_for(
            supervisor._run_tracked_prompt(
                messages._process_prompt(
                    self.run_id,
                    self.project_id,
                    self.owner_id,
                    self.user_message_id,
                    self.assistant_id,
                    self.parent_id,
                    "Implement the requested working calculation module",
                    "fixture-model",
                    is_free=free,
                    orchestrate=True,
                ),
                run_id=self.run_id,
                project_id=self.project_id,
                assistant_message_id=self.assistant_id,
                label="flow-contract",
            ),
            15,
        )

    async def assert_publication_visible(self, engine, snapshot_id, boundary):
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            snapshot = await session.get(Snapshot, snapshot_id)
            project = await session.get(Project, self.project_id)
            message = await session.get(Message, self.assistant_id)
            owner = await session.get(User, self.owner_id)
            assert snapshot is not None and snapshot.parent_id == self.parent_id
            assert project.current_snapshot_id == message.snapshot_id == snapshot_id
            assert message.tokens_out is not None and message.content
            assert owner.free_generations_used == 3 + int(self.free)
        self.visible_publications.append(boundary)

    async def saved(self):
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with factory() as session:
            return (
                await session.get(GenerationRun, self.run_id),
                await session.get(Project, self.project_id),
                await session.get(Message, self.assistant_id),
                await session.get(User, self.owner_id),
                list((await session.scalars(select(Snapshot).order_by(Snapshot.created_at))).all()),
                list(
                    (
                        await session.scalars(select(GenerationEvent).order_by(GenerationEvent.seq))
                    ).all()
                ),
            )


@pytest.fixture
async def flow_factory(test_engine, monkeypatch):
    async def make(template):
        owner = User(
            email=f"flow-{uuid4().hex}@example.test",
            password_hash="fixture",
            free_generations_used=3,
        )
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(owner)
            await session.flush()
            project = Project(
                owner_id=owner.id,
                name="Calculation",
                slug=f"flow-{uuid4().hex}",
                template=template,
                design_preset_id="saas",
                image_gen_enabled=False,
            )
            session.add(project)
            await session.flush()
            sha = repo.init_from_files(project.id, {"README.md": "Preserve this source"}, "seed")
            parent = Snapshot(project_id=project.id, commit_sha=sha, prompt_text="Original source")
            user_message = Message(
                project_id=project.id, role="user", content="Implement calculation"
            )
            assistant = Message(project_id=project.id, role="assistant", content="")
            session.add_all([parent, user_message, assistant])
            await session.flush()
            project.current_snapshot_id = parent.id
            run = GenerationRun(
                project_id=project.id,
                user_id=owner.id,
                user_message_id=user_message.id,
                assistant_message_id=assistant.id,
                idempotency_key="flow-contract",
                prompt_hash="f" * 64,
                status="pending",
                response_mode="build",
            )
            session.add(run)
            await session.commit()
        flow = Flow(
            test_engine, owner.id, project.id, user_message.id, assistant.id, run.id, parent.id, sha
        )
        disabled = (
            "use_project_memory",
            "use_art_director_freeform",
            "use_director_polish",
            "use_section_catalog",
            "use_build_plan",
            "use_build_attestation",
            "use_coverage_gate",
            "use_functional_gate",
            "use_security_gate",
            "use_sast_gate",
            "use_skill_injection",
            "use_design_mood",
            "use_design_intelligence_plugin",
            "use_native_agent",
            "use_edit_auto_repair",
            "use_agent_gate_feedback",
            "use_visual_enricher",
            "use_acceptance_gate",
            "use_signature_floor",
            "use_image_gen",
            "use_video_gen",
            "use_max_finalization_coordinator",
        )
        assert set(disabled) <= config.Settings.model_fields.keys()
        settings = config.get_settings().model_copy(
            update={
                **dict.fromkeys(disabled, False),
                "use_agentic_builder": True,
                "multipass_models": "off",
                "agentic_builder_canary_users": "",
                "use_clean_chat_content": True,
                "max_project_shell_enabled": False,
                "role_models": "single_shot=gemini-3.1-pro-preview-customtools",
            }
        )
        monkeypatch.setattr(config, "get_settings", lambda: settings)
        set_generation_settings(monkeypatch, settings)
        monkeypatch.setattr(db, "get_engine", lambda: test_engine)
        monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
        monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)

        async def publish(project_id, kind, payload):
            assert project_id == flow.project_id
            if kind == "snapshot.created":
                await flow.assert_publication_visible(
                    test_engine,
                    UUID(payload["snapshot"]["id"]),
                    "snapshot.created",
                )
            flow.events.append((kind, payload))
            flow.trace.append(kind)

        async def clear(*_args):
            flow.trace.append("clear_stream")

        async def wait_cancel(*_args):
            await asyncio.Event().wait()

        async def noop(*_args, **_kwargs):
            return None

        async def forbidden_http(*_args, **_kwargs):
            pytest.fail("whole-flow fixture attempted external HTTP")

        def enqueue(snapshot_id):
            assert isinstance(snapshot_id, UUID)

            async def check_committed_rows():
                # enqueue_preview is called via to_thread. Its independent loop
                # must own a fresh pool, not reuse the test loop's asyncpg pool.
                engine = create_async_engine(flow.engine.url)
                try:
                    await flow.assert_publication_visible(engine, snapshot_id, "enqueue_preview")
                finally:
                    await engine.dispose()

            asyncio.run(check_committed_rows())
            flow.trace.append("enqueue_preview")

        monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_http)
        monkeypatch.setattr(agent_pipeline, "publish_event", publish)
        monkeypatch.setattr(agent_publication, "publish_event", publish)
        monkeypatch.setattr(lifecycle, "publish_event", publish)
        monkeypatch.setattr(lightweight_turns, "publish_event", publish)
        monkeypatch.setattr(progress, "publish_event", publish)
        monkeypatch.setattr(supervisor, "publish_event", publish)
        monkeypatch.setattr(messages, "clear_stream_state", clear)
        monkeypatch.setattr(agent_pipeline, "clear_stream_state", clear)
        monkeypatch.setattr(supervisor, "clear_generation_cancel", noop)
        monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_cancel)
        monkeypatch.setattr(agent_publication, "enqueue_preview", enqueue)
        return flow

    return make


@pytest.mark.parametrize("cancel", [False, True])
async def test_real_max_failure_or_cancel_waits_for_executor_cleanup(
    flow_factory,
    monkeypatch,
    cancel,
):
    flow = await flow_factory("max_miniapp")
    snapshot_started = asyncio.Event()
    release_started = asyncio.Event()
    finish_release = asyncio.Event()
    cancel_signal = asyncio.Event()
    releases = []

    class Runtime:
        """Existing cell boundary: fail/stop during mandatory SDK refresh."""

        def is_portable(self):
            return False

        async def execute(self, action):
            pytest.fail(f"unexpected executor action before SDK refresh: {action.name}")

        async def snapshot_files(self):
            flow.trace.append("snapshot_files")
            snapshot_started.set()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError("fixture managed SDK unavailable")

        async def release(self):
            releases.append("started")
            flow.trace.append("release_started")
            release_started.set()
            await finish_release.wait()
            releases.append("finished")
            flow.trace.append("release_finished")

    handle = Runtime()

    async def acquire(**kwargs):
        assert kwargs["generation_run_id"] == flow.run_id
        assert kwargs["project_template"] == "max_miniapp"
        flow.trace.append("acquire")
        return handle

    async def wait_cancel(*_args):
        await cancel_signal.wait()

    async def forbidden(*_args, **_kwargs):
        pytest.fail("MAX preparation reached a model or legacy runtime")

    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", acquire)
    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", forbidden)
    monkeypatch.setattr(agent_builder, "run_agent_build", forbidden)
    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_cancel)
    work = asyncio.create_task(flow.run())
    try:
        await asyncio.wait_for(snapshot_started.wait(), 5)
        if cancel:
            cancel_signal.set()
        await asyncio.wait_for(release_started.wait(), 5)
        assert not work.done()
        run_before, *_ = await flow.saved()
        if cancel:
            assert run_before.status == "running" and run_before.finished_at is None
        finish_release.set()
        await asyncio.wait_for(work, 5)
    finally:
        finish_release.set()
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)
    run, project, message, owner, snapshots, durable_events = await flow.saved()
    assert releases == ["started", "finished"]
    assert project.current_snapshot_id == flow.parent_id and message.snapshot_id is None
    assert owner.free_generations_used == 3 and len(snapshots) == 1
    assert run.status == ("cancelled" if cancel else "failed")
    assert run.finished_at is not None and message.tokens_out == 0
    assert len(durable_events) == 1 and durable_events[0].payload["tool"] == "project_cell"
    assert "snapshot.created" not in flow.trace and "enqueue_preview" not in flow.trace
    assert "llm.done" not in flow.trace
    assert flow.trace.count("generation.cancelled") == int(cancel)
    assert flow.trace.count("llm.error") == int(not cancel)
    assert flow.trace.index("release_finished") < flow.trace.index("clear_stream")
    if cancel:
        assert flow.trace.index("release_finished") < flow.trace.index("generation.cancelled")
    else:
        assert run.error == "fixture managed SDK unavailable"
        assert "fixture managed SDK unavailable" in message.content


@pytest.mark.parametrize("stopped", [False, True])
async def test_real_agent_publication_or_rollback_reaches_terminal_state(
    flow_factory,
    monkeypatch,
    stopped,
):
    flow = await flow_factory("nextjs_entities")
    runtime_files = {"README.md": "Preserve this source"}
    changed = {
        "README.md": "Changed by provider",
        "src/app/page.tsx": "export default function Page(){return <main>Calculation</main>}",
    }
    patches = []
    calls = []

    async def ensure(*_args, **kwargs):
        assert kwargs == {"require_ready": True}
        flow.trace.append("ensure")

    async def empty(*_args, **_kwargs):
        return ""

    async def no_cell(**_kwargs):
        return None

    async def build(*_args, **_kwargs):
        flow.trace.append("build")
        return {"ok": True}

    async def runtime(*_args, **_kwargs):
        flow.trace.append("runtime")
        return {"ok": True, "status_code": 200}

    async def hot_reload(*args, **kwargs):
        patch = kwargs.get("files") if "files" in kwargs else args[2]
        patches.append(dict(patch))
        flow.trace.append("hot_reload")
        for path, content in patch.items():
            if content:
                runtime_files[path] = content
            else:
                runtime_files.pop(path, None)
        return {"ok": True}

    async def provider(**kwargs):
        calls.append(kwargs)
        flow.trace.append("provider")
        flow.model_free.append(llm_client._free_generation.get())
        runtime_files.update(changed)
        await kwargs["emit"](
            "agent.step",
            {
                "step": 1,
                "action": "write_file",
                "path": "src/app/page.tsx",
                "detail": "Wrote the calculation page",
                "ok": True,
            },
        )
        return agent_builder.AgentResult(
            done=not stopped,
            summary="Calculation implemented.",
            files=dict(changed),
            steps=1,
            stop_reason="max_steps_red" if stopped else "done",
        )

    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", ensure)
    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", no_cell)
    monkeypatch.setattr(orchestrator_client, "agent_list_dir", empty)
    monkeypatch.setattr(orchestrator_client, "agent_read_file", empty)
    monkeypatch.setattr(orchestrator_client, "agent_build", build)
    monkeypatch.setattr(orchestrator_client, "runtime_status", runtime)
    monkeypatch.setattr(orchestrator_client, "warm_routes", empty)
    monkeypatch.setattr(orchestrator_client, "hot_reload", hot_reload)
    monkeypatch.setattr(agent_builder, "run_agent_build", provider)
    await flow.run()
    run, project, message, owner, snapshots, durable_events = await flow.saved()
    assert len(calls) == 1 and flow.model_free == [True]
    assert [event.seq for event in durable_events] == list(range(1, len(durable_events) + 1))
    assert all(event.event_type == "agent.step" for event in durable_events)
    assert [event.payload["tool"] for event in durable_events[:3]] == [
        "runtime",
        "runtime",
        "write_file",
    ]
    assert message.agent_steps == [
        {key: value for key, value in event.payload.items() if key != "message_id"}
        for event in durable_events
    ]
    assert flow.trace.index("ensure") < flow.trace.index("provider")
    assert len([kind for kind, _ in flow.events if kind == "llm.done"]) == 1
    assert flow.trace[-1] == "clear_stream"
    if stopped:
        assert run.status == "failed" and run.error == "build finished without a committed snapshot"
        assert message.snapshot_id is None and project.current_snapshot_id == flow.parent_id
        assert owner.free_generations_used == 3 and len(snapshots) == 1
        assert patches == [{"README.md": "Preserve this source"}, {"src/app/page.tsx": ""}]
        assert runtime_files == {"README.md": "Preserve this source"}
        assert "последняя рабочая версия" in message.content
        assert durable_events[-1].payload["tool"] == "rollback"
        assert "enqueue_preview" not in flow.trace
    else:
        assert run.status == "completed" and run.error is None
        assert owner.free_generations_used == 4 and len(snapshots) == 2
        snapshot = next(row for row in snapshots if row.id != flow.parent_id)
        assert project.current_snapshot_id == message.snapshot_id == snapshot.id
        assert repo.read_files(flow.project_id, snapshot.commit_sha) == changed
        assert flow.visible_publications == ["enqueue_preview", "snapshot.created"]
        assert message.content == "Calculation implemented." and message.tokens_out == 0
        assert (
            flow.trace.index("provider")
            < flow.trace.index("enqueue_preview")
            < flow.trace.index("snapshot.created")
            < flow.trace.index("llm.done")
        )
    assert repo.read_files(flow.project_id, flow.parent_sha) == {
        "README.md": "Preserve this source"
    }


async def test_identical_exact_edit_fails_without_snapshot_or_version(
    flow_factory, monkeypatch
):
    flow = await flow_factory("nextjs_entities")
    baseline_files = {"README.md": "Preserve this source"}
    error_cards: list[dict] = []

    async def noop(*_args, **_kwargs):
        return None

    async def empty(*_args, **_kwargs):
        return ""

    async def no_cell(**_kwargs):
        return None

    async def build(*_args, **_kwargs):
        return {"ok": True}

    async def runtime(*_args, **_kwargs):
        return {"ok": True, "status_code": 200}

    async def provider(**_kwargs):
        return agent_builder.AgentResult(
            done=False,
            summary="Step budget exhausted.",
            files=dict(baseline_files),
            steps=1,
            stop_reason="max_steps",
            needs_finalization=True,
        )

    async def record_error_card(*_args, **kwargs):
        error_cards.append(kwargs)

    async def structural_followup(**kwargs):
        return kwargs["prompt_text"], False, False, True

    async def byte_identical_finalization(**kwargs):
        assert kwargs["_is_edit"] is False
        return object(), dict(baseline_files), "Готово — правка применена и проверена."

    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", noop)
    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", no_cell)
    monkeypatch.setattr(orchestrator_client, "agent_list_dir", empty)
    monkeypatch.setattr(orchestrator_client, "agent_read_file", empty)
    monkeypatch.setattr(orchestrator_client, "agent_build", build)
    monkeypatch.setattr(orchestrator_client, "runtime_status", runtime)
    monkeypatch.setattr(orchestrator_client, "warm_routes", empty)
    monkeypatch.setattr(orchestrator_client, "hot_reload", noop)
    monkeypatch.setattr(agent_builder, "run_agent_build", provider)
    monkeypatch.setattr(agent_pipeline, "classify_agent_turn", structural_followup)
    monkeypatch.setattr(agent_pipeline, "finalize_max_candidate", byte_identical_finalization)
    monkeypatch.setattr(agent_pipeline.app_errors, "publish", record_error_card)

    await flow.run()

    run, project, message, owner, snapshots, _events = await flow.saved()
    assert run.status == "failed" and run.error == "edit produced no source changes"
    assert run.agent_state["product_outcome"] == {
        "status": "failed",
        "error": "edit produced no source changes",
    }
    assert len(snapshots) == 1 and project.current_snapshot_id == flow.parent_id
    assert message.snapshot_id is None and owner.free_generations_used == 3
    assert message.content == (
        "Не удалось применить правку: итоговый код не изменился. "
        "Повтори запрос или уточни, что именно нужно изменить."
    )
    assert "snapshot.created" not in flow.trace and "enqueue_preview" not in flow.trace
    assert error_cards == []
    assert repo.read_files(flow.project_id, flow.parent_sha) == baseline_files


async def test_real_native_candidate_red_restored_green_stays_failed(flow_factory, monkeypatch):
    flow = await flow_factory("nextjs_entities")
    settings = config.get_settings().model_copy(update={"use_native_agent": True})
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    set_generation_settings(monkeypatch, settings)
    runtime_files = {"README.md": "Preserve this source"}
    changed = {
        "README.md": "Changed by provider",
        "src/app/page.tsx": "export default function Page(){return <main>Candidate</main>}",
    }
    patches, build_results, provider_calls = [], [], []

    async def noop(*_args, **_kwargs):
        return None

    async def empty(*_args, **_kwargs):
        return ""

    async def build(*_args, **_kwargs):
        # Candidate fails the independent probe. Only the restored tree is green.
        restored = runtime_files == {"README.md": "Preserve this source"}
        build_results.append(restored)
        flow.trace.append("restored_build" if restored else "candidate_build")
        return {"ok": restored, "detail": "" if restored else "candidate typecheck red"}

    async def runtime(*_args, **_kwargs):
        flow.trace.append("candidate_runtime")
        return {"ok": False, "status_code": 500, "error": "candidate runtime red"}

    async def hot_reload(*args, **kwargs):
        patch = kwargs["files"] if "files" in kwargs else args[2]
        patches.append(dict(patch))
        flow.trace.append("restore_patch")
        for path, content in patch.items():
            if content:
                runtime_files[path] = content
            else:
                runtime_files.pop(path, None)
        return {"ok": True}

    async def provider(**kwargs):
        provider_calls.append(kwargs)
        assert kwargs["free"] is True and kwargs["run_id"] == str(flow.run_id)
        runtime_files.update(changed)
        await kwargs["emit"](
            "agent.step",
            {
                "step": 1,
                "action": "write_file",
                "path": "src/app/page.tsx",
                "detail": "Created candidate",
                "ok": True,
            },
        )
        return agent_builder.AgentResult(
            done=True,
            summary="The model claims completion.",
            files=dict(changed),
            steps=1,
            stop_reason="done",
        )

    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", noop)
    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", noop)
    monkeypatch.setattr(orchestrator_client, "agent_list_dir", empty)
    monkeypatch.setattr(orchestrator_client, "agent_read_file", empty)
    monkeypatch.setattr(orchestrator_client, "agent_build", build)
    monkeypatch.setattr(orchestrator_client, "runtime_status", runtime)
    monkeypatch.setattr(orchestrator_client, "hot_reload", hot_reload)
    monkeypatch.setattr(agent_native, "run_native_build", provider)
    await flow.run()
    run, project, message, owner, snapshots, events = await flow.saved()
    assert len(provider_calls) == 1 and build_results == [False, True]
    assert patches == [{"README.md": "Preserve this source"}, {"src/app/page.tsx": ""}]
    assert runtime_files == {"README.md": "Preserve this source"}
    assert run.status == "failed" and run.error == "final verification did not succeed"
    assert run.agent_state["product_outcome"] == {
        "status": "failed",
        "error": "final verification did not succeed",
    }
    assert len(snapshots) == 1 and owner.free_generations_used == 3
    assert project.current_snapshot_id == flow.parent_id and message.snapshot_id is None
    assert message.content == (
        "Сборка не завершена: финальная проверка не прошла. "
        "Незавершённые изменения отброшены; оставлена рабочая версия."
    )
    assert message.tokens_out == 0 and events[-1].payload["tool"] == "rollback"
    assert flow.trace.index("candidate_build") < flow.trace.index("restored_build")
    assert flow.trace.index("restored_build") < flow.trace.index("llm.done")
    assert flow.trace.count("llm.done") == 1
    assert not flow.visible_publications
    assert "snapshot.created" not in flow.trace and "enqueue_preview" not in flow.trace
    assert repo.read_files(flow.project_id, flow.parent_sha) == runtime_files


async def test_actual_worker_cancels_real_agent_build_without_replay(flow_factory, monkeypatch):
    """Dispatcher contract: a DB cancel stops the running build and nothing replays it."""
    flow = await flow_factory("nextjs_entities")
    factory = async_sessionmaker(flow.engine, expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, flow.run_id)
        run.execution_backend = "worker"
        store_generation_dispatch(
            run,
            GenerationDispatch(
                schema_version=1,
                project_id=flow.project_id,
                user_id=flow.owner_id,
                user_message_id=flow.user_message_id,
                assistant_message_id=flow.assistant_id,
                current_snapshot_id=flow.parent_id,
                prompt_text="Implement the requested working calculation module",
                model_id="fixture-model",
                force_model=None,
                is_free=True,
                free_business_id=None,
                orchestrate=True,
                selected_elements=[],
            ),
        )
        await session.commit()
    entered, provider_cancelled = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(**_kwargs):
        calls.append(llm_client._free_generation.get())
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            provider_cancelled.set()

    async def ready(*_args, **_kwargs):
        return None

    async def empty(*_args, **_kwargs):
        return ""

    async def no_cell(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_engine", lambda: flow.engine)
    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", ready)
    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", no_cell)
    monkeypatch.setattr(orchestrator_client, "agent_list_dir", empty)
    monkeypatch.setattr(orchestrator_client, "agent_read_file", empty)
    monkeypatch.setattr(agent_builder, "run_agent_build", provider)
    # Keep real execute_dispatch, ownership monitor, tracker and process. The
    # existing fixture's Redis watcher never fires; DB cancellation must work.
    work = asyncio.create_task(generation.execute_dispatch(flow.run_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not await generation.execute_dispatch(flow.run_id)
        async with factory() as session:
            run = await session.get(GenerationRun, flow.run_id)
            run.status = "cancel_requested"
            await session.commit()
        assert await asyncio.wait_for(work, 5)
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)
    run, project, message, owner, snapshots, events = await flow.saved()
    assert provider_cancelled.is_set() and calls == [True]
    assert run.execution_started_at is not None and run.status == "cancelled"
    assert run.finished_at is not None and message.tokens_out == 0
    assert project.current_snapshot_id == flow.parent_id and message.snapshot_id is None
    assert len(snapshots) == 1 and owner.free_generations_used == 3
    # Only the two runtime-readiness steps were recorded before the build was stopped.
    assert [event.payload["tool"] for event in events] == ["runtime", "runtime"]
    assert flow.trace.count("generation.cancelled") == 1
    assert "llm.done" not in flow.trace and "snapshot.created" not in flow.trace
    assert not flow.visible_publications
    assert not await generation.execute_dispatch(flow.run_id)
    assert calls == [True]
    assert repo.read_files(flow.project_id, flow.parent_sha) == {
        "README.md": "Preserve this source"
    }


async def test_real_agent_build_plan_survives_load_session_close(flow_factory, monkeypatch):
    from omnia_api.services import build_plan

    captured = []
    planner_calls = []
    expected_plan = {
        "summary": "Calculation screen",
        "screens": [
            {
                "route": "/calculation",
                "name": "Calculation",
                "purpose": "Compute a result",
                "primary_entity": None,
            }
        ],
        "entities": [],
        "capabilities": [],
        "acceptance": ["The result is visible"],
    }

    async def configured_factory(template):
        flow = await flow_factory(template)
        captured.append(flow)
        settings = config.get_settings().model_copy(update={"use_build_plan": True})
        monkeypatch.setattr(config, "get_settings", lambda: settings)
        set_generation_settings(monkeypatch, settings)
        factory = async_sessionmaker(flow.engine, expire_on_commit=False)
        async with factory() as session:
            project = await session.get(Project, flow.project_id)
            project.discovery_spec = {"contract_marker": "preserve-existing-discovery"}
            await session.commit()
        return flow

    async def plan(prompt, **kwargs):
        planner_calls.append((prompt, kwargs))
        return build_plan.BuildPlan(
            summary="Calculation screen",
            screens=(
                build_plan.Screen(
                    route="/calculation",
                    name="Calculation",
                    purpose="Compute a result",
                ),
            ),
            acceptance=("The result is visible",),
        )

    monkeypatch.setattr(build_plan, "plan_build", plan)
    await test_real_agent_publication_or_rollback_reaches_terminal_state(
        configured_factory,
        monkeypatch,
        stopped=False,
    )
    assert len(captured) == len(planner_calls) == 1
    flow = captured[0]
    assert planner_calls[0][1]["project_id"] == str(flow.project_id)
    assert planner_calls[0][1]["user_id"] == str(flow.owner_id)
    factory = async_sessionmaker(flow.engine, expire_on_commit=False)
    async with factory() as fresh_session:
        persisted = await fresh_session.get(Project, flow.project_id)
        assert persisted.discovery_spec == {
            "contract_marker": "preserve-existing-discovery",
            "build_plan": expected_plan,
        }


async def test_real_nonmax_selected_cell_owns_executor_and_final_probes(flow_factory, monkeypatch):
    flow = await flow_factory("nextjs_entities")
    selected = False
    calls = []
    files = {"README.md": "Preserve this source"}
    page = "export default function Page(){return <main>Calculation</main>}"

    async def legacy_boundary(*_args, **_kwargs):
        assert not selected, "legacy runtime accessed after selecting Project Cell"
        return ""

    async def forbidden_legacy_execute(_action):
        pytest.fail("provider received legacy execute after selecting Project Cell")

    async def unexpected(*_args, **_kwargs):
        pytest.fail("unexpected Cell operation in the bounded non-MAX success scenario")

    async def execute(action):
        assert selected
        calls.append(action.name)
        if action.name == "write_file":
            assert action.args == {"path": "src/app/page.tsx", "content": page}
            files[action.path] = action.args["content"]
            return {"ok": True}
        if action.name == "runtime_check":
            assert action.args == {"path": "/"}
            return {"ok": True, "status_code": 200}
        if action.name == "build":
            return {"ok": True}
        pytest.fail(f"unexpected Cell action: {action.name}")

    async def export():
        calls.append("export_files")
        return dict(files)

    async def release():
        calls.append("release")

    handle = project_cell_executor.ProjectCellExecutorHandle(
        execute=execute,
        sync_preview=unexpected,
        snapshot_files=export,
        stage_patch=unexpected,
        stage_files=unexpected,
        apply_external_files=unexpected,
        export_files=export,
        workspace_id=uuid4(),
        create_preview_session=unexpected,
        release=release,
    )

    async def select_cell(**kwargs):
        nonlocal selected
        assert kwargs["project_id"] == flow.project_id
        assert kwargs["generation_run_id"] == flow.run_id
        selected = True
        calls.append("selected")
        return handle

    async def provider(**kwargs):
        calls.append("provider")
        result = await kwargs["execute"](
            agent_builder.Action(
                name="write_file",
                args={"path": "src/app/page.tsx", "content": page},
            )
        )
        assert result["ok"] is True
        await kwargs["emit"](
            "agent.step",
            {
                "step": 1,
                "action": "write_file",
                "path": "src/app/page.tsx",
                "detail": "Wrote the calculation page",
                "ok": True,
            },
        )
        return agent_builder.AgentResult(
            done=True,
            summary="Calculation implemented.",
            files={"src/app/page.tsx": page},
            steps=1,
            stop_reason="done",
        )

    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", legacy_boundary)
    for name in (
        "agent_list_dir",
        "agent_read_file",
        "agent_build",
        "runtime_status",
        "warm_routes",
        "hot_reload",
        "get_status",
    ):
        monkeypatch.setattr(orchestrator_client, name, legacy_boundary)
    monkeypatch.setattr(
        agent_builder, "make_container_executor", lambda **_: forbidden_legacy_execute
    )
    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", select_cell)
    monkeypatch.setattr(agent_builder, "run_agent_build", provider)
    await flow.run()
    run, project, message, owner, snapshots, events = await flow.saved()
    assert run.status == "completed" and run.error is None
    assert calls == [
        "selected",
        "provider",
        "write_file",
        "export_files",
        "runtime_check",
        "build",
        "release",
    ]
    assert owner.free_generations_used == 4 and len(snapshots) == 2
    snapshot = next(row for row in snapshots if row.id != flow.parent_id)
    assert project.current_snapshot_id == message.snapshot_id == snapshot.id
    assert repo.read_files(flow.project_id, snapshot.commit_sha) == {
        "README.md": "Preserve this source",
        "src/app/page.tsx": page,
    }
    assert message.content == "Calculation implemented."
    assert flow.visible_publications == ["enqueue_preview", "snapshot.created"]
    assert len([kind for kind, _ in flow.events if kind == "llm.done"]) == 1
    assert any(event.payload["tool"] == "project_cell" for event in events)


async def test_real_terminal_cell_failure_stops_native_and_waits_release_without_snapshot(
    flow_factory, monkeypatch
):
    flow = await flow_factory("nextjs_entities")
    settings = config.get_settings().model_copy(update={"use_native_agent": True})
    set_generation_settings(monkeypatch, settings)
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    calls = []
    release_started = asyncio.Event()
    finish_release = asyncio.Event()

    async def forbidden(*args, **kwargs):
        pytest.fail("fatal infrastructure triggered another runtime/model/publication operation")

    async def snapshot():
        return {"README.md": "Preserve this source"}

    async def execute(action):
        calls.append(action.name)
        raise orchestrator_client.OrchestratorBadRequest(
            "private controller diagnostic",
            status_code=409,
            upstream_code="protected_environment_recovery_required",
        )

    async def release():
        calls.append("release_started")
        release_started.set()
        await finish_release.wait()
        calls.append("release_finished")

    handle = project_cell_executor.ProjectCellExecutorHandle(
        execute=execute,
        sync_preview=forbidden,
        snapshot_files=snapshot,
        stage_patch=forbidden,
        stage_files=forbidden,
        apply_external_files=forbidden,
        export_files=forbidden,
        workspace_id=uuid4(),
        create_preview_session=forbidden,
        release=release,
    )

    async def select_cell(**kwargs):
        return handle

    async def provider(*args, **kwargs):
        calls.append("model")
        return {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "read", "name": "read_file", "input": {"path": "a.ts"}},
                {"type": "tool_use", "id": "second", "name": "list_dir", "input": {"path": "."}},
            ],
        }

    async def before_selection_runtime(*args, **kwargs):
        assert not calls
        return ""

    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", before_selection_runtime)
    monkeypatch.setattr(orchestrator_client, "agent_list_dir", before_selection_runtime)
    monkeypatch.setattr(orchestrator_client, "agent_read_file", before_selection_runtime)
    monkeypatch.setattr(project_cell_executor, "maybe_create_project_cell_executor", select_cell)
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    work = asyncio.create_task(flow.run())
    try:
        await asyncio.wait_for(release_started.wait(), 5)
        assert not work.done()
        finish_release.set()
        await asyncio.wait_for(work, 5)
    finally:
        finish_release.set()
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)
    run, project, message, owner, snapshots, _events = await flow.saved()
    assert calls == ["model", "read_file", "release_started", "release_finished"]
    assert run.status == "failed" and run.error == "protected_environment_recovery_required"
    assert "private controller diagnostic" not in message.content
    assert project.current_snapshot_id == flow.parent_id and message.snapshot_id is None
    assert owner.free_generations_used == 3 and len(snapshots) == 1
    assert not flow.visible_publications and "llm.done" not in flow.trace
    assert repo.read_files(flow.project_id, flow.parent_sha) == {
        "README.md": "Preserve this source"
    }
