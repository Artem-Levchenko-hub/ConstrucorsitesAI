"""Tests for `services.file_extractor` — `<file>` blocks + `<edit>` SEARCH/REPLACE.

The `<file>` path was already covered by integration smoke; this file focuses
on the new `<edit>` parser + apply pipeline since that's the new surface most
likely to regress when the prompt instructions evolve.
"""

from __future__ import annotations

import pytest

from omnia_api.services.file_extractor import (
    apply_edits,
    extract_edits,
    extract_files,
)

# ---------------------------------------------------------------------------
# extract_files — existing path, just confirm we didn't regress
# ---------------------------------------------------------------------------


def test_extract_files_basic() -> None:
    answer = '<file path="a.txt">hello</file>'
    assert extract_files(answer) == {"a.txt": "hello"}


def test_extract_files_skips_placeholder_stub() -> None:
    answer = '<file path="a.tsx">(код без изменений)</file>'
    assert extract_files(answer) == {}


def test_extract_files_whitespace_body_is_delete_intent() -> None:
    # The app writer empties the starter page.tsx; the model often leaves a
    # newline inside the tag. Whitespace-only body must normalise to "" so
    # downstream treats it as a delete (not a 1-byte broken module).
    assert extract_files('<file path="src/app/page.tsx"></file>') == {
        "src/app/page.tsx": ""
    }
    assert extract_files('<file path="src/app/page.tsx">\n</file>') == {
        "src/app/page.tsx": ""
    }
    assert extract_files('<file path="src/app/page.tsx">\n   \n</file>') == {
        "src/app/page.tsx": ""
    }


def test_extract_files_rewrites_invented_palette_vars() -> None:
    # The writer invents var(--muted)/var(--accent)/var(--bg)/var(--fg)/
    # var(--bg-alt) for landings and never defines them; --muted/--accent
    # collide with shadcn surface tokens => invisible text. The extractor
    # rewrites usages to real kit tokens (.tsx only).
    answer = (
        '<file path="src/app/page.tsx">'
        '<p className="text-[var(--muted)] bg-[var(--bg)]">hi</p>'
        '<span className="text-[var(--accent)]">x</span>'
        '<section className="bg-[var(--bg-alt)] text-[var(--fg)]" /></file>'
    )
    out = extract_files(answer)["src/app/page.tsx"]
    assert "var(--muted-foreground)" in out  # secondary text now visible
    assert "var(--primary)" in out  # accent -> brand
    assert "var(--background)" in out
    assert "var(--foreground)" in out
    assert "bg-[var(--muted)]" in out  # bg-alt -> muted surface (still light)
    # no invented var leaks through
    for bad in ("var(--bg)", "var(--fg)", "var(--accent)", "var(--bg-alt)"):
        assert bad not in out
    # bare var(--muted) only survives as the bg-alt rewrite, never as text
    assert "text-[var(--muted)]" not in out


def test_extract_files_palette_fix_is_scoped_to_source_files() -> None:
    # A non-source file (e.g. .md/.txt) is left byte-identical.
    answer = '<file path="notes.md">use var(--muted) here</file>'
    assert extract_files(answer) == {"notes.md": "use var(--muted) here"}


def test_extract_files_aliases_hallucinated_lucide_brand_icons() -> None:
    # lucide-react does not export Telegram/Whatsapp/Tiktok; importing them
    # bare breaks the Turbopack build. We alias a valid glyph to the local name
    # so `<Telegram/>` usages keep working and the build survives.
    answer = (
        '<file path="src/app/page.tsx">'
        'import { Mail, Telegram, Whatsapp } from "lucide-react";\n'
        "export const F = () => <><Telegram/><Whatsapp/></>;</file>"
    )
    out = extract_files(answer)["src/app/page.tsx"]
    assert "Send as Telegram" in out
    assert "MessageCircle as Whatsapp" in out
    assert "Mail," in out  # valid icon untouched
    # usages keep their original local name (no usage rewrite needed)
    assert "<Telegram/>" in out and "<Whatsapp/>" in out
    # no bare hallucinated specifier leaks into the import
    assert "{ Mail, Send as Telegram, MessageCircle as Whatsapp }" in out


def test_extract_files_lucide_fix_handles_casing_and_existing_alias() -> None:
    # Internal-caps (TikTok) match via lower-cased lookup; an already-aliased
    # hallucinated import keeps its chosen local name.
    answer = (
        '<file path="src/F.tsx">'
        'import { TikTok, VK as Soc } from "lucide-react";</file>'
    )
    out = extract_files(answer)["src/F.tsx"]
    assert "Music2 as TikTok" in out
    assert "Share2 as Soc" in out


def test_extract_files_lucide_fix_noop_when_all_valid() -> None:
    # An import of only real icons passes through byte-identical (R-10 fail-soft),
    # and brand glyphs lucide actually ships (Github, Twitter) are preserved.
    src = 'import { Github, Twitter, Mail } from "lucide-react";'
    answer = f'<file path="src/F.tsx">{src}</file>'
    assert extract_files(answer)["src/F.tsx"] == src


