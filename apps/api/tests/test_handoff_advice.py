"""Author advice survives real completion paths without granting completion authority."""
import pytest

from yleum_api.services import agent_native
from yleum_api.services.agent_output_budget import SOL_MODEL
from yleum_api.services.generation.handoff_advice import normalize_handoff
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.generation.recovery_pipeline import _result_data, _result_from_data

from .test_agent_native_prose_progress import tool
from .test_sol_legacy_product_handoff import GAP, PAGE, WATER, _repair_action, run_sol

ACTION = {"primary_action": "Откройте анкету и заполните профиль."}
NEXT = {"builder_next": "Добавить сохранение анкеты."}


@pytest.mark.parametrize("unsafe", [
    "Перейдите на https://example.com", "Откройте example.ru", "Проверьте user@example.ru",
    "<script>test</script>", "Выполните fetch('/api')", "Первая строка\nВторая строка",
    "Тест sk-" + "a" * 32, "Повторите " + "действие " * 35, "Передайте пароль",
    "Текст\u202eпроверки", "Откройте **главную**", "Проверьте /project/config",
])
def test_unsafe_advice_drops_whole_field(unsafe):
    assert normalize_handoff({**ACTION, "builder_next": unsafe}) == ACTION


def test_model_data_has_no_completion_authority_and_owner_fields_are_paired():
    quote = "Фотографии пришлю отдельно"
    owner = {"owner_input": "Добавьте свои фотографии.", "owner_request_quote": quote}
    assert normalize_handoff({**owner, "verified": True, "status": "completed"}) == owner
    assert normalize_handoff({"owner_input": "Почините сборку."}) is None
    assert normalize_handoff({"owner_request_quote": quote}) is None
    assert normalize_handoff({**owner, "owner_input": "Исправьте сборку приложения."}) is None


async def test_legacy_partial_and_repeated_done_replace_the_advice_without_extra_turns():
    write = _repair_action("write_file", path=PAGE, content=WATER, handoff=ACTION)
    build = _repair_action("build")
    partial = _repair_action("done", summary="Добавлена вода.", remaining_work=GAP, handoff=NEXT)
    stopped, calls, _ = await run_sol([write, build, partial])
    assert len(calls) == 3 and not stopped.done
    assert stopped.stop_reason == "product_work_remaining" and stopped.handoff == NEXT
    result, calls, _ = await run_sol([
        write, build, partial,
        _repair_action(
            "edit_file", path=PAGE, search=WATER, replace=WATER + "\nexport const q = 1;",
        ),
        build, _repair_action("done", summary="Готово.", remaining_work=""),
    ])
    assert len(calls) == 6 and result.done and result.handoff is None


async def test_last_changed_write_can_supply_advice_without_done_or_extra_call():
    write = _repair_action("write_file", path=PAGE, content=WATER, handoff=ACTION)
    result, calls, _ = await run_sol([write])
    assert len(calls) == 1 and result.handoff == ACTION
    later_edit = _repair_action(
        "edit_file", path=PAGE, search=WATER, replace=WATER + "\n// updated",
    )
    result, calls, _ = await run_sol([write, later_edit])
    assert len(calls) == 2 and result.handoff is None


async def test_cold_finalization_accepts_old_shape_and_round_trips_current_advice():
    result, _, _ = await run_sol([
        _repair_action("write_file", path=PAGE, content=WATER), _repair_action("build"),
        _repair_action("done", summary="Готово.", remaining_work="", handoff=ACTION),
    ])
    wire = _result_data(result)
    assert _result_from_data(wire).handoff == ACTION
    del wire["handoff"]
    assert _result_from_data(wire).handoff is None
    assert _result_data(_result_from_data(wire)) == wire
    assert _result_from_data({**wire, "handoff": {"primary_action": 5}}).handoff is None
    with pytest.raises(RecoveryContractError):
        _result_from_data({**wire, "unexpected": True})


@pytest.mark.parametrize("model,include_advice,unfinished", [
    ("claude-sonnet-4-5", True, False), ("claude-sonnet-4-5", False, False),
    (SOL_MODEL, True, True),
])
async def test_native_keeps_advice_optional_and_preserves_product_gate(
    monkeypatch, model, include_advice, unfinished,
):
    from yleum_api.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://qa:qa@127.0.0.1:59983/qa_disposable")
    monkeypatch.setenv("JWT_SECRET", "qa-handoff-advice-key-at-least-32-bytes")
    get_settings.cache_clear()
    done_args = {"summary": "Анкета готова."}
    if include_advice:
        done_args["handoff"] = ACTION
    if unfinished:
        done_args["remaining_work"] = GAP
    replies = iter([
        tool("write_file", {"path": PAGE, "content": WATER}),
        tool("build", {}), tool("done", done_args),
    ])
    work = {}

    async def provider(*args, **kwargs):
        schema = next(x["input_schema"] for x in kwargs["tools"] if x["name"] == "done")
        assert "handoff" in schema["properties"] and "handoff" not in schema["required"]
        assert ("remaining_work" in schema["required"]) is unfinished
        return next(replies)

    async def execute(action):
        if action.name == "write_file":
            work[action.path] = action.args["content"]
        return {"ok": True}

    async def snapshot():
        return dict(work)

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    try:
        result = await agent_native.run_native_build(
            model=model, system="MAX VERIFICATION OVERRIDE",
            task="Сделай анкету.", execute=execute, snapshot_files=snapshot,
            max_steps=3, max_segments=1, portable_cell=True, coordinator_handoff=True,
        )
    finally:
        get_settings.cache_clear()
    assert result.done is not unfinished and result.handoff == (ACTION if include_advice else None)
    if unfinished:
        assert result.stop_reason == "product_work_remaining" and GAP in result.summary
