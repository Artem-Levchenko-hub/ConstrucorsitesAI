from uuid import uuid4

import pytest

from omnia_orchestrator.routers import runtime


@pytest.mark.asyncio
async def test_missing_deployment_is_idle_not_a_queued_job(monkeypatch):
    monkeypatch.setattr(runtime, "_verify_token", lambda token: None)
    monkeypatch.setattr(runtime.deploy_state, "get", lambda project_id: None)

    response = await runtime.get_deploy(str(uuid4()))

    assert response.phase == "idle"
    assert response.run_id is None
