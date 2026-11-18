"""Prompt progress follows a real claimed call; all providers and storage are inert."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core import config
from yleum_api.services import prompt_preparation as prep
from yleum_api.services.generation.contracts import GenerationIds
from yleum_api.services.generation.progress import GenerationProgress, _is_action


class Session:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.fixture
def claimed(monkeypatch):
    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(prompt_preparation_enabled=True),
    )
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    claim = {"claim_id": str(uuid4()), "messages": [{"role": "user", "content": "PRIVATE"}]}
    admitted = AsyncMock(side_effect=lambda *_: (dict(claim), ""))
    write = AsyncMock(return_value=True)
    monkeypatch.setattr(prep, "_claim", admitted)
    monkeypatch.setattr(prep, "_write", write)
    session = Session()
    # Use the real event-to-transcript adapter, without invoking DB or pubsub.
    journal = object.__new__(GenerationProgress)
    journal.record_agent_step = AsyncMock()
    return SimpleNamespace(
        ids=ids, claim=admitted, write=write, session=session, factory=lambda: session,
        progress=journal,
        rows=lambda: [call.args[0] for call in journal.record_agent_step.await_args_list],
    )


@pytest.mark.asyncio
async def test_pending_state_covers_only_the_actual_claimed_request(claimed):
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return "  Prepared brief  "

    task = asyncio.create_task(prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=complete,
        emit_progress=claimed.progress.emit_agent_event,
    ))
    await entered.wait()
    assert [row["action"] for row in claimed.rows()] == ["Подготавливаю промпт"]
    assert claimed.rows()[0]["ok"] is None
    claimed.write.assert_not_awaited()
    release.set()
    assert await task == "Prepared brief"
    assert [row["action"] for row in claimed.rows()] == [
        "Подготавливаю промпт", "Промпт подготовлен",
    ]
    assert claimed.rows()[-1]["ok"] is True
    assert all(row["kind"] == "heartbeat" and row["tool"] == "prompt_preparation"
               and not _is_action(row) for row in claimed.rows())
    claimed.write.assert_awaited_once()
    claimed.session.commit.assert_awaited_once()
    assert claimed.write.await_args.args[2]["result"] == "Prepared brief"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", ["", "Cached brief"])
async def test_cached_or_previously_claimed_input_has_no_request_state(claimed, cached):
    claimed.claim.side_effect = None
    claimed.claim.return_value = (None, cached)
    complete = AsyncMock(side_effect=AssertionError("no provider request"))
    assert await prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=complete,
        emit_progress=claimed.progress.emit_agent_event,
    ) == cached
    complete.assert_not_awaited()
    claimed.progress.record_agent_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_preparation_has_no_claim_call_or_progress(claimed, monkeypatch):
    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(prompt_preparation_enabled=False),
    )
    complete = AsyncMock()
    assert await prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=complete,
        emit_progress=claimed.progress.emit_agent_event,
    ) == ""
    claimed.claim.assert_not_awaited()
    complete.assert_not_awaited()
    claimed.progress.record_agent_step.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["provider_error", "empty", "oversize", "lost_claim", "save_error"],
)
async def test_optional_failure_finishes_with_original_request_without_retry(claimed, outcome):
    complete = AsyncMock(return_value="Prepared brief")
    if outcome == "provider_error":
        complete.side_effect = RuntimeError("PRIVATE_PROVIDER_BODY")
    elif outcome == "empty":
        complete.return_value = "  "
    elif outcome == "oversize":
        complete.return_value = "x" * (prep.MAX_RESULT + 1)
    elif outcome == "lost_claim":
        claimed.write.return_value = False
    elif outcome == "save_error":
        claimed.write.side_effect = RuntimeError("PRIVATE_DATABASE_BODY")
    assert await prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=complete,
        emit_progress=claimed.progress.emit_agent_event,
    ) == ""
    complete.assert_awaited_once()
    rows = claimed.rows()
    assert [row["action"] for row in rows] == [
        "Подготавливаю промпт", "Продолжаю по исходному запросу",
    ]
    assert rows[-1]["ok"] is False
    assert "PRIVATE" not in str(rows)
    assert all(not _is_action(row) for row in rows)


@pytest.mark.asyncio
async def test_progress_failures_do_not_repeat_or_block_the_request(claimed, caplog):
    complete = AsyncMock(return_value="Prepared brief")
    emit = AsyncMock(side_effect=RuntimeError("PRIVATE_PROGRESS_ERROR"))
    assert await prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=complete, emit_progress=emit,
    ) == "Prepared brief"
    assert emit.await_count == 2
    complete.assert_awaited_once()
    claimed.write.assert_awaited_once()
    claimed.session.commit.assert_awaited_once()
    assert "PRIVATE_PROGRESS_ERROR" not in caplog.text


@pytest.mark.asyncio
async def test_stuck_progress_callback_cannot_hold_the_request(claimed, monkeypatch):
    monkeypatch.setattr(prep, "_PROGRESS_TIMEOUT_SECONDS", 0.001)

    async def stalled(*_args):
        await asyncio.Event().wait()

    complete = AsyncMock(return_value="Prepared brief")
    emit = AsyncMock(side_effect=stalled)
    result = await asyncio.wait_for(prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=complete, emit_progress=emit,
    ), timeout=1)
    assert result == "Prepared brief"
    assert emit.await_count == 2
    complete.assert_awaited_once()
    claimed.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_does_not_announce_completion_or_redispatch(claimed):
    entered = asyncio.Event()

    async def complete(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    call = AsyncMock(side_effect=complete)
    task = asyncio.create_task(prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=call,
        emit_progress=claimed.progress.emit_agent_event,
    ))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [row["action"] for row in claimed.rows()] == ["Подготавливаю промпт"]
    claimed.write.assert_not_awaited()
    claimed.claim.side_effect = None
    claimed.claim.return_value = (None, "")
    assert await prep.prepare_prompt_brief(
        claimed.factory, claimed.ids, complete=call,
        emit_progress=claimed.progress.emit_agent_event,
    ) == ""
    call.assert_awaited_once()
