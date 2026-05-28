"""Tests for the intent triage router (Phase N)."""

from __future__ import annotations

from omnia_api.services.intent_triage import CHEAP, ORCHESTRATE, decide_intent


def test_first_prompt_always_orchestrates() -> None:
    assert decide_intent("привет", is_first_prompt=True) == ORCHESTRATE
    # Even a trivial-looking first prompt is the initial build.
    assert decide_intent("просто кнопка", is_first_prompt=True) == ORCHESTRATE


def test_cosmetic_edit_is_cheap() -> None:
    assert (
        decide_intent("покрась кнопку войти в синий", is_first_prompt=False, selected_count=1)
        == CHEAP
    )
    assert decide_intent("поменяй текст заголовка", is_first_prompt=False) == CHEAP
    assert decide_intent("сделай шрифт побольше", is_first_prompt=False) == CHEAP


def test_structural_or_backend_change_orchestrates() -> None:
    assert decide_intent("добавь раздел с ценами", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("прикрути авторизацию по телефону", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("переделай весь сайт заново", is_first_prompt=False) == ORCHESTRATE


def test_batch_of_edits_orchestrates() -> None:
    assert decide_intent("поправь это", is_first_prompt=False, selected_count=3) == ORCHESTRATE


def test_long_detailed_prompt_orchestrates() -> None:
    long_prompt = "нужно добавить много деталей и требований по каждому пункту, " * 4
    assert len(long_prompt) > 200
    assert decide_intent(long_prompt, is_first_prompt=False) == ORCHESTRATE


def test_very_short_followup_is_cheap() -> None:
    assert decide_intent("сделай поярче", is_first_prompt=False) == CHEAP


def test_targeted_single_element_edit_is_cheap() -> None:
    assert (
        decide_intent("вот тут поинтереснее обыграй", is_first_prompt=False, selected_count=1)
        == CHEAP
    )
