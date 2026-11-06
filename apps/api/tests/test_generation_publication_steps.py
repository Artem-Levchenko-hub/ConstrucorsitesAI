"""Publication preserves the recorder's committed transcript after a restart."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tests.test_generation_artifacts import OfflineSession, green_max_promotion, records
from yleum_api.models.message import Message
from yleum_api.services.generation import agent_publication
from yleum_api.services.generation import progress as progress_module
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.generation.progress import GenerationProgress


def committed_history(run_id):
    # 201 real actions, bounded to the retained first199 and latest201 rows.
    return [
        {
            "step": number,
            "kind": "step",
            "tool": "read_file",
            "action": "Read source",
            "path": "src/app/page.tsx",
            "detail": "",
            "ok": True,
            "eventId": str(uuid4()),
            "runId": str(run_id),
            "seq": number,
            "actionCount": number,
            "historyTruncated": number == 201,
        }
        for number in [*range(1, 200), 201]
    ]


@pytest.mark.parametrize("has_files", [False, True], ids=["no-files", "files"])
@pytest.mark.parametrize("cache", ["empty", "stale", "fresh"])
async def test_actual_publication_keeps_committed_full_count_with_any_recorder_cache(
    monkeypatch,
    has_files,
    cache,
):
    rows, trace = records(), []
    owner, project, parent, message, run = rows
    message.agent_steps = committed_history(run.id)
    session = OfflineSession(rows, trace)

    def factory():
        return session
    files = {"src/app/page.tsx": "export default function Page() { return 'ready'; }"}
    green = green_max_promotion(rows, files)
    quota = AsyncMock()
    commit = Mock(return_value="c" * 40)
    monkeypatch.setattr(agent_publication.repo_svc, "commit_files", commit)
    monkeypatch.setattr(agent_publication, "publish_event", AsyncMock())
    monkeypatch.setattr(agent_publication, "_snapshot_payload", lambda value: {"id": str(value.id)})
    monkeypatch.setattr(
        "yleum_api.services.snapshot_preview_capture.capture_snapshot_frontend",
        AsyncMock(),
    )
    monkeypatch.setattr(progress_module, "append_generation_event", AsyncMock(return_value=None))
    monkeypatch.setattr(progress_module, "publish_event", AsyncMock())
    recorder = GenerationProgress(factory, run.id, project.id, message.id)
    if cache == "stale":
        recorder.steps = deepcopy(message.agent_steps[:1])
    if cache == "fresh":
        await recorder.record_agent_step(
            {
                "kind": "step",
                "tool": "build",
                "action": "Check source",
                "step": 202,
            }
        )
        assert message.agent_steps[-1]["actionCount"] == 202
    # Healthy finalization emits a phase event, not another transcript action.
    await recorder.record_generation_event("generation.phase", {"phase": "finalization"})
    expected = deepcopy(message.agent_steps)

    await agent_publication.publish_agent_candidate(
        _att_capture=None,
        _attestation_stack="",
        _consume_free_generation=quota,
        _max_finalization_proof=green["_max_finalization_proof"] if has_files else None,
        _promotion_permit=green["_promotion_permit"] if has_files else None,
        _orch_name=None,
        accumulated="Ready and verified",
        baseline=SourceBaseline(parent.id, parent.commit_sha, {}),
        factory=factory,
        files=files if has_files else {},
        ids=GenerationIds(run.id, project.id, owner.id, uuid4(), message.id),
        model_id="test-model",
        progress=recorder,
        project_info=project,
        prompt_text="Finish the app",
        runtime=green["runtime"],
    )

    durable = next(value for value in session.durable if isinstance(value, Message))
    assert message.agent_steps == durable.agent_steps == expected
    assert len(durable.agent_steps) == 200
    assert durable.agent_steps[-1]["actionCount"] == (202 if cache == "fresh" else 201)
    assert durable.agent_steps[-1]["historyTruncated"] is True
    assert durable.content == "Ready and verified"
    assert durable.tokens_in == 7 and durable.tokens_out == 0
    assert quota.await_count == int(has_files)
    assert commit.call_count == int(has_files)
    if has_files:
        assert durable.snapshot_id == project.current_snapshot_id != parent.id
    else:
        assert durable.snapshot_id is None
        assert project.current_snapshot_id == parent.id
