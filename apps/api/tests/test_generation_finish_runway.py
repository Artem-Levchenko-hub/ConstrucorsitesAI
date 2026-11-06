"""A nearly spent source window verifies once, without another paid request."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from yleum_api.services import agent_builder as ab
from yleum_api.services import llm_client


async def run_near_boundary(monkeypatch, *, observation=None, gap=None, mutate=False,
                            cancel=False, check_timeout=False, ship=True, changed=True):
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
        return dict(state)

    async def execute(action):
        actions.append(action.name)
        if action.name == "write_file":
            state[action.path] = action.args["content"]
            Clock.current = started + timedelta(seconds=240)
            return {"ok": True, "content": "product"}
        assert action.name == "build"
        if cancel:
            raise asyncio.CancelledError
        if check_timeout:
            raise TimeoutError
        if mutate:
            state["src/app/page.tsx"] = "unverified source change"
        return observation if observation is not None else {"ok": True}

    monkeypatch.setattr(llm_client, "complete_chat", complete)
    result = await ab.run_agent_build(
        system_prompt="s", user_prompt="build the product", model="claude-sonnet-5.5",
        execute=execute, snapshot_files=snapshot,
        completion_check=lambda files, evidence: gap,
        edit_source_changed=lambda: changed,
        edit_deadline=started + timedelta(seconds=300),
        coordinator_handoff=True, require_green_before_done=True,
        ship_green_on_abort=ship, max_steps=4, max_segments=1,
    )
    return result, calls, actions


async def test_final_window_hands_fresh_checked_source_to_normal_finalization(monkeypatch):
    result, calls, actions = await run_near_boundary(monkeypatch)
    assert len(calls) == 1 and actions == ["write_file", "build"]
    assert result.done and result.needs_finalization
    assert result.stop_reason == "done_on_green"
    assert result.continuation_reason == "source_deadline"
    assert result.files == {"src/app/page.tsx": "product"}
    # The already-admitted model call keeps its ordinary full response window.
    assert calls[0]["timeout_seconds"] == 300


@pytest.mark.parametrize("options", [
    {"observation": {"ok": False}},
    {"observation": {"ok": "true"}},
    {"gap": "Connect the requested catalog before accepting."},
    {"mutate": True},
    {"check_timeout": True},
    {"ship": False},
    {"changed": False},
])
async def test_final_window_cannot_promote_unverified_or_incomplete_source(monkeypatch, options):
    result, calls, _ = await run_near_boundary(monkeypatch, **options)
    assert len(calls) == 1
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "source_deadline"


async def test_final_check_cancellation_propagates(monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        await run_near_boundary(monkeypatch, cancel=True)


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
