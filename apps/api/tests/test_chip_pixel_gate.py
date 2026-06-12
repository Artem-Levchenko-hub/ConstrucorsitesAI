"""Chip→Pixel fidelity gate — request↔output truth check (V1.6 4/5).

The gate is "JS extracts, Python scores", so the whole thing is exercised here
with hand-built observation dicts — no browser, no LLM. Each axis has a CLEAN
case (honoured → 0 findings) and a RED case (contradicted → exactly that axis
fires), plus the headline contract: a fixed render + a *swapped spec* flips the
verdict. The floor only goes up.
"""

from omnia_api.services.chip_pixel_gate import (
    PALETTE_BG,
    PRIMARY_FAMILY,
    SECTION_ANCHOR,
    TONE_MARKER,
    FidelitySpec,
    evaluate_fidelity,
    family_of_hue,
)

# Seeded "dark + violet" palette signals: near-black surface, violet CTA (#A855F7).
DARK_BG = [10, 10, 10, 1]
LIGHT_BG = [250, 250, 250, 1]
VIOLET_FILL = {"bg": [168, 85, 247, 1], "tag": "button", "area": 6000}  # hue ≈271°
EMERALD_FILL = {"bg": [5, 150, 105, 1], "tag": "button", "area": 6000}  # hue ≈161°


def _obs(
    *,
    page_bg=DARK_BG,
    fills=(VIOLET_FILL,),
    ids=(),
    nav=(),
    headings=(),
    tone=None,
):
    return {
        "pageBg": page_bg,
        "fills": list(fills),
        "ids": list(ids),
        "navHrefs": list(nav),
        "headings": list(headings),
        "declaredTone": tone,
    }


def _full_obs(tone=None):
    """A render that honours dark + violet + [каталог, отзывы, контакты]."""
    return _obs(
        page_bg=DARK_BG,
        fills=(VIOLET_FILL,),
        ids=("catalog", "reviews", "contact"),
        headings=("Каталог", "Отзывы клиентов", "Контакты"),
        tone=tone,
    )


# ── hue families ───────────────────────────────────────────────────────────────


def test_family_of_hue_violet():
    assert family_of_hue(271.0) == "violet"


def test_family_of_hue_emerald():
    assert family_of_hue(161.0) == "emerald"


def test_family_of_hue_red_wraps():
    assert family_of_hue(355.0) == "red"
    assert family_of_hue(5.0) == "red"


# ── spec parsing (scripted answers → reified spec) ─────────────────────────────


def test_from_answers_dark_violet():
    spec = FidelitySpec.from_answers(palette="тёмная + фиолетовый")
    assert spec.dark_mode is True
    assert spec.primary_family == "violet"


def test_from_answers_light_emerald_en():
    spec = FidelitySpec.from_answers(palette="light emerald")
    assert spec.dark_mode is False
    assert spec.primary_family == "emerald"


def test_from_answers_sections_ru_list():
    spec = FidelitySpec.from_answers(sections=["каталог", "отзывы", "контакты"])
    assert spec.sections == ("catalog", "testimonials", "contacts")


def test_from_answers_sections_ru_string():
    spec = FidelitySpec.from_answers(sections="каталог, отзывы и контакты")
    assert spec.sections == ("catalog", "testimonials", "contacts")


def test_from_answers_tone_normalised():
    assert FidelitySpec.from_answers(tone="Playful").tone == "playful"


def test_from_answers_unknown_palette_word_ignored():
    spec = FidelitySpec.from_answers(palette="нечто непонятное")
    assert spec.primary_family is None
    assert spec.dark_mode is None


# ── the headline contract: fixed render, swap the spec → verdict flips ─────────


def test_clean_pass_all_axes():
    spec = FidelitySpec(
        dark_mode=True, primary_family="violet", sections=("catalog", "testimonials", "contacts")
    )
    rep = evaluate_fidelity(_full_obs(), spec)
    assert rep.passed
    assert set(rep.checked) == {PALETTE_BG, PRIMARY_FAMILY, SECTION_ANCHOR}


def test_swap_palette_answer_flips_verdict():
    obs = _full_obs()  # painted dark + violet
    assert evaluate_fidelity(obs, FidelitySpec(dark_mode=True)).passed
    light_spec = FidelitySpec(dark_mode=False)
    rep = evaluate_fidelity(obs, light_spec)
    assert not rep.passed
    assert rep.classes == (PALETTE_BG,)


def test_swap_primary_answer_flips_verdict():
    obs = _full_obs()  # painted violet CTA
    assert evaluate_fidelity(obs, FidelitySpec(primary_family="violet")).passed
    rep = evaluate_fidelity(obs, FidelitySpec(primary_family="emerald"))
    assert not rep.passed
    assert rep.classes == (PRIMARY_FAMILY,)


