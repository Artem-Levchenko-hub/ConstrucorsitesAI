"""Design DNA: distinct per project, idempotent, structurally valid."""

from __future__ import annotations

import re

from omnia_api.services.design_dna import MARKER, design_dna_css, inject_into_globals

_SAMPLE = '@import "tailwindcss";\n\n:root{\n  --background: oklch(1 0 0);\n  --primary: oklch(0.2 0 0);\n}\n'


def test_two_projects_get_different_identity() -> None:
    a_font, a_root = design_dna_css("11111111-aaaa-4aaa-8aaa-111111111111")
    b_font, b_root = design_dna_css("99999999-bbbb-4bbb-8bbb-999999999999")
    # Different projects must not look identical (the whole point).
    assert (a_font, a_root) != (b_font, b_root)
    assert MARKER in a_root and ":root{" in a_root and "--primary" in a_root


def test_stable_per_project() -> None:
    pid = "55555555-cccc-4ccc-8ccc-555555555555"
    assert design_dna_css(pid) == design_dna_css(pid)  # stable across reprompts


def test_injection_places_font_import_at_top_and_block_at_end() -> None:
    out = inject_into_globals(_SAMPLE, "11111111-aaaa-4aaa-8aaa-111111111111")
    lines = out.split("\n")
    assert lines[0].strip() == '@import "tailwindcss";'
    assert lines[1].strip().startswith("@import url('https://fonts.googleapis.com")
    assert out.rstrip().endswith("}")
    assert MARKER in out
    # the original template :root is preserved (we only append our own)
    assert "--background: oklch(1 0 0)" in out


def test_idempotent_no_stacking() -> None:
    pid = "11111111-aaaa-4aaa-8aaa-111111111111"
    once = inject_into_globals(_SAMPLE, pid)
    twice = inject_into_globals(once, pid)
    assert once == twice  # re-run swaps, never stacks
    assert once.count(MARKER) == 1
    assert len(re.findall(r"fonts\.googleapis\.com", once)) == 1
