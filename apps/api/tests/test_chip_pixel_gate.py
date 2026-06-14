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
    compile_build_spec,
    evaluate_fidelity,
    family_of_hue,
    spec_confidence,
    spec_from_discovery,
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


# ── V2.5.0 spec_from_discovery / to_dict / is_empty ───────────────────────────


def test_spec_from_discovery_round_trip_dark_violet():
    # The falsifiable round-trip: raw chip text "тёмная + фиолетовый" persisted
    # → dark_mode True, primary_family violet (the V2.5.0 acceptance criterion).
    history = [
        {"role": "assistant", "content": "Какая палитра?"},
        {"role": "user", "content": "тёмная + фиолетовый"},
    ]
    spec = spec_from_discovery(history, latest_prompt="сделай каталог и контакты")
    assert spec is not None
    assert spec.dark_mode is True
    assert spec.primary_family == "violet"
    assert "catalog" in spec.sections
    assert "contacts" in spec.sections


def test_spec_from_discovery_tone_detected():
    history = [{"role": "user", "content": "хочу премиум стиль"}]
    spec = spec_from_discovery(history)
    assert spec is not None
    assert spec.tone == "premium"


def test_spec_from_discovery_empty_history_is_none():
    # Adversarial: empty discovery → spec None (must not crash), so the column
    # persists NULL rather than an empty spec.
    assert spec_from_discovery([], latest_prompt=None) is None
    assert spec_from_discovery(None, latest_prompt="") is None


def test_spec_from_discovery_no_signal_is_none():
    # User said things, but nothing maps to an axis → no assertable spec.
    history = [{"role": "user", "content": "ну сделай что-нибудь нормальное"}]
    assert spec_from_discovery(history) is None


def test_spec_from_discovery_ignores_assistant_turns():
    # Only the user's own answers are the source of truth — an assistant turn
    # mentioning "тёмная" must not leak into the spec.
    history = [
        {"role": "assistant", "content": "Может тёмная фиолетовая тема?"},
        {"role": "user", "content": "да"},
    ]
    # "да" carries no assertable axis → None (assistant suggestion ignored).
    assert spec_from_discovery(history) is None


# ─── compile_build_spec + spec_confidence (V2.12 zero-question compiler) ─────


def test_compile_build_spec_rich_prompt_pins_every_axis():
    # The zero-question case: one rich prompt carries the whole brief — theme,
    # accent, two sections, tone — extracted with no chips and no LLM.
    spec = compile_build_spec(
        "тёмный минималистичный лендинг с каталогом и отзывами на фиолетовом"
    )
    assert spec.dark_mode is True
    assert spec.primary_family == "violet"
    assert "catalog" in spec.sections
    assert "testimonials" in spec.sections
    assert spec.tone == "minimal"
    assert spec_confidence(spec) == 4


def test_compile_build_spec_plan_example_extracts_tone():
    # The plan's canonical example: tone is the pinned axis ("минимал" wins over
    # "строг" by alias order). One axis → below the zero-question floor, so this
    # prompt still earns an onboarding question (compiler works, skip stays shy).
    spec = compile_build_spec("строгий минималистичный лендинг финтех-стартапа")
    assert spec.tone == "minimal"
    assert spec_confidence(spec) == 1


def test_compile_build_spec_vague_prompt_is_empty():
    # Adversarial: an unsteerable prompt reifies to an empty spec (confidence 0) —
    # the signal that the intent is NOT clear enough to skip onboarding.
    spec = compile_build_spec("сделай сайт")
    assert spec.is_empty
    assert spec_confidence(spec) == 0


def test_compile_build_spec_blank_prompt_is_empty():
    assert compile_build_spec("").is_empty
    assert spec_confidence(compile_build_spec("   ")) == 0


def test_spec_confidence_counts_sections_once():
    # Three sections are one "we learned the structure" signal, not three points,
    # so a multi-section prompt can't outweigh a palette+theme+tone one.
    three = compile_build_spec("сайт с каталогом, отзывами и контактами")
    assert len(three.sections) == 3
    assert spec_confidence(three) == 1


