from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.agent_builder import Action
from yleum_api.services.generation import runtime
from yleum_api.services.max_project_kit import render_max_starter_files
from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError


@pytest.fixture
def substrate():
    return render_max_starter_files(
        MaxProjectConfigPayload(app_name="QA", app_type="custom", summary="QA"),
        uuid4(), portable=True,
    )


def _handle(tree, execute=None):
    async def snapshot():
        return dict(tree)

    async def stage(writes, deletes):
        tree.update(writes)
        for path in deletes:
            tree.pop(path, None)

    async def dispatch(action):
        if action.name == "write_file":
            tree[action.path] = action.args["content"]
        return {"ok": True}

    return SimpleNamespace(
        is_portable=lambda: True,
        execute=AsyncMock(side_effect=execute or dispatch),
        snapshot_files=AsyncMock(side_effect=snapshot),
        refresh_snapshot_files=AsyncMock(side_effect=snapshot),
        stage_patch=AsyncMock(side_effect=stage),
        sync_preview=AsyncMock(side_effect=AssertionError("guard must not build")),
    )


async def _execute(handle, action):
    return await runtime._execute_max_agent_action(
        action, project_id=uuid4(), project_slug="qa",
        base_agent_executor=AsyncMock(side_effect=AssertionError("legacy dispatch")),
        max_shell_enabled=True, project_cell_handle=handle,
        active_max_locked_files=frozenset({"src/lib/db/index.ts", "src/app/layout.tsx"}),
        max_model_write_rejection=lambda *_: None,
    )


@pytest.mark.parametrize("path", [
    "src/lib/db/index.ts", "./src/lib/db/index.ts", "src/lib/db/../db/index.ts",
    "src\\lib\\db\\index.ts", "src/lib/db.ts", "src/lib/db.tsx", "src/lib/db.d.ts",
    "src/lib/db/schema.ts",
])
async def test_portable_trusted_db_rejects_replacement_and_shadow_before_write(substrate, path):
    handle = _handle(substrate)
    result = await _execute(handle, Action("write_file", {
        "path": path, "content": "export const workouts = {};",
    }))
    assert result["ok"] is False
    assert "MAX database compatibility" in result["error"]
    handle.execute.assert_not_awaited()


async def test_portable_schema_edit_validates_whole_result(substrate):
    handle = _handle(substrate)
    result = await _execute(handle, Action("edit_file", {
        "path": "src/lib/db/schema.ts",
        "search": "export const maxUsers", "replace": "export const workouts",
    }))
    assert result["ok"] is False
    handle.execute.assert_not_awaited()


async def test_portable_schema_extension_and_product_migration_remain_allowed(substrate):
    handle = _handle(substrate)
    result = await _execute(handle, Action("write_file", {
        "path": "src/lib/db/schema.ts",
        "content": substrate["src/lib/db/schema.ts"]
        + "\nexport const workouts = pgTable('workouts', { id: text('id') });\n",
    }))
    assert result["ok"] is True
    assert (await _execute(handle, Action("write_file", {
        "path": "drizzle/0002_workouts.sql", "content": "CREATE TABLE workouts(id text);",
    })))["ok"] is True
    # The first allowed extension cannot disable protection for the next tool call.
    assert (await _execute(handle, Action("write_file", {
        "path": "src/lib/db/index.ts", "content": "export const db = {};",
    })))["ok"] is False
    assert handle.execute.await_count == 2


async def test_portable_custom_stack_does_not_acquire_next_locks_from_paths():
    tree = {"server.py": "print('custom')", "src/lib/db/index.ts": "custom data module"}
    handle = _handle(tree)
    for action in (
        Action("write_file", {"path": "src/lib/db/index.ts", "content": "new custom module"}),
        Action("write_file", {"path": "src/app/layout.tsx", "content": "custom layout"}),
        Action("bash", {"cmd": "pip install flask"}),
    ):
        assert (await _execute(handle, action))["ok"] is True
    handle.stage_patch.assert_not_awaited()


