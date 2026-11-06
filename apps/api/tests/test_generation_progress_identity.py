"""Offline checks for one identity across transcript storage and publication."""

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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _message_id):
        return self.message

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        self.committed = True

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
    step = {"step": 1, "kind": "step", "action": "read_file", "path": "page.tsx"}
    await recorder.record_agent_step(step)
    await recorder.record_agent_step(step)
    assert len(recorder.steps) == 2
    assert len({row["eventId"] for row in recorder.steps}) == 2
    assert all(row["runId"] == str(recorder.run_id) for row in recorder.steps)
    other = progress.GenerationProgress(lambda: Session(), uuid4(), recorder.project_id, uuid4())
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
