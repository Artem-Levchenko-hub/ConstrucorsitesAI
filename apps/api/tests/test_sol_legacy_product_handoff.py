"""Production Sol text actions must finish a reported gap in the existing project."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services import agent_builder
from yleum_api.services.agent_output_budget import SOL_MODEL
from yleum_api.services.agent_product_work import SOL_COMPLETION_REPORT
from yleum_api.services.generation.agent_generation import uses_native_protocol
from yleum_api.services.generation.recovery_execution import GenerationYielded

from .test_agent_builder import _ok_executor, _repair_action

PAGE = "src/app/page.tsx"
WATER = "export const water = 250;"
GAP = (
    "Это не полный MVP: анкета, двухдневный план, отметки блюд из плана, "
    "покупки и личный профиль не реализованы в этой правке."
)
UNVERIFIED = "Отображение на телефоне не проверено."


async def run_sol(actions, *, after_reply=None, deadline=None, segment_steps=None):
    tree, calls, executed = {}, [], []

    async def snapshot():
        return dict(tree)

    async def complete(messages, *args, **kwargs):
        index = len(calls)
        assert index < len(actions), "Unexpected additional provider turn"
        calls.append([dict(message) for message in messages])
        if after_reply is not None:
            after_reply(index)
        return actions[index]

    assert not uses_native_protocol(SOL_MODEL, None, configured=True, directed_repair=False)
    result = await agent_builder.run_agent_build(
        model=SOL_MODEL, system_prompt="Create a working app.", user_prompt="Сделай нутрициолога.",
        complete=complete, execute=_ok_executor(executed, tree), snapshot_files=snapshot,
        coordinator_handoff=True, completion_check=lambda files, evidence: None,
        ship_green_on_abort=True, max_steps=segment_steps or len(actions),
        max_segments=2 if segment_steps else 1, edit_deadline=deadline,
    )
    return result, calls, tree


@pytest.mark.parametrize("report", [
    _repair_action("done", summary="Добавлен счётчик воды. " + GAP),
    _repair_action("done", summary="Добавлен счётчик воды.", remaining_work=GAP),
    _repair_action("done", summary="Добавлен счётчик воды.", remaining_work="") + "\n" + GAP,
], ids=["production-summary", "explicit-remaining-work", "disclosure-after-action"])
async def test_reported_gap_requires_source_repair_and_fresh_build(report):
    repaired = WATER + "\nexport const planDays = 2;"
    premature = _repair_action("done", summary="Готово.", remaining_work="")
    result, calls, tree = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER),
        _repair_action("build"),
        report,
        premature,
        _repair_action("edit_file", path=PAGE, search=WATER, replace=repaired),
        premature,
        _repair_action("build"),
        _repair_action("done", summary="Доработан сохранённый исходник.", remaining_work=""),
    ])

    assert len(calls) == 8
    feedback = "\n".join(message["content"] for message in calls[3] if message["role"] == "user")
    assert "двухдневный план" in feedback and "покупки" in feedback
    assert all("двухдневный план" not in message["content"]
               for call in calls for message in call if message["role"] == "system")
    assert result.done and result.needs_finalization
    assert result.summary == "Доработан сохранённый исходник."
    assert tree[PAGE] == repaired


async def test_unverified_device_check_alone_does_not_create_missing_product_work():
    result, calls, tree = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER),
        _repair_action("build"),
        _repair_action("done", summary="Учёт воды готов. " + UNVERIFIED, remaining_work=""),
    ])
    assert len(calls) == 3 and tree[PAGE] == WATER
    assert result.done and result.needs_finalization


@pytest.mark.parametrize("first_report,unchanged_report", [
    (
        _repair_action("done", summary=GAP),
        _repair_action("done", summary="Готово.", remaining_work=""),
    ),
    (GAP, GAP),
], ids=["done-without-repair", "plain-text-only"])
async def test_repeated_reports_stop_at_existing_repeat_bound_and_keep_the_draft(
    first_report, unchanged_report,
):
    result, calls, tree = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER),
        _repair_action("build"),
        first_report,
        *([unchanged_report] * (agent_builder._REPEAT_ABORT_AT + 3)),
    ])
    assert len(calls) == 3 + agent_builder._REPEAT_ABORT_AT
    assert result.stop_reason == agent_builder.PRODUCT_WORK_REMAINING
    assert not result.done and not result.needs_finalization and result.proof_checkpoint is None
    assert tree == {PAGE: WATER} and result.files == tree
    assert "двухдневный план" in result.summary


async def test_same_run_keeps_report_when_a_new_segment_rebuilds_unchanged_source():
    repaired = WATER + "\nexport const shopping = [];"
    done = _repair_action("done", summary="Доработано.", remaining_work="")
    result, calls, tree = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER),
        _repair_action("read_file", path=PAGE),
        _repair_action("build"),
        _repair_action("docs", query="integration"),
        _repair_action("done", summary=GAP),
        _repair_action("build"),
        done,
        _repair_action("edit_file", path=PAGE, search=WATER, replace=repaired),
        _repair_action("build"),
        done,
    ], segment_steps=5)
    assert len(calls) == 10 and result.segments == 2
    assert result.done and result.needs_finalization and tree[PAGE] == repaired


async def test_deadline_with_reported_gap_preserves_draft_without_promotion(monkeypatch):
    start = datetime.now(UTC)

    class Clock(datetime):
        current = start

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(agent_builder, "datetime", Clock)

    def expire_on_report(index):
        if index == 2:
            Clock.current = start + timedelta(minutes=2)

    result, calls, tree = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER),
        _repair_action("build"),
        _repair_action("done", summary="Добавлен счётчик воды.", remaining_work=GAP),
    ], after_reply=expire_on_report, deadline=start + timedelta(minutes=1))

    assert len(calls) == 3 and tree[PAGE] == WATER
    assert not result.done and not result.needs_finalization and result.proof_checkpoint is None
    assert "двухдневный план" in result.summary


@pytest.mark.parametrize("old_frame", [True, False], ids=["pre-upgrade", "outstanding-work"])
async def test_cold_sol_uses_original_paid_frame_and_keeps_the_reported_gap(old_frame):
    tree, executed = {}, []
    saved = None
    cold = False
    reconciled = False
    before_yield = [
        _repair_action("write_file", path=PAGE, content=WATER), _repair_action("build"),
    ]
    if not old_frame:
        before_yield.append(_repair_action("done", summary=GAP))
    repaired = WATER + "\nexport const planDays = 2;"
    after_restore = [
        _repair_action("done", summary="Закончено.", remaining_work=""),
        _repair_action("edit_file", path=PAGE, search=WATER, replace=repaired),
        _repair_action("build"),
        _repair_action("done", summary="Рацион готов.", remaining_work=""),
    ]
    if old_frame:
        after_restore.insert(0, _repair_action("build"))

    async def provider(frame, invoke):
        nonlocal saved, reconciled
        if not cold and not before_yield:
            saved = deepcopy(frame)
            if old_frame:
                value = saved["value"]
                value.pop("product_work", None)
                driver.context["legacy_outer"].pop("product_work", None)
                system = value["call_messages"][0]
                system["content"] = system["content"].replace("\n" + SOL_COMPLETION_REPORT, "")
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=1, phase="waiting_dependency",
            )
        if cold and not reconciled:
            assert frame == saved
            reconciled = True
            return _repair_action("done", summary=GAP) if old_frame else _repair_action("build")
        return await invoke()

    async def complete(*args, **kwargs):
        scripted = after_restore if cold else before_yield
        assert scripted, "Unexpected new provider call"
        return scripted.pop(0)

    async def tool(_key, _action, invoke):
        return await invoke()

    async def snapshot():
        return dict(tree)

    driver = SimpleNamespace(
        context={}, restore_frame=lambda _: deepcopy(saved["value"]) if cold else None,
        provider=provider, tool=tool,
    )

    async def run():
        return await agent_builder.run_agent_build(
            model=SOL_MODEL, system_prompt="sys", user_prompt="Сделай нутрициолога.",
            execute=_ok_executor(executed, tree), complete=complete, continuation=driver,
            coordinator_handoff=True, completion_check=lambda *_: None,
            snapshot_files=snapshot, max_steps=10,
        )

    with pytest.raises(GenerationYielded):
        await run()
    assert saved is not None and tree[PAGE] == WATER
    cold = True
    result = await run()
    assert reconciled and not after_restore
    assert result.done and result.needs_finalization and result.summary == "Рацион готов."
    assert tree[PAGE] == repaired
