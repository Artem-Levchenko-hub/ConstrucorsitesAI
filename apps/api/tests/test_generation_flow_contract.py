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
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from yleum_api.core import config, db
from yleum_api.models.generation_event import GenerationEvent
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.restoration import Restoration
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services import (
    agent_builder,
    project_cell_executor,
    repo,
)
from yleum_api.services.generation import (
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
from yleum_api.services.generation import lifecycle as messages


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
    assert "snapshot.created" not in flow.trace
    assert "llm.done" not in flow.trace
    assert flow.trace.count("generation.cancelled") == int(cancel)
    assert flow.trace.count("llm.error") == int(not cancel)
    assert flow.trace.index("release_finished") < flow.trace.index("clear_stream")
    if cancel:
        assert flow.trace.index("release_finished") < flow.trace.index("generation.cancelled")
    else:
        assert run.error == "fixture managed SDK unavailable"
        assert "fixture managed SDK unavailable" in message.content


async def test_real_process_prompt_hands_uncertain_activation_to_late_reconciler(
    flow_factory,
    monkeypatch,
):
    from datetime import UTC, datetime

    from yleum_api.services.generation.agent_finalization import (
        AdaptationActivationPending,
    )
    from yleum_api.services.generation_runs import terminalize_generation_run_locked

    flow = await flow_factory("max_miniapp")
    factory = async_sessionmaker(flow.engine, expire_on_commit=False)
    operation_id = uuid4()

    async def pending_pipeline(*_args, **kwargs):
        ids = kwargs["ids"]
        pipeline_factory = kwargs["factory"]
        async with pipeline_factory() as session:
            run = await session.get(GenerationRun, ids.run_id, with_for_update=True)
            assert run is not None
            workspace = ProjectCellWorkspace(
                project_id=flow.project_id,
                owner_id=flow.owner_id,
                provider="docker_owner_canary",
                state="ready",
                generation_run_id=flow.run_id,
                fencing_epoch=11,
            )
            session.add(workspace)
            await session.flush()
            pending = Restoration(
                id=operation_id,
                project_id=flow.project_id,
                owner_id=flow.owner_id,
                workspace_id=workspace.id,
                source_version_id=uuid4(),
                source_snapshot_id=flow.parent_id,
                base_draft_snapshot_id=flow.parent_id,
                target_commit_sha=flow.parent_sha,
                base_commit_sha=flow.parent_sha,
                idempotency_key=f"forward-activation-{flow.run_id}",
                request_digest="b" * 64,
                selected_branch="adaptive",
                adaptation_run_id=flow.run_id,
                state="reconciling",
                phase="activation_status",
                revision=3,
                fencing_epoch=workspace.fencing_epoch,
                request_payload={},
                activation_request={"offer": {"activation_id": str(uuid4())}},
                error="apply timeout; status timeout",
            )
            session.add(pending)
            run.agent_state = {
                **(run.agent_state or {}),
                "restoration_adaptation": {
                    "operation_id": str(pending.id),
                    "adaptation_run_id": str(run.id),
                },
                "max_finalization": {
                    "restoration_adaptation_proof": {"state": "proof_ready"}
                },
                "restoration_adaptation_owner_status": "sealed_proof_retained",
            }
            await session.commit()
        raise AdaptationActivationPending(
            "adaptation activation is awaiting reconciliation"
        )

    statuses: list[str] = []
    real_status = supervisor.set_generation_run_status

    async def tracked_status(run_id, new_status, **kwargs):
        statuses.append(new_status)
        await real_status(run_id, new_status, **kwargs)

    monkeypatch.setattr(lifecycle, "run_agent_generation", pending_pipeline)
    monkeypatch.setattr(supervisor, "set_generation_run_status", tracked_status)
    monkeypatch.setattr(lifecycle, "set_generation_run_status", tracked_status)

    await flow.run()

    run, _project, message, _owner, _snapshots, _durable_events = await flow.saved()
    async with factory() as session:
        pending = await session.get(Restoration, operation_id)
    assert statuses == ["running"]
    assert run.status == "running" and run.finished_at is None and run.error is None
    assert pending is not None and pending.state == "reconciling"
    assert pending.phase == "activation_status"
    assert message.content == "" and message.tokens_out is None
    assert "llm.error" not in flow.trace

    # A later controller receipt can still win the canonical transaction: the
    # process/tracker did not terminalize or poison either durable owner.
    async with factory() as session:
        late_run = await session.get(GenerationRun, flow.run_id, with_for_update=True)
        late_operation = await session.get(Restoration, operation_id, with_for_update=True)
        late_message = await session.get(Message, flow.assistant_id, with_for_update=True)
        assert late_run is not None and late_operation is not None and late_message is not None
        late_operation.state = "completed"
        late_operation.phase = "activation_complete"
        late_operation.error = None
        late_operation.activation_settled_at = datetime.now(UTC)
        late_run.agent_state = {
            **(late_run.agent_state or {}),
            "restoration_adaptation_activation": {
                "state": "completed",
                "operation_id": str(late_operation.id),
                "publication_consumed": True,
            },
        }
        late_message.content = "Готово — версия адаптирована и восстановлена."
        late_message.tokens_in = late_message.tokens_in or 0
        late_message.tokens_out = late_message.tokens_out or 0
        await terminalize_generation_run_locked(session, late_run, status="completed")
        await session.commit()

    run, _project, message, _owner, _snapshots, _durable_events = await flow.saved()
    assert run.status == "completed" and run.error is None
    assert message.content.startswith("Готово") and message.tokens_out == 0
    assert "failed" not in statuses and "llm.error" not in flow.trace
