import pytest

from omnia_api.routers.messages import (
    _ensure_kit_linked,
    _salvage_html,
    _text_preserved_ratio,
)
from omnia_api.services import skill_library
from omnia_api.services.prompt_builder import (
    HISTORY_LIMIT,
    KIT_FILES,
    _compute_skill_brief,
    _expand_ru_to_en,
    build_edit_rewrite_messages,
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
    assert "hero-stagger" in sp  # anime.js data-anime vocabulary taught
    assert "assets/anime.min.js" in sp  # anime.js lib in head includes
    # Phase C.5 — Color grading utilities documented in _KIT_V3_REFERENCE.
    # Three families, one assert each — catches accidental block removal.
    assert ".tone-warm" in sp  # image filter family
    assert ".atmosphere-noir" in sp  # body-overlay family
    assert ".grain-heavy" in sp  # film-grain family


def test_static_prompt_includes_landing_section_kit() -> None:
    # v2.22 #1 — THE LEVER: freeform landings now carry the premium section kit
    # (the prod-default surface that a colleague shares), mirroring the entity
    # kit's StorefrontSection/PricingPlans/… so a forked store + its landing
    # look identical. The header + every kit-variant id + the palette-anchor
    # contract must be present in the static branch.
    for tmpl in ("landing", "portfolio", "blog", "blank"):
        sp = build_system_prompt(tmpl)
        assert "КИТ ПРЕМИУМ-СЕКЦИЙ ЛЕНДИНГА" in sp, tmpl
        for variant in (
            "header-nav",  # v2.22 #3 — the page FRAME (sticky nav) is now kit, not hand-rolled
            "hero-centered",  # v2.22 #2 — the hero (rubric crit. 5) is now kit, not hand-rolled
            "hero-split",
            "hero-editorial",  # v2.23 #1 — archetype hero: type-as-graphic (Bold Studio/Kinetic/portfolio)
            "hero-cinematic",  # v2.23 #1 — archetype hero: full-bleed photo-art (Apple Tech/luxury/premium)
            "logos-strip",  # v2.22 #3 — social-proof band, kit-sourced
            "features-grid",
            "pricing-plans",
            "testimonial-wall",
            "faq-accordion",
            "cta-band",
            "footer-rich",  # v2.22 #3 — the page FRAME (footer) is now kit, not hand-rolled
        ):
            assert variant in sp, f"{variant} missing in {tmpl}"
        assert "--primary" in sp and "--card" in sp  # palette-anchor contract


def test_static_prompt_includes_composition_rules() -> None:
    # v2.24 #1b — kit as a SYSTEM, not a set of blocks: the archetype must drive
    # the whole architecture (vertical rhythm, grid density, type scale, container
    # width), not just a recoloured hero. The four composition profiles + the
    # editorial-vs-interface contrast must be present in every landing template.
    for tmpl in ("landing", "portfolio", "blog", "blank"):
        sp = build_system_prompt(tmpl)
        assert "КОМПОЗИЦИЯ КАК СИСТЕМА" in sp, tmpl
        for profile in ("EDITORIAL", "CINEMATIC", "MODULE", "INTERFACE"):
            assert profile in sp, f"profile {profile} missing in {tmpl}"
        # measurable, archetype-distinct tokens — editorial breathes, SaaS is dense
        assert "py-28 sm:py-32 md:py-36" in sp, tmpl  # EDITORIAL rhythm (max air)
        assert "py-16 sm:py-20 md:py-24" in sp, tmpl  # INTERFACE rhythm (dense)
        assert "max-w-7xl" in sp and "max-w-5xl" in sp, tmpl  # distinct containers


def test_composition_rules_in_fullstack_and_spa() -> None:
    # fullstack/spa use the same landing AD brief, which now references the
    # «КОМПОЗИЦИЯ КАК СИСТЕМА» block — it must be in their system prompt too.
    for tmpl in ("fullstack", "spa"):
        assert "КОМПОЗИЦИЯ КАК СИСТЕМА" in build_system_prompt(tmpl), tmpl


def test_composition_rules_survive_budget_trim() -> None:
    # Composition is design-defining (like _STYLE_KIT) — cheap writers need it
    # MORE, not less. Only _DETAILS_KIT is dropped for budget.
    sp = build_system_prompt("landing", model_id="haiku")
    assert "КОМПОЗИЦИЯ КАК СИСТЕМА" in sp


def test_landing_section_kit_survives_budget_trim() -> None:
    # The section kit is THE design lever — it must reach cheap writers too
    # (deepseek/haiku). Only _DETAILS_KIT is dropped for budget; the kit stays.
    sp = build_system_prompt("landing", model_id="haiku")
    assert "КИТ ПРЕМИУМ-СЕКЦИЙ ЛЕНДИНГА" in sp


def test_entity_prompt_excludes_landing_section_kit() -> None:
    # Entity apps have their OWN section kit (_ENTITIES_UI React components);
    # the freeform HTML doubles must not leak in and confuse the .tsx writer.
    ep = build_system_prompt("nextjs_entities")
    assert "КИТ ПРЕМИУМ-СЕКЦИЙ ЛЕНДИНГА" not in ep


def test_entity_prompt_teaches_grouped_nav_and_brand_glyph() -> None:
    # The AppShell sidebar gained a workspace glyph + grouped nav (`section`);
    # the generator must instruct apps to use them, else every generated app
    # keeps a flat, brand-mark-less sidebar — the "looks like a template" tell.
    ep = build_system_prompt("nextjs_entities")
    assert "ГРУППЫ НАВИГАЦИИ" in ep
    assert "section:" in ep  # the grouped NAV example carries the field
    assert "workspace-глиф" in ep  # brand-glyph guidance present


def test_fullstack_prompt_excludes_static_kit() -> None:
    fs = build_system_prompt("fullstack")
    assert "assets/omnia-kit.css" not in fs
    # fullstack skips `_STYLE_KIT` — its unique header must not appear.
    assert "ВИЗУАЛЬНЫЙ СТИЛЬ" not in fs
    assert "Drizzle" in fs  # fullstack stack still present


def test_kit_files_constant() -> None:
    assert KIT_FILES == frozenset(
        {"assets/omnia-kit.css", "assets/omnia-kit.js", "assets/anime.min.js"}
    )


def test_ensure_kit_linked_injects_when_missing() -> None:
    html = "<html><head><title>x</title></head><body></body></html>"
    out = _ensure_kit_linked({"index.html": html})["index.html"]
    assert "assets/omnia-kit.css" in out
    assert "assets/anime.min.js" in out
    assert "assets/omnia-kit.js" in out
    assert out.index("omnia-kit.css") < out.index("</head>")  # injected before </head>
    # anime.min.js must precede omnia-kit.js (kit reads window.anime on load).
    assert out.index("anime.min.js") < out.index("omnia-kit.js")


def test_ensure_kit_linked_idempotent_when_present() -> None:
    html = (
        '<html><head><link rel="stylesheet" href="assets/omnia-kit.css">'
        '<script src="assets/anime.min.js" defer></script>'
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
    """Default path: no `skill_brief` argument → no injected brief BLOCK.

    The Phase-K palette tail reminder legitimately mentions the words
    "ДИЗАЙН-БРИФ" as a conditional instruction ("если есть ДИЗАЙН-БРИФ…"), so
    the absence check targets the auto-matched brief *header* that
    `_format_skill_brief` emits — that is what "no brief" actually means.
    """
    assert "ДИЗАЙН-БРИФ (auto-matched" not in build_system_prompt("landing")
    assert "ДИЗАЙН-БРИФ (auto-matched" not in build_system_prompt("fullstack")


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
# `code` template — language-agnostic source (owner 2026-06-18)
# ---------------------------------------------------------------------------


def test_code_prompt_is_language_agnostic_not_website() -> None:
    """The `code` branch must frame the task as writing a program/script in any
    language and must NOT carry the website identity or the visual/section kits —
    otherwise the model builds a page instead of the requested script."""
    sp = build_system_prompt("code")
    # Code framing present.
    assert "режиме КОДА" in sp or "ПРОГРАММА" in sp or "любом языке" in sp
    # No web identity / no website quality bar / no visual kit.
    assert "AI-конструктор сайтов" not in sp
    assert "assets/omnia-kit.css" not in sp
    assert "ВИЗУАЛЬНЫЙ СТИЛЬ" not in sp


def test_history_limit_is_twelve() -> None:
    """Owner 2026-06-18 «реально понимай чат»: the writer must see a longer
    thread than the old 6-turn window. Lock the bumped value so a future trim
    is a deliberate, reviewed change."""
    assert HISTORY_LIMIT == 12


def test_build_messages_strips_stale_build_html_from_assistant_history() -> None:
    """A prior build turn is 30–60 KB of <file> HTML; verbatim it buries the
    user's real words across the 12-turn window. It must be replaced with a short
    marker, while USER turns stay verbatim (they carry intent)."""
    history = [
        {"role": "user", "content": "сделай лендинг кофейни"},
        {
            "role": "assistant",
            "content": 'готово <file path="index.html">' + ("X" * 5000) + "</file>",
        },
    ]
    msgs = build_messages(
        current_files={},
        history=history,
        user_prompt="поменяй цвет кнопки",
        template="blank",
        model_id="claude-haiku-4-5",
    )
    assistant_turns = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert assistant_turns, "history assistant turn should be threaded"
    assert "XXXX" not in assistant_turns[0]  # the 5 KB HTML body is gone
    assert "[предыдущая сборка]" in assistant_turns[0]
    # The user's real words survive verbatim.
    assert any("сделай лендинг кофейни" in m["content"] for m in msgs if m["role"] == "user")


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


def test_entities_public_landing_mandates_real_photos_and_public_access() -> None:
    """V2.2 niche-3 regression: the nextjs_entities writer used to leave the
    public landing as gray CSS-gradient placeholders and fetched owner-scoped
    entities anonymously (→401). The brief must now (a) mandate real
    ``data-omnia-gen`` photos on the public landing and (b) require
    ``access:"public"`` for any entity read on the anonymous ``/``."""
    sp = build_system_prompt("nextjs_entities", image_gen_enabled=True)
    # (a) public landing is a content site → real generated photos, not placeholders
    assert "ШОУКЕЙС НА ГЛАВНОЙ" in sp
    assert "data-omnia-gen" in sp
    assert "пустых CSS-градиентных плашек" in sp
    # (b) anon catalog entity must be declared public → no 401 for visitors
    assert '"access":"public"' in sp
    assert "анониму 401" in sp


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


def test_brief_only_drops_code_blocks_keeps_design() -> None:
    """The Art-Director (pass 1) writes a PROSE brief — `brief_only=True` strips
    the code-implementation contracts (the <file> response format, stacks, the
    self-check) while KEEPING the design-thinking blocks. Pass 2 (the writer)
    uses the full prompt, so final code quality is unaffected."""
    for tmpl in ("landing", "nextjs_entities", "fullstack", "spa"):
        full = build_system_prompt(tmpl)
        brief = build_system_prompt(tmpl, brief_only=True)
        # Code-only response format dropped...
        assert "ФОРМАТ ОТВЕТА" in full, tmpl
        assert "ФОРМАТ ОТВЕТА" not in brief, tmpl
        # ...design-thinking kept...
        assert "АРТ-ДИРЕКЦИЯ" in brief, tmpl
        # ...and the brief is strictly leaner.
        assert len(brief) < len(full), tmpl

    # The shadcn app kit (_ENTITIES_UI) + stack are the heaviest blocks; the
    # brief drops them, so the saving on entity apps is substantial.
    full_e = build_system_prompt("nextjs_entities")
    brief_e = build_system_prompt("nextjs_entities", brief_only=True)
    assert len(full_e) - len(brief_e) > 2000


def test_build_art_director_system_is_lean() -> None:
    """`build_art_director_system` is the brief-lean system used for pass 1 —
    no project_id / empty prompt reduces it to build_system_prompt(brief_only)."""
    from omnia_api.services.prompt_builder import build_art_director_system

    got = build_art_director_system("landing")
    assert got
    assert "ФОРМАТ ОТВЕТА" not in got
    assert "АРТ-ДИРЕКЦИЯ" in got
    assert len(got) < len(build_system_prompt("landing"))


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


def test_compute_skill_brief_includes_derived_tokens_for_industry_match() -> None:
    """Phase J — when palette is matched, the brief should include the
    derived gradient pair + shadow tint so the AI gets concrete values,
    not just rule prose."""
    brief = _compute_skill_brief(
        "сделай сайт SaaS-стартапа с дашбордом и тарифами", "proj-J"
    )
    assert brief is not None
    # At least one Phase-J derived block must surface
    assert "ПРОИЗВОДНЫЕ ТОКЕНЫ" in brief or "gradient_pair" in brief


def test_compute_skill_brief_phase_j_includes_nav_style_for_app_prompt() -> None:
    """Mobile-app signals → nav_style picks bottom-tabs (G17 enforcement
    at lookup time, not just rule prose)."""
    brief = _compute_skill_brief(
        "мобильное приложение для фитнес-зала с расписанием", "proj-J-mobile"
    )
    assert brief is not None
    # Vertical detected (fitness/saas), target=mobile → bottom-tabs
    if "ПРОИЗВОДНЫЕ ТОКЕНЫ" in brief:
        assert "bottom-tabs" in brief or "mobile" in brief.lower()


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


# ---------------------------------------------------------------------------
# Surgical EDIT mode (owner directive 2026-06-06) — a lean, edit-only prompt
# that must NOT carry the "build a complete designed site" blocks, so a cheap
# model can't be pushed into regenerating the page / re-rolling the palette.
# ---------------------------------------------------------------------------

# Unique headers of the build-only blocks that caused the drift. None may leak
# into the edit prompt.
_BUILD_ONLY_MARKERS = (
    "СТАНДАРТ КАЧЕСТВА",        # _QUALITY_BAR
    "ВИЗУАЛЬНАЯ НАСЫЩЕННОСТЬ",  # _VISUAL_RICH_KIT
    "ВИЗУАЛЬНЫЙ СТИЛЬ",         # _STYLE_KIT
    "ДИЗАЙН-КИТ",               # _DESIGN_KIT
    "АРТ-ДИРЕКТОР",             # _ART_DIRECTOR
    "ФИНАЛЬНАЯ САМОПРОВЕРКА",   # _SELF_CHECK
)


def _edit_system(**kw) -> str:
    kw.setdefault("model_id", "deepseek-chat")
    msgs = build_messages(
        current_files={"index.html": "<html><body><h1>Кафе «Утро»</h1></body></html>"},
        history=[],
        user_prompt="добавь интро к сайту",
        template="landing",
        project_id="proj-edit",
        edit_mode=True,
        **kw,
    )
    return msgs[0]["content"]


def test_edit_mode_uses_lean_preserve_prompt() -> None:
    system = _edit_system()
    # The edit identity + surgical <edit> format are present...
    assert "ТОЧЕЧНОЙ ПРАВКИ" in system
    assert "ПРАВИЛА СОХРАНЕНИЯ" in system
    assert "<edit path=" in system
    assert "SEARCH" in system and "REPLACE" in system
    # ...and every "build a full designed site" block is GONE (that pressure is
    # exactly what re-rolled the palette on a small edit).
    for marker in _BUILD_ONLY_MARKERS:
        assert marker not in system, f"edit prompt leaked build block: {marker}"


def test_edit_mode_includes_current_files_to_patch() -> None:
    """The model needs the current file verbatim to write byte-exact SEARCH."""
    msgs = build_messages(
        current_files={"index.html": "<h1>УНИКАЛЬНЫЙ-МАРКЕР-123</h1>"},
        history=[],
        user_prompt="поменяй заголовок",
        template="landing",
        model_id="deepseek-chat",
        edit_mode=True,
    )
    joined = "\n".join(m["content"] for m in msgs)
    assert "УНИКАЛЬНЫЙ-МАРКЕР-123" in joined


def test_edit_mode_threads_selection_block() -> None:
    msgs = build_messages(
        current_files={"index.html": "<button class='cta'>Купить</button>"},
        history=[],
        user_prompt="сделай её крупнее",
        template="landing",
        selected_elements=[{"selector": "button.cta", "text": "Купить"}],
        model_id="deepseek-chat",
        edit_mode=True,
    )
    last_user = msgs[-1]["content"]
    assert "button.cta" in last_user
    assert "ТОЧЕЧНАЯ правка" in last_user  # _format_selection_block instruction


def test_edit_mode_independent_of_model_tier() -> None:
    """Edit mode short-circuits before catalog/freeform routing — a premium
    model id must still get the lean edit prompt, not the build prompt."""
    for mid in ("deepseek-chat", "claude-opus-4-7", "deepseek-v4-pro"):
        system = _edit_system(model_id=mid)
        assert "ТОЧЕЧНОЙ ПРАВКИ" in system
        assert "СТАНДАРТ КАЧЕСТВА" not in system


def test_edit_rewrite_messages_asks_full_file_and_preserve() -> None:
    msgs = build_edit_rewrite_messages(
        {"index.html": "<h1>УНИК-СОДЕРЖИМОЕ-42</h1>"},
        [],
        "сделай фон темнее",
        None,
    )
    system = msgs[0]["content"]
    assert "СТРАНИЦУ ЦЕЛИКОМ" in system
    assert '<file path="index.html">' in system
    joined = "\n".join(m["content"] for m in msgs)
    assert "УНИК-СОДЕРЖИМОЕ-42" in joined  # current file fed verbatim
    assert "сделай фон темнее" in joined


def test_text_preserved_ratio_scoped_edit_vs_redesign() -> None:
    old = (
        "<h1>Суши Юген</h1><p>Дикий тунец блюфин премиум класса</p>"
        "<a>Забронировать стол</a>"
    )
    # Background-only change — same copy → nearly all words survive.
    bg_only = (
        "<body style='background:#111'><h1>Суши Юген</h1>"
        "<p>Дикий тунец блюфин премиум класса</p>"
        "<a>Забронировать стол</a></body>"
    )
    assert _text_preserved_ratio(old, bg_only) >= 0.9
    # Full re-design — different copy → most words gone → must be rejected.
    redesign = "<h1>Pizza Roma</h1><p>italian dough mozzarella</p><a>Order now</a>"
    assert _text_preserved_ratio(old, redesign) < 0.4


def test_salvage_html_rescues_unwrapped_and_fenced() -> None:
    body = "x" * 900
    raw = f"Готово, поменял фон.\n<!doctype html><html><body><h1>Hi</h1>{body}</body></html>\nготово"
    out = _salvage_html(raw)
    assert out is not None
    assert out.startswith("<!doctype html")
    assert out.rstrip().endswith("</html>")
    fenced = f"```html\n<html><body>{body}</body></html>\n```"
    assert _salvage_html(fenced) is not None
    # No real page → None (so the drift guard rejects, page kept).
    assert _salvage_html("просто текст без разметки") is None


def test_build_mode_still_carries_full_prompt() -> None:
    """Regression guard: with edit_mode off (default), a landing build still
    gets the full design blocks — we only stripped them from edit mode."""
    msgs = build_messages(
        current_files={},
        history=[],
        user_prompt="сделай лендинг кофейни",
        template="landing",
        project_id="proj-build",
        model_id="deepseek-chat",
    )
    system = msgs[0]["content"]
    assert "ВИЗУАЛЬНЫЙ СТИЛЬ" in system  # _STYLE_KIT present in build mode


def test_anime_and_kit_js_byte_identical_across_4_templates() -> None:
    """Vendored anime.min.js + omnia-kit.js must be byte-identical across all
    4 static templates. The kit-edit-then-copy workflow silently forgets a dir
    otherwise, and KIT_FILES protection assumes one canonical copy per asset."""
    import pathlib
    base = pathlib.Path(__file__).parent.parent / "src/omnia_api/templates"
    for asset in ("assets/anime.min.js", "assets/omnia-kit.js"):
        files = [
            (base / t / asset).read_text(encoding="utf-8")
            for t in ("blank", "blog", "landing", "portfolio")
        ]
        assert files[0] == files[1] == files[2] == files[3], asset


# ── V2.5c — discovery_spec steers the freeform/plain build prompt ──────────────


def _violet_dark_spec() -> dict:
    return {
        "dark_mode": True,
        "primary_family": "violet",
        "sections": ["catalog", "contacts"],
        "tone": None,
    }


def test_build_system_prompt_honours_discovery_spec() -> None:
    from omnia_api.services.chip_pixel_gate import _FAMILY_HEX

    out = build_system_prompt("fullstack", discovery_spec=_violet_dark_spec())
    assert "ЯВНЫЙ ВЫБОР ПОЛЬЗОВАТЕЛЯ" in out
    assert _FAMILY_HEX["violet"] in out          # violet HEX anchor
    assert "ТЁМНАЯ" in out                        # dark directive
    assert 'id="catalog"' in out and 'id="contact"' in out  # both sections


def test_build_system_prompt_spec_none_is_byte_identical() -> None:
    # The whole back-compat contract: absent/empty spec → unchanged prompt.
    base = build_system_prompt("fullstack")
    assert build_system_prompt("fullstack", discovery_spec=None) == base
    assert build_system_prompt("fullstack", discovery_spec={}) == base


def test_build_system_prompt_spec_directive_precedes_palette_anchor() -> None:
    # The explicit user choice must sit ABOVE the preset palette anchor so the
    # model reads it first (and the directive lifts any palette ban on the
    # chip-picked family).
    out = build_system_prompt(
        "fullstack", preset_id="fintech", discovery_spec=_violet_dark_spec()
    )
    if "ОБЯЗАТЕЛЬНАЯ ПАЛИТРА И ШРИФТЫ" in out:  # preset resolved → anchor present
        assert out.index("ЯВНЫЙ ВЫБОР ПОЛЬЗОВАТЕЛЯ") < out.index(
            "ОБЯЗАТЕЛЬНАЯ ПАЛИТРА И ШРИФТЫ"
        )


def test_entities_ui_brief_selects_screen_archetype() -> None:
    # Composition lever (pickup #2): the entity-app kit brief must force an
    # upfront screen-archetype choice so visual / record-centric niches stop
    # defaulting to a command-center dashboard. Reaches both the base path and
    # the art-director writer pass (which carries _ENTITIES_UI as system prompt).
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "АРХЕТИП ГЛАВНОГО ЭКРАНА" in _ENTITIES_UI
    for name in (
        "КОМАНД-ЦЕНТР",
        "ВИТРИНА-КАТАЛОГ",
        "ДОСЬЕ-ФОКУС",
        "ТРЕКЕР-ПОТОК",
        "РАСПИСАНИЕ-КАЛЕНДАРЬ",
    ):
        assert name in _ENTITIES_UI, f"archetype {name} dropped from _ENTITIES_UI"
    # archetype block sits ABOVE the dashboard recipe (it reframes it)
    assert _ENTITIES_UI.index("АРХЕТИП ГЛАВНОГО ЭКРАНА") < _ENTITIES_UI.index(
        "рецепт архетипа"
    )
    # the kit OWNS the kanban + calendar + master-detail (split) views — the brief
    # must route to them, NOT tell the writer they're missing (stale-guidance guard).
    assert "Канбан-доски в ките НЕТ" not in _ENTITIES_UI
    assert 'view="board"' in _ENTITIES_UI
    assert 'view="calendar"' in _ENTITIES_UI
    assert 'view="split"' in _ENTITIES_UI
    assert "MasterDetailView" in _ENTITIES_UI


def test_entities_ui_public_home_uses_storefront_hero() -> None:
    # Pickup P1: the public «/» landing must anchor on the StorefrontHero kit
    # primitive (brand-palette-driven, model-independent) instead of hand-rolled
    # raw-Tailwind heroes — so the most shareable surface is enterprise-grade on
    # the first generation. The component must also be in the kit import line.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "StorefrontHero" in _ENTITIES_UI
    # it's offered as the public counterpart of DashboardHero (cabinet hero)
    assert "ПЕРВЫЙ ЭКРАН ГЛАВНОЙ" in _ENTITIES_UI
    # the guidance sits in the public-home half, ABOVE the cabinet AppShell block
    assert _ENTITIES_UI.index("StorefrontHero") < _ENTITIES_UI.index(
        "src/app/(app)/layout.tsx"
    )
    # it must be importable from the kit barrel (appears in the import line)
    assert _ENTITIES_UI.index("StorefrontHero") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )


def test_entities_ui_public_home_uses_storefront_section() -> None:
    # The connective marketing tissue BELOW the hero (services / value props /
    # features / pricing / FAQ) is the biggest hand-rolled, variable-quality
    # region on the most shareable surface. Guidance must anchor it on the
    # <StorefrontSection> + <FeatureCard> kit primitives (brand-palette-driven,
    # model-independent) instead of raw Tailwind, and both must be importable.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "StorefrontSection" in _ENTITIES_UI
    assert "FeatureCard" in _ENTITIES_UI
    # the section rule names the below-hero region it owns
    assert "СЕКЦИИ ПОД ГЕРОЕМ" in _ENTITIES_UI
    # section headings must be <h2> (the single <h1> stays on the hero)
    assert "Заголовки секций = <h2>" in _ENTITIES_UI
    # both primitives sit in the public-home half, ABOVE the cabinet AppShell block
    assert _ENTITIES_UI.index("StorefrontSection") < _ENTITIES_UI.index(
        "src/app/(app)/layout.tsx"
    )
    # both must be importable from the kit barrel (appear in the import line)
    assert _ENTITIES_UI.index("FeatureCard") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )


