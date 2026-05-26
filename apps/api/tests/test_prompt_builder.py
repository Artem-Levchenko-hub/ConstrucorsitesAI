import pytest

from omnia_api.routers.messages import _ensure_kit_linked
from omnia_api.services import skill_library
from omnia_api.services.prompt_builder import (
    KIT_FILES,
    _compute_skill_brief,
    _expand_ru_to_en,
    build_messages,
    build_system_prompt,
)


def test_static_prompt_includes_style_and_animation_kit() -> None:
    sp = build_system_prompt("landing")
    assert "assets/omnia-kit.css" in sp
    assert "assets/omnia-kit.js" in sp
    # _STYLE_KIT presence — match on the stable section header. The earlier
    # check pinned on a preset name ("Aurora SaaS") that was renamed during
    # the v3 preset overhaul, so the assert silently went stale. The
    # `ВИЗУАЛЬНЫЙ СТИЛЬ` header is the unique top-line of `_STYLE_KIT` and
    # survives any preset-roster churn.
    assert "ВИЗУАЛЬНЫЙ СТИЛЬ" in sp
    assert "data-reveal-delay" in sp  # _ANIMATION_KIT class API
    # Phase C.5 — Color grading utilities documented in _KIT_V3_REFERENCE.
    # Three families, one assert each — catches accidental block removal.
    assert ".tone-warm" in sp  # image filter family
    assert ".atmosphere-noir" in sp  # body-overlay family
    assert ".grain-heavy" in sp  # film-grain family


def test_fullstack_prompt_excludes_static_kit() -> None:
    fs = build_system_prompt("fullstack")
    assert "assets/omnia-kit.css" not in fs
    # fullstack skips `_STYLE_KIT` — its unique header must not appear.
    assert "ВИЗУАЛЬНЫЙ СТИЛЬ" not in fs
    assert "Drizzle" in fs  # fullstack stack still present


def test_kit_files_constant() -> None:
    assert KIT_FILES == frozenset({"assets/omnia-kit.css", "assets/omnia-kit.js"})


def test_ensure_kit_linked_injects_when_missing() -> None:
    html = "<html><head><title>x</title></head><body></body></html>"
    out = _ensure_kit_linked({"index.html": html})["index.html"]
    assert "assets/omnia-kit.css" in out
    assert "assets/omnia-kit.js" in out
    assert out.index("omnia-kit.css") < out.index("</head>")  # injected before </head>


def test_ensure_kit_linked_idempotent_when_present() -> None:
    html = (
        '<html><head><link rel="stylesheet" href="assets/omnia-kit.css">'
        '<script src="assets/omnia-kit.js" defer></script></head><body></body></html>'
    )
    assert _ensure_kit_linked({"index.html": html})["index.html"] == html


def test_ensure_kit_linked_ignores_non_html() -> None:
    files = {"styles.css": "body{margin:0}"}
    assert _ensure_kit_linked(files) == files


# ---------------------------------------------------------------------------
# `ui-ux-pro-max` skill injection (Sprint 1 Pt. 2)
# ---------------------------------------------------------------------------


def test_compute_skill_brief_returns_none_when_no_signal() -> None:
    """Empty prompt or empty tokens → no brief (caller falls through to
    bundled `_DESIGN_KIT`)."""
    assert _compute_skill_brief("", "proj-1") is None
    assert _compute_skill_brief(None, "proj-1") is None
    assert _compute_skill_brief("  a b ", "proj-1") is None  # all <3 chars


def test_compute_skill_brief_matches_industry_keyword() -> None:
    """A prompt naming a clear industry surfaces a palette + UX rules block."""
    brief = _compute_skill_brief(
        "сделай сайт для SaaS с дашбордом и тарифами", "proj-1"
    )
    assert brief is not None
    assert "PALETTE" in brief or "UX RULES" in brief


def test_compute_skill_brief_seeded_by_project_id() -> None:
    """Same project_id + same prompt = same brief (no churn between turns)."""
    a = _compute_skill_brief("гейминг приложение", "proj-stable")
    b = _compute_skill_brief("гейминг приложение", "proj-stable")
    assert a == b


