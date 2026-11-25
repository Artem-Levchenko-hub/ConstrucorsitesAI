"""A bounded Sol turn with outstanding product work keeps its resumable draft."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_generation_stopped_recovery import stopped_recovery as stopped_recovery
from yleum_api.services import agent_builder
from yleum_api.services.agent_product_work import SolProductWork
from yleum_api.services.generation import agent_generation, agent_recovery
from yleum_api.services.generation.contracts import GenerationRuntime, SourceBaseline

MISSING = "Анкета, план питания и личный профиль не реализованы."


@pytest.mark.parametrize("complete_summary", [
    "Если рацион не готов, экран показывает пустое состояние.",
    "Если план пока не реализован владельцем, доступна ручная запись.",
    "If a feature is not implemented by the host, the fallback is shown.",
    "Нет функций из запроса, которые не реализованы.",
    "No requested features remain unimplemented.",
    "При ошибке сервис показывает «Экспорт пока не реализован».",
])
def test_completed_user_flow_does_not_reopen_sol_work(complete_summary):
    tracker = SolProductWork()
    assert not tracker.feedback(
        {"summary": complete_summary, "remaining_work": ""}, {"src/app/page.tsx": "app"},
        requested=True, preamble="",
    )
    assert tracker.detail is None


@pytest.mark.parametrize("invalid_report", [None, {}, [""], [None], ["профиль", 2]])
def test_invalid_report_cannot_declare_all_work_complete(invalid_report):
    tracker = SolProductWork()
    feedback = tracker.feedback(
        {"summary": "Готово.", "remaining_work": invalid_report}, {},
        requested=True, preamble="",
    )
    assert "remaining_work" in feedback


async def test_sol_fixes_reported_list_in_current_source_before_empty_list_is_accepted():
    from tests.test_sol_legacy_product_handoff import PAGE, WATER, _repair_action, run_sol

    gaps = ["Добавить анкету.", "Записывать приёмы пищи."]
    repaired = WATER + "\nexport const diary = true;"
    empty = _repair_action("done", summary="Готово.", remaining_work=[])
    result, calls, tree = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER),
        _repair_action("build"),
        _repair_action("done", summary="Уже готов учёт воды.", remaining_work=gaps),
        empty,
        _repair_action("edit_file", path=PAGE, search=WATER, replace=repaired),
        empty,
        _repair_action("build"),
        empty,
    ])
    feedback = "\n".join(message["content"] for message in calls[3] if message["role"] == "user")
    assert all(item in feedback for item in gaps)
    assert result.done and result.needs_finalization and tree[PAGE] == repaired


async def test_empty_turn_export_preserves_the_reported_work_for_recovery():
    candidate = agent_builder.AgentResult(
        done=False, summary=MISSING, stop_reason=agent_builder.PRODUCT_WORK_REMAINING,
        files={"src/app/page.tsx": "water tracker"}, steps=23,
    )
    starter = {"src/app/layout.tsx": "platform layout"}
    existing = {"lib/old.ts": "prior source"}
    draft = {**starter, **candidate.files, "lib/water.ts": "saved earlier"}
    handle = SimpleNamespace(
        export_files=AsyncMock(return_value={}), snapshot_files=AsyncMock(return_value=draft),
    )
    operations = SimpleNamespace(emit=AsyncMock())
    result, provider_failure = await agent_generation.execute_agent_turn(
        _agent_res=candidate, _is_edit=False, _max_has_generated_snapshot=False,
        _max_seed_files=starter, _max_shell_enabled=False,
        baseline=SourceBaseline(None, None, existing), ids=None, is_free=False, project_info=None,
        prompt_text="", runtime=GenerationRuntime(handle=handle), plan=None, operations=operations,
        allow_autonomous_source_repair=True,
    )
    assert result.stop_reason == agent_builder.PRODUCT_WORK_REMAINING
    assert result.summary == MISSING and not result.done and not result.needs_finalization
    assert result.files == {
        "src/app/page.tsx": "water tracker", "lib/water.ts": "saved earlier", "lib/old.ts": "",
    }
    assert provider_failure is None
    operations.emit.assert_not_awaited()


@pytest.mark.parametrize(
    "existing_product,stale_finalization",
    [(True, False), (False, False), (True, True)],
)
async def test_reported_work_checkpoints_before_restoring_served_source(
    stopped_recovery, existing_product, stale_finalization,
):
    kwargs, tree, baseline, trace, _ = stopped_recovery
    candidate = kwargs["_agent_res"]
    candidate.stop_reason = agent_builder.PRODUCT_WORK_REMAINING
    candidate.summary = MISSING
    candidate.needs_finalization = stale_finalization
    kwargs["_max_has_generated_snapshot"] = existing_product
    if not existing_product:
        baseline.pop("src/app/page.tsx")
    runtime = kwargs["runtime"]
    runtime.handle.is_portable = lambda: True
    runtime.handle.snapshot_files = AsyncMock(return_value=tree)
    original = dict(tree)
    saved = {}

    async def save(_result):
        saved.update(await runtime.handle.snapshot_files())
        trace.append("checkpoint")

    kwargs["operations"].rollback_checkpoint = save
    with pytest.raises(RuntimeError) as stopped:
        await agent_recovery.recover_stopped_candidate(**kwargs, allow_source_repair=True)
    assert MISSING in str(stopped.value) and "rolled_back" in str(stopped.value)
    assert agent_builder.PRODUCT_WORK_REMAINING in str(stopped.value)
    assert saved == original and tree == baseline
    assert trace == [
        "checkpoint", "restore", "source_check", "compile_and_restart", "rollback_event",
    ]