def test_to_dict_round_trips_via_constructor():
    spec = FidelitySpec.from_answers(
        palette="тёмная + фиолетовый", sections="каталог, контакты", tone="premium"
    )
    d = spec.to_dict()
    assert d == {
        "dark_mode": True,
        "primary_family": "violet",
        "sections": ["catalog", "contacts"],
        "tone": "premium",
    }
    rebuilt = FidelitySpec(
        dark_mode=d["dark_mode"],
        primary_family=d["primary_family"],
        sections=tuple(d["sections"]),
        tone=d["tone"],
    )
    assert rebuilt == spec


def test_is_empty():
    assert FidelitySpec().is_empty is True
    assert FidelitySpec(dark_mode=False).is_empty is False
    assert FidelitySpec(sections=("catalog",)).is_empty is False


# ── V2.5.1 from_dict: rebuild a persisted discovery_spec back into a spec ──────


def test_from_dict_round_trips_to_dict():
    spec = FidelitySpec.from_answers(
        palette="тёмная + фиолетовый", sections="каталог, контакты", tone="premium"
    )
    assert FidelitySpec.from_dict(spec.to_dict()) == spec


def test_from_dict_none_and_empty_are_empty_spec():
    assert FidelitySpec.from_dict(None) == FidelitySpec()
    assert FidelitySpec.from_dict({}) == FidelitySpec()


def test_from_dict_partial_row_abstains_on_missing_axes():
    # A legacy / partial row carries only some axes — the rest must abstain
    # (None / empty tuple), never raise.
    spec = FidelitySpec.from_dict({"dark_mode": True})
    assert spec.dark_mode is True
    assert spec.primary_family is None
    assert spec.sections == ()
    assert spec.tone is None


def test_from_dict_coerces_sections_list_and_str_to_tuple():
    assert FidelitySpec.from_dict({"sections": ["catalog", "contacts"]}).sections == (
        "catalog",
        "contacts",
    )
    # A bare string (defensive — should never persist, but must not explode into chars)
    assert FidelitySpec.from_dict({"sections": "catalog"}).sections == ("catalog",)


# ── V2.5c — spec_prompt_directive (generation-side of the causality bridge) ───
# The gate JUDGES a build against the spec; this directive is what finally STEERS
# the writer toward it. Tests: empty → "" (back-compat no-op), a populated spec
# carries every axis, and — the load-bearing invariant — the HEX the directive
# hands the writer resolves back to the SAME family the gate reads, so honouring
# the directive also passes PRIMARY_FAMILY (no honour-but-still-fail).


def test_spec_directive_empty_is_blank():
    from omnia_api.services.chip_pixel_gate import spec_prompt_directive

    assert spec_prompt_directive(None) == ""
    assert spec_prompt_directive(FidelitySpec()) == ""


def test_spec_directive_carries_every_axis():
    from omnia_api.services.chip_pixel_gate import _FAMILY_HEX, spec_prompt_directive

    spec = FidelitySpec(
        dark_mode=True,
        primary_family="violet",
        sections=("catalog", "contacts"),
        tone="premium",
    )
    out = spec_prompt_directive(spec)
    # palette: family name + its gate-consistent HEX
    assert "violet" in out
    assert _FAMILY_HEX["violet"] in out
    # theme: dark directive present, not the light one
    assert "ТЁМНАЯ" in out
    assert "СВЕТЛАЯ" not in out
    # tone + both section anchor ids
    assert "premium" in out
    assert 'id="catalog"' in out
    assert 'id="contact"' in out  # contacts → anchor[0] = "contact"


def test_spec_directive_light_mode_picks_light_line():
    from omnia_api.services.chip_pixel_gate import spec_prompt_directive

    out = spec_prompt_directive(FidelitySpec(dark_mode=False, primary_family="blue"))
    assert "СВЕТЛАЯ" in out
    assert "ТЁМНАЯ" not in out


def test_spec_directive_hex_is_gate_consistent_for_every_family():
    # The whole point of V2.5c: a writer that uses the HEX we hand it must land
    # on the family the gate reads back. Convert each swatch → hue → family and
    # assert it round-trips. A future palette-band edit that breaks this fires
    # here, not silently in production as a chip→gate mismatch loop.
    from omnia_api.services.chip_pixel_gate import _FAMILY_HEX, family_of_hue, rgb_to_hsl

    for family, hexv in _FAMILY_HEX.items():
        r, g, b = int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)
        hue, _, _ = rgb_to_hsl((r, g, b))
        assert family_of_hue(hue) == family, (
            f"{family} swatch {hexv} resolves to {family_of_hue(hue)}"
        )