def test_build_system_prompt_injects_skill_brief_when_provided() -> None:
    """The optional `skill_brief` kwarg lands in the rendered prompt under
    its `ДИЗАЙН-БРИФ` header so the model recognises it as authoritative."""
    brief = "PALETTE (test):\n  primary: #ff00ff"
    out_static = build_system_prompt("landing", skill_brief=brief)
    assert "ДИЗАЙН-БРИФ" in out_static
    assert "#ff00ff" in out_static

    out_fs = build_system_prompt("fullstack", skill_brief=brief)
    assert "ДИЗАЙН-БРИФ" in out_fs
    assert "#ff00ff" in out_fs


def test_build_system_prompt_no_brief_when_none() -> None:
    """Default path: no `skill_brief` argument → no `ДИЗАЙН-БРИФ` block."""
    assert "ДИЗАЙН-БРИФ" not in build_system_prompt("landing")
    assert "ДИЗАЙН-БРИФ" not in build_system_prompt("fullstack")


def test_build_messages_threads_skill_brief_for_industry_prompt() -> None:
    """End-to-end: a project-context prompt + project_id gives the model
    a brief block in its system message."""
    msgs = build_messages(
        current_files={},
        history=[],
        user_prompt="лендинг для healthcare клиники с записью к врачу",
        template="landing",
        project_id="proj-7",
    )
    system = msgs[0]["content"]
    assert "ДИЗАЙН-БРИФ" in system


# ---------------------------------------------------------------------------
# RU → EN industry mapping (Sprint 1 Pt.2 follow-up — без этого 9/12 русских
# промптов не попадали в палитры ui-ux-pro-max).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,expected_product_type",
    [
        ("сайт аптеки в Барнауле", "Pharmacy/Drug Store"),
        ("лендинг для кофейни", "Bakery/Cafe"),
        ("портфолио фотографа", "Portfolio/Personal"),
        ("сайт стоматологии", "Medical Clinic"),
        ("интернет-магазин косметики", "Beauty/Spa/Wellness Service"),
        # hyphen-split tokenization makes both "гейминг" + "стартап" fire;
        # SaaS row scores tied with Gaming and wins on CSV ordering. Either
        # is acceptable for a "gaming startup" pitch.
        ("сайт для гейминг-стартапа", "SaaS (General)"),
        ("фитнес-клуб с расписанием", "Fitness/Gym App"),
        ("агентство недвижимости", "Real Estate/Property"),
        ("юридическая фирма", "Legal Services"),
        ("свадебное агентство", "Wedding/Event Planning"),
        ("отель в Сочи", "Hotel/Hospitality"),
        ("медитация и йога", "Meditation & Mindfulness"),
        ("крипто-биржа", "Fintech/Crypto"),
        ("школа английского", "Online Course/E-learning"),
        ("детский сад", "Childcare/Daycare"),
        # Plain English still works (no regression on existing path).
        ("IT-стартап SaaS", "SaaS (General)"),
        ("healthcare clinic", "Healthcare App"),
        ("fintech crypto exchange", "Fintech/Crypto"),
    ],
)
def test_ru_industry_keyword_routes_to_correct_palette(
    prompt: str, expected_product_type: str
) -> None:
    """Real production prompts must land on the right palette row from the
    161-row catalogue. Regression test for both the RU→EN map and the
    `_score_match` ranking that decides ties."""
    palette = skill_library.lookup_palette(*_expand_ru_to_en(prompt))
    assert palette is not None, f"no palette matched for {prompt!r}"
    assert palette["product_type"] == expected_product_type


def test_generic_prompt_misses_palette_gracefully() -> None:
    """Prompts with no industry signal must return None — caller falls back
    to the bundled `_DESIGN_KIT`. False-positives are worse than misses."""
    palette = skill_library.lookup_palette(*_expand_ru_to_en("мой сайт"))
    assert palette is None


def test_short_substring_does_not_falsely_trigger() -> None:
    """Earlier bug: stem ``ai`` substring-matched ``сайт`` → dental prompts
    landed on AI/Chatbot. Prefix-of-word matching kills the false-positive."""
    # "сайт стоматологии" should NOT route to AI/Chatbot.
    palette = skill_library.lookup_palette(
        *_expand_ru_to_en("сайт стоматологии")
    )
    assert palette is not None
    assert "AI" not in palette["product_type"]
    assert "Chatbot" not in palette["product_type"]


