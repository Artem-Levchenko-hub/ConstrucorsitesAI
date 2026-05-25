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
