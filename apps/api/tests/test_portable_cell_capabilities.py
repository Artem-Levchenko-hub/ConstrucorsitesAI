from uuid import uuid4

import pytest

from yleum_api.services import project_cell_executor
from yleum_api.services.orchestrator_client import ProjectCellAgentWorkspaceSnapshot


def test_bootstrap_transports_real_provider_capability_and_legacy_remains_empty():
    payload = {
        "files": {},
        "seeded_from_project": False,
        "generation_run_id": str(uuid4()),
        "fencing_epoch": 7,
        "workspace_revision": "a" * 64,
        "capabilities": {"portable_machine": True, "manifest_path": ".omnia/cell.json"},
    }
    assert (
        ProjectCellAgentWorkspaceSnapshot.from_json(payload).capabilities["portable_machine"]
        is True
    )
    payload.pop("capabilities")
    assert ProjectCellAgentWorkspaceSnapshot.from_json(payload).capabilities == {}


def test_portable_dispatch_requires_provider_capability_and_manifest():
    files = {".omnia/cell.json": "{}", "server.py": "print('product')"}
    assert project_cell_executor.portable_selected({"portable_machine": True}, files)
    assert not project_cell_executor.portable_selected({}, files)
    assert not project_cell_executor.portable_selected({"portable_machine": True}, {})
    assert not project_cell_executor.portable_selected({"portable_machine": "true"}, files)


def test_adaptation_execution_requires_controller_attested_copy():
    gap = project_cell_executor.adaptation_execution_capability_gap

    assert gap({"portable_machine": True, "database_admin": "full"}) == (
        "изолированная копия базы данных не подтверждена"
    )
    assert (
        gap(
            {
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
            }
        )
        is None
    )


def test_main_stack_guide_keeps_next_max_tools_and_preserves_legacy_selection():
    from yleum_api.services import agent_native
    from yleum_api.services.portable_cell_contract import machine_stack_guide

    legacy = "MAX PLATFORM CORE CONTRACT\nlegacy Next guide"
    assert machine_stack_guide(legacy, {"portable_machine": True}, {}) == legacy
    guide = machine_stack_guide(legacy, {"portable_machine": True}, {".omnia/cell.json": "{}"})
    prompt = agent_native.native_system_prompt(guide)
    assert "MAX VERIFICATION OVERRIDE" in prompt
    assert "Next.js" in guide and "Node.js 22" in guide and "pnpm" in guide
    assert "any language, framework" not in guide
    assert "src/app/page.tsx" in guide
    assert "DATABASE_URL" in guide
    assert "dedicated project PostgreSQL" in guide
    assert "isolated from the managed MAX core" in guide
    assert "Do NOT call or retry probe/verify_isolation" in prompt


@pytest.mark.parametrize("capabilities,files,portable", [
    ({"portable_machine": True}, {".omnia/cell.json": "{}"}, True),
    ({"portable_machine": True}, {}, False),
    ({}, {".omnia/cell.json": "{}"}, False),
])
def test_async_loading_contract_reaches_native_prompt_only_for_portable_apps(
    capabilities, files, portable,
):
    from yleum_api.services import agent_native
    from yleum_api.services.portable_cell_contract import machine_stack_guide

    legacy = "MAX PLATFORM CORE CONTRACT\nlegacy Next guide"
    guide = machine_stack_guide(legacy, capabilities, files)
    prompt = agent_native.native_system_prompt(guide)
    if not portable:
        assert guide == legacy
        assert prompt == agent_native.native_system_prompt(legacy)
        return

    delivered = " ".join(prompt.split())
    for requirement in (
        "idle/loading/success/error",
        "Automatically fetch only idle resources",
        "null data plus !loading is not a retry condition",
        "including empty lists",
        "After failure, stop until an explicit Retry action",
        "only one in-flight request",
        "synchronous in-flight guard inside the shared loader",
        "before the first await or state update",
        "initial load, Retry, focus and visibility",
        "React loading state alone is not a lock",
        "Release the guard in finally, including error paths",
        "401/403 access failures",
        "network/503 temporary failures",
        "never bypass authentication",
        "Reset cached state when the authenticated identity or query changes",
    ):
        assert requirement in delivered


@pytest.mark.asyncio
@pytest.mark.parametrize("portable,new_product", [(True, False), (False, False), (False, True)])
async def test_prompt_assembly_awaits_real_executor_snapshot(portable, new_product):
    from yleum_api.services.portable_cell_contract import machine_stack_guide_from_executor

    called = []

    async def snapshot():
        called.append("awaited")
        return {".omnia/cell.json": "{}"} if portable else {}

    async def unused(*args):
        raise AssertionError("only source snapshot may run during guide assembly")

    handle = project_cell_executor.ProjectCellExecutorHandle(
        execute=unused,
        sync_preview=unused,
        snapshot_files=snapshot,
        stage_patch=unused,
        stage_files=unused,
        apply_external_files=unused,
        export_files=unused,
        workspace_id=uuid4(),
        create_preview_session=unused,
        release=unused,
        capabilities={"portable_machine": True},
    )
    guide = await machine_stack_guide_from_executor("legacy guide", handle, new_product=new_product)
    assert called == ["awaited"]
    assert ("EXTENSIBLE MAIN STACK" in guide) is portable


@pytest.mark.asyncio
async def test_portable_build_client_sends_task_role_and_stable_operation_id(monkeypatch):
    from yleum_api.services import orchestrator_client

    captured = []

    async def request(method, path, **kwargs):
        captured.append(kwargs["json"])
        return {
            "ok": True,
            "exit_code": 0,
            "detail": "compiled",
            "timed_out": False,
            "workspace_revision": "a" * 64,
        }

    monkeypatch.setattr(orchestrator_client, "_request", request)
    operation = uuid4()
    await orchestrator_client.project_cell_agent_exec(
        uuid4(),
        "omnia:build",
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        task_role="build",
        operation_id=operation,
    )
    assert captured[0]["task_role"] == "build"
    assert captured[0]["operation_id"] == str(operation)


def test_portable_loading_prompt_prevents_effect_cleanup_deadlock():
    from yleum_api.services import agent_native
    from yleum_api.services.portable_cell_contract import machine_stack_guide

    prompt = agent_native.native_system_prompt(machine_stack_guide(
        'legacy', {'portable_machine': True}, {'.omnia/cell.json': '{}'}))
    delivered = ' '.join(prompt.split())
    for rule in (
        'Do not invent loading for static content',
        'Changing an effect dependency runs its cleanup',
        'stable identity/query/retry key',
        'loading reaches success or error',
    ):
        assert rule in delivered


def test_old_green_build_without_effect_check_cannot_promote():
    from types import SimpleNamespace

    from yleum_api.services.max_finalization import _current_build_result

    result = SimpleNamespace(
        redacted_detail='[build-contract:max-full-build-project-migrations-v2] green',
        outcome='green', artifact_ref='build/sha256/' + 'a' * 64)
    assert not _current_build_result(result, 'a' * 64)
