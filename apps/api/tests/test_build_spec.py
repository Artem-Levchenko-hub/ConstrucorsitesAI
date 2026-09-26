"""Build spec — scripted onboarding answers reified, parsed and previewed.

Pure data in, pure data out: every case here is a hand-built answer set, so the
whole vocabulary (palette families, sections, tone), the persisted round-trip
and the live preview payload are exercised without a browser or an LLM.

The pixel audit these cases used to end in left with the site builder: it judged
a static page against the same spec. What stayed is the contract three readers
share — discovery (how many axes are decided), onboarding (what to paint in the
preview) and acceptance (what to persist into ``projects.discovery_spec``).
"""

from yleum_api.services.build_spec import (
    FidelitySpec,
    compile_build_spec,
    spec_confidence,
    spec_from_discovery,
    spec_preview,
)

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


# ── live design-preview payload (onboarding pillar 2×3) ────────────────────────


def test_spec_preview_none_and_empty_abstain():
    assert spec_preview(None) is None
    assert spec_preview(FidelitySpec()) is None


def test_spec_preview_resolves_family_to_hex():
    prev = spec_preview(FidelitySpec(primary_family="violet"))
    assert prev is not None
    assert prev["accent_family"] == "violet"
    # Same _FAMILY_HEX table the writer-directive + gate use (single source).
    assert prev["accent"] == "#AA3EDA"


def test_spec_preview_carries_all_axes():
    prev = spec_preview(
        FidelitySpec(
            dark_mode=True,
            primary_family="emerald",
            sections=("catalog", "testimonials"),
            tone="premium",
        )
    )
    assert prev == {
        "accent": "#3EDABA",
        "accent_family": "emerald",
        "dark_mode": True,
        "tone": "premium",
        "sections": ["catalog", "testimonials"],
    }


def test_spec_preview_accent_none_when_family_undecided():
    # A theme-only answer still previews (dark canvas), accent falls back to UI.
    prev = spec_preview(FidelitySpec(dark_mode=True))
    assert prev is not None
    assert prev["accent"] is None
    assert prev["dark_mode"] is True


def test_spec_preview_morphs_with_answers():
    # Pillar-2 causality: each gathered answer changes the preview payload.
    one = spec_preview(spec_from_discovery([], "тёмный сайт"))
    history = [{"role": "user", "content": "тёмный сайт"}]
    two = spec_preview(spec_from_discovery(history, "фиолетовый акцент"))
    assert one is not None and two is not None
    assert one.get("accent_family") is None
    assert two["accent_family"] == "violet"
    assert two["dark_mode"] is True


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
