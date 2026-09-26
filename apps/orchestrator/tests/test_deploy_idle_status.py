from uuid import uuid4

import pytest

from yleum_orchestrator.routers import runtime


@pytest.mark.asyncio
async def test_missing_deployment_is_idle_not_a_queued_job(monkeypatch):
    """An app that has never been published is idle, not «queued».

    The legacy deploy journal left with the site builder, so the answer comes
    from the cell publication service alone.
    """
    monkeypatch.setattr(runtime, "_verify_token", lambda token: None)

    response = await runtime.get_deploy(str(uuid4()))

    assert response.phase == "idle"
    assert response.run_id is None