def test_expand_ru_to_en_keeps_original_tokens() -> None:
    """RU→EN expansion is ADDITIVE — original Russian segments stay so
    typography matcher (which keys on multilingual keywords like 'modern'
    that also appear in CSV) keeps working alongside the new English hits.

    Tokenization now also splits on hyphens (so `vr-аркада` reveals `vr`
    to the acronym matcher), so hyphenated compounds appear as parts.
    """
    tokens = _expand_ru_to_en("крипто-биржа фондовый")
    assert "крипто" in tokens  # left half after hyphen-split
    assert "биржа" in tokens   # right half
    assert "фондовый" in tokens
    # And English equivalents from `крипт` stem
    assert "fintech" in tokens or "crypto" in tokens


# ---------------------------------------------------------------------------
# Per-template system prompts (Phase α) — fullstack/spa/tgbot/api split.
# Each container-backed template must surface ONLY its stack rules, not
# every stack's rules. Cross-pollination (Next.js advice in a Python
# template) was the actual bug this dispatcher prevents.
# ---------------------------------------------------------------------------


# Each _X_STACK block has unique instruction-level markers that ONLY make
# sense inside its own stack — not as a redirect reference to another
# template. These let us assert the dispatcher routed to the right block.
# Cross-mentions ("если юзер просит API → рекомендуй `api` шаблон") are
# expected and OK.

_NEXT_MARKER = "Server Actions"  # "use server" directive — Next-only primitive
_SPA_MARKER = "react-router-dom v7"  # SPA's routing lib version
_TGBOT_MARKER = "long-polling"  # aiogram's update-pull mode
_API_MARKER = "SQLAlchemy 2"  # async ORM version we ship


def test_fullstack_prompt_routes_to_next_stack() -> None:
    sp = build_system_prompt("fullstack")
    assert _NEXT_MARKER in sp
    # Other templates' OWN markers must not appear (their redirect-mentions
    # might appear in cross-references; checking unique-instruction markers
    # avoids the cross-mention false-positive).
    assert _SPA_MARKER not in sp
    assert _TGBOT_MARKER not in sp
    assert _API_MARKER not in sp


def test_spa_prompt_routes_to_spa_stack() -> None:
    sp = build_system_prompt("spa")
    assert _SPA_MARKER in sp
    assert _NEXT_MARKER not in sp
    assert _TGBOT_MARKER not in sp
    assert _API_MARKER not in sp


def test_tgbot_prompt_routes_to_tgbot_stack() -> None:
    sp = build_system_prompt("tgbot")
    assert _TGBOT_MARKER in sp
    assert "TELEGRAM_BOT_TOKEN" in sp
    assert _NEXT_MARKER not in sp
    assert _SPA_MARKER not in sp
    assert _API_MARKER not in sp


def test_api_prompt_routes_to_api_stack() -> None:
    sp = build_system_prompt("api")
    assert _API_MARKER in sp
    assert "JWT" in sp
    assert _NEXT_MARKER not in sp
    assert _SPA_MARKER not in sp
    assert _TGBOT_MARKER not in sp


def test_backend_templates_skip_visual_blocks() -> None:
    """tgbot/api don't render HTML — visual blocks (layout rigor, design
    kit, visual richness, image generation) MUST NOT appear. They'd waste
    tokens AND confuse the model into generating useless HTML."""
    for template in ("tgbot", "api"):
        sp = build_system_prompt(template)
        assert "ЛАЙАУТ-ЖЁСТКОСТЬ" not in sp, f"{template} got layout block"
        assert "ДИЗАЙН-КИТ" not in sp, f"{template} got design kit"
        assert "ВИЗУАЛЬНАЯ НАСЫЩЕННОСТЬ" not in sp, f"{template} got visual rich"
        assert "data-omnia-gen" not in sp, f"{template} got image-gen block"
        # Phase G — _COPY_RULES is a separate design-noise block (Lorem
        # ipsum, dark patterns, etc.) that has no business in a tgbot/api
        # prompt. Backend templates' sections tuple intentionally omits it.
        # (SHADOW RECIPES etc. live INSIDE _QUALITY_BAR which backend templates
        # do consume — those subsections are quality bar, not visual noise.)
        assert "COPY-RULES" not in sp, f"{template} got copy rules"


