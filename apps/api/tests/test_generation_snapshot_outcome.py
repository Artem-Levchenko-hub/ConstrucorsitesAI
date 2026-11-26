from types import SimpleNamespace
from typing import cast

import pytest

from yleum_api.services.generation.outcome import advice_for_published_source, snapshot_outcome
from yleum_api.services.generation.recovery_acceptance import AcceptanceSealV2, MealAcceptanceSeal
from yleum_api.services.max_finalization import ProofBundle


def proof(build: str, runtime: str) -> ProofBundle:
    return cast(ProofBundle, SimpleNamespace(
        full_build=SimpleNamespace(outcome=build), runtime=SimpleNamespace(outcome=runtime),
    ))


def test_handoff_belongs_to_original_written_or_restored_snapshot_only():
    advice = {"builder_next": "Подключить источник рецептов"}
    written = {"menu": "new"}
    def choose(published):
        return advice_for_published_source(advice, authored=written, base={"v": "older"},
                                           restored={"v": "draft"}, published=published)
    assert choose(written) == advice
    assert choose({"v": "draft", **written}) == advice
    assert choose({"v": "older", **written}) is None
    assert choose({"v": "draft", **written, "source": "connected"}) is None


def test_saved_snapshot_does_not_imply_main_action_or_customer_obligation():
    advice = {
        "primary_action": "Составить рацион", "builder_next": "Подключить источник рецептов.",
        "owner_input": "Пришлите базу", "owner_request_quote": "я предоставлю рецепты",
    }
    result = snapshot_outcome(proof("green", "green"), None, advice, owner_message="Хочу рацион")
    assert "Сборка прошла" in result and "запустилось" in result
    assert "проверить в превью действие «Составить рацион»" in result
    assert "Подключить источник рецептов." in result and "От вас" not in result


def test_standard_ready_snapshot_uses_one_sentence():
    result = snapshot_outcome(proof("green", "green"), None, None, owner_message="")
    assert result == "Приложение готово."


def test_version_report_leads_with_visible_changes_and_separates_verified_checks():
    changes = ["Добавлена кнопка «Записаться».", "Изменён заголовок «Расписание»."]
    result = snapshot_outcome(proof("green", "green"), None, None, owner_message="",
                              changes=changes)
    assert result.startswith("Приложение готово.\n\n**Что изменилось**\n\n")
    assert "\n".join(f"- {change}" for change in changes) in result
    assert result.index(changes[-1]) < result.index("Сборка прошла.")
    assert "превью" in result and "проверен сценарий" not in result


def test_version_report_retains_known_failure_without_announcing_readiness():
    result = snapshot_outcome(proof("green", "green"), None, None, owner_message="",
                              saw_failed_actions=True, changes=["Добавлен экран «Расписание»."])
    assert result.startswith("Версия сохранена.")
    assert "Расписание" in result and "требуется повторная проверка" in result
    assert "Приложение готово." not in result


def test_version_with_open_owner_action_reports_a_build_without_claiming_readiness():
    result = snapshot_outcome(
        proof("green", "green"), None,
        {"primary_action": "Сохранить меню", "builder_next": "Подключить источник рецептов"},
        owner_message="", changes=["Добавили кнопку «Сохранить меню»."],
    )
    assert result.startswith("Версия собрана.")
    assert "Осталось проверить в превью действие «Сохранить меню»" in result
    assert "Подключить источник рецептов" in result


def test_private_input_requires_original_commitment_and_displays_the_original():
    advice = {"owner_input": "Внесите предоплату", "owner_request_quote": "Я пришлю свои цены"}
    result = snapshot_outcome(proof("unknown", "unknown"), None, advice,
                              owner_message="Нужен каталог. Я пришлю свои цены.")
    assert "От вас по исходному запросу — «Я пришлю свои цены»" in result
    assert all(value not in result for value in ["предоплату", "Сборка прошла", "запустилось"])


@pytest.mark.parametrize(("seal", "checks"), [
    (MealAcceptanceSeal.model_construct(), ("список покупок", "скачали PDF")),
    (AcceptanceSealV2.model_construct(), ("создали заметку", "после удаления")),
])
def test_functional_result_comes_from_current_publication_acceptance(seal, checks):
    result = snapshot_outcome(proof("green", "green"), seal, None, owner_message="")
    assert all(check in result for check in checks)
    assert "Осталось проверить" not in result


@pytest.mark.parametrize(("quote", "original"), [
    ("я предоставлю каталог", "Я не обещаю, что я предоставлю каталог."),
    ("I can provide a catalog", "I can provide a catalog."),
    ("Я пришлю цены", "Я пришлю цены?"),
    ("Я пришлю цены если смогу", "Я пришлю цены если смогу."),
])
def test_optional_or_out_of_context_quotes_do_not_assign_work_to_owner(quote, original):
    advice = {"owner_input": "Нужен каталог", "owner_request_quote": quote}
    result = snapshot_outcome(proof("green", "green"), None, advice, owner_message=original,
                              saw_failed_actions=True)
    assert "От вас" not in result
    assert "Со стороны Yleum требуется повторная проверка" in result