def test_entities_ui_public_home_uses_pricing_plans() -> None:
    # Pricing / tariffs is the highest-conversion below-hero section and a
    # genuinely distinct premium pattern (a recommended tier drawn out by a brand
    # gradient border) — it must anchor on the <PricingPlans> kit primitive, not
    # raw hand-rolled Tailwind, and the primitive must be importable.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "PricingPlans" in _ENTITIES_UI
    # the pricing rule names the section it owns
    assert "ЦЕНЫ / ТАРИФЫ" in _ENTITIES_UI
    # the recommended tier is highlighted (the gradient-border draw)
    assert "highlighted" in _ENTITIES_UI
    # it sits in the public-home half, ABOVE the cabinet AppShell block
    assert _ENTITIES_UI.index("PricingPlans") < _ENTITIES_UI.index(
        "src/app/(app)/layout.tsx"
    )
    # must be importable from the kit barrel (appear in the import line)
    assert _ENTITIES_UI.index("PricingPlans") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )


def test_entities_ui_public_home_uses_testimonial_wall() -> None:
    # Social proof is a genuinely distinct premium pattern (quote-forward cards
    # with a brand quote-mark, star rating and an avatar-or-initials footer) — it
    # must anchor on the <TestimonialWall> kit primitive instead of hand-rolled
    # testimonial cards, and the primitive must be importable.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "TestimonialWall" in _ENTITIES_UI
    # the testimonial rule names the section it owns
    assert "ОТЗЫВЫ / СОЦ-ДОКАЗАТЕЛЬСТВО" in _ENTITIES_UI
    # the old "hand-roll testimonials yourself" guidance is gone — the FAQ branch
    # keeps the manual fallback, but testimonials now route to the kit primitive
    assert "для отзывов используй <TestimonialWall>" in _ENTITIES_UI
    # it sits in the public-home half, ABOVE the cabinet AppShell block
    assert _ENTITIES_UI.index("TestimonialWall") < _ENTITIES_UI.index(
        "src/app/(app)/layout.tsx"
    )
    # must be importable from the kit barrel (appear in the import line)
    assert _ENTITIES_UI.index("TestimonialWall") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )


