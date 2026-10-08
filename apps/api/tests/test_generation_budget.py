import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation_deadline import source_edit_deadline


@pytest.mark.parametrize(("coordinator", "portable", "capability", "expected"), [
    (True, True, True, True), (False, True, True, False),
    (True, False, True, False), (True, True, "true", False),
])
def test_legacy_handoff_requires_the_invocation_owned_portable_coordinator(
    coordinator, portable, capability, expected,
):
    from types import SimpleNamespace

    from yleum_api.services.generation.contracts import GenerationRuntime

    runtime = GenerationRuntime(
        handle=SimpleNamespace(capabilities={"portable_machine": capability},
                               is_portable=lambda: portable),
        coordinator=object() if coordinator else None,
    )
    assert runtime.legacy_coordinator_handoff() is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("handoff", [False, True])
async def test_legacy_edit_repair_keeps_runtime_with_its_actual_owner(monkeypatch, handoff):
    from types import SimpleNamespace
    from uuid import uuid4

    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation import agent_verification as verification

    monkeypatch.setattr(verification, "get_settings", lambda: SimpleNamespace(
        use_edit_auto_repair=True, use_native_agent=False, edit_auto_repair_attempts=1,
        agent_require_green_before_done=True, agent_ship_green_on_abort=True,
    ))
    builder = AsyncMock(return_value=AgentResult(
        done=True, summary="candidate", files={"page.tsx": "fixed"}, steps=3,
        needs_finalization=handoff,
    ))
    monkeypatch.setattr(verification.agent_builder, "run_agent_build", builder)
    operations = SimpleNamespace(
        execute=AsyncMock(), emit=AsyncMock(),
        probe_build=AsyncMock(return_value={"ok": True}),
        probe_runtime=AsyncMock(return_value={"ok": True}),
    )
    result = await verification.repair_legacy_edit(
        _is_edit=True, _rt_error="", _runtime_ok=True,
        _tc_error="TS error", _typecheck_ok=False, accumulated="pending",
        files={"page.tsx": "broken"}, ids=SimpleNamespace(user_id=uuid4(), project_id=uuid4()),
        project_info=SimpleNamespace(), prompt_text="fix form",
        plan=SimpleNamespace(seed_context="", stack_guide="", model="m",
                             escalate_model=None, steps=3),
        operations=operations, coordinator_handoff=handoff,
    )
    assert builder.await_args.kwargs["coordinator_handoff"] is handoff
    assert operations.probe_runtime.await_count == (0 if handoff else 1)
    assert result.runtime_ok and result.typecheck_ok


@pytest.mark.parametrize("capabilities", [None, "portable", False])
def test_legacy_handoff_fails_closed_without_capability_metadata(capabilities):
    from types import SimpleNamespace

    from yleum_api.services.generation.contracts import GenerationRuntime

    handle = SimpleNamespace(is_portable=lambda: True)
    if capabilities is not None:
        handle.capabilities = capabilities
    runtime = GenerationRuntime(handle=handle, coordinator=object())
    assert runtime.legacy_coordinator_handoff() is False


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


@pytest.mark.parametrize("mode", ["legacy_build", "legacy_edit", "native"])
def test_shared_max_contract_supplies_installed_uikit_guidance_in_every_agent_mode(mode):
    from yleum_api.services import agent_builder
    from yleum_api.services.max_project_kit import MAX_MODEL_DIRECTIVE

    compose = {
        "legacy_build": agent_builder.build_system_prompt,
        "legacy_edit": agent_builder.build_edit_system_prompt,
        "native": agent_native.native_system_prompt,
    }[mode]
    prompt = " ".join(compose(MAX_MODEL_DIRECTIVE).split())

    for instruction in (
        "Typography is a namespace, not a JSX component",
        "Never render <Typography>",
        "Inspect the installed public exports/types",
        "concrete text components and their props",
        "Do not assume Typography.Caption exists or guess level values",
        "Keep the installed MAX UI version",
    ):
        assert instruction in prompt, (mode, instruction)


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
