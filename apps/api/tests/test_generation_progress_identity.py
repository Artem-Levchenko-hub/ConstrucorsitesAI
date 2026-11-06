"""Offline checks for one identity across transcript storage and publication."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from yleum_api.schemas.message import MessagePublic
from yleum_api.services.agent_progress import sanitize_agent_step
from yleum_api.services.generation import progress
from yleum_api.services.generation_events import redact_event_payload


class Session:
    def __init__(self, *, fail_commit=False):
        self.message = SimpleNamespace(agent_steps=None)
        self.committed = False
        self.fail_commit = fail_commit
        self.committed_steps = None

    async def __aenter__(self):
        self.message.agent_steps = deepcopy(self.committed_steps)
        return self

    async def __aexit__(self, *_args):
        self.message.agent_steps = deepcopy(self.committed_steps)
        return False

    async def get(self, _model, _message_id, **kwargs):
        assert kwargs == {"with_for_update": True, "populate_existing": True}
        return self.message

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        self.committed = True
        self.committed_steps = deepcopy(self.message.agent_steps)

    async def refresh(self, _event):
        pass

    def expunge(self, _event):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
async def test_storage_and_wire_share_exact_identity_despite_different_detail_limits(
    monkeypatch, durable,
):
    session = Session()
    recorder = progress.GenerationProgress(lambda: session, uuid4(), uuid4(), uuid4())
    session.message.project_id = recorder.project_id
    event_id = uuid4()

    async def append(_session, **kwargs):
        if not durable:
            return None
        return SimpleNamespace(
            id=event_id, generation_run_id=kwargs["run_id"], seq=7,
            event_type=kwargs["event_type"], payload=redact_event_payload(kwargs["payload"]),
        )

    async def publish(project_id, event_type, payload):
        assert session.committed
        assert project_id == recorder.project_id
        saved = session.message.agent_steps[0]
        UUID(saved["eventId"])
        assert saved["runId"] == str(recorder.run_id)
        assert saved["detail"] == "x" * 4112
        assert sanitize_agent_step(saved) == saved
        if durable:
            assert event_type == "generation.event"
            assert (payload["event_id"], payload["run_id"], payload["seq"]) == (
                saved["eventId"], saved["runId"], saved["seq"],
            )
            assert len(payload["data"]["detail"]) == 4096
        else:
            assert event_type == "agent.step"
            assert payload == {"message_id": str(recorder.assistant_message_id), **saved}
            assert "seq" not in saved and "seq" not in payload

    monkeypatch.setattr(progress, "append_generation_event", append)
    published = AsyncMock(side_effect=publish)
    monkeypatch.setattr(progress, "publish_event", published)
    await recorder.record_agent_step({
        "step": 1, "kind": "step", "action": "read_file", "path": "page.tsx",
        "detail": "x" * 4112, "ok": True,
    })
    published.assert_awaited_once()
    assert recorder.steps == session.message.agent_steps


@pytest.mark.asyncio
async def test_legacy_repeated_actions_have_distinct_ids_and_do_not_leak_between_runs(monkeypatch):
    monkeypatch.setattr(progress, "append_generation_event", AsyncMock(return_value=None))
    published = AsyncMock()
    monkeypatch.setattr(progress, "publish_event", published)
    session = Session()
    recorder = progress.GenerationProgress(lambda: session, uuid4(), uuid4(), uuid4())
    session.message.project_id = recorder.project_id
    step = {"step": 1, "kind": "step", "action": "read_file", "path": "page.tsx"}
    await recorder.record_agent_step(step)
    await recorder.record_agent_step(step)
    assert len(recorder.steps) == 2
    assert len({row["eventId"] for row in recorder.steps}) == 2
    assert all(row["runId"] == str(recorder.run_id) for row in recorder.steps)
    other_session = Session()
    other_session.message.project_id = recorder.project_id
    other = progress.GenerationProgress(
        lambda: other_session, uuid4(), recorder.project_id, uuid4(),
    )
    await other.record_agent_step(step)
    assert len(other.steps) == 1 and len(recorder.steps) == 2
    assert other.steps[0]["runId"] == str(other.run_id)
    assert other.steps[0]["eventId"] not in {row["eventId"] for row in recorder.steps}


@pytest.mark.asyncio
async def test_failed_commit_does_not_advance_local_history_or_publish(monkeypatch):
    monkeypatch.setattr(progress, "append_generation_event", AsyncMock(return_value=None))
    published = AsyncMock()
    monkeypatch.setattr(progress, "publish_event", published)
    session = Session(fail_commit=True)
    recorder = progress.GenerationProgress(lambda: session, uuid4(), uuid4(), uuid4())
    session.message.project_id = recorder.project_id
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await recorder.record_agent_step({"step": 1, "kind": "step", "action": "read_file"})
    assert recorder.steps == []
    published.assert_not_awaited()


@pytest.mark.parametrize("kind,tool", [("step", "read_file"),
                                      ("provider_error", "provider_request"),
                                      ("tool", "provider_response")])
def test_only_valid_identity_survives_the_diagnostic_redaction_boundary(kind, tool):
    identity = {"eventId": str(uuid4()), "runId": str(uuid4()), "seq": 3}
    step = {"kind": kind, "tool": tool, "detail": "token=private-value", **identity}
    safe = sanitize_agent_step(step)
    assert all(safe[key] == value for key, value in identity.items())
    assert "private-value" not in str(safe)
    assert sanitize_agent_step(safe) == safe
    public = MessagePublic.model_validate({
        "id": uuid4(), "project_id": uuid4(), "role": "assistant", "content": "",
        "created_at": datetime.now(UTC), "agent_steps": [safe],
    })
    assert public.agent_steps == [safe]
    for invalid in ["token=private-value", [], None, "1" * 5000]:
        rejected = sanitize_agent_step({**step, "eventId": invalid})
        assert not {"eventId", "runId", "seq"}.intersection(rejected)
        assert "private-value" not in str(rejected)
    for invalid in [True, 0, -1, "3", None]:
        rejected = sanitize_agent_step({**step, "seq": invalid})
        assert rejected["eventId"] == identity["eventId"]
        assert "seq" not in rejected


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
async def test_full_count_and_latest_survive_cap_restart_and_failed_commit(monkeypatch, durable):
    session = Session()
    recorder = progress.GenerationProgress(lambda: session, uuid4(), uuid4(), uuid4())
    session.message.project_id = recorder.project_id
    sequence = 0
    published = []

    async def append(_session, **kwargs):
        nonlocal sequence
        sequence += 1
        if not durable:
            return None
        return SimpleNamespace(id=uuid4(), generation_run_id=recorder.run_id, seq=sequence,
                               event_type=kwargs["event_type"],
                               payload=redact_event_payload(kwargs["payload"]))

    async def publish(_project_id, event_type, payload):
        published.append(payload["data"] if event_type == "generation.event" else payload)

    monkeypatch.setattr(progress, "append_generation_event", append)
    monkeypatch.setattr(progress, "publish_event", publish)
    for number in range(1, 202):
        await recorder.record_agent_step({
            "step": number, "kind": "step", "action": "Read", "path": "page.tsx",
            "eventId": str(uuid4()), "runId": str(uuid4()), "actionCount": 999999,
            "historyTruncated": True,
        })
    assert [row["step"] for row in recorder.steps] == [*range(1, 200), 201]
    assert [row["actionCount"] for row in published] == list(range(1, 202))
    assert recorder.steps[-1]["historyTruncated"] is True
    assert all(row["historyTruncated"] is False for row in recorder.steps[:199])
    restarted = progress.GenerationProgress(
        lambda: session, recorder.run_id, recorder.project_id, recorder.assistant_message_id,
    )
    await restarted.record_agent_step({"step": 202, "kind": "step", "action": "Read"})
    assert len(restarted.steps) == 200
    assert restarted.steps[-1]["actionCount"] == 202
    assert restarted.steps[-1]["step"] == 202
    session.fail_commit = True
    saved = deepcopy(restarted.steps)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await restarted.record_agent_step({"step": 203, "kind": "step", "action": "Read"})
    assert restarted.steps == session.committed_steps == saved
    assert published[-1]["actionCount"] == 202


@pytest.mark.asyncio
async def test_mixed_service_and_capacity_records_do_not_inflate_cap_counter(monkeypatch):
    session = Session()
    recorder = progress.GenerationProgress(lambda: session, uuid4(), uuid4(), uuid4())
    session.message.project_id = recorder.project_id
    monkeypatch.setattr(progress, "append_generation_event", AsyncMock(return_value=None))
    monkeypatch.setattr(progress, "publish_event", AsyncMock())
    for number in range(101):
        await recorder.record_agent_step({"step": number, "kind": "step", "action": "Read"})
        await recorder.record_agent_step({"step": number, "kind": "checkpoint", "action": "Saved"})
    for row in [{"kind": "step", "tool": "capacity_wait", "action": "Другая позиция"},
                {"kind": "step", "action": "Ожидаю ресурсы сервера"},
                {"kind": "tool", "tool": "provider_response"}]:
        await recorder.record_agent_step(row)
    await recorder.emit_agent_event("agent.provider_error", {"detail": "token=private-value"})
    assert len(recorder.steps) == 200
    assert recorder.steps[-1]["kind"] == "provider_error"
    assert recorder.steps[-1]["actionCount"] == 101
    assert recorder.steps[-1]["historyTruncated"] is True
    assert "private-value" not in str(recorder.steps[-1])
    assert sanitize_agent_step(recorder.steps[-1]) == recorder.steps[-1]


def test_bad_count_on_truncated_history_is_not_reconstructed_from_retained_length():
    run_id = uuid4()
    row = {"eventId": str(uuid4()), "runId": str(run_id), "kind": "step",
           "actionCount": 300, "historyTruncated": True}
    for bad in [None, True, -1, 2**31, "300"]:
        invalid = {**row, "actionCount": bad}
        safe = sanitize_agent_step(invalid)
        assert "actionCount" not in safe and safe["historyTruncated"] is True
        with pytest.raises(ValueError, match="count is unavailable"):
            progress._committed_steps([invalid], run_id)
    with pytest.raises(ValueError, match="another run"):
        progress._committed_steps([row], uuid4())


@pytest.mark.parametrize("kind,expected", [(None, True), ("step", True), ("checkpoint", False),
                                          ("future", False), ("heartbeat", False)])
def test_counter_kind_semantics_match_live_projection(kind, expected):
    assert progress._is_action({"kind": kind}) is expected
    assert progress._is_action({"kind": kind, "tool": "capacity_wait"}) is False
    assert progress._is_action({"kind": kind, "action": "Ожидаю ресурсы сервера"}) is False


@pytest.mark.asyncio
async def test_concurrent_callbacks_on_one_recorder_do_not_regress_committed_cache(monkeypatch):
    session = Session()
    recorder = progress.GenerationProgress(lambda: session, uuid4(), uuid4(), uuid4())
    session.message.project_id = recorder.project_id
    monkeypatch.setattr(progress, "append_generation_event", AsyncMock(return_value=None))
    observed = []

    async def publish(_project, _type, payload):
        await asyncio.sleep(0)
        observed.append(payload["actionCount"])

    monkeypatch.setattr(progress, "publish_event", publish)
    await asyncio.gather(*(recorder.record_agent_step({"kind": "step", "action": "Read"})
                           for _ in range(10)))
    assert observed == list(range(1, 11))
    assert recorder.steps[-1]["actionCount"] == session.committed_steps[-1]["actionCount"] == 10
