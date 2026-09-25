"""Краткая строка об отказе проверки должна называть отказ, а не заголовок.

23.09.2026 адаптивный откат на проде не удался, и владелец получил сообщение
«Почти готово, но осталась ошибка: [typecheck]». В журнале оператора стояло то
же самое. Это не просто бесполезно — это неправда: проверка типов как раз
прошла, а упали тесты приложения. Человек с такой подсказкой идёт искать ошибку
типов, которой нет.

Причина была в одной строке: сводка бралась как первая строка отчёта, а отчёт
начинается с заголовка раздела. Ниже закреплено поведение на НАСТОЯЩЕМ отчёте
того прогона (2236 символов, сокращены до сути) и на соседних случаях.
"""

from __future__ import annotations

from yleum_api.services.generation.agent_messages import summarize_check_failure

# Дословный (с сокращениями) отчёт прогона c9b8d59a с прода 23.09.2026.
LIVE_REPORT = """[typecheck]
> omnia-max-miniapp@0.1.0 typecheck /workspace
> tsc --noEmit


[targeted-test]
> omnia-max-miniapp@0.1.0 test /workspace
> node --test tests/*.test.mjs

TAP version 13
# Subtest: leads: create, list scoped by owner, required note, and owned delete
not ok 1 - leads: create, list scoped by owner, required note, and owned delete
  ---
  duration_ms: 72.048985
  failureType: 'testCodeFailure'
  error: 'Missing expected rejection.'
  ...
# Subtest: leads table schema matches the API contract (required columns)
not ok 2 - leads table schema matches the API contract (required columns)
"""


def test_the_live_report_names_the_failing_test_not_the_section_header() -> None:
    summary = summarize_check_failure(LIVE_REPORT)

    assert "[typecheck]" not in summary
    assert "leads: create, list scoped by owner" in summary


def test_the_live_report_names_the_stage_that_actually_failed() -> None:
    """Самое важное: не обвинять проверку типов, когда она прошла."""
    summary = summarize_check_failure(LIVE_REPORT)

    assert summary.startswith("тесты приложения:")
    assert "тип" not in summary.split(":", 1)[0]


def test_a_real_type_error_is_quoted_as_is() -> None:
    report = """[typecheck]
> tsc --noEmit

src/app/page.tsx(42,13): error TS2739: Type '{}' is missing properties.
"""

    summary = summarize_check_failure(report)

    assert "error TS2739" in summary
    assert summary.startswith("проверка типов:")


def test_the_first_failure_wins_when_several_stages_break() -> None:
    # Разделы идут по порядку выполнения; чинить надо с первого.
    report = """[typecheck]
src/a.ts(1,1): error TS1005: ';' expected.

[targeted-test]
not ok 1 - что-то другое
"""

    assert "TS1005" in summarize_check_failure(report)


def test_an_unrecognised_report_behaves_exactly_as_before() -> None:
    """Неизвестный формат не повод выдумывать: берём первую непустую строку."""
    report = "\n\nчто-то пошло не так\nи ещё строка\n"

    assert summarize_check_failure(report) == "что-то пошло не так"


def test_a_report_of_only_headers_never_returns_an_empty_string() -> None:
    assert summarize_check_failure("[typecheck] \n\n[targeted-test] \n") == "ошибка проверки"


def test_the_summary_stays_short_enough_for_a_chat_line() -> None:
    long_name = "х" * 900
    report = f"[targeted-test] \nnot ok 1 - {long_name}\n"

    assert len(summarize_check_failure(report)) <= 240
