"""Acceptance-lock for BS-34 (dogfood run #33, 2026-06-17): a generated entity
app filters/segments a list ONLY over the loaded ≤50-row window, client-side, so
a status filter or a kanban column silently UNDERCOUNTS — or falsely reads empty —
the moment the collection grows past the first page. This is the windowing family
again (search BS-20, sort BS-21, export BS-24, KPI BS-26, pagination BS-18) but on
the never-probed FILTER / SEGMENT axis, and it is most damning for the kanban
`board` view — which the writer is PRESCRIBED to use for every pipeline entity.

THE PATH IS THE DEFAULT, NOT AN EDGE CASE:
  SYSTEM_PROMPT.md (nextjs-entities) lines ~104-109 instruct the writer: for an
  entity that moves through stages (заявка/тикет/заказ/сделка/задача) set
  view="board" + filterField=<status> + filterTabs=<one per stage>. Real dogfood
  run #28 generated exactly this — "CRM для отдела продаж: сделки со стадиями …
  канбан-доска". So the windowed segment ships on every status-driven app.

Code-proven chain (four template surfaces; engine ALREADY supports the fix):
  1. use-entity.ts  useEntity() calls `entities[name].list(params)` ONCE on mount
     and after each mutation — no pagination loop, no limit. Its own doc says
     "client-side search/sort/paging happen in <DataTable>, not here."
  2. engine.ts  listRecords returns `DEFAULT_LIMIT = 50` rows when no `limit` is
     passed (and the managed component never passes one). So `data.rows` is a
     ≤50 window of the full collection.
  3. data-table.tsx  the quick-filter `segmented` = `all.filter(row => String(
     rawValue(row, filterField)) === want)` runs over `rows` (the loaded window),
     client-side. File header: "client-side search, sort and pagination".
  4. board-view.tsx  `grouped` walks `for (const card of filtered)` (the loaded
     window) and buckets into status columns client-side.
  5. crud-resource.tsx  `mergedParams` spreads `listParams` and adds ONLY
     `expand` — the active filter tab / board column value is NEVER threaded into
     the SDK `list()` call, so it never reaches the server. `boardCards` is built
     from `data.rows`.

The fix IS reachable server-side: engine.listRecords already applies whitelisted
equality filters — `conds.push(sql`${records.data} ->> ${key} = ${value}`)` — so
pushing the active segment value as a list param would filter the WHOLE set. The
gap is purely client: the managed component keeps the filter local.

Why a PROPOSAL (P-FILTERWINDOW), not a blind ship — identical to its windowing
siblings (P-SORT/P-SEARCH/P-EXPORT/P-KPICOUNT, all proposals): the real fix lifts
the active-segment state out of DataTable, threads it into useEntity's params for
a server re-query, and reconciles board drag-to-reorder with server-side paging —
a coupled change across the managed component that ships on EVERY generated app
and must not be shipped blind. One fix per run.

Deterministic file-content asserts (money-free, no container, no LLM)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_OMNIA = _ENTITIES / "src" / "components" / "omnia"
_USE_ENTITY = _OMNIA / "use-entity.ts"
_DATA_TABLE = _OMNIA / "data-table.tsx"
_BOARD = _OMNIA / "board-view.tsx"
_CRUD = _OMNIA / "crud-resource.tsx"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"


def _merged_params_block() -> str:
    """The `mergedParams = React.useMemo(...)` body in crud-resource.tsx — the one
    place the managed component builds the params it hands to useEntity."""
    src = _CRUD.read_text(encoding="utf-8")
    m = re.search(r"const mergedParams = React\.useMemo\((.*?)\);", src, re.S)
    assert m, "mergedParams useMemo not found in crud-resource.tsx"
    return m.group(1)


def test_use_entity_loads_a_single_window_today() -> None:
    """EVIDENCE (green today): useEntity issues ONE `list(params)` call with no
    pagination — no loop, no growing offset — so `rows` is a single server page."""
    src = _USE_ENTITY.read_text(encoding="utf-8")
    assert "entities[name].list(" in src
    # No pagination machinery: a single fetch, never a loop accumulating pages.
    assert "while" not in src
    assert "offset +=" not in src and "page +=" not in src


def test_engine_default_limit_is_50_but_server_filter_exists_today() -> None:
    """EVIDENCE (green today): the engine caps an unparametrised list at 50 rows,
    AND already supports a server-side equality filter on whitelisted fields — so
    the windowed segment is a CLIENT gap, and the fix is reachable server-side."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert "DEFAULT_LIMIT = 50" in src
    # The reachable fix: listRecords already pushes `data->>field = value`.
    assert "->> ${key} = ${value}" in src


def test_table_quick_filter_is_client_side_today() -> None:
    """EVIDENCE (green today): the quick-filter segment runs `.filter(...)` over
    the loaded `rows` array, client-side — it never re-queries the server."""
    src = _DATA_TABLE.read_text(encoding="utf-8")
    assert "client-side" in src  # the component's own header admits it
    m = re.search(
        r"const segmented = React\.useMemo\(\(\) => \{(.*?)\}, \[", src, re.S
    )
    assert m, "segmented useMemo not found in data-table.tsx"
    seg = m.group(1)
    # Segments the in-memory window: `const all = ... rows ...; all.filter(...)`.
    assert "rows" in seg
    assert ".filter(" in seg


def test_board_groups_the_loaded_window_client_side_today() -> None:
    """EVIDENCE (green today): the kanban board buckets the already-loaded cards
    into status columns in memory (`for (const card of filtered)`) — cards beyond
    the loaded window are simply absent from every column."""
    src = _BOARD.read_text(encoding="utf-8")
    assert "for (const card of filtered)" in src
    assert "map.get(key)" in src


def test_active_filter_is_not_sent_to_the_server_today() -> None:
    """EVIDENCE (green today): the params the managed component hands to useEntity
    add ONLY `expand` — the active filter field / board column value is never put
    into the SDK `list()` call, so the filter cannot reach records 51+."""
    block = _merged_params_block()
    assert "expand" in block
    # The filter value does not enter the server params today.
    assert "filterField" not in block


@pytest.mark.xfail(
    strict=False,
    reason="BS-34 / P-FILTERWINDOW not yet landed: the quick-filter segment and "
    "the kanban board grouping run client-side over the loaded ≤50 window, so a "
    "status filter / board column undercounts at scale. When the active segment "
    "value is threaded into the server query (the managed component pushes "
    "filterField=value into useEntity's list params, or DataTable re-queries the "
    "server on filter change), flip to XPASS.",
)
def test_active_filter_should_reach_the_full_dataset() -> None:
    """DESIRED: selecting a status filter / kanban column must constrain the
    SERVER query (engine.listRecords already supports `?field=value`), so the
    segment reflects the whole collection — not just the first page that happened
    to load. Detected when the managed component routes the active filter value
    into the server list params instead of filtering the in-memory window."""
    crud = _CRUD.read_text(encoding="utf-8")
    block = _merged_params_block()
    data_table = _DATA_TABLE.read_text(encoding="utf-8")

    # The filter value reaches the server when EITHER the managed component folds
    # filterField into the params it gives useEntity, OR the table triggers a
    # server reload keyed on the active filter (an onFilterChange → list() path).
    server_filtered_via_crud = "filterField" in block or "filterValue" in crud
    server_filtered_via_table = bool(
        re.search(r"onFilter\w*\s*[:=].*list\(", data_table, re.S)
    )

    assert server_filtered_via_crud or server_filtered_via_table