async def test_shell_rollback_uses_actual_snapshot_and_preserves_product_changes(substrate):
    before = dict(substrate)

    async def shell(_action):
        substrate["src/lib/db/index.ts"] = "broken"
        substrate["src/lib/db/schema.ts"] = "export const workouts = {};"
        substrate["src/lib/db.ts"] = "export const db = {};"
        substrate["src/app/page.tsx"] = "legitimate product edit"
        return {"ok": False, "files": {"src/app/page.tsx": "legitimate product edit"}}

    handle = _handle(substrate, shell)
    result = await _execute(handle, Action("bash", {"cmd": "generator"}))
    assert result["ok"] is False
    assert "MAX database compatibility" in result["error"]
    assert substrate["src/lib/db/index.ts"] == before["src/lib/db/index.ts"]
    assert substrate["src/lib/db/schema.ts"] == before["src/lib/db/schema.ts"]
    assert "src/lib/db.ts" not in substrate
    assert substrate["src/app/page.tsx"] == "legitimate product edit"
    handle.stage_patch.assert_awaited_once()
    handle.sync_preview.assert_not_awaited()


async def test_shell_failed_protected_rollback_is_terminal(substrate):
    async def shell(_action):
        substrate.pop("src/lib/db/index.ts")
        return {"ok": True}

    handle = _handle(substrate, shell)
    handle.stage_patch.side_effect = RuntimeError("unavailable")
    with pytest.raises(
        ProjectCellInfrastructureError, match="protected_environment_recovery_required",
    ):
        await _execute(handle, Action("bash", {"cmd": "generator"}))


async def test_shell_extension_does_not_rollback_or_autobuild(substrate):
    async def shell(_action):
        substrate["src/lib/db/schema.ts"] += "\nexport const workouts = pgTable('workouts', {});\n"
        return {"ok": True}

    handle = _handle(substrate, shell)
    assert (await _execute(handle, Action("bash", {"cmd": "generator"})))["ok"] is True
    handle.stage_patch.assert_not_awaited()
    handle.sync_preview.assert_not_awaited()


async def test_schema_core_cannot_be_hidden_in_comment(substrate):
    handle = _handle(substrate)
    result = await _execute(handle, Action("write_file", {
        "path": "src/lib/db/schema.ts",
        "content": "/*\n" + substrate["src/lib/db/schema.ts"] + "\n*/\nexport const workouts = {};",
    }))
    assert result["ok"] is False
    handle.execute.assert_not_awaited()


async def test_shell_delete_restores_previous_product_extensions_even_on_exception(substrate):
    substrate["src/lib/db/schema.ts"] += "\nexport const workouts = pgTable('workouts', {});\n"
    before = dict(substrate)

    async def shell(_action):
        substrate.pop("src/lib/db/schema.ts")
        raise RuntimeError("command transport failure")

    handle = _handle(substrate, shell)
    with pytest.raises(RuntimeError, match="command transport failure"):
        await _execute(handle, Action("bash", {"cmd": "generator"}))
    assert substrate == before
    handle.stage_patch.assert_awaited_once_with({
        "src/lib/db/schema.ts": before["src/lib/db/schema.ts"],
    }, ())


async def test_shell_unconfirmed_restore_is_terminal(substrate):
    async def shell(_action):
        substrate["src/lib/db.ts"] = "export const db = {};"
        return {"ok": True}

    handle = _handle(substrate, shell)
    handle.stage_patch.side_effect = None  # Acknowledged write did not reach the workspace.
    with pytest.raises(
        ProjectCellInfrastructureError, match="protected_environment_recovery_required",
    ):
        await _execute(handle, Action("bash", {"cmd": "generator"}))


async def test_build_refuses_existing_schema_damage_with_attested_index(substrate):
    substrate["src/lib/db/schema.ts"] = "export const workouts = {};"
    handle = _handle(substrate)
    assert (await _execute(handle, Action("build", {})))["ok"] is False
    handle.execute.assert_not_awaited()


def test_portable_guide_explains_db_compatibility_and_next15_params():
    from yleum_api.services.portable_cell_contract import PORTABLE_CELL_GUIDE

    for contract in (
        "db, pool, schema, withMaxUser and MaxUserTx", "shadows @/lib/db",
        "maxUsers", "maxAnalyticsEvents", "Append new product imports/tables",
        "params: Promise<{ id: string }>", "await context.params",
    ):
        assert contract in PORTABLE_CELL_GUIDE
