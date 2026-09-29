import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation_deadline import source_edit_deadline


@pytest.mark.parametrize(("path", "expected"), [
    ("/workspace/src/app/page.tsx", "src/app/page.tsx"),
    ("/workspace/../secret", "/workspace/../secret"),
    ("/etc/passwd", "/etc/passwd"),
    ("/workspace-other/file", "/workspace-other/file"),
])
def test_portable_tool_paths_accept_only_the_real_workspace_prefix(path, expected):
    action = agent_native._tool_use_to_action(
        {"name": "read_file", "input": {"path": path}}, portable_cell=True,
    )
    assert action.path == expected


def test_workspace_root_is_accepted_for_listing_but_http_path_is_unchanged():
    assert agent_native._tool_use_to_action(
        {"name": "list_dir", "input": {"path": "/workspace"}}, portable_cell=True,
    ).path == "."
    assert agent_native._tool_use_to_action(
        {"name": "runtime_check", "input": {"path": "/workspace/orders"}}, portable_cell=True,
    ).path == "/workspace/orders"


def test_prompt_prevents_the_observed_max_ui_namespace_error():
    prompt = agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT")
    assert "Never render <Typography>" in prompt


@pytest.mark.asyncio
async def test_provider_call_cannot_consume_finalization_reserve(monkeypatch):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    from yleum_api.core.config import get_settings

    get_settings.cache_clear()

    async def blocked_provider(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_native, "_call_messages", blocked_provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="finish product", execute=AsyncMock(),
        portable_cell=True, completion_check=lambda files, evidence: None,
        initial_files={"src/app/page.tsx": "complete"},
        edit_deadline=datetime.now(UTC) + timedelta(milliseconds=20),
    )
    assert result.needs_finalization and not result.done
    assert result.stop_reason == "finalization_reserve"


@pytest.mark.asyncio
async def test_unrelated_timeout_is_provider_failure_not_a_successful_handoff(monkeypatch):
    monkeypatch.setattr(agent_native, "_call_messages", AsyncMock(side_effect=TimeoutError()))
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="finish product", execute=AsyncMock(),
        portable_cell=True, completion_check=lambda files, evidence: None,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "provider_error"


def test_source_budget_reserves_finalization_without_extending_deadline():
    end = datetime.now(UTC) + timedelta(seconds=1500)
    assert source_edit_deadline(end) == end - timedelta(seconds=300)


@pytest.mark.parametrize("seconds", [60, 120, 300, 1500])
def test_short_valid_budgets_retain_editing_time(seconds):
    start = datetime.now(UTC)
    end = start + timedelta(seconds=seconds)
    assert source_edit_deadline(end, started_at=start) == start + timedelta(seconds=seconds * 0.8)


@pytest.mark.asyncio
async def test_expired_edit_budget_hands_off_complete_source_without_provider(monkeypatch):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    from yleum_api.core.config import get_settings

    get_settings.cache_clear()
    call = AsyncMock()
    monkeypatch.setattr(agent_native, "_call_messages", call)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="complete the product",
        execute=AsyncMock(),
        portable_cell=True,
        initial_files={"src/app/page.tsx": "complete product"},
        completion_check=lambda files, evidence: None,
        edit_deadline=datetime.now(UTC) - timedelta(seconds=1),
        max_segments=3,
    )
    assert result.needs_finalization and not result.done
    assert result.stop_reason == "finalization_reserve"
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_budget_does_not_certify_incomplete_source(monkeypatch):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    from yleum_api.core.config import get_settings

    get_settings.cache_clear()
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="complete the product",
        execute=AsyncMock(),
        portable_cell=True,
        completion_check=lambda files, evidence: "missing product",
        edit_deadline=datetime.now(UTC) - timedelta(seconds=1),
        max_segments=3,
    )
    assert not result.needs_finalization and not result.done
    assert result.segments == 1


@pytest.mark.asyncio
async def test_first_source_write_gets_early_typecheck_feedback(monkeypatch):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    from yleum_api.core.config import get_settings

    get_settings.cache_clear()
    turns = iter(
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "write",
                        "name": "write_file",
                        "input": {"path": "src/app/page.tsx", "content": "bad type"},
                    }
                ]
            },
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "fix",
                        "name": "write_file",
                        "input": {"path": "src/app/page.tsx", "content": "fixed type"},
                    }
                ]
            },
            {"content": [{"type": "tool_use", "id": "build", "name": "build", "input": {}}]},
            {"content": [{"type": "tool_use", "id": "done", "name": "done", "input": {}}]},
        ]
    )
    observations = []

    async def call(*args, **kwargs):
        observations.append(str(args[2]))
        return next(turns)

    build_count = 0

    async def execute(action):
        nonlocal build_count
        if action.name == "build":
            build_count += 1
            return {"ok": build_count > 1, "detail": "TS2604 Typography is a namespace"}
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", call)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        portable_cell=True,
        max_steps=4,
        completion_check=lambda files, evidence: None,
    )
    assert "TS2604" in observations[1]
    assert result.done
    assert build_count == 2
