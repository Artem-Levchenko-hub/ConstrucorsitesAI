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


def test_extract_files_aliases_nonbrand_hallucinated_icons() -> None:
    # The app-killer that motivated the comprehensive fix: the writer reached for
    # `<Trend/>` (lucide exports `TrendingUp`, not `Trend`) and `<Dashboard/>`
    # (it's `LayoutDashboard`). These are not brands, so the old denylist missed
    # them and the whole build died. Now any name absent from the canonical export
    # set is aliased to a neutral `Circle`, keeping `<Trend/>` usages working.
    answer = (
        '<file path="src/app/page.tsx">'
        'import { TrendingUp, Trend, Dashboard } from "lucide-react";\n'
        "export const F = () => <><Trend/><Dashboard/><TrendingUp/></>;</file>"
    )
    out = extract_files(answer)["src/app/page.tsx"]
    assert "Circle as Trend" in out
    assert "Circle as Dashboard" in out
    assert "TrendingUp," in out  # real icon untouched (not aliased)
    assert "<Trend/>" in out and "<Dashboard/>" in out  # usages keep local names


def test_extract_files_lucide_fix_passes_through_alias_forms() -> None:
    # lucide also ships `<Base>Icon` and `Lucide<Base>` aliases for every icon;
    # flagging those as invalid would corrupt a working import. They must survive.
    src = 'import { TrendingUpIcon, LucideHome, Mail } from "lucide-react";'
    answer = f'<file path="src/F.tsx">{src}</file>'
    assert extract_files(answer)["src/F.tsx"] == src


def test_extract_files_pins_locale_on_bare_to_locale_string() -> None:
    # A bare `.toLocaleString()` resolves to the runtime locale, which differs
    # between the SSR (container Node) and client (browser) passes -> the price
    # renders "4 500" server-side and "4,500" client-side => hydration mismatch.
    # The extractor pins 'ru-RU' so both passes emit the identical string.
    answer = (
        '<file path="src/app/page.tsx">'
        "<span>{price.toLocaleString()} ₽</span>"
        "<time>{date.toLocaleDateString()}</time></file>"
    )
    out = extract_files(answer)["src/app/page.tsx"]
    assert "price.toLocaleString('ru-RU')" in out
    assert "date.toLocaleDateString('ru-RU')" in out
    assert "toLocaleString()" not in out  # no bare call leaks through


def test_extract_files_locale_fix_leaves_explicit_locale_untouched() -> None:
    # A call that already passes a first argument is deterministic across runtimes
    # and must pass through byte-identical (R-10 fail-soft). Non-source files too.
    src = (
        "a.toLocaleString('en-US');"
        "b.toLocaleString(undefined, { style: 'currency' });"
    )
    answer = f'<file path="src/F.tsx">{src}</file>'
    assert extract_files(answer)["src/F.tsx"] == src
    md = '<file path="notes.md">{n.toLocaleString()}</file>'
    assert extract_files(md)["notes.md"] == "{n.toLocaleString()}"


def test_extract_files_adds_use_client_for_inline_handler() -> None:
    # A landing page.tsx is a Server Component by default; an inline onSubmit
    # handler makes RSC throw a 500. The extractor prepends "use client".
    answer = (
        '<file path="src/app/page.tsx">'
        'import Link from "next/link";\n'
        "export default function HomePage() {\n"
        "  return <form onSubmit={(e) => e.preventDefault()}><Link href='/'/></form>;\n"
        "}</file>"
    )
    out = extract_files(answer)["src/app/page.tsx"]
    assert out.startswith('"use client";\n\n')
    assert "onSubmit" in out  # body preserved


def test_extract_files_use_client_noop_when_safe() -> None:
    # Already-directed file, handler-free file, server-only file (metadata /
    # async page), and non-source file all pass through byte-identical.
    already = '"use client";\nexport const F = () => <button onClick={() => 0}/>;'
    assert extract_files(f'<file path="a.tsx">{already}</file>')["a.tsx"] == already

    static = 'export default function P() { return <div>hi</div>; }'
    assert extract_files(f'<file path="b.tsx">{static}</file>')["b.tsx"] == static

    # server component with metadata + a handler => can't be client; left as-is
    server = (
        "export const metadata = { title: 'x' };\n"
        "export default function P() { return <form onSubmit={() => 0}/>; }"
    )
    assert extract_files(f'<file path="c.tsx">{server}</file>')["c.tsx"] == server

    md = '<file path="d.md">use onClick={x} in prose</file>'
    assert extract_files(md)["d.md"] == "use onClick={x} in prose"


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


