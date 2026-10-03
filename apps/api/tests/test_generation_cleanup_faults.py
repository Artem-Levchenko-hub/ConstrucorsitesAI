"""Execute real dispatcher/lifecycle coroutines with deterministic I/O faults."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.services.generation import lifecycle, supervisor
from yleum_api.services.generation.agent_finalization import AdaptationActivationPending
from yleum_api.workers import generation

pytestmark = pytest.mark.asyncio


class Session:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, model, key, **_):
        return self.rows.get((model, key))

    async def commit(self):
        pass


async def test_ownership_fault_drains_work_before_blocked_orphan_write(monkeypatch):
    run_id, project_id, owner_id, user_id, assistant_id = (uuid4() for _ in range(5))
    run = SimpleNamespace(execution_backend="worker", status="queued_for_capacity",
                          execution_started_at=None, user_id=owner_id, project_id=project_id,
                          user_message_id=user_id, assistant_message_id=assistant_id)
    dispatch = SimpleNamespace(project_id=project_id, user_id=owner_id, user_message_id=user_id,
                               assistant_message_id=assistant_id, current_snapshot_id=None,
                               prompt_text="synthetic", model_id="synthetic", force_model=None,
                               is_free=False, orchestrate=True, selected_elements=[])
    rows = {(GenerationRun, run_id): run,
            (Project, project_id): SimpleNamespace(owner_id=owner_id),
            (Message, user_id): SimpleNamespace(project_id=project_id, role="user"),
            (Message, assistant_id): SimpleNamespace(project_id=project_id, role="assistant")}

    class Ownership(Session):
        async def scalar(self, *_):
            return True

        async def execute(self, *_):
            return None

    engine = SimpleNamespace(connect=lambda: Ownership({}))
    monkeypatch.setattr(generation, "get_engine", lambda: engine)
    monkeypatch.setattr(generation, "async_sessionmaker", lambda *_a, **_k: lambda: Session(rows))
    monkeypatch.setattr(generation, "load_generation_dispatch", lambda _: dispatch)
    entered, cancelling, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finish_cleanup, orphan_started = asyncio.Event(), asyncio.Event()
    finish_orphan = asyncio.Event()

    async def work(**_):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelling.set()
            await finish_cleanup.wait()
            drained.set()

    async def no_signal(*_):
        await asyncio.Future()

    async def no_io(*_):
        return None

    async def monitor(*_):
        await entered.wait()
        raise RuntimeError("synthetic ownership connection lost")

    async def orphan(*_):
        orphan_started.set()
        await finish_orphan.wait()  # Persistence cannot progress while DB is unavailable.

    monkeypatch.setattr(lifecycle, "_process_prompt", work)
    # Keep the real tracker: prove it forwards cancellation to the real work
    # before any orphan write. Only Redis/database side effects are doubled.
    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", no_signal)
    monkeypatch.setattr(supervisor, "_wait_for_capacity_dispatch_lease_loss", no_signal)
    monkeypatch.setattr(supervisor, "_finalize_cancelled_generation", no_io)
    monkeypatch.setattr(supervisor, "clear_generation_cancel", no_io)
    monkeypatch.setattr(generation, "_ownership_monitor", monitor)
    monkeypatch.setattr(generation, "_fail_orphan", orphan)
    task = asyncio.create_task(generation.execute_dispatch(run_id))
    waiters = [asyncio.create_task(event.wait()) for event in (cancelling, orphan_started)]
    try:
        await asyncio.wait_for(asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED), 1)
        assert cancelling.is_set(), "lost-ownership work remains alive behind orphan DB write"
        assert not orphan_started.is_set(), "persistence started before work cleanup drained"
        finish_cleanup.set()
        await asyncio.wait_for(orphan_started.wait(), 1)
        assert drained.is_set()
        finish_orphan.set()
        assert await asyncio.wait_for(task, 1) is False
    finally:
        finish_cleanup.set()
        finish_orphan.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, *waiters, return_exceptions=True)


@pytest.mark.parametrize(
    "primary",
    ["cancelled", "activation_pending", "completed", "cancel_during_release", "release_failed"],
)
async def test_failed_deadline_still_releases_cell_and_clears_stream(monkeypatch, primary):
    from yleum_api.services import restoration_adaptation

    project_id, run_id, user_id, user_message_id, assistant_id = (uuid4() for _ in range(5))
    rows = {(Project, project_id): SimpleNamespace(template="max_miniapp", slug="synthetic",
             name="Synthetic", design_preset_id=None, image_gen_enabled=False,
             discovery_spec=None, language="ru", source="native")}
    monkeypatch.setattr(lifecycle, "get_engine", lambda: object())
    monkeypatch.setattr(lifecycle, "async_sessionmaker", lambda *_a, **_k: lambda: Session(rows))
    monkeypatch.setattr(lifecycle, "get_settings", lambda: SimpleNamespace(
        use_project_memory=False, use_agentic_builder=True, agentic_builder_canary_users=""))

    async def context(*_):
        return ""

    monkeypatch.setattr(restoration_adaptation, "append_adaptation_context", context)
    trace = []
    release_started, finish_release = asyncio.Event(), asyncio.Event()
    original = AdaptationActivationPending("synthetic sealed activation")

    class Handle:
        async def release(self):
            if primary == "cancel_during_release":
                release_started.set()
                await finish_release.wait()
            if primary == "release_failed":
                trace.append("release_attempted")
                raise RuntimeError("synthetic release failure")
            trace.append("released")

    async def agent(**kwargs):
        runtime = kwargs["runtime"]
        runtime.handle = Handle()
        runtime.deadline_task = asyncio.get_running_loop().create_future()
        runtime.deadline_task.set_exception(RuntimeError("synthetic watchdog database error"))
        if primary == "cancelled":
            raise asyncio.CancelledError("synthetic primary cancellation")
        if primary in {"activation_pending", "release_failed"}:
            raise original

    async def clear(*_):
        trace.append("cleared")

    monkeypatch.setattr(lifecycle, "run_agent_generation", agent)
    monkeypatch.setattr(lifecycle, "clear_stream_state", clear)
    coro = lifecycle._process_prompt(run_id, project_id, user_id, user_message_id,
                                    assistant_id, None, "synthetic", "synthetic")
    if primary == "cancelled":
        with pytest.raises(asyncio.CancelledError, match="synthetic primary cancellation"):
            await coro
    elif primary in {"activation_pending", "release_failed"}:
        with pytest.raises(AdaptationActivationPending) as caught:
            await coro
        assert caught.value is original
    elif primary == "cancel_during_release":
        task = asyncio.create_task(coro)
        await asyncio.wait_for(release_started.wait(), 1)
        try:
            task.cancel("synthetic cancellation during release")
            await asyncio.sleep(0)
            assert not task.done(), "physical release must drain before cancellation completes"
            finish_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            finish_release.set()
            await asyncio.gather(task, return_exceptions=True)
    else:
        await coro
    assert trace == (["release_attempted", "cleared"] if primary == "release_failed"
                     else ["released", "cleared"])