def test_extract_files_lucide_fix_scoped_to_source_files() -> None:
    # A non-source file mentioning lucide is left byte-identical.
    answer = '<file path="README.md">import { Telegram } from "lucide-react"</file>'
    assert extract_files(answer)["README.md"] == (
        'import { Telegram } from "lucide-react"'
    )


# ---------------------------------------------------------------------------
# extract_edits — new parser
# ---------------------------------------------------------------------------


def test_extract_edits_single_block() -> None:
    answer = (
        '<edit path="index.html">\n'
        "<<<<<<< SEARCH\n"
        '<button class="bg-blue-500">Купить</button>\n'
        "=======\n"
        '<button class="bg-emerald-500">Купить сейчас</button>\n'
        ">>>>>>> REPLACE\n"
        "</edit>"
    )
    edits = extract_edits(answer)
    assert "index.html" in edits
    assert len(edits["index.html"]) == 1
    search, replace = edits["index.html"][0]
    assert "bg-blue-500" in search
    assert "bg-emerald-500" in replace


def test_extract_edits_multiple_sr_blocks_in_one_edit() -> None:
    answer = (
        '<edit path="page.tsx">\n'
        "<<<<<<< SEARCH\nfoo()\n=======\nfoo(1)\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\nbar()\n=======\nbar(2)\n>>>>>>> REPLACE\n"
        "</edit>"
    )
    edits = extract_edits(answer)
    assert len(edits["page.tsx"]) == 2
    assert edits["page.tsx"][0] == ("foo()", "foo(1)")
    assert edits["page.tsx"][1] == ("bar()", "bar(2)")


def test_extract_edits_multiple_files() -> None:
    answer = (
        '<edit path="a.html">\n'
        "<<<<<<< SEARCH\nA\n=======\nA1\n>>>>>>> REPLACE\n"
        "</edit>\n"
        '<edit path="b.html">\n'
        "<<<<<<< SEARCH\nB\n=======\nB1\n>>>>>>> REPLACE\n"
        "</edit>"
    )
    edits = extract_edits(answer)
    assert set(edits.keys()) == {"a.html", "b.html"}


def test_extract_edits_skips_edit_with_no_sr_markers() -> None:
    """Model forgot the markers → we log and skip rather than guess."""
    answer = (
        '<edit path="ok.html">\n'
        "<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE\n"
        "</edit>\n"
        '<edit path="empty.html">\nplaceholder text, no markers\n</edit>'
    )
    edits = extract_edits(answer)
    assert "ok.html" in edits
    assert "empty.html" not in edits


def test_extract_edits_returns_empty_when_no_edits() -> None:
    """Answer that contains only <file> blocks (legacy) yields no edits."""
    answer = '<file path="a.txt">hello</file>'
    assert extract_edits(answer) == {}


# ---------------------------------------------------------------------------
# apply_edits — the actual diff application
# ---------------------------------------------------------------------------


BASE = {
    "index.html": (
        "<!doctype html>\n"
        "<html>\n"
        "<body>\n"
        '<button class="bg-blue-500">Купить</button>\n'
        "<p>Привет, мир</p>\n"
        "</body>\n"
        "</html>"
    ),
}


def test_apply_edits_happy_path() -> None:
    edits = {
        "index.html": [
            (
                '<button class="bg-blue-500">Купить</button>',
                '<button class="bg-emerald-500">Купить сейчас</button>',
            )
        ]
    }
    updated, conflicts = apply_edits(edits, BASE)
    assert conflicts == []
    assert "bg-emerald-500" in updated["index.html"]
    assert "bg-blue-500" not in updated["index.html"]
    # Surrounding content preserved
    assert "<p>Привет, мир</p>" in updated["index.html"]


def test_apply_edits_multiple_sr_in_one_file() -> None:
    edits = {
        "index.html": [
            ("Купить", "Заказать"),
            ("Привет", "Здравствуй"),
        ]
    }
    updated, conflicts = apply_edits(edits, BASE)
    assert conflicts == []
    assert "Заказать" in updated["index.html"]
    assert "Здравствуй" in updated["index.html"]


def test_apply_edits_missing_search_records_conflict() -> None:
    edits = {
        "index.html": [
            ("этой строки нет в файле", "новая строка"),
        ]
    }
    updated, conflicts = apply_edits(edits, BASE)
    assert "index.html" not in updated
    assert len(conflicts) == 1
    assert "SEARCH-блок не найден" in conflicts[0]


def test_apply_edits_ambiguous_search_records_conflict() -> None:
    base = {"a.txt": "x\nx\n"}  # "x" appears twice
    edits = {"a.txt": [("x", "y")]}
    updated, conflicts = apply_edits(edits, base)
    assert "a.txt" not in updated
    assert "неоднозначен" in conflicts[0]
    assert "2 вхождений" in conflicts[0]


