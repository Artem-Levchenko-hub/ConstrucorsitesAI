"""`_agent_result_message` — the agentic-build chat text must never leak the raw
internal summary on a non-done exit (the "hit step budget without calling done"
bug the user saw in chat)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnia_api.services.generation.agent_messages import (
    _agent_needs_continue_card,
    _agent_product_failure,
    _agent_result_message,
    _agent_step_budget,
    _is_continue_request,
)


def _res(**kw) -> SimpleNamespace:
    base = {"done": False, "summary": "", "stop_reason": ""}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "reason",
    [
        "max_steps",
        "provider_error",
        "infra_error",
        "verification_rolled_back",
        "unsafe_changes_rolled_back",
        "provider_stopped_rolled_back",
    ],
)
def test_unfinished_edit_is_a_failed_product_outcome(reason: str) -> None:
    assert (
        _agent_product_failure(
            _res(stop_reason=reason),
            verification_failed=False,
            finalization_complete=False,
        )
        is not None
    )


def test_red_verification_remains_failed_even_when_agent_claimed_done() -> None:
    assert (
        _agent_product_failure(
            _res(done=True),
            verification_failed=True,
            finalization_complete=False,
        )
        == "final verification did not succeed"
    )


def test_proven_done_noop_is_not_a_failed_edit() -> None:
    assert (
        _agent_product_failure(
            _res(done=True, files={}),
            verification_failed=False,
            finalization_complete=False,
        )
        is None
    )


def test_successful_coordinator_repair_can_complete_a_bounded_agent_exit() -> None:
    assert (
        _agent_product_failure(
            _res(stop_reason="max_steps", needs_finalization=True),
            verification_failed=True,
            finalization_complete=True,
        )
        is None
    )


@pytest.mark.parametrize(
    "res, finalized, card",
    [
        # The audit's case: the step budget ran out with complete source, the
        # coordinator finished the product — «Готово» must stand alone.
        (dict(stop_reason="max_steps", needs_finalization=True), True, False),
        (dict(stop_reason="max_steps"), False, True),
        (dict(stop_reason="max_steps", needs_finalization=True), False, True),
        (dict(done=True, stop_reason="done"), False, False),
        (dict(stop_reason="looping"), False, False),
    ],
)
def test_continue_card_follows_the_run_verdict(res, finalized, card) -> None:
    failure = _agent_product_failure(
        _res(**res), verification_failed=False, finalization_complete=finalized
    )
    assert _agent_needs_continue_card(_res(**res), product_failure=failure) is card
    # The card never accompanies a successful run.
    assert not (failure is None and card)


def test_pipeline_asks_the_verdict_instead_of_deciding_the_card_itself() -> None:
    """The contradiction came from a second, inline copy of the rule in the pipeline."""
    import ast
    from pathlib import Path

    from omnia_api.services.generation import agent_pipeline

    tree = ast.parse(Path(agent_pipeline.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_agent_needs_continue_card"
    ]
    assert len(calls) == 1
    assert [keyword.arg for keyword in calls[0].keywords] == ["product_failure"]
    inline_rules = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(side, ast.Constant) and side.value == "max_steps"
            for side in [node.left, *node.comparators]
        )
    ]
    assert inline_rules == [], "the pipeline must not compare stop_reason to max_steps itself"


def test_max_build_restores_proven_single_pass_budget() -> None:
    assert _agent_step_budget("max_miniapp", configured_steps=24) == 40
    assert _agent_step_budget("max_miniapp", configured_steps=48) == 48


def test_other_stacks_keep_their_configured_budget() -> None:
    assert _agent_step_budget("nextjs_postgres", configured_steps=24) == 24


def test_done_uses_model_summary() -> None:
    m = _agent_result_message(
        _res(done=True, summary="Собрал аптеку: каталог, кабинет, поиск.", stop_reason="done"),
        is_edit=False,
    )
    assert m == "Собрал аптеку: каталог, кабинет, поиск."


def test_done_empty_summary_falls_back_friendly() -> None:
    assert _agent_result_message(_res(done=True, summary=""), is_edit=False) == (
        "Готово — приложение собрано."
    )


def test_source_complete_handoff_keeps_finalization_message() -> None:
    message = _agent_result_message(
        _res(
            done=False,
            needs_finalization=True,
            summary="Исходники собраны; выполняю финальную проверку.",
            stop_reason="done",
        ),
        is_edit=False,
    )
    assert message == "Исходники собраны; выполняю финальную проверку."
    assert _agent_result_message(_res(done=True, summary="   "), is_edit=True) == (
        "Готово — правка применена."
    )


def test_max_steps_does_not_leak_raw_summary() -> None:
    m = _agent_result_message(
        _res(summary="hit step budget without calling done", stop_reason="max_steps"),
        is_edit=False,
    )
    assert "step budget" not in m and "done" not in m  # raw English never shown
    # status prose only — the «Продолжить» action lives on the card published
    # alongside (app_errors category="incomplete"), so the prose no longer repeats it.
    assert "часть уже на месте" in m


def test_looping_does_not_leak_internal_diagnostic() -> None:
    m = _agent_result_message(
        _res(summary="stuck repeating grep src/app", stop_reason="looping"),
        is_edit=False,
    )
    assert "stuck repeating" not in m and "grep" not in m
    assert "Сборка прервана" in m


def test_gateway_error_does_not_leak() -> None:
    m = _agent_result_message(
        _res(summary="gateway error after retries: ReadTimeout", stop_reason="error"),
        is_edit=True,
    )
    assert "gateway error" not in m and "ReadTimeout" not in m
    assert "правку" in m


def test_edit_max_steps_message_is_edit_flavoured() -> None:
    m = _agent_result_message(
        _res(summary="hit step budget without calling done", stop_reason="max_steps"),
        is_edit=True,
    )
    assert "step budget" not in m
    assert "правку" in m


# ── continue/resume detection — «продолжи» finishes a partial build ──────────


def test_continue_detected() -> None:
    for p in (
        "продолжи",
        "продолжай сборку",
        "доделай приложение",
        "доведи до конца",
        "достройка не закончилась, дострой",
        "finish the build please",
    ):
        assert _is_continue_request(p) is True, p


def test_continue_not_detected_for_edits_or_features() -> None:
    for p in (
        "поменяй цвет кнопки на синий",
        "добавь раздел отзывов",
        "сделай заголовок крупнее",
        "",
    ):
        assert _is_continue_request(p) is False, p


# ── «incomplete» card — the resumable-build affordance ──────────────────────


def test_app_errors_incomplete_category_renders_card() -> None:
    # The backend emits the exact <app-error> shape the web parser keys off; a
    # missing _DEFAULT_TITLE key would KeyError in publish()'s WS payload.
    from omnia_api.services import app_errors

    assert app_errors._DEFAULT_TITLE["incomplete"] == "Сборка не завершена"
    block = app_errors.render_block(
        category="incomplete",
        title="Сборка не завершена",
        detail="часть уже на месте",
        file=None,
        fixable=True,
    )
    assert 'category="incomplete"' in block
    assert 'fixable="1"' in block
    assert block.strip().startswith("<app-error") and block.strip().endswith("</app-error>")
