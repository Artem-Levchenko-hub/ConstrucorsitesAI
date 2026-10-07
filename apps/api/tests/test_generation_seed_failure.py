from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services.generation import agent_seed
from yleum_api.services.generation.runtime import PreviewSyncFailed


@pytest.mark.parametrize("failure", [None, "sync", "build"])
async def test_portable_seed_preserves_manifest_before_sync_and_stops_on_infra_failure(
    failure,
):
    manifest = '{"version": 1, "tasks": []}'
    workspace = {".omnia/cell.json": manifest, "src/app/page.tsx": "old product"}
    starter = {"package.json": '{"scripts":{"build":"next build"}}'}
    staged = []

    async def stage(writes, deletes):
        staged.append(dict(writes))
        workspace.update(writes)
        for path in deletes:
            workspace.pop(path, None)

    async def sync():
        return SimpleNamespace(
            failure="typescript missing" if failure == "sync" else None,
            runtime_absent=False,
        )

    handle = SimpleNamespace(
        is_portable=lambda: True,
        snapshot_files=AsyncMock(side_effect=lambda: dict(workspace)),
        stage_patch=stage,
        sync_preview=sync,
    )
    runtime = SimpleNamespace(handle=handle, coordinator=None, migration_baseline=None)
    bindings = SimpleNamespace(probe_build=AsyncMock(return_value={"ok": failure != "build"}))
    call = agent_seed.stage_max_starter(
        _agent_emit=AsyncMock(),
        _agent_res=None,
        _design_contract=None,
        _max_has_generated_snapshot=False,
        _render_current_max_starter_files=AsyncMock(return_value=starter),
        bindings=bindings,
        ids=SimpleNamespace(project_id=uuid4()),
        orchestrate=True,
        project_info=SimpleNamespace(),
        runtime=runtime,
    )
    if failure:
        with pytest.raises(ApiError) as raised:
            await call
        assert raised.value.code == "runtime_unavailable"
        assert raised.value.status_code == 503
        # Lifecycle persists str(error), losing ApiError.code. History and the
        # stored assistant body must still name infrastructure, not verification.
        from yleum_api.services.generation.agent_messages import _failed_build_body
        from yleum_api.services.generation_failure import public_generation_failure

        stored = SimpleNamespace(status="failed", error=str(raised.value), agent_state={})
        card = public_generation_failure(stored)
        assert card.code == "runtime_unavailable"
        assert "Генерация не запускалась" in card.message
        assert "Деньги за вызов модели не списаны" in card.message
        assert _failed_build_body("", raised.value) == f"[Ошибка генерации: {card.message}]"
        assert "typescript" not in card.model_dump_json()
        if failure == "sync":
            assert isinstance(raised.value.__cause__, PreviewSyncFailed)
            bindings.probe_build.assert_not_awaited()
    else:
        seed, result = await call
        assert result is None
        assert seed == {**starter, ".omnia/cell.json": manifest}
    assert staged == [{**starter, ".omnia/cell.json": manifest}]
    assert runtime.migration_baseline == {**starter, ".omnia/cell.json": manifest}
    assert "src/app/page.tsx" not in workspace
