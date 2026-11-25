from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from yleum_api.core.errors import ApiError
from yleum_api.routers import integration_runtime as runtime
from yleum_api.schemas.integration_runtime import RuntimeAIRequest
from yleum_api.services import max_runtime_observation as observation


async def test_diagnosis_reads_only_latest_result_of_same_project_and_operation(monkeypatch):
    stored = {}

    async def put(key, value, *, ex):
        assert ex == 24 * 60 * 60
        stored[key] = value

    redis = SimpleNamespace(set=put, get=AsyncMock(side_effect=stored.get))
    monkeypatch.setattr(observation, "get_redis", lambda: redis)
    project = uuid4()
    await observation.record_ai_outcome(project, "meal_plan", "wallet_empty")
    code, timestamp = await observation.latest_ai_outcome(project, "meal_plan")
    assert code == "wallet_empty"
    assert datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0
    assert await observation.latest_ai_outcome(uuid4(), "meal_plan") is None
    assert await observation.latest_ai_outcome(project, "nutrition") is None
    await observation.record_ai_outcome(project, "meal_plan", "ok")
    assert (await observation.latest_ai_outcome(project, "meal_plan"))[0] == "ok"
    await observation.record_ai_outcome(project, "meal_plan", "arbitrary visitor input")
    assert (await observation.latest_ai_outcome(project, "meal_plan"))[0] == "failed"
    assert "arbitrary" not in str(stored)
    assert observation.ai_operation({"unexpected": "private"}) == "ai"


async def test_observation_outage_does_not_change_runtime_result(monkeypatch):
    def unavailable():
        raise RedisConnectionError()

    monkeypatch.setattr(observation, "get_redis", unavailable)
    await observation.record_ai_outcome(uuid4(), "nutrition", "ok")
    assert await observation.latest_ai_outcome(uuid4(), "nutrition") is None


async def test_runtime_reports_verified_project_failure_without_visitor_data(monkeypatch):
    pid = uuid4()
    record = AsyncMock()
    monkeypatch.setattr(observation, "record_ai_outcome", record)
    context = runtime.RuntimeContext(project_id=pid, max_user_id=123, same_origin=True)
    auth = AsyncMock(return_value=context)
    monkeypatch.setattr(runtime, "_runtime_context", auth)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(
        runtime_ai_enabled=False, runtime_aitunnel_ai_enabled=False,
    )
    payload = RuntimeAIRequest(message="private questionnaire", context={"task": "meal_plan"})
    with pytest.raises(ApiError, match="Включите ИИ"):
        await runtime.request_runtime_ai(pid, payload, session, SimpleNamespace(), "")
    record.assert_awaited_once_with(pid, "meal_plan", "ai_integration_required")
    auth.side_effect = ApiError("max_init_data_invalid", "invalid", 401)
    with pytest.raises(ApiError, match="invalid"):
        await runtime.request_runtime_ai(pid, payload, session, SimpleNamespace(), "")
    assert record.await_count == 1
