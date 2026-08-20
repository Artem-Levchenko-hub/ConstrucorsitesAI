"""Acceptance-lock for BS-20 (dogfood run #17, 2026-06-16): a generated entity
app cannot FIND a record once the collection grows. Two layers compound:

  1. The engine exposes NO server-side text search. `listRecords` supports only
     EXACT-equality filters (`data->>field = value`) — there is no
     ILIKE / substring / `%contains%` / case-insensitive match anywhere. So a
     search box's natural query ("Zebra") can never be pushed to the server; only
     a request for the *entire exact value* in the *exact case* matches.
  2. The managed search box is purely CLIENT-side. `<DataTable>` filters with
     `String(v).toLowerCase().includes(q)` over its `rows` prop, and those rows
     are the ≤50 already loaded by `useEntity` (one `list()` with no limit — the
     BS-18 cap). So search only ever sees the newest 50; anything older is invisible
     to it AND there is no server endpoint it could query to reach further.

LIVE PROOF (run #17, throwaway container from the deployed base image
`omnia-template-nextjs-entities:dev`, project dogfood-search-probe-972063,
starter `Task` entity, owner-auth via Auth.js credentials — no LLM, no gen):
  - Seeded 55 `Task` records; the OLDEST is titled "Zebra-UNIQUE-OMEGA-001".
  - GET /api/entities/Task              (no params — what useEntity sends)  → 50 rows, MARK ABSENT
  - GET ?title=Zebra-UNIQUE-OMEGA-001   (exact, full value)                 → 1 row   (it DOES exist)
  - GET ?title=Zebra                    (prefix substring)                  → 0 rows
  - GET ?title=UNIQUE                   (infix substring)                   → 0 rows
  - GET ?title=zebra-unique-omega-001   (exact value, wrong case)           → 0 rows  (no ILIKE)
  - GET ?notes=найди-меня-секрет        (exact full)                        → 1 row
  - GET ?notes=секрет                   (note substring)                    → 0 rows
  - GET ?priority=high                  (exact enum — control, mechanism works) → 1 row
  - GET ?limit=200                      (raise the cap)                     → 55 rows, MARK PRESENT
The record is in the DB (limit=200 returns it). But the UI's default fetch never
loads it, the client-side search box can never see it, and the engine offers no
substring/case-insensitive endpoint the search box could query. A user with >50
records who types an existing client's name gets "Ничего не найдено" and concludes
the record is gone — a FALSE ABSENCE, worse than the BS-18 silent truncation.

Distinct from BS-18 (P-PAGINATE): even if pagination were fixed so every page
loaded, SEARCH would still be broken — there is no server-side text search to push
the query to. Fixing the cap does not give the search box a way to query the server.
Family: BS-14 (unreachable feed cards), BS-16 (invisible reference column),
BS-18 (50-row cap) — the generator surfaces only part of the data while a visible
control (here, the search box) implies the whole set is reachable.

Why this is a PROPOSAL, not a blind ship (→ P-SEARCH):
  - The real fix is a new server-side search primitive (e.g. a `q` param doing
    ILIKE across text/string fields) PLUS wiring useEntity/CrudResource to push the
    search box's query to the server (debounced) instead of filtering in memory.
  - That is a TEMPLATE + engine change → base-image rebuild on prod + regen-verify,
    and it changes the list data-flow contract for every generated app. Larger/risky.
  - Min one fix per run, no blind template ship.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_DATA_TABLE = _ENTITIES / "src" / "components" / "omnia" / "data-table.tsx"
_USE_ENTITY = _ENTITIES / "src" / "components" / "omnia" / "use-entity.ts"


def test_engine_list_filter_is_exact_equality_only_today() -> None:
    """EVIDENCE (green today): the list filter applies a raw exact-equality match
    on the jsonb-extracted text (`data->>field = value`). The full value in the
    exact case is the ONLY thing that matches."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert "conds.push(sql`${records.data} ->> ${key} = ${value}`);" in src


def test_engine_has_no_substring_or_case_insensitive_search_today() -> None:
    """EVIDENCE (green today): there is no ILIKE, no SQL LIKE on the jsonb value,
    and no `%` wildcard anywhere in the engine — i.e. no server-side text-search
    primitive a search box could query. (The word "like" appears only in prose
    comments, never as a SQL operator on the data column.)"""
    src = _ENGINE.read_text(encoding="utf-8")
    assert "ilike" not in src.lower()
    # No LIKE / wildcard applied to a jsonb extraction (`data ->> ... LIKE/%`).
    assert re.search(r"->>.*\b(i?like)\b", src, re.I) is None
    assert "%" not in src  # no wildcard pattern matching at all


def test_managed_search_box_is_client_side_over_loaded_rows_today() -> None:
    """EVIDENCE (green today): the DataTable search filters its already-loaded
    `rows` in memory via `.toLowerCase().includes(q)`. It has no server-search
    callback and never fetches — so it can only ever match within the rows
    useEntity already handed it (the ≤50 default page, BS-18)."""
    src = _DATA_TABLE.read_text(encoding="utf-8")
    assert "String(v).toLowerCase().includes(q)" in src
    # the search box wires to local state only — no server round-trip.
    assert "onSearch" not in src
    assert "fetch(" not in src


def test_use_entity_punts_search_to_the_client_table_today() -> None:
    """EVIDENCE (green today): the collection hook explicitly defers search (and
    sort/paging) to the in-memory DataTable rather than querying the server, so a
    search never reaches the engine."""
    src = _USE_ENTITY.read_text(encoding="utf-8")
    assert "search/sort/paging happen in <DataTable>, not here" in src


@pytest.mark.xfail(
    strict=False,
    reason="BS-20 / P-SEARCH not yet landed: a generated entity app cannot find a "
    "record past the loaded page — the engine has only exact-equality filters (no "
    "substring/ILIKE) and the search box filters the ≤50 loaded rows client-side. "
    "Flip to XPASS when the engine grows a server-side text-search primitive "
    "(ILIKE/substring, e.g. a `q` param) AND the managed read path pushes the "
    "search query to the server instead of filtering only in memory.",
)
def test_entity_app_should_be_able_to_find_a_record_by_search() -> None:
    """DESIRED: search must be able to reach a record regardless of how many exist.
    That needs (a) a server-side text-search primitive in the engine (ILIKE /
    substring across text fields), AND (b) the managed read path pushing the search
    box's query to the server. Until then, any record outside the loaded ≤50 page
    is unfindable and the search box reports a false absence."""
    engine = _ENGINE.read_text(encoding="utf-8")
    use_entity = _USE_ENTITY.read_text(encoding="utf-8")
    data_table = _DATA_TABLE.read_text(encoding="utf-8")

    # (a) engine offers a substring / case-insensitive search primitive…
    engine_has_text_search = bool(
        re.search(r"ilike", engine, re.I)
        or re.search(r"->>.*\blike\b", engine, re.I)
        or re.search(r"\bsearch\b|\bq\b", engine, re.I)
    )
    # …and (b) the search query is pushed to the server (the hook/table issues a
    # server-side search rather than only filtering rows in memory).
    search_pushed_to_server = bool(
        re.search(r"onSearch|search.*list\(|\bq:\s", use_entity + data_table)
    )
    assert engine_has_text_search and search_pushed_to_server, (
        "search still cannot reach records beyond the loaded client-side page"
    )
