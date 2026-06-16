"""Acceptance-lock for BS-21 (dogfood run #18, 2026-06-16): sorting a generated
entity list returns the extremum of the NEWEST ~50 rows, not of the full dataset
— so "sort by price / due / amount" gives a confidently WRONG top result.

The engine ALREADY has a correct, typed, server-side sort:
  - `listRecords` reads `sort` / `order` from the query (engine.ts:180-181), and
  - `fieldExpr` casts the jsonb value before ordering — `::numeric` for number
    fields, `::timestamptz` for date fields (engine.ts:74-75) — so the server
    orders the WHOLE table correctly (numbers numerically, dates chronologically).

But the managed list never uses it. `<CrudResource>` makes every column sortable
by default (crud-resource.tsx:168), a header click calls `toggleSort` which sets
CLIENT-side sort state (data-table.tsx:363), and the table re-sorts its already
-loaded `rows` prop in memory (data-table.tsx:305-307). Those rows are the ≤50
`useEntity` loaded with one `list()` call that pushes NO `sort`/`order` — the hook
explicitly defers sort to the client table (use-entity.ts:21). So the column-header
sort only ever orders the newest ~50 rows (created_at DESC default page); the global
extremum, if it lives outside that window, can never reach the top.

LIVE PROOF (run #18, throwaway container from the deployed base image
`omnia-template-nextjs-entities:dev`, project dogfood-sortcap-probe-1bb521,
container omnia-dev-dogfood-sortcap-probe-1bb521 port 3302, starter `Task` entity,
owner-auth via Auth.js credentials — no LLM, no gen):
  - Seeded 55 `Task`; the OLDEST-created is "AAA-EARLIEST-DUE-MARK" with due 2018-01-01
    (the global earliest); the 54 filler tasks all have 2026-* dues.
  - GET /api/entities/Task          (no params — exactly what useEntity sends) → 50 rows, MARK ABSENT;
        the earliest `due` present in that loaded window is 2026-01-15
        → the BEST a client-side "sort by due ↑" can show as TOP is 2026-01-15.
  - GET ?sort=due&order=asc&limit=200 (engine server-sort, full set)           → TOP = MARK, due 2018-01-01
  - GET ?sort=due&order=asc            (engine server-sort, DEFAULT 50-cap)    → TOP = MARK, due 2018-01-01,
        MARK PRESENT — i.e. the server sorts the WHOLE table THEN takes the top 50,
        so pushing sort to the server surfaces the true extremum even at the cap.
  - GET ?limit=200                     (control)                                → 55 rows, MARK present.

So the engine returns the right answer (2018-01-01) when ASKED to sort; the managed
UI never asks, and a user clicking "Срок ↑" to find the most overdue task sees
2026-01-15 and believes it is the earliest. Worse than the BS-18 silent truncation
(rows merely hidden) and the BS-20 false absence (search says "not found"): a sort
returns a wrong row that LOOKS authoritative, with no count/empty cue.

Distinct from its siblings:
  - BS-18 (P-PAGINATE): records 51+ are unreachable. SORT is a different operation
    that produces a misleading WRONG top rather than just hiding rows.
  - BS-20 (P-SEARCH): there is NO server text-search primitive to push a query to.
    Here the server sort primitive EXISTS and even works at the 50-cap (proof above)
    — the gap is purely that the managed read path does not push `sort`/`order`.
Family: BS-14 / BS-16 / BS-18 / BS-20 — a visible control (here, the sortable column
header) implies the whole set is reachable when only the loaded window is.

Class wider than CRM: any growing list with an ordered column — orders by amount,
invoices by due date, products by price, leads by created date, students by grade.

Why this is a PROPOSAL, not a blind ship (→ P-SORT):
  - The fix wires the DataTable's sort state into useEntity's server params (push
    `sort`/`order`, reusing the existing engine sort) so the header sort orders the
    full table. It is small and reuses a built primitive, BUT it is a TEMPLATE change
    → base-image rebuild on prod + regen-verify, it changes the list data-flow
    contract for every generated app (a header click now triggers a server fetch with
    loading state), and it is tightly coupled to BS-18/BS-20 (the same client-side
    ≤50 window) — fixing sort alone while search/pagination stay client-side is
    inconsistent. Best landed together with P-PAGINATE as one read-path refactor.
  - Live-gen is currently WALLET-blocked, so a regenerated app cannot be verified
    end-to-end this run. Min one fix per run, no blind template ship.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_DATA_TABLE = _ENTITIES / "src" / "components" / "omnia" / "data-table.tsx"
_USE_ENTITY = _ENTITIES / "src" / "components" / "omnia" / "use-entity.ts"
_CRUD = _ENTITIES / "src" / "components" / "omnia" / "crud-resource.tsx"


def test_engine_has_typed_server_side_sort_today() -> None:
    """EVIDENCE (green today): the engine DOES expose a correct, typed server-side
    sort — it reads `sort`/`order` from the query and casts the jsonb value so the
    WHOLE table is ordered numerically (number) / chronologically (date). The
    capability the managed UI needs already exists; it simply is not used."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert 'params.get("sort")' in src
    assert 'params.get("order")' in src
    assert "(${records.data} ->> ${field})::numeric" in src
    assert "(${records.data} ->> ${field})::timestamptz" in src


