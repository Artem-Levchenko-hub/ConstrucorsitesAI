"""Tests for the intent triage router (Phase N, reworked 2026-06-06).

New contract: after the first build, the DEFAULT is a cheap surgical EDIT. Only
the first prompt, an explicit rebuild/redesign, or a genuine full-stack/backend
addition earns the expensive BUILD orchestration. Build-noun follow-ups
("добавь интро к сайту", "добавь раздел") are edits, not rebuilds.
"""

from __future__ import annotations

from omnia_api.services.intent_triage import CHEAP, ORCHESTRATE, decide_intent


def test_first_prompt_always_orchestrates() -> None:
    assert decide_intent("привет", is_first_prompt=True) == ORCHESTRATE
    # Even a trivial-looking first prompt is the initial build.
    assert decide_intent("просто кнопка", is_first_prompt=True) == ORCHESTRATE
    assert decide_intent("сделай лендинг кофейни", is_first_prompt=True) == ORCHESTRATE


def test_add_intro_to_existing_site_is_cheap() -> None:
    """Owner's regression (2026-06-06): "добавь интро к сайту" used to match the
    "сайт" keyword → full orchestration → whole-page rewrite + palette re-roll
    ("всё потерялось"). On an existing project it must be a cheap surgical edit."""
    assert decide_intent("добавь интро к сайту", is_first_prompt=False) == CHEAP
    assert decide_intent("добавь интро", is_first_prompt=False) == CHEAP


def test_section_add_on_existing_project_is_cheap() -> None:
    # Adding a section to a built page is a scoped insert, not a rebuild.
    assert decide_intent("добавь раздел с ценами", is_first_prompt=False) == CHEAP
    assert decide_intent("добавь секцию отзывов", is_first_prompt=False) == CHEAP
    assert decide_intent("добавь блок с FAQ", is_first_prompt=False) == CHEAP
    # Even with a payment-ish CTA word that used to over-trigger orchestration.
    assert decide_intent("добавь кнопку оплатить в hero", is_first_prompt=False) == CHEAP


def test_cosmetic_edit_is_cheap() -> None:
    assert (
        decide_intent(
            "покрась кнопку войти в синий", is_first_prompt=False, selected_count=1
        )
        == CHEAP
    )
    assert decide_intent("поменяй текст заголовка", is_first_prompt=False) == CHEAP
    assert decide_intent("сделай шрифт побольше", is_first_prompt=False) == CHEAP
    assert decide_intent("поменяй фон секции на тёмный", is_first_prompt=False) == CHEAP


def test_selected_elements_are_cheap_even_in_a_batch() -> None:
    # A pointed element (any count) is the strongest "edit just this" signal.
    assert (
        decide_intent("вот тут поинтереснее обыграй", is_first_prompt=False, selected_count=1)
        == CHEAP
    )
    assert decide_intent("поправь это", is_first_prompt=False, selected_count=3) == CHEAP


def test_long_detailed_edit_stays_cheap() -> None:
    """A long, detailed edit on an existing project is a multi-<edit> tweak, not
    a rebuild — it must NOT be dragged into the premium pipeline by length."""
    long_prompt = "поменяй заголовок hero, подзаголовок, и три буллета ниже " * 4
    assert len(long_prompt) > 200
    assert decide_intent(long_prompt, is_first_prompt=False) == CHEAP


def test_explicit_rebuild_orchestrates() -> None:
    assert decide_intent("переделай весь сайт заново", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("сделай сайт с нуля", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("нужен полный редизайн", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("пересоздай страницу", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("поменяй дизайн полностью", is_first_prompt=False) == ORCHESTRATE


def test_bare_peredelai_on_one_thing_is_cheap() -> None:
    """"переделай" alone (a single element) must stay an edit — only whole-page
    rebuild phrases ("переделай сайт", "с нуля", "заново") orchestrate."""
    assert decide_intent("переделай кнопку покрасивее", is_first_prompt=False) == CHEAP
    assert decide_intent("переделай этот заголовок", is_first_prompt=False, selected_count=1) == CHEAP


def test_structural_fullstack_addition_orchestrates() -> None:
    assert decide_intent("добавь бэкенд на FastAPI", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("сделай многостраничным", is_first_prompt=False) == ORCHESTRATE
    assert decide_intent("прикрути базу данных", is_first_prompt=False) == ORCHESTRATE
