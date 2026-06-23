"""`_agent_result_message` — the agentic-build chat text must never leak the raw
internal summary on a non-done exit (the "hit step budget without calling done"
bug the user saw in chat)."""

from __future__ import annotations

from types import SimpleNamespace

from omnia_api.routers.messages import _agent_result_message


def _res(**kw) -> SimpleNamespace:
    base = {"done": False, "summary": "", "stop_reason": ""}
    base.update(kw)
    return SimpleNamespace(**base)


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

from omnia_api.routers.messages import _is_continue_request  # noqa: E402


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
