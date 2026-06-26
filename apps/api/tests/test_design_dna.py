"""Design DNA: distinct per project, idempotent, and CSS-safe (no @import)."""

from __future__ import annotations

from omnia_api.services.design_dna import MARKER, design_dna_css, inject_into_globals

_SAMPLE = '@import "tailwindcss";\n:root{--background: oklch(1 0 0);}\n.x{color:red}\n'
_A = "11111111-aaaa-4aaa-8aaa-111111111111"
_B = "99999999-bbbb-4bbb-8bbb-999999999999"


def test_two_projects_get_different_identity() -> None:
    a, b = design_dna_css(_A), design_dna_css(_B)
    assert a != b
    for css in (a, b):
        assert MARKER in css and ":root{" in css
        assert "--primary" in css and "--radius" in css
        # Brand overridden in dark mode too, or it loses to the template default.
        assert ":root.dark{" in css


def test_stable_per_project() -> None:
    pid = "55555555-cccc-4ccc-8ccc-555555555555"
    assert design_dna_css(pid) == design_dna_css(pid)


def test_block_is_css_safe_no_at_import() -> None:
    out = inject_into_globals(_SAMPLE, _A)
    managed = out.split(MARKER, 1)[1]
    # The managed block is a plain :root rule — NO @import (which would need to be
    # first and broke prod when appended mid-file).
    assert "@import" not in managed
    assert out.rstrip().endswith("}")
    assert "--background: oklch(1 0 0)" in out  # original preserved


def test_strips_a_broken_font_import() -> None:
    broken = (
        _SAMPLE
        + "\n@import url('https://fonts.googleapis.com/css2?family=Archivo');\n"
    )
    out = inject_into_globals(broken, _A)
    assert "fonts.googleapis.com" not in out  # the breakage is cleaned up


def test_idempotent_no_stacking() -> None:
    once = inject_into_globals(_SAMPLE, _A)
    twice = inject_into_globals(once, _A)
    assert once == twice
    assert once.count(MARKER) == 1
