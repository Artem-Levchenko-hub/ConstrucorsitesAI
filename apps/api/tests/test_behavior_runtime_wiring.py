"""Ordinary controller path installs only operator-configured typed driver."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services.generation import agent_runtime
from yleum_api.services.max_behavior_proof import ControllerBehaviorDriver

from .test_behavior_driver_configuration import settings


@pytest.mark.parametrize("mode", ["configured", "empty", "invalid"])
async def test_normal_generation_controller_installs_trusted_driver_without_probe_on_setup(
    tmp_path,
    monkeypatch,
    mode,
):
    from yleum_api.services import max_finalization

    config = settings(tmp_path)
    config.max_project_shell_enabled = False
    config.use_max_finalization_coordinator = True
    config.use_project_cell_activity_watchdog = False
    if mode == "empty":
        config.max_behavior_browser_executable = ""
    if mode == "invalid":
        config.max_behavior_browser_sha256 = "0" * 64
    handle = NS(
        is_portable=lambda: True, create_preview_session=AsyncMock(), operation_status=AsyncMock()
    )
    runtime = NS(handle=None, coordinator=None)
    created = []
    monkeypatch.setattr(agent_runtime, "get_settings", lambda: config)
    monkeypatch.setattr(
        agent_runtime.agent_builder, "make_docs_media_executor", lambda **_: object()
    )
    monkeypatch.setattr(
        agent_runtime,
        "_prepare_max_runtime_context",
        AsyncMock(
            return_value={
                "project_cell_handle": handle,
                "base_agent_executor": object(),
                "max_shell_enabled": False,
                "active_max_locked_files": frozenset(),
                "agent_result": object(),
            }
        ),
    )

    def coordinator(**kwargs):
        created.append(kwargs)
        return NS(**kwargs)

    monkeypatch.setattr(max_finalization, "MaxFinalizationCoordinator", coordinator)
    await agent_runtime.prepare_agent_runtime(
        ids=NS(project_id=uuid4(), user_id=uuid4(), run_id=uuid4()),
        project_info=NS(slug="owned-fixture"),
        runtime=runtime,
        progress=NS(emit_agent_event=AsyncMock(), record_generation_event=AsyncMock()),
        factory=object(),
        prompt_text="synthetic generic request",
        _design_contract=None,
        _agent_res=None,
        capacity_dispatch_token=None,
    )
    assert len(created) == 1 and created[0]["executor"] is handle
    assert (
        isinstance(created[0]["behavior_driver"], ControllerBehaviorDriver)
        if mode == "configured"
        else created[0]["behavior_driver"] is None
    )
    handle.create_preview_session.assert_not_awaited()
    handle.operation_status.assert_not_awaited()
