"""Known routing evidence survives the durable journal and history projection."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_events import _new_project, _new_run, _new_user
from yleum_api.models.message import Message
from yleum_api.services.generation.progress import GenerationProgress
from yleum_api.services.generation.recovery_trace import provider_error_context
from yleum_api.services.generation_events import generation_event_envelope, replay_generation_events
from yleum_api.services.llm_client import _gateway_failure


async def test_provider_attribution_survives_postgres_and_replay(test_engine, monkeypatch):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_user(session, "provider-context")
        project = await _new_project(session, owner)
        run = await _new_run(session, owner, project)
        message = Message(project_id=project.id, role="assistant", content="")
        session.add(message)
        await session.flush()
        run.assistant_message_id = message.id
        await session.commit()

    call_id = str(uuid4())
    failure = _gateway_failure(httpx.Response(502, json={"detail": {"error": {
        "code": "model_unavailable", "message": "PRIVATE_PROVIDER_BODY",
        "details": {"upstream_http_status": 402, "provider_charge_ambiguous": True,
                    "provider_scope": "aitunnel", "transport_stage": "body",
                    "provider_call_id": call_id},
    }}}))
    monkeypatch.setattr("yleum_api.services.generation.progress.publish_event", AsyncMock())
    progress = GenerationProgress(factory, run.id, project.id, message.id)
    await progress.emit_agent_event("agent.provider_error", {
        **provider_error_context(failure), "http_status": failure.http_status,
        "upstream_http_status": failure.upstream_http_status,
        "provider_charge_ambiguous": failure.provider_charge_ambiguous,
        "will_retry": False, "raw_response": "PRIVATE_PROVIDER_BODY",
    })

    async with factory() as session:
        events = await replay_generation_events(session, run_id=run.id, after_seq=0)
        assert len(events) == 1
        saved_message = await session.get(Message, message.id)
        traces = [events[0].payload["recovery_trace"],
                  generation_event_envelope(events[0])["data"]["recovery_trace"],
                  saved_message.agent_steps[-1]["recovery_trace"]]
        for trace in traces:
            assert trace["provider_scope"] == "aitunnel"
            assert trace["transport_stage"] == "body"
            assert trace["provider_call_id"] == call_id
            assert trace["run_id"] == str(run.id)
            assert trace["upstream_http_status"] == 402
            assert trace["provider_charge_ambiguous"] is True
            assert trace["will_retry"] is False
        assert "PRIVATE_PROVIDER_BODY" not in json.dumps(
            [events[0].payload, saved_message.agent_steps], ensure_ascii=False,
        )
