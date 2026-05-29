"""Tests for the per-vendor instruction layer (Phase N+).

Covers the vendor-detection table, directive non-emptiness / json_strict
behaviour, the GENERIC no-op contract, and that the message builders in
director_polish / multipass actually inject the right family's block into the
*user* turn while leaving the *system* turn byte-identical (prompt-cache
safety).
"""

from __future__ import annotations

import os

# Settings() requires these env vars; set before importing config-touching code.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET", "test-secret")

from omnia_api.services.vendor_profiles import (  # noqa: E402
    CLAUDE,
    DEEPSEEK,
    GENERIC,
    GOOGLE,
    OPENAI,
    SBER,
    YANDEX,
    vendor_directive,
    vendor_for_model,
)

_NON_GENERIC = [
    "claude-opus-4-7",
    "gpt-5",
    "gemini-2.5-flash",
    "yandexgpt-5",
    "gigachat-2-pro",
    "deepseek-chat",
]


def test_vendor_for_model_table() -> None:
    assert vendor_for_model("claude-opus-4-7") == CLAUDE
    assert vendor_for_model("claude-haiku-4-5") == CLAUDE
    assert vendor_for_model("gpt-5") == OPENAI
    assert vendor_for_model("gpt-5-nano") == OPENAI
    assert vendor_for_model("gpt-4.1") == OPENAI
    assert vendor_for_model("o3-mini") == OPENAI
    assert vendor_for_model("gemini-2.5-flash") == GOOGLE
    assert vendor_for_model("gemini-2.5-pro") == GOOGLE
    assert vendor_for_model("yandexgpt-5") == YANDEX
    assert vendor_for_model("gigachat-2-pro") == SBER
    assert vendor_for_model("deepseek-chat") == DEEPSEEK


def test_unknown_and_none_are_generic() -> None:
    assert vendor_for_model("totally-unknown-model") == GENERIC
    assert vendor_for_model(None) == GENERIC
    assert vendor_for_model("") == GENERIC


def test_case_insensitive_detection() -> None:
    assert vendor_for_model("Claude-Opus-4-7") == CLAUDE
    assert vendor_for_model("GEMINI-2.5-PRO") == GOOGLE


def test_non_generic_vendors_have_nonempty_directive() -> None:
    for mid in _NON_GENERIC:
        assert vendor_directive(mid).strip(), f"empty directive for {mid}"


def test_generic_directive_is_empty() -> None:
    # The whole point of GENERIC: appending it is a no-op (zero regression).
    assert vendor_directive("unknown-x") == ""
    assert vendor_directive(None) == ""
    assert vendor_directive(None, json_strict=True) == ""
    assert vendor_directive("unknown-x", json_strict=True) == ""


def test_json_strict_extends_base() -> None:
    for mid in _NON_GENERIC:
        base = vendor_directive(mid, json_strict=False)
        strict = vendor_directive(mid, json_strict=True)
        assert strict != base, f"json_strict identical to base for {mid}"
        assert base in strict, f"json_strict should extend base for {mid}"


def test_gemini_json_strict_demands_no_fences() -> None:
    # The load-bearing calibration: Gemini wraps JSON in ```json fences and
    # breaks strict PageIR validation. The directive must explicitly forbid it.
    strict = vendor_directive("gemini-2.5-flash", json_strict=True)
    assert "```json" in strict
    assert "markdown" in strict.lower()


# ── Message-builder injection (depends on A2 wiring) ──────────────────────────

_BASE = [
    {"role": "system", "content": "SYSTEM-PROMPT-BLOCK"},
    {"role": "user", "content": "сайт для кофейни"},
]


def test_director_messages_inject_gemini_directive() -> None:
    from omnia_api.services.director_polish import _build_director_messages

    msgs = _build_director_messages(_BASE, "сайт для кофейни", "gemini-2.5-flash")
    last = msgs[-1]["content"]
    assert "```json" in last  # google no-fences directive landed in user turn


def test_polish_messages_inject_gemini_directive() -> None:
    from omnia_api.services.director_polish import _build_polish_messages

    msgs = _build_polish_messages(_BASE, "сайт для кофейни", '{"sections": []}', "gemini-2.5-flash")
    assert "```json" in msgs[-1]["content"]


def test_skeleton_messages_inject_gemini_directive() -> None:
    from omnia_api.services.multipass_generator import _build_skeleton_messages

    msgs = _build_skeleton_messages(_BASE, "сайт для кофейни", "gemini-2.5-flash")
    assert "```json" in msgs[-1]["content"]


def test_system_prompt_stays_byte_identical_for_cache() -> None:
    # Vendor block goes in the USER turn only — the system block must be
    # untouched so Anthropic's ephemeral prompt cache still hits across passes.
    from omnia_api.services.director_polish import (
        _build_director_messages,
        _build_polish_messages,
    )

    d = _build_director_messages(_BASE, "x", "gemini-2.5-flash")
    p = _build_polish_messages(_BASE, "x", "{}", "claude-opus-4-7")
    assert d[0] == _BASE[0]
    assert p[0] == _BASE[0]


def test_generic_model_adds_nothing_to_messages() -> None:
    # An uncalibrated/unknown model must not change the assembled user turn
    # beyond the existing role instruction (no stray empty lines from "").
    from omnia_api.services.director_polish import _build_director_messages

    msgs = _build_director_messages(_BASE, "сайт для кофейни", "some-unknown-model")
    generic_tail = msgs[-1]["content"]
    msgs_none = _build_director_messages(_BASE, "сайт для кофейни", None)
    assert generic_tail == msgs_none[-1]["content"]