def test_managed_sort_is_client_side_over_loaded_rows_today() -> None:
    """EVIDENCE (green today): a column-header click sorts in memory. `toggleSort`
    sets local React state and the table re-sorts its already-loaded `rows` prop —
    there is no server round-trip (the presentational table never calls list/fetch
    /useEntity)."""
    src = _DATA_TABLE.read_text(encoding="utf-8")
    assert "function toggleSort(" in src
    assert "if (!sort) return filtered;" in src
    assert "out.sort(" in src
    # purely presentational: it sorts the prop, it never fetches a sorted page.
    assert "useEntity" not in src
    assert "fetch(" not in src
    assert ".list(" not in src


def test_crud_resource_makes_every_column_sortable_but_never_pushes_sort_today() -> None:
    """EVIDENCE (green today): CrudResource opts EVERY column into sorting by
    default (so the user can click any header), yet it passes only the caller's
    static `listParams` to useEntity — the chosen sort key/direction is never added
    to the server query."""
    src = _CRUD.read_text(encoding="utf-8")
    assert "columns.map((c) => ({ ...c, sortable: c.sortable ?? true }))" in src
    # the only params handed to useEntity are the caller's static listParams plus an
    # auto-`expand` — the chosen sort key/direction is never merged into the query.
    assert "useEntity(entity, expand.length ? mergedParams : listParams)" in src
    assert "expand: [...(listParams?.expand ?? []), ...expand]," in src
    # the sort state lives in <DataTable>; it is not threaded back into list params.
    assert "order:" not in src


def test_use_entity_defers_sort_to_the_client_table_today() -> None:
    """EVIDENCE (green today): the collection hook explicitly defers sort (and
    search/paging) to the in-memory DataTable rather than re-querying the server, so
    a header sort never reaches the engine's (working) server-side sort."""
    src = _USE_ENTITY.read_text(encoding="utf-8")
    assert "search/sort/paging happen in <DataTable>, not here" in src


@pytest.mark.xfail(
    strict=False,
    reason="BS-21 / P-SORT not yet landed: the managed column-header sort orders only "
    "the loaded ~50-row window client-side, so the global extremum (cheapest, "
    "earliest-due, highest amount) outside that window never reaches the top and the "
    "user sees a wrong-but-authoritative result. The engine already has a correct "
    "typed server-side sort. Flip to XPASS when the managed read path pushes the "
    "table's `sort`/`order` to the server instead of sorting only in memory.",
)
def test_managed_sort_should_order_the_full_dataset() -> None:
    """DESIRED: sorting a managed list by a column must order the ENTIRE dataset, not
    just the loaded page — i.e. the read path must push the table's sort key/direction
    to the server (which can already do it). Until then, the header sort lies whenever
    the collection exceeds the default page."""
    crud = _CRUD.read_text(encoding="utf-8")
    use_entity = _USE_ENTITY.read_text(encoding="utf-8")
    data_table = _DATA_TABLE.read_text(encoding="utf-8")
    combined = crud + use_entity + data_table

    # A real fix threads the table's chosen sort key/direction into the SERVER list
    # query and re-fetches on change. Today CrudResource feeds useEntity only
    # `listParams` + auto-`expand` (no `order:`), the hook has no setter to re-query,
    # and it explicitly defers sort to the in-memory <DataTable>.
    sort_pushed_to_server = (
        "order:" in crud  # CrudResource now builds sort/order into the server params
        or "onSortChange" in combined  # or the table emits a sort-change callback up
        or "setParams" in use_entity  # or the hook re-queries when the sort changes
    )
    defer_removed = (
        "search/sort/paging happen in <DataTable>, not here" not in use_entity
    )
    assert sort_pushed_to_server and defer_removed, (
        "column-header sort is still client-side over the loaded page; the global "
        "extremum outside the loaded window cannot reach the top"
    )
