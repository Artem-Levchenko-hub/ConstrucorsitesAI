"""Progressive continuation uses real source snapshots, never model counters."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from yleum_api.services import agent_builder, agent_native
from yleum_api.services.agent_builder import AgentResult


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
