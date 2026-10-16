"""Progressive continuation uses real source snapshots, never model counters."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services import agent_builder, agent_native
from yleum_api.services.agent_builder import AgentResult


async def test_legacy_write_floor_preserves_the_recovery_observer(monkeypatch):
    from tests.test_generation_recovery_policy import binding
    from yleum_api.services.generation import agent_generation
    from yleum_api.services.generation.contracts import GenerationRuntime
    from yleum_api.services.generation.recovery_contract import RecoveryDirective

    monkeypatch.setattr(agent_generation, "get_settings", lambda: SimpleNamespace(
        use_agentic_builder=True, use_native_agent=False,
        agent_require_green_before_done=False, agent_ship_green_on_abort=False))
    replies = iter([
        '<omnia:action name="write_file">{"path":"app.ts","content":"saved"}</omnia:action>',
        '<omnia:action name="done">{}</omnia:action>',
    ])

    async def provider(*args, **kwargs):
        return next(replies)

    monkeypatch.setattr(agent_builder.llm_client, "complete_chat", provider)
    observer = AsyncMock(return_value=RecoveryDirective(action="needs_input", reason="shadow"))
    result, files, _ = await agent_generation.complete_empty_legacy_build(
        _agent_res=AgentResult(done=True, summary="empty", files={}, steps=1),
        _is_edit=False, _max_seed_files={},
        ids=SimpleNamespace(user_id=uuid4(), project_id=uuid4()), runtime=GenerationRuntime(),
        plan=SimpleNamespace(stack_system="s", seed_context="", model="m",
                             escalate_model=None, steps=3),
        operations=SimpleNamespace(execute=AsyncMock(return_value={"ok": True, "content": "saved"}),
            emit=AsyncMock(), snapshot_files=None, recovery=observer,
            recovery_binding=AsyncMock(return_value=binding())),
    )
    assert result.done and files == {"app.ts": "saved"}
    assert observer.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_real_loops_keep_shadow_directives_and_provider_count(monkeypatch, native, cancelled):
    from tests.test_agent_native import _turn
    from tests.test_generation_recovery_policy import binding
    from yleum_api.core.config import get_settings
    from yleum_api.services.generation.recovery_contract import RecoveryDirective

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unit:unit@127.0.0.1:1/unit")
    monkeypatch.setenv("JWT_SECRET", "unit-test-no-live-credentials")
    get_settings.cache_clear()
    actions = iter([("read_file", {"path": "src/app.ts"}), ("build", {}),
                    ("done", {"summary": "finished"})])
    provider_calls = 0
    effects = []
    observations = []

    async def provider(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        name, arguments = next(actions)
        if native:
            return _turn((name, arguments))
        return f'<omnia:action name="{name}">{json.dumps(arguments)}</omnia:action>'

    async def execute(action):
        effects.append(action.name)
        return {"ok": True, "detail": "clean", "content": "source"}

    async def observer(observation):
        observations.append(observation)
        if cancelled:
            raise asyncio.CancelledError
        return RecoveryDirective(action="needs_input", reason="shadow only")

    async def current_binding():
        return binding()

    common = dict(execute=execute, recovery=observer, recovery_binding=current_binding,
                  max_steps=4)

    async def invoke():
        if native:
            monkeypatch.setattr(agent_native, "_call_messages", provider)
            return await agent_native.run_native_build(system="s", task="t", **common)
        return await agent_builder.run_agent_build(system_prompt="s", user_prompt="t",
            model="m", complete=provider, **common)

    try:
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await invoke()
            assert provider_calls == 1 and effects == ["read_file"]
        else:
            assert (await invoke()).done
            assert provider_calls == 3 and effects == ["read_file", "build"]
        assert len(observations) == len(effects)
        assert all(o.kind == "tool" and o.witness is None for o in observations)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("action", ["needs_input", "yield", "reset_context"])
async def test_recovery_observer_is_shared_but_directives_stay_shadow(monkeypatch, native, action):
    from tests.test_generation_recovery_policy import binding
    from yleum_api.services.agent_builder import Action
    from yleum_api.services.generation.recovery_contract import RecoveryDirective

    observed = []
    effects = []

    async def execute(tool):
        effects.append(tool.name)
        return {"ok": True, "content": "secret raw output is not recovery evidence"}

    async def recovery(observation):
        observed.append(observation)
        return RecoveryDirective(action=action, reason="shadow only")

    async def current_binding():
        return binding()

    async def segment(**kwargs):
        await kwargs["execute"](Action("read_file", {"path": "src/app.ts"}))
        return AgentResult(done=True, summary="unchanged result", files={}, steps=1)

    module = agent_native if native else agent_builder
    monkeypatch.setattr(module, "_run_native_segment" if native else "_run_agent_segment", segment)
    common = dict(execute=execute, recovery=recovery, recovery_binding=current_binding)
    result = (await agent_native.run_native_build(system="s", task="t", **common) if native
              else await agent_builder.run_agent_build(
                  system_prompt="s", user_prompt="t", model="m", **common))
    assert result.done and result.summary == "unchanged result"
    assert effects == ["read_file"] and len(observed) == 1
    assert observed[0].kind == "tool" and observed[0].binding == binding()
    assert observed[0].witness is None
    assert "secret raw" not in observed[0].model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("failure", ["executor", "observer", "cancelled", "missing_binding"])
async def test_shadow_observation_preserves_executor_errors_and_cancellation(
    monkeypatch, native, failure,
):
    from tests.test_generation_recovery_policy import binding
    from yleum_api.services.agent_builder import Action

    effects = []

    async def execute(tool):
        effects.append(tool.name)
        if failure == "executor":
            raise RuntimeError("original executor error")
        return {"ok": True}

    async def recovery(observation):
        if failure == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("observer unavailable")

    async def current_binding():
        return binding()

    async def segment(**kwargs):
        await kwargs["execute"](Action("read_file", {}))
        return AgentResult(done=True, summary="unchanged", files={}, steps=1)

    module = agent_native if native else agent_builder
    monkeypatch.setattr(module, "_run_native_segment" if native else "_run_agent_segment", segment)
    common = dict(execute=execute, recovery=recovery,
                  recovery_binding=None if failure == "missing_binding" else current_binding)

    async def invoke():
        if native:
            return await agent_native.run_native_build(system="s", task="t", **common)
        return await agent_builder.run_agent_build(
            system_prompt="s", user_prompt="t", model="m", **common)

    if failure in {"executor", "cancelled"}:
        with pytest.raises(RuntimeError if failure == "executor" else asyncio.CancelledError):
            await invoke()
    else:
        assert (await invoke()).done
    assert effects == ["read_file"]


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "behavior,expected_calls,expected_stop",
    [
        ("productive", 11, "done"),
        ("budget_handoff", 11, "done"),
        ("cycle", 2, "no_progress"),
        ("claimed_files_only", 1, "no_progress"),
        ("terminal", 1, "provider_error"),
    ],
)
async def test_progressive_real_wrapper(
    monkeypatch, native, behavior, expected_calls, expected_stop
):
    files = {"src/app/page.tsx": "A"}
    calls = 0

    async def snapshot():
        return dict(files)

    async def segment(**kwargs):
        nonlocal calls
        calls += 1
        if behavior in {"productive", "budget_handoff"}:
            files["src/app/page.tsx"] = f"feature-{calls}"
        elif behavior == "cycle":
            files["src/app/page.tsx"] = "B" if calls % 2 else "A"
        result = AgentResult(
            done=(behavior == "productive" and calls == 11) or behavior == "budget_handoff",
            summary="candidate",
            files={f"claimed-{calls}.tsx": "same"},
            steps=40,
            stop_reason="provider_error"
            if behavior == "terminal"
            else "done"
            if behavior in {"productive", "budget_handoff"} and calls == 11
            else "max_steps_green"
            if behavior == "budget_handoff" and native
            else "done_on_green"
            if behavior == "budget_handoff"
            else "max_steps",
            evidence={"build_after_write": calls},
        )
        if behavior == "budget_handoff" and calls < 11:
            result.needs_finalization = True
            result.continuation_reason = "max_steps"
        if not native:
            await kwargs["on_progress"](result)
        return result

    common = dict(
        execute=None,
        max_steps=40,
        max_segments=None,
        snapshot_files=snapshot,
        edit_deadline=datetime.now(UTC) + timedelta(minutes=10),
    )
    if native:
        monkeypatch.setattr(agent_native, "_run_native_segment", segment)
        result = await agent_native.run_native_build(system="s", task="t", **common)
    else:
        monkeypatch.setattr(agent_builder, "_run_agent_segment", segment)
        result = await agent_builder.run_agent_build(
            system_prompt="s", user_prompt="t", model="m", **common
        )
    assert calls == expected_calls
    assert result.stop_reason == expected_stop
    if behavior in {"productive", "budget_handoff"}:
        assert result.done and result.steps == 440


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_progressive_requires_deadline_and_actual_snapshot(native):
    kwargs = dict(execute=None, max_segments=None)
    call = (
        (lambda: agent_native.run_native_build(system="s", task="t", **kwargs))
        if native
        else (
            lambda: agent_builder.run_agent_build(
                system_prompt="s", user_prompt="t", model="m", **kwargs
            )
        )
    )
    with pytest.raises(ValueError, match=r"deadline.*snapshot"):
        await call()


def test_source_novelty_rejects_cycles_but_accepts_new_verified_receipt():
    from yleum_api.services.generation.continuation import SourceNoveltyTracker

    tracker = SourceNoveltyTracker({"a": "A"})
    assert tracker.observe({"a": "B"})
    assert not tracker.observe({"a": "A"})
    assert tracker.observe({"a": "A"}, verified_receipts=frozenset({"server-proof-1"}))
    assert not tracker.observe({"a": "A"}, verified_receipts=frozenset({"server-proof-1"}))
    assert tracker.observe({})  # Deletion is an actual source-state change.
    assert not tracker.observe({"a": "B"})


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("case", ["expired", "between_segments", "snapshot_error", "cancelled"])
async def test_progressive_stops_before_another_segment(monkeypatch, native, case):
    calls = reads = 0
    deadline = datetime.now(UTC) + timedelta(seconds=-1 if case == "expired" else 60)

    async def snapshot():
        nonlocal reads
        reads += 1
        if reads > 1 and case == "snapshot_error":
            raise RuntimeError("snapshot unavailable")
        if reads > 1 and case == "cancelled":
            raise asyncio.CancelledError
        return {"page.tsx": str(reads)}

    class Clock:
        @staticmethod
        def now(tz):
            return deadline + timedelta(seconds=1 if calls else -1)

    async def segment(**kwargs):
        nonlocal calls
        calls += 1
        result = AgentResult(
            done=False,
            summary="candidate",
            files={"page.tsx": str(calls)},
            steps=40,
            stop_reason="max_steps",
        )
        if not native:
            await kwargs["on_progress"](result)
        return result

    module = agent_native if native else agent_builder
    monkeypatch.setattr(module, "_run_native_segment" if native else "_run_agent_segment", segment)
    if case == "between_segments":
        monkeypatch.setattr(module, "datetime", Clock)
    common = dict(execute=None, max_segments=None, snapshot_files=snapshot, edit_deadline=deadline)

    async def invoke():
        if native:
            return await agent_native.run_native_build(system="s", task="t", **common)
        return await agent_builder.run_agent_build(
            system_prompt="s",
            user_prompt="t",
            model="m",
            **common,
        )

    if case in {"snapshot_error", "cancelled"}:
        with pytest.raises(RuntimeError if case == "snapshot_error" else asyncio.CancelledError):
            await invoke()
    else:
        result = await invoke()
        assert not result.done
        assert result.stop_reason == ("finalization_reserve" if native else "source_deadline")
    assert calls == 1  # First segment retains its existing deadline/handoff logic.


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_progressive_expired_native_retains_actual_checked_handoff(monkeypatch, complete):
    from yleum_api.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unit:unit@127.0.0.1:1/unit")
    monkeypatch.setenv("JWT_SECRET", "unit-test-no-live-credentials")
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    get_settings.cache_clear()
    provider = AsyncMock()
    snapshot = AsyncMock()
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    try:
        result = await agent_native.run_native_build(
            system="MAX VERIFICATION OVERRIDE",
            task="finish",
            execute=AsyncMock(),
            portable_cell=True,
            initial_files={"src/app/page.tsx": "candidate"},
            completion_check=lambda files, evidence: None if complete else "missing product",
            edit_deadline=datetime.now(UTC) - timedelta(seconds=1),
            max_segments=None,
            snapshot_files=snapshot,
        )
        assert result.needs_finalization is complete
        assert not result.done
        provider.assert_not_awaited()
        snapshot.assert_not_awaited()
    finally:
        get_settings.cache_clear()
