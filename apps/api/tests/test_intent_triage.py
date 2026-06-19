"""Tests for the intent triage router (Phase N, reworked 2026-06-06).

New contract: after the first build, the DEFAULT is a cheap surgical EDIT. Only
the first prompt, an explicit rebuild/redesign, or a genuine full-stack/backend
addition earns the expensive BUILD orchestration. Build-noun follow-ups
("добавь интро к сайту", "добавь раздел") are edits, not rebuilds.
"""

from __future__ import annotations

import pytest

from omnia_api.services.intent_triage import CHEAP, ORCHESTRATE, decide_intent


def test_first_prompt_always_orchestrates() -> None:
    assert decide_intent("привет", is_first_prompt=True) == ORCHESTRATE
    # Even a trivial-looking first prompt is the initial build.
    assert decide_intent("просто кнопка", is_first_prompt=True) == ORCHESTRATE
    assert decide_intent("сделай лендинг кофейни", is_first_prompt=True) == ORCHESTRATE


def test_selected_element_always_edits_even_if_first_build() -> None:
    """Owner regression (2026-06-06): after a rollback to the starter snapshot the
    project looks like a first build, but a CLICK on the page means there's a zone
    to edit — it must be a scoped edit, never a full rebuild that loses images."""
    assert (
        decide_intent("поменяй фон на графику", is_first_prompt=True, selected_count=1)
        == CHEAP
    )
    # And even with a build-noun in the prompt, a selection keeps it an edit.
    assert (
        decide_intent("сделай этот раздел ярче", is_first_prompt=True, selected_count=2)
        == CHEAP
    )


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


# ── BLIND SPOT H1 (dogfood-eval run #1, 2026-06-16) ──────────────────────────
# Owner: "написал «создай сайт» → получил статику; кинул 5 промптов «переделай в
# полноценное веб-приложение» — генератор по сути ничего не делал".
#
# Root cause (confirmed by code-path proof, not a guess):
#   1. `decide_intent` has no "app-ification" intent — "переделай это в
#      полноценное веб-приложение: вход, личный кабинет, база записей" matches
#      neither _REBUILD_KEYWORDS ("переделай это" ∉ set) nor _STRUCTURAL_KEYWORDS
#      ("база записей" ≠ "база данных"; auth/login stems deliberately excluded to
#      avoid false-firing on "кнопка входа"). → falls to CHEAP surgical edit.
#   2. Even if it returned ORCHESTRATE, `stack_routing.switch_to_stack` is called
#      ONLY inside the `is_first_build` branch (routers/messages.py:675), so a
#      follow-up can NEVER escalate static→container. The static page is
#      surgical-edited in place; the user's "make it a real app" is a no-op.
#
# These specs lock the acceptance criterion for the H1 fix (see the PROPOSAL in
# docs/plans/2026-06-16-dogfood-eval-routine.md). The xfail runs green today (the
# bug is present) and will XPASS when app-ification follow-ups are routed to a
# (consent-gated, non-destructive) stack escalation. `strict=False` so CI never
# breaks on the current/broken state.
_APPIFY_FOLLOWUPS = [
    "переделай это в полноценное веб-приложение: вход, личный кабинет, база записей",
    "сделай настоящее приложение с авторизацией и личным кабинетом",
    "хочу чтобы пользователи могли регистрироваться и сохранять свои записи",
    "добавь вход и кабинет, чтобы клиенты записывались онлайн",
]


@pytest.mark.xfail(
    reason="H1 blind spot: app-ification follow-ups are not detected — they fall "
    "to CHEAP surgical edit of static HTML instead of escalating the stack. "
    "Remove this marker when the fix lands.",
    strict=False,
)
def test_appification_followup_should_escalate_not_surgical_edit() -> None:
    # Desired: a "make-this-a-real-app" follow-up on a built (static) project must
    # NOT be treated as a cheap surgical edit of the static page.
    for prompt in _APPIFY_FOLLOWUPS:
        assert decide_intent(prompt, is_first_prompt=False) == ORCHESTRATE


def test_appification_followup_is_currently_cheap_evidence() -> None:
    """Evidence lock (not desired behavior): documents that, TODAY, every
    app-ification follow-up returns CHEAP. If this ever changes, the xfail above
    starts XPASSing and both markers should be revisited together."""
    for prompt in _APPIFY_FOLLOWUPS:
        assert decide_intent(prompt, is_first_prompt=False) == CHEAP