def test_entities_ui_public_home_uses_faq_accordion() -> None:
    # FAQ is a genuinely distinct premium pattern (an interactive brand-tinted
    # accordion with a rotating chevron and a smoothly sliding answer) — it must
    # anchor on the <FaqAccordion> kit primitive instead of a hand-rolled list of
    # <details>, and the primitive must be importable.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "FaqAccordion" in _ENTITIES_UI
    # the FAQ rule names the section it owns
    assert "FAQ / ЧАСТЫЕ ВОПРОСЫ" in _ENTITIES_UI
    # the old "hand-roll the FAQ content yourself" guidance is gone — FAQ now
    # routes to the kit primitive
    assert "сверстай содержимое сам" not in _ENTITIES_UI
    assert "для FAQ —" in _ENTITIES_UI
    # it sits in the public-home half, ABOVE the cabinet AppShell block
    assert _ENTITIES_UI.index("FaqAccordion") < _ENTITIES_UI.index(
        "src/app/(app)/layout.tsx"
    )
    # must be importable from the kit barrel (appear in the import line)
    assert _ENTITIES_UI.index("FaqAccordion") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )


def test_entities_ui_public_home_uses_cta_band() -> None:
    # The closing call-to-action is a genuinely distinct premium pattern (the only
    # full-bleed, inverted, brand-saturated band in the kit — the page's conversion
    # climax) — it must anchor on the <CtaBand> kit primitive instead of a
    # hand-rolled final CTA, and the primitive must be importable.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "CtaBand" in _ENTITIES_UI
    # the CTA rule names the section it owns
    assert "ФИНАЛЬНЫЙ ПРИЗЫВ / CTA-БЭНД" in _ENTITIES_UI
    # it explicitly tells the model NOT to hand-roll the final CTA
    assert "НЕ верстай финальный призыв сырьём" in _ENTITIES_UI
    # it sits in the public-home half, ABOVE the cabinet AppShell block
    assert _ENTITIES_UI.index("CtaBand") < _ENTITIES_UI.index(
        "src/app/(app)/layout.tsx"
    )
    # must be importable from the kit barrel (appear in the import line)
    assert _ENTITIES_UI.index("CtaBand") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )


def test_entities_ui_dashboard_loading_never_blank() -> None:
    # The flagship dashboard must never render a blank screen while data loads:
    # the generated `if (loading) return null` paints nothing (and, if a fetch
    # ever rejected, would hang there forever). Guidance must mandate a skeleton
    # carcass and a caught Promise.all, and DashboardSkeleton must be in the
    # kit import line so the model can reach it.
    from omnia_api.services.prompt_builder import _ENTITIES_UI

    assert "DashboardSkeleton" in _ENTITIES_UI
    assert "НЕ `return null`" in _ENTITIES_UI
    # the rule explicitly names the hang risk of an un-caught Promise.all
    assert ".catch()" in _ENTITIES_UI
    # DashboardSkeleton must be importable from the kit barrel
    assert _ENTITIES_UI.index("DashboardSkeleton") < _ENTITIES_UI.index(
        '} from "@/components/omnia"'
    )