# ---------------------------------------------------------------------------
# _fix_dead_internal_links — dead CTA → nearest generated ancestor (Phase 8.1)
# ---------------------------------------------------------------------------


_STUB = "export default function S(){return null}"


def _answer(files: dict[str, str]) -> str:
    return "\n".join(
        f'<file path="{p}">{b}</file>' for p, b in files.items()
    )


def test_dead_link_rewritten_to_nearest_ancestor() -> None:
    """CTA to an ungenerated route → rewritten to the deepest existing ancestor."""
    files = extract_files(
        _answer(
            {
                "src/app/(app)/dashboard/page.tsx": (
                    'export default function P(){return <a '
                    'href="/dashboard/appointments/new">Записаться</a>}'
                ),
                "src/app/(app)/dashboard/appointments/page.tsx": _STUB,
            }
        )
    )
    # /dashboard/appointments/new is absent; /dashboard/appointments exists.
    assert 'href="/dashboard/appointments"' in files["src/app/(app)/dashboard/page.tsx"]
    assert "/dashboard/appointments/new" not in files["src/app/(app)/dashboard/page.tsx"]


def test_link_to_existing_route_untouched() -> None:
    files = extract_files(
        _answer(
            {
                "src/app/(app)/dashboard/page.tsx": (
                    'export default function P(){return <Link '
                    'href="/dashboard/clients">Клиенты</Link>}'
                ),
                "src/app/(app)/dashboard/clients/page.tsx": _STUB,
            }
        )
    )
    assert 'href="/dashboard/clients"' in files["src/app/(app)/dashboard/page.tsx"]


def test_dynamic_route_link_untouched() -> None:
    """A literal link to a concrete id matches the generated [id] route."""
    files = extract_files(
        _answer(
            {
                "src/app/(app)/dashboard/page.tsx": (
                    'export default function P(){return <a '
                    'href="/dashboard/clients/42">open</a>}'
                ),
                "src/app/(app)/dashboard/clients/[id]/page.tsx": _STUB,
            }
        )
    )
    assert 'href="/dashboard/clients/42"' in files["src/app/(app)/dashboard/page.tsx"]


def test_external_and_hash_links_untouched() -> None:
    body = (
        'export default function P(){return <div>'
        '<a href="https://x.io/y">x</a>'
        '<a href="#features">f</a>'
        '<a href="mailto:a@b.io">m</a>'
        '<a href="//cdn.x/z">z</a>'
        "</div>}"
    )
    files = extract_files(
        _answer(
            {
                "src/app/(app)/dashboard/page.tsx": body,
                "src/app/(marketing)/page.tsx": "export default function H(){return null}",
            }
        )
    )
    assert files["src/app/(app)/dashboard/page.tsx"] == body


def test_cross_turn_link_with_no_ancestor_untouched() -> None:
    """Link whose ancestors are also absent → likely another turn → left alone."""
    body = (
        'export default function P(){return <a '
        'href="/store/items/7">go</a>}'
    )
    files = extract_files(
        _answer({"src/app/(app)/dashboard/page.tsx": body})
    )
    assert files["src/app/(app)/dashboard/page.tsx"] == body


def test_concatenated_href_untouched() -> None:
    """`href={"/x/" + id}` is interpolation, not a literal — never rewritten."""
    body = (
        'export default function P(){return <a '
        'href={"/dashboard/missing/" + id}>go</a>}'
    )
    files = extract_files(
        _answer(
            {
                "src/app/(app)/dashboard/page.tsx": body,
            }
        )
    )
    assert files["src/app/(app)/dashboard/page.tsx"] == body


def test_root_link_untouched() -> None:
    body = (
        'export default function P(){return <a href="/">home</a>}'
    )
    files = extract_files(
        _answer(
            {
                "src/app/(marketing)/page.tsx": body,
            }
        )
    )
    assert files["src/app/(marketing)/page.tsx"] == body


def test_brace_literal_href_rewritten() -> None:
    """`href={"/dead"}` (brace closes immediately) is a literal → rewritten."""
    files = extract_files(
        _answer(
            {
                "src/app/(app)/dashboard/page.tsx": (
                    'export default function P(){return <a '
                    'href={"/dashboard/reports/new"}>r</a>}'
                ),
            }
        )
    )
    assert 'href={"/dashboard"}' in files["src/app/(app)/dashboard/page.tsx"]


