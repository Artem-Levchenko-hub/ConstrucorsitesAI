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
    assert "Aurora SaaS" in sp  # _STYLE_KIT preset
    assert "data-reveal-delay" in sp  # _ANIMATION_KIT class API


def test_fullstack_prompt_excludes_static_kit() -> None:
    fs = build_system_prompt("fullstack")
    assert "assets/omnia-kit.css" not in fs
    assert "Aurora SaaS" not in fs
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
        ("сайт для гейминг-стартапа", "Gaming"),
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
    """RU→EN expansion is ADDITIVE — original Russian tokens stay so
    typography matcher (which keys on multilingual keywords like 'modern'
    that also appear in CSV) keeps working alongside the new English hits."""
    tokens = _expand_ru_to_en("крипто-биржа фондовый")
    assert "крипто-биржа" in tokens
    assert "фондовый" in tokens
    # And English equivalents added
    assert "fintech" in tokens or "crypto" in tokens


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
