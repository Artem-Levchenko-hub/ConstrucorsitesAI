"""Acceptance-lock for BS-24 (dogfood run #21, 2026-06-16): the managed
`<CrudResource>` ships a CSV **export** button ("Экспорт") that is ON BY DEFAULT
for every list screen, and it serializes the rows ALREADY IN MEMORY — i.e. the
≤50 the page loaded (BS-18 cap) — not the owner's full server dataset. So an
owner who clicks "Экспорт" to back up / report / hand off their data silently
downloads only the first 50 records of N, with NO warning. Worse than BS-18's
in-app cap: the artifact LEAVES the app as a trusted, authoritative-looking file
(a backup, a report), so the missing rows are invisible at the exact moment the
user most believes the data is complete. The handler's own comment ("not just
the visible page") reinforces that false belief — it spans pages but never the
server set.

LIVE PROOF (run #21, throwaway container from the DEPLOYED base image
`omnia-template-nextjs-entities:dev`, schema proj_dogfood_export_916, starter
`Task` entity, owner-auth via Auth.js credentials — no LLM, no gen, wallet-empty
bypassed exactly like runs #14–#20):
  - Created 55 `Task` records (EXPORT-ROW-01..55).
  - GET /api/entities/Task            (no params — what useEntity/CrudResource
    sends, and exactly what handleExport serializes)          → 50 rows
  - GET /api/entities/Task?limit=200  (engine MAX_LIMIT)                  → 55 rows
  - GET /api/entities/Task?sort=created_at&order=asc (default 50)         → 50 rows,
    first=EXPORT-ROW-01 last=EXPORT-ROW-50  (rows 51..55 absent)
The 5 overflow records exist in the DB (limit=200 returns them) but are absent
from the load the CSV export consumes → the exported file is missing them
silently.

Root cause (code-proven on the template source):
  - crud-resource.tsx: `exportable = true` BY DEFAULT (comment: "On by default
    for managed screens"), and it passes `rows={data.rows}` to the DataTable —
    `data.rows` is the ≤50 already fetched by useEntity.
  - data-table.tsx handleExport(): `exportRows = selectedRows.length ?
    selectedRows : sorted`, where `sorted`←`filtered`←the `rows` prop. It builds
    the CSV from those in-memory rows — never refetching the full server set. The
    comment "not just the visible page" is true about the client pager but false
    about the server dataset.
  - use-entity.ts: loads the collection with ONE `entities[name].list()` and no
    `limit`/`page` → inherits the engine's DEFAULT_LIMIT=50 (the BS-18 cap).

Class wider than CRM: any growing collection an owner would export — clients,
orders, products, bookings, leads, students. Same "looks complete, isn't"
family as BS-14 (unreachable feed cards), BS-16 (invisible reference column),
BS-18 (list cap), BS-20 (search misses), BS-21 (sort windowed). This one is the
data-portability surface: the truncation rides out of the app in a file.

Why this is a PROPOSAL, not a blind ship (→ P-EXPORT):
  - DataTable is presentational (gets `rows`, doesn't know the entity), so a
    correct export — fetch the FULL set (or stream server-side) before
    serializing — must be wired at the CrudResource level (it knows the entity
    name) or via a dedicated server export endpoint. Either touches the list
    data-flow / limits, ties directly into P-PAGINATE, and is a TEMPLATE change →
    base-image rebuild on prod + regen-verify.
  - A naive band-aid (export fetches limit=200) only MOVES the cliff and loads
    200 rows + ref-expansions on demand; the right fix is a real "export all"
    that pages the whole set. Larger/risky.
  - Min one fix per run, no blind template ship.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_DATA_TABLE = _ENTITIES / "src" / "components" / "omnia" / "data-table.tsx"
_CRUD = _ENTITIES / "src" / "components" / "omnia" / "crud-resource.tsx"
_USE_ENTITY = _ENTITIES / "src" / "components" / "omnia" / "use-entity.ts"


def test_export_is_on_by_default_for_every_managed_screen_today() -> None:
    """EVIDENCE (green today): CrudResource enables the CSV export button by
    default, so virtually every generated list screen carries it."""
    src = _CRUD.read_text(encoding="utf-8")
    assert "exportable = true," in src
    assert "On by default for managed screens" in src
    # the button + filename are passed straight through to the DataTable.
    assert "exportable={exportable}" in src


def test_export_serializes_the_in_memory_rows_not_the_full_set_today() -> None:
    """EVIDENCE (green today): handleExport builds the CSV from `sorted` (derived
    from the `rows` prop) — the rows already in memory — never refetching the
    server. The "not just the visible page" comment is about the client pager,
    not the server dataset."""
    src = _DATA_TABLE.read_text(encoding="utf-8")
    assert "function handleExport()" in src
    assert "selectedRows.length ? selectedRows : sorted" in src
    assert "rowsToCsv(visibleColumns, exportRows)" in src
    # the misleading reassurance that it is complete:
    assert "not just the visible page" in src
    # `sorted` is derived purely from the in-memory `rows` prop (no fetch):
    assert "const all = Array.isArray(rows) ? rows : [];" in src
    # handleExport never issues a network call to widen the set.
    assert not re.search(r"handleExport[\s\S]{0,400}?(fetch|\.list\(|await )", src)


def test_export_source_rows_are_the_capped_load_today() -> None:
    """EVIDENCE (green today): the DataTable's `rows` come from `data.rows`, which
    useEntity loads with a single list() and no `limit`/`page` → the ≤50 BS-18
    cap. So the export source is the capped window, end to end."""
    crud = _CRUD.read_text(encoding="utf-8")
    use_entity = _USE_ENTITY.read_text(encoding="utf-8")
    assert "rows={data.rows}" in crud
    assert "entities[name].list(paramsRef.current)" in use_entity
    assert "limit" not in use_entity  # hook never raises the cap for the load
    assert not re.search(r"limit:\s*\d", crud)  # nor does CrudResource


@pytest.mark.xfail(
    strict=False,
    reason="BS-24 / P-EXPORT not yet landed: the managed CSV export serializes "
    "only the ≤50 rows already in memory, so an owner's 'export everything' "
    "silently drops records 51+. Flip to XPASS when export fetches the full "
    "server set (or a server-side export endpoint) before building the CSV — "
    "wired at the CrudResource level (it knows the entity) rather than from the "
    "presentational DataTable's in-memory rows.",
)
def test_export_should_cover_the_full_dataset_not_the_loaded_window() -> None:
    """DESIRED: clicking "Экспорт" with no row selection must export the whole
    collection, not just the loaded window. Either CrudResource fetches the full
    set on export (entities[name].list with MAX_LIMIT / paged-until-done) and
    feeds THAT to the CSV, or a dedicated server export endpoint streams all
    rows. Until then the downloaded file is silently incomplete past 50."""
    crud = _CRUD.read_text(encoding="utf-8")
    data_table = _DATA_TABLE.read_text(encoding="utf-8")

    # (a) CrudResource wires an export that fetches the full set itself…
    crud_fetches_all_for_export = bool(
        re.search(r"(onExport|exportAll|fetchAll|getAll)", crud)
        or re.search(r"export[\s\S]{0,200}?\.list\(\s*\{[^}]*limit", crud)
    )
    # …or (b) a dedicated server export route exists the button hits.
    server_export_endpoint = bool(
        re.search(r"(/export|exportUrl|export/route)", crud + data_table)
    )
    assert crud_fetches_all_for_export or server_export_endpoint, (
        "CSV export still serializes only the in-memory ≤50 rows; an "
        "'export everything' silently drops records past the cap"
    )
