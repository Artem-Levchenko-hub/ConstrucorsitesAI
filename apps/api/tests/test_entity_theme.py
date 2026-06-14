"""Tests for the entity/.tsx neutral-colour → theme-token guard."""

from __future__ import annotations

from omnia_api.services.entity_theme import tokenize_neutrals
from omnia_api.services.structure_audit import audit_entity_app


def _tok(code: str) -> str:
    out, _ = tokenize_neutrals({"src/app/(app)/page.tsx": code})
    return out["src/app/(app)/page.tsx"]


def test_neutral_text_and_bg_become_tokens():
    assert _tok('<p className="text-gray-800">x</p>') == '<p className="text-foreground">x</p>'
    assert _tok('<div className="bg-gray-100">') == '<div className="bg-muted">'
    assert _tok('<div className="bg-white">') == '<div className="bg-card">'
    # light text shade → muted-foreground, dark → foreground
    assert _tok('<span className="text-zinc-500">') == '<span className="text-muted-foreground">'
    assert _tok('<span className="text-slate-900">') == '<span className="text-foreground">'


def test_border_and_ring_map_to_their_tokens():
    assert _tok("border border-gray-200") == "border border-border"
    assert _tok("ring-neutral-300") == "ring-ring"


def test_variants_and_opacity_are_preserved():
    # tokenize_neutrals works on raw text, so bare utility strings suffice here.
    assert _tok("hover:bg-gray-100") == "hover:bg-muted"
    assert _tok("dark:text-gray-400") == "dark:text-muted-foreground"
    assert _tok("bg-gray-100/50") == "bg-muted/50"


def test_semantic_status_colours_are_left_untouched():
    # The brief prescribes green/yellow/red for paid/pending/cancelled — not neutrals.
    src = '<span className="bg-green-100 text-green-800">Оплачено</span>'
    assert _tok(src) == src
    src2 = '<span className="bg-red-100 text-red-800">Отменено</span>'
    assert _tok(src2) == src2


def test_fixed_kit_and_non_tsx_untouched():
    files = {
        "src/components/ui/button.tsx": '<button className="bg-gray-100">',  # fixed kit
        "src/app/globals.css": ".x{ background: white }",  # not .tsx
        "src/lib/utils.ts": "const c = 'bg-gray-100'",  # lib
    }
    out, n = tokenize_neutrals(files)
    assert out == files
    assert n == 0


def test_rewrite_clears_the_structure_audit_hardcoded_colour_class():
    dirty = {
        "src/app/(app)/page.tsx": (
            '<div className="bg-white"><p className="text-gray-800">Дашборд</p>'
            '<span className="border-gray-200">x</span></div>'
        )
    }
    before = audit_entity_app(dirty)
    assert any("hardcoded colour" in w for w in before)
    cleaned, n = tokenize_neutrals(dirty)
    assert n == 3
    after = audit_entity_app(cleaned)
    assert not any("hardcoded colour" in w for w in after)