def test_apply_edits_indent_tolerant() -> None:
    """A SEARCH whose lines match but with different indentation still applies —
    a cheap model often reproduces the right lines with the wrong leading
    whitespace, the #1 reason a correct edit silently fails to land."""
    base = {
        "index.html": (
            '    <section id="hero" class="bg-[#0C0A09]">\n'
            "      <h1>Заголовок</h1>\n"
            "    </section>"
        )
    }
    edits = {
        "index.html": [
            (
                # no indentation, different from the file
                '<section id="hero" class="bg-[#0C0A09]">\n<h1>Заголовок</h1>\n</section>',
                '<section id="hero" class="bg-[#1A1A2E]">\n<h1>Заголовок</h1>\n</section>',
            )
        ]
    }
    updated, conflicts = apply_edits(edits, base)
    assert conflicts == []
    assert "bg-[#1A1A2E]" in updated["index.html"]
    assert "bg-[#0C0A09]" not in updated["index.html"]


def test_apply_edits_content_mismatch_still_refused() -> None:
    """Indent tolerance must NOT forgive a real content difference — a SEARCH
    naming a tag/attribute that isn't in the file (e.g. a hallucinated
    data-omnia-photo where the committed file has a resolved <img src>) must
    still be refused, not fuzzed onto the wrong element."""
    base = {"index.html": '<img src="https://cdn.example/x.jpg" alt="a">'}
    edits = {
        "index.html": [
            ('<img data-omnia-photo="sushi plate" alt="a">', '<img src="y.jpg">')
        ]
    }
    updated, conflicts = apply_edits(edits, base)
    assert "index.html" not in updated
    assert len(conflicts) == 1


def test_apply_edits_one_bad_pair_does_not_drop_the_good_ones() -> None:
    """A multi-pair edit (e.g. image swap + overlay tweak): if a later pair's
    SEARCH misses, the pairs that DID match must still be committed — one miss
    must not throw away the whole edit."""
    base = {"index.html": "<img src='a.jpg'>\n<div class='keep'>x</div>"}
    edits = {
        "index.html": [
            ("<img src='a.jpg'>", "<img src='b.jpg'>"),  # applies
            ("NO-SUCH-ANCHOR-HERE", "y"),  # misses
        ]
    }
    updated, conflicts = apply_edits(edits, base)
    assert "b.jpg" in updated["index.html"]  # the good pair landed
    assert "a.jpg" not in updated["index.html"]
    assert "<div class='keep'>x</div>" in updated["index.html"]  # rest intact
    assert len(conflicts) == 1  # the miss was still reported


def test_apply_edits_missing_file_records_conflict() -> None:
    edits = {"nope.html": [("foo", "bar")]}
    updated, conflicts = apply_edits(edits, BASE)
    assert updated == {}
    assert len(conflicts) == 1
    assert "несуществующего файла" in conflicts[0]


def test_apply_edits_partial_success_keeps_independent_files() -> None:
    """One file's edit fails → other files still get applied. Fail-soft."""
    base = {
        "good.html": "hello world",
        "bad.html": "nothing useful here",
    }
    edits = {
        "good.html": [("hello", "Hello")],
        "bad.html": [("missing-search", "x")],
    }
    updated, conflicts = apply_edits(edits, base)
    assert updated == {"good.html": "Hello world"}
    assert len(conflicts) == 1
    assert "bad.html" in conflicts[0]


def test_apply_edits_idempotency_returns_only_changed() -> None:
    """A file present in `edits` but with no actual change ends up in updated
    (we ran .replace, content might be byte-identical). That's fine: the
    downstream commit step is idempotent."""
    edits = {
        "index.html": [
            (
                '<button class="bg-blue-500">Купить</button>',
                '<button class="bg-blue-500">Купить</button>',
            )
        ]
    }
    updated, conflicts = apply_edits(edits, BASE)
    assert conflicts == []
    assert updated["index.html"] == BASE["index.html"]


@pytest.mark.parametrize(
    "marker_left,marker_right",
    [
        ("<<<<<<<", ">>>>>>>"),  # canonical 7 chars
        ("<<<<<<<<", ">>>>>>>>"),  # 8 chars — some models drift
        ("<<<<<<", ">>>>>>"),  # 6 chars — also seen
    ],
)
def test_extract_edits_tolerates_marker_length_drift(
    marker_left: str, marker_right: str
) -> None:
    """Models occasionally emit 6 or 8 char marker runs. Parser tolerates 6-9."""
    answer = (
        '<edit path="x.txt">\n'
        f"{marker_left} SEARCH\n"
        "foo\n"
        "=======\n"
        "bar\n"
        f"{marker_right} REPLACE\n"
        "</edit>"
    )
    edits = extract_edits(answer)
    assert edits["x.txt"] == [("foo", "bar")]