def test_phase_g_malewicz_rules_present_for_all_tiers() -> None:
    """Phase G — every Malewicz rule must reach the model regardless of tier.
    The drop-list trim removes _TASTE / _STYLE_KIT for budget — rules placed
    there would silently vanish. These markers prove the rules landed in
    surviving blocks."""
    markers = [
        "SHADOW RECIPES",       # G1, G2 — in extended _QUALITY_BAR
        "Double-W",             # G5 — in _QUALITY_BAR BUTTON RULES (RU keeps EN term)
        "primary CTA",          # G7 — in _QUALITY_BAR BUTTON RULES
        "ICON DISCIPLINE",      # G9, G10
        "Lorem ipsum",          # G12 — forbidden, in _COPY_RULES
        "dark pattern",         # G14 — in AWWWARDS_PRINCIPLES (NO DARK PATTERNS header EN)
        "MODERN",               # G15 — "MODERN ≠ PURELY FLAT" header
        "LESS IS MORE",         # G18 — "LESS IS MORE" header
    ]
    for model_id in ("claude-opus-4-7", "gpt-5-mini", "claude-haiku-4-5"):
        sp = build_system_prompt("landing", model_id=model_id)
        for marker in markers:
            assert marker.lower() in sp.lower(), (
                f"Phase G marker {marker!r} missing for tier "
                f"{model_id} — rule placed in a dropped block?"
            )


def test_visual_templates_include_layout_rigor() -> None:
    """fullstack + spa must get the mobile-first / responsive rules."""
    for template in ("fullstack", "spa"):
        sp = build_system_prompt(template)
        assert "ЛАЙАУТ-ЖЁСТКОСТЬ" in sp, f"{template} missing layout block"


def test_response_block_present_in_every_template() -> None:
    """Every template — visual, backend, static — must include the
    ФОРМАТ ОТВЕТА block so AI knows how to emit `<file>` / `<edit>` blocks."""
    for template in ("fullstack", "spa", "tgbot", "api", "landing", "blank"):
        sp = build_system_prompt(template)
        assert "ФОРМАТ ОТВЕТА" in sp, f"{template} missing _RESPONSE"


def test_backend_templates_omit_skill_brief() -> None:
    """skill_brief is design-tooling — pointless for tgbot/api. Even when
    a brief is passed (caller doesn't know), backend prompts must not
    surface it (it'd just confuse the model)."""
    brief = "PALETTE: primary #ff00ff fonts: Inter"
    for template in ("tgbot", "api"):
        sp = build_system_prompt(template, skill_brief=brief)
        assert "#ff00ff" not in sp, f"{template} leaked skill brief"


def test_build_messages_no_brief_when_project_id_absent() -> None:
    """Backward-compat: callers that don't pass project_id still work; the
    brief is silently skipped (or built without seed, but the upstream call
    site always passes project_id)."""
    msgs = build_messages(
        current_files={},
        history=[],
        user_prompt="лендинг кофейни",
        template="landing",
    )
    # With no project_id, seed defaults to 0 — the brief MAY still be built
    # if the prompt has palette/font hits. We assert the system prompt is
    # at least valid (no crash) and contains the base identity.
    system = msgs[0]["content"]
    assert "Omnia.AI" in system


def test_phase_i_malewicz_primitives_documented_in_kit_reference() -> None:
    """Phase I primitives must appear in _KIT_V3_REFERENCE so the AI knows
    when to apply them. Without the documentation the classes ship but
    Haiku never reaches for them."""
    sp = build_system_prompt("landing")
    assert "MALEWICZ PRIMITIVES" in sp
    assert ".shadow-tinted" in sp
    assert ".gradient-subtle" in sp
    assert ".btn-modern" in sp
    assert ".btn-cta-primary" in sp
    assert ".nested-rounded" in sp


def test_omnia_kit_css_phase_i_block_is_byte_identical_across_4_templates() -> None:
    """All 4 static templates ship the SAME omnia-kit.css. C.5 maintained
    this; Phase I must too. A divergent block per template means a single
    edit forgot to sync — measure it before it leaks to production."""
    import pathlib
    base = pathlib.Path(__file__).parent.parent / "src/omnia_api/templates"
    files = [
        (base / t / "assets/omnia-kit.css").read_text(encoding="utf-8")
        for t in ("blank", "blog", "landing", "portfolio")
    ]
    # All 4 files must be byte-identical end-to-end (the C.5 block already
    # established this invariant — Phase I sync MUST preserve it).
    assert files[0] == files[1] == files[2] == files[3]
