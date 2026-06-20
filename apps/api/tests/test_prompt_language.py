"""Golden tests for Phase A3 — multi-language prompt parameterisation.

CRITICAL INVARIANT: passing language="ru" (or the default) must produce
*byte-identical* output to calling the same function without a language
argument.  Any deviation is a regression, even a single leading newline.
"""

from omnia_api.services.prompt_builder import _language_directive, build_system_prompt


# ─── _language_directive unit tests ──────────────────────────────────────────


def test_directive_empty_for_ru() -> None:
    assert _language_directive("ru") == ""
    assert _language_directive("RU") == ""
    assert _language_directive("RU-RU") == ""
    assert _language_directive("Ru") == ""


def test_directive_non_empty_for_non_ru() -> None:
    assert "fr" in _language_directive("fr")
    assert "en" in _language_directive("en")
    assert "ЯЗЫК ПРОЕКТА: en" in _language_directive("en")
    assert "ЯЗЫК ПРОЕКТА: fr" in _language_directive("fr")
    assert "ЯЗЫК ПРОЕКТА: de" in _language_directive("de")


# ─── build_system_prompt byte-identity tests ─────────────────────────────────


def test_ru_is_byte_identical_to_default() -> None:
    """Passing language='ru' must equal calling without language at all.

    This is the CRITICAL invariant: the RU prompt must never be mutated
    by the language-parameterisation machinery.
    """
    for template in ("blank", "landing", "portfolio", "blog", "fullstack", "spa", "code"):
        default_out = build_system_prompt(template=template)
        ru_out = build_system_prompt(template=template, language="ru")
        assert default_out == ru_out, (
            f"[{template}] language='ru' produced different output from default!\n"
            f"First diff at char {next(i for i,(a,b) in enumerate(zip(default_out, ru_out)) if a != b) if default_out != ru_out and len(default_out) == len(ru_out) else 'length differs'}"
        )
        # No leading blank sections introduced
        assert not ru_out.startswith("\n"), f"[{template}] output starts with newline"
        assert not default_out.startswith("\n"), f"[{template}] default starts with newline"


def test_non_ru_injects_directive_and_differs() -> None:
    """Non-RU language must inject the override directive and differ from RU."""
    for template in ("landing", "fullstack", "code"):
        ru_out = build_system_prompt(template=template, language="ru")
        en_out = build_system_prompt(template=template, language="en")
        assert "ЯЗЫК ПРОЕКТА: en" in en_out, f"[{template}] directive missing"
        assert en_out != ru_out, f"[{template}] en output same as ru — directive was not injected"
        # Directive must appear at the very START (before everything else)
        assert en_out.startswith("ЯЗЫК ПРОЕКТА: en"), (
            f"[{template}] directive is not at position 0 of the output.\n"
            f"Actual start: {en_out[:120]!r}"
        )


def test_non_ru_preserves_no_leading_newline() -> None:
    """Non-RU output must not start with a blank line / double newline."""
    for template in ("landing", "blank"):
        out = build_system_prompt(template=template, language="fr")
        assert not out.startswith("\n"), f"[{template}] fr output starts with newline"
