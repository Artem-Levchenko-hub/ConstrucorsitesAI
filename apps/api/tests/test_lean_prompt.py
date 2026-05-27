"""Regression tests for the lean catalog system prompt (Phase L4).

Guards against the prompt drifting back to a 14 KB monolith. If a future
edit needs more than ~14 K chars (~3.5 K tokens approx), it should land
as a new opt-in section in the freeform path — not bloat the lean prompt.
"""

from __future__ import annotations


# Approx 4 chars per token for mixed Russian + English + Tailwind classes.
# Real tiktoken count is slightly lower (closer to 3.7 chars/token for
# Russian-heavy text); 4 chars/token is a *conservative* over-count, so
# if this passes, real token count is comfortably below the limit.
_CHARS_PER_TOKEN = 4
_TOKEN_BUDGET = 3500
_CHAR_BUDGET = _TOKEN_BUDGET * _CHARS_PER_TOKEN  # 14_000 chars


def test_lean_prompt_under_token_budget() -> None:
    """The single source of truth: lean prompt must stay ≤ 14 K chars."""
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    assert len(prompt) <= _CHAR_BUDGET, (
        f"Lean prompt grew to {len(prompt)} chars (>{_CHAR_BUDGET}). "
        f"Approx token count: {len(prompt) // _CHARS_PER_TOKEN}. "
        f"Add new material to the freeform prompt or split the lean prompt — "
        f"do NOT exceed the budget."
    )


def test_lean_prompt_contains_all_eight_vibes() -> None:
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    required_vibes = [
        "swiss-minimal",
        "apple-tech",
        "linear-dark",
        "fintech-trust",
        "editorial-luxury",
        "brutalist",
        "glassmorphism",
        "y2k-neo",
    ]
    missing = [v for v in required_vibes if v not in prompt]
    assert not missing, f"Lean prompt missing vibes: {missing}"


def test_palette_tail_is_at_the_tail() -> None:
    """The palette HEX table must appear in the LAST 1500 chars of the
    prompt — that's the anti-lost-in-middle anchor; if it migrates to
    the middle, this test catches the regression."""
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    tail = prompt[-1500:]
    assert "<palette_tail>" in tail, "palette_tail anchor moved out of tail"
    # Sample HEX from the table — picked because it's industry-specific
    # and stable; if someone changes it they will have to update the test
    # too, which is the desired tripwire.
    assert "#0B1220" in tail, "fintech palette HEX missing from tail"


def test_catalog_blurb_inlined() -> None:
    """If sections.catalog.CATALOG_BLURB changes shape, this fails fast."""
    from omnia_api.sections.catalog import CATALOG_BLURB
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    # Take a stable line from CATALOG_BLURB (variant ids never rename
    # casually). If you DO rename a variant, update both.
    assert "header.v1" in prompt
    assert "footer.v1" in prompt
    # Bonus: confirm the blurb's text actually inlines (not a stale copy)
    sentinel = CATALOG_BLURB.strip().splitlines()[0]
    assert sentinel in prompt


def test_output_format_block_present_and_states_json_only() -> None:
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    assert "<output_format>" in prompt
    assert "JSON" in prompt
    # Must explicitly forbid markdown fences — Opus has a habit of
    # wrapping JSON in ```json``` even when told not to.
    assert "БЕЗ" in prompt and "```json" in prompt


def test_identity_at_the_head() -> None:
    """Identity tag should be in the FIRST 500 chars (high-attention)."""
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    head = prompt[:500]
    assert "<identity>" in head


def test_skill_brief_inlined_when_provided() -> None:
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    brief = {"brief_text": "TEST_BRIEF_SENTINEL_42"}
    prompt = build_lean_system_prompt(preset_id=None, skill_brief=brief)
    assert "<ux_brief>" in prompt
    assert "TEST_BRIEF_SENTINEL_42" in prompt


def test_skill_brief_absent_when_not_provided() -> None:
    from omnia_api.services.lean_prompt import build_lean_system_prompt

    prompt = build_lean_system_prompt(preset_id=None, skill_brief=None)
    assert "<ux_brief>" not in prompt


def test_build_catalog_messages_returns_system_plus_user() -> None:
    from omnia_api.services.lean_prompt import build_catalog_messages

    msgs = build_catalog_messages(
        history=[],
        user_prompt="Сайт для кофейни в Москве",
        selected_elements=None,
        preset_id=None,
        project_id=None,
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "кофейни" in msgs[1]["content"]
