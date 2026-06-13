"""V2.5-override — classify_preset honours the onboarding spec (money-free).

Generation-side of the chip→design causality bridge AT THE CLASSIFIER. The
substring/heuristic passes keep their confident industry pick unchanged (the
aesthetic of the chip is honoured downstream by V2.5c's writer); the persisted
``discovery_spec`` only tie-breaks the genuinely-ambiguous LLM-fallback path,
where the user's explicit onboarding choices (above all the section set) are the
strongest remaining industry signal.

Everything here runs WITHOUT a browser, an LLM, or an orchestrator — the LLM
prompt is built deterministically and the confident-industry path is exercised
through the pure substring matcher. That is the contract: the override is
replayable money-free.
"""

from __future__ import annotations

import pytest

from omnia_api.services import preset_classifier as pc

# ── _build_classifier_prompt threads the spec as a hint ───────────────────────


def test_spec_none_prompt_byte_identical() -> None:
    """No spec → prompt is byte-identical to the pre-V2.5-override build."""
    base = pc._build_classifier_prompt("Кафе", "freeform", "сделай сайт")
    with_none = pc._build_classifier_prompt("Кафе", "freeform", "сделай сайт", None)
    assert base == with_none


def test_empty_spec_adds_no_hint() -> None:
    """A spec whose every axis abstains carries no assertable choice → no hint."""
    empty = {"dark_mode": None, "primary_family": None, "sections": [], "tone": None}
    base = pc._build_classifier_prompt("Кафе", "freeform", "сделай сайт")
    with_empty = pc._build_classifier_prompt("Кафе", "freeform", "сделай сайт", empty)
    assert base == with_empty
    assert pc._format_spec_hint(empty) == ""
    assert pc._format_spec_hint(None) == ""


def test_sections_reach_the_prompt() -> None:
    """The section set — the strongest industry signal — lands in the LLM prompt."""
    spec = {"sections": ["catalog", "cart"], "tone": None, "primary_family": None}
    prompt = pc._build_classifier_prompt("Магазин", "freeform", "сделай сайт", spec)
    assert "catalog" in prompt
    assert "cart" in prompt
    assert "Пожелания из онбординга" in prompt


def test_tone_family_dark_reach_the_prompt() -> None:
    """Tone / palette family / dark-mode chips all surface in the hint block."""
    spec = {
        "dark_mode": True,
        "primary_family": "violet",
        "sections": [],
        "tone": "premium",
    }
    prompt = pc._build_classifier_prompt("Стартап", "freeform", "сделай сайт", spec)
    assert "violet" in prompt
    assert "premium" in prompt
    assert "тёмная" in prompt
    # Light-mode chip renders the other branch.
    light = dict(spec, dark_mode=False)
    assert "светлая" in pc._format_spec_hint(light)


# ── confident industry is NEVER overridden by the spec (non-regression) ───────


@pytest.mark.asyncio
async def test_confident_industry_beats_conflicting_spec() -> None:
    """«клиника» → medical-clinic even with a retail-leaning section spec.

    The adversarial case framed correctly for this data model: a confident
    industry keyword keeps its industry preset (substring pass), and the chip's
    aesthetic is honoured downstream (V2.5c) — the spec must NOT swap the preset
    here, or industry-appropriate structure regresses. Pure substring path: no
    LLM, money-free.
    """
    retail_spec = {"sections": ["catalog", "cart"], "tone": "playful"}
    picked = await pc.classify_preset(
        project_name="Моя клиника",
        template="freeform",
        first_prompt="стоматология в центре",
        discovery_spec=retail_spec,
    )
    assert picked == "medical-clinic"


@pytest.mark.asyncio
async def test_confident_heuristic_beats_spec() -> None:
    """A confident keyword-heuristic pick also wins ahead of the spec hint."""
    # «юрист» → law-authority via substring; a premium/dark spec must not flip it.
    picked = await pc.classify_preset(
        project_name="Юрист Иванов",
        template="freeform",
        first_prompt="юридические услуги для бизнеса",
        discovery_spec={"primary_family": "violet", "dark_mode": True},
    )
    assert picked == "law-authority"