def test_dead_link_falls_back_to_root_when_only_root_exists() -> None:
    files = extract_files(
        _answer(
            {
                "src/app/(marketing)/page.tsx": (
                    'export default function H(){return <a '
                    'href="/pricing">Цены</a>}'
                ),
            }
        )
    )
    assert 'href="/"' in files["src/app/(marketing)/page.tsx"]


# ---------------------------------------------------------------------------
# app killer-bug auto-fix — globals.css v3 + starter/(app) route conflict
# ---------------------------------------------------------------------------


def test_killer_bug_drops_tailwind_v3_globals() -> None:
    # A v3-syntax globals.css breaks the fixed v4 build; drop it ("" = delete-intent)
    # so the container image's good v4 token file stays in force.
    answer = (
        '<file path="src/app/globals.css">@tailwind base;\n@apply border-border;'
        "</file>"
    )
    assert extract_files(answer)["src/app/globals.css"] == ""


def test_killer_bug_keeps_valid_v4_globals() -> None:
    # A correct v4 globals.css (no v3 signatures) passes through untouched.
    valid = '@import "tailwindcss";\n@theme inline { --color-x: red; }'
    answer = f'<file path="src/app/globals.css">{valid}</file>'
    assert extract_files(answer)["src/app/globals.css"] == valid


def test_killer_bug_empties_starter_page_on_route_conflict() -> None:
    # A non-empty starter page.tsx alongside (app)/page.tsx both resolve to "/".
    # Empty the starter so the (app) dashboard owns "/".
    answer = (
        '<file path="src/app/page.tsx">export default function S(){return <h1>x</h1>}'
        "</file>"
        '<file path="src/app/(app)/page.tsx">export default function D(){return null}'
        "</file>"
    )
    out = extract_files(answer)
    assert out["src/app/page.tsx"] == ""
    assert out["src/app/(app)/page.tsx"].strip() != ""


def test_killer_bug_keeps_starter_page_when_no_app_index() -> None:
    # A standalone starter page (no (app)/page.tsx) is a legitimate single-page
    # build — leave it alone.
    body = "export default function S(){return <h1>x</h1>}"
    answer = f'<file path="src/app/page.tsx">{body}</file>'
    assert extract_files(answer)["src/app/page.tsx"] == body


# ---------------------------------------------------------------------------
# app inline-theme oklch guard — drop broken/low-contrast brand override
# ---------------------------------------------------------------------------

_LAYOUT = "src/app/(app)/layout.tsx"


def _layout_with_style(css: str) -> str:
    return (
        f'<file path="{_LAYOUT}">'
        f"export default function L(){{return <html><body>"
        f'<style>{{"{css}"}}</style>'
        f"</body></html>}}</file>"
    )


def test_app_theme_keeps_valid_dark_primary() -> None:
    # Dark primary (L 0.52) + near-white foreground (L 0.99) → big lightness gap,
    # legible button text → override kept verbatim.
    css = ":root{--primary:oklch(0.52 0.12 233);--primary-foreground:oklch(0.99 0 0)}"
    out = extract_files(_layout_with_style(css))[_LAYOUT]
    assert "oklch(0.52 0.12 233)" in out


def test_app_theme_drops_malformed_oklch() -> None:
    # A malformed oklch (missing components) breaks the theme; drop the override.
    css = ":root{--primary:oklch(notacolor);--primary-foreground:oklch(0.99 0 0)}"
    out = extract_files(_layout_with_style(css))[_LAYOUT]
    assert "<style>" not in out


def test_app_theme_drops_low_contrast_primary() -> None:
    # Light primary (L 0.95) with the default near-white foreground → tiny gap,
    # unreadable button text → drop the override so the kit default theme stays.
    css = ":root{--primary:oklch(0.95 0.04 233)}"
    out = extract_files(_layout_with_style(css))[_LAYOUT]
    assert "<style>" not in out


def test_app_theme_guard_scoped_to_app_layout() -> None:
    # The same broken style in a non-layout file is left untouched (the guard only
    # owns the single app-theme knob in (app)/layout.tsx).
    css = ":root{--primary:oklch(notacolor)}"
    answer = (
        '<file path="src/app/(app)/page.tsx">'
        f'<style>{{"{css}"}}</style></file>'
    )
    out = extract_files(answer)["src/app/(app)/page.tsx"]
    assert "<style>" in out
