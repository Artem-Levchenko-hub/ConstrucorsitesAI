"""A nearly spent source window hands off the candidate without another paid request."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from yleum_api.services import agent_builder as ab
from yleum_api.services import llm_client


async def run_near_boundary(monkeypatch, *, gap=None, invalid_inventory=False,
                            cancel_snapshot=False, ship=True, changed=True, elapsed=240,
                            max_steps=4, max_segments=1, durable_tool_keys=None):
    started = datetime(2026, 11, 6, tzinfo=UTC)

    class Clock(datetime):
        current = started

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(ab, "datetime", Clock)
    calls, actions, state = [], [], {}

    async def complete(*args, **kwargs):
        calls.append(kwargs)
        assert len(calls) == 1, "A source finishing check must not redispatch the model"
        return ('<omnia:action name="write_file">'
                '{"path":"src/app/page.tsx","content":"product"}</omnia:action>')

    async def snapshot():
        if state and cancel_snapshot:
            raise asyncio.CancelledError
        return dict(state)

    async def execute(action):
        actions.append(action.name)
        if action.name == "write_file":
            state[action.path] = action.args["content"]
            if invalid_inventory:
                state["../outside"] = "invalid"
            Clock.current = started + timedelta(seconds=elapsed)
            return {"ok": True, "content": "product"}
        assert action.name == "build"
        raise TimeoutError("the check would outlast the edit window")

    driver = None
    if durable_tool_keys is not None:
        async def provider(frame, invoke):
            return await invoke()

        async def tool(key, action, invoke):
            durable_tool_keys.append(key)
            return await invoke()

        driver = SimpleNamespace(
            context={}, provider=provider, tool=tool, restore_frame=lambda kind: None,
        )

    monkeypatch.setattr(llm_client, "complete_chat", complete)
    result = await ab.run_agent_build(
        system_prompt="s", user_prompt="build the product", model="claude-sonnet-5.5",
        execute=execute, snapshot_files=snapshot,
        completion_check=lambda files, evidence: gap,
        edit_source_changed=lambda: changed,
        edit_deadline=started + timedelta(seconds=300),
        coordinator_handoff=True, require_green_before_done=True,
        ship_green_on_abort=ship, max_steps=max_steps, max_segments=max_segments,
        continuation=driver,
    )
    return result, calls, actions


@pytest.mark.parametrize("elapsed", [240, 300])
async def test_last_write_hands_off_without_an_extra_typecheck(monkeypatch, elapsed):
    result, calls, actions = await run_near_boundary(monkeypatch, elapsed=elapsed)

    assert len(calls) == 1
    assert actions == ["write_file"]
    assert not result.done and result.needs_finalization
    assert result.continuation_reason == "source_deadline"
    assert result.files == {"src/app/page.tsx": "product"}
    assert calls[0]["timeout_seconds"] == 300


@pytest.mark.parametrize("max_segments,elapsed", [(2, 240), (None, 240), (None, 300)])
async def test_last_write_can_handoff_at_a_segment_boundary(monkeypatch, max_segments, elapsed):
    result, calls, actions = await run_near_boundary(
        monkeypatch, max_steps=1, max_segments=max_segments, elapsed=elapsed,
    )
    assert len(calls) == 1
    assert actions == ["write_file"]
    assert not result.done and result.needs_finalization
    assert result.continuation_reason == "source_deadline"


async def test_durable_deadline_check_keeps_the_original_tool_cursor(monkeypatch):
    keys = []
    result, calls, actions = await run_near_boundary(
        monkeypatch, durable_tool_keys=keys,
    )
    assert keys == ["legacy-action", "legacy-deadline-build"]
    assert len(calls) == 1 and actions == ["write_file", "build"]
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "source_deadline"


async def test_checked_edit_still_hands_off_when_its_check_finishes_at_expiry(monkeypatch):
    started = datetime(2026, 11, 6, tzinfo=UTC)
    deadline = started + timedelta(minutes=5)
    current = started
    tree = {"src/app/page.tsx": "accepted"}
    replies = iter([
        '<omnia:action name="write_file">'
        '{"path":"src/app/page.tsx","content":"edited"}</omnia:action>',
        '<omnia:action name="build">{}</omnia:action>',
    ])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current

    async def complete(*args, **kwargs):
        return next(replies)

    async def execute(action):
        nonlocal current
        if action.name == "write_file":
            tree[action.path] = action.args["content"]
            current = started + timedelta(seconds=160)
            return {"ok": True, "content": tree[action.path]}
        assert action.name == "build"
        current = deadline
        return {"ok": True}

    monkeypatch.setattr(ab, "datetime", Clock)
    monkeypatch.setattr(llm_client, "complete_chat", complete)
    result = await ab.run_agent_build(
        system_prompt="s", user_prompt="edit the product", model="claude-sonnet-5.5",
        execute=execute, snapshot_files=lambda: asyncio.sleep(0, result=dict(tree)),
        edit_source_changed=lambda: tree["src/app/page.tsx"] == "edited",
        edit_mode=True, edit_deadline=deadline, coordinator_handoff=True,
        require_green_before_done=True, max_steps=4,
    )
    assert result.done and result.needs_finalization
    assert result.stop_reason == "done_on_green"


async def test_managed_gap_hands_off_to_repair_when_the_source_window_ends(monkeypatch):
    result, calls, actions = await run_near_boundary(
        monkeypatch, gap=ab.ManagedSourceGap("Connect catalog to the active screen"),
    )
    assert len(calls) == 1 and actions == ["write_file"]
    assert not result.done and result.needs_finalization
    assert result.continuation_reason == "source_deadline"


@pytest.mark.parametrize("options", [
    {"gap": "Connect the requested catalog before accepting."},
    {"invalid_inventory": True},
    {"ship": False},
    {"changed": False},
])
async def test_final_window_does_not_handoff_unverified_source(monkeypatch, options):
    result, calls, _ = await run_near_boundary(monkeypatch, **options)
    assert len(calls) == 1
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "source_deadline"


async def test_source_snapshot_cancellation_propagates(monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        await run_near_boundary(monkeypatch, cancel_snapshot=True)


async def test_short_initial_window_cannot_accept_a_green_starter(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("No model/check is allowed without attributable product source")

    monkeypatch.setattr(llm_client, "complete_chat", forbidden)
    result = await ab.run_agent_build(
        system_prompt="s", user_prompt="build", model="claude-sonnet-5.5",
        execute=forbidden, snapshot_files=lambda: asyncio.sleep(0, result={}),
        completion_check=lambda *_: None, coordinator_handoff=True,
        ship_green_on_abort=True, edit_deadline=datetime.now(UTC) + timedelta(seconds=22),
        max_steps=3, max_segments=1,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "source_deadline"