# ── per-axis RED cases ─────────────────────────────────────────────────────────


def test_palette_bg_red_dark_asked_light_painted():
    rep = evaluate_fidelity(_obs(page_bg=LIGHT_BG), FidelitySpec(dark_mode=True))
    assert not rep.passed
    assert rep.classes == (PALETTE_BG,)


def test_palette_bg_red_light_asked_dark_painted():
    rep = evaluate_fidelity(_obs(page_bg=DARK_BG), FidelitySpec(dark_mode=False))
    assert not rep.passed
    assert rep.classes == (PALETTE_BG,)


def test_palette_bg_clean_dark():
    rep = evaluate_fidelity(_obs(page_bg=DARK_BG), FidelitySpec(dark_mode=True))
    assert rep.passed


def test_primary_family_red_wrong_hue():
    rep = evaluate_fidelity(_obs(fills=(EMERALD_FILL,)), FidelitySpec(primary_family="violet"))
    assert not rep.passed
    assert rep.classes == (PRIMARY_FAMILY,)


def test_primary_family_red_no_cta_painted():
    # asked a colour but the page painted no saturated CTA at all → a miss, not a pass.
    rep = evaluate_fidelity(_obs(fills=()), FidelitySpec(primary_family="violet"))
    assert not rep.passed
    assert rep.classes == (PRIMARY_FAMILY,)


def test_primary_family_ignores_grey_fills():
    grey = {"bg": [40, 40, 40, 1], "tag": "button", "area": 9000}
    rep = evaluate_fidelity(
        _obs(fills=(grey, VIOLET_FILL)), FidelitySpec(primary_family="violet")
    )
    assert rep.passed  # grey is not a saturated accent; violet wins


def test_primary_family_picks_largest_area():
    small_emerald = {"bg": [5, 150, 105, 1], "tag": "a", "area": 100}
    big_violet = {"bg": [168, 85, 247, 1], "tag": "button", "area": 9000}
    rep = evaluate_fidelity(
        _obs(fills=(small_emerald, big_violet)), FidelitySpec(primary_family="violet")
    )
    assert rep.passed


def test_section_red_missing_one():
    obs = _obs(ids=("catalog", "reviews"), headings=("Каталог", "Отзывы"))
    rep = evaluate_fidelity(obs, FidelitySpec(sections=("catalog", "testimonials", "contacts")))
    assert not rep.passed
    assert rep.classes == (SECTION_ANCHOR,)
    assert "contacts" in rep.detected["sections_missing"]


def test_section_clean_via_heading_only():
    obs = _obs(ids=(), headings=("Наш каталог", "Что говорят клиенты", "Свяжитесь с нами"))
    rep = evaluate_fidelity(obs, FidelitySpec(sections=("catalog", "testimonials", "contacts")))
    assert rep.passed


def test_section_clean_via_nav_hash():
    obs = _obs(nav=("catalog", "reviews", "contact"))
    rep = evaluate_fidelity(obs, FidelitySpec(sections=("catalog", "testimonials", "contacts")))
    assert rep.passed


# ── tone: declared-but-wrong fails; absent abstains (no flaky vibe-guess) ──────


def test_tone_red_declared_wrong():
    rep = evaluate_fidelity(_full_obs(tone="strict"), FidelitySpec(tone="playful"))
    assert not rep.passed
    assert rep.classes == (TONE_MARKER,)


def test_tone_clean_declared_match():
    rep = evaluate_fidelity(_full_obs(tone="playful"), FidelitySpec(tone="playful"))
    assert rep.passed
    assert TONE_MARKER in rep.checked


def test_tone_abstains_when_undeclared():
    # page declares no marker → tone axis abstains: not checked, not a finding.
    rep = evaluate_fidelity(_full_obs(tone=None), FidelitySpec(tone="playful"))
    assert rep.passed
    assert TONE_MARKER not in rep.checked


# ── abstain / empty-spec semantics ─────────────────────────────────────────────


def test_not_rendered_abstains():
    rep = evaluate_fidelity({}, FidelitySpec(dark_mode=True), rendered=False)
    assert not rep.passed  # abstain ≠ pass
    assert not rep.rendered
    assert "ABSTAIN" in rep.summary()


def test_empty_spec_passes_and_checks_nothing():
    rep = evaluate_fidelity(_full_obs(), FidelitySpec())
    assert rep.passed
    assert rep.checked == ()


def test_subscore_shape():
    spec = FidelitySpec(dark_mode=True, primary_family="emerald")
    sub = evaluate_fidelity(_full_obs(), spec).subscore()
    assert sub["gate"] == "chip-pixel"
    assert sub["passed"] is False
    assert PRIMARY_FAMILY in sub["failed"]
    assert "accent_hex" in sub["detected"]
