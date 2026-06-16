"""Acceptance-lock for BS-18 (dogfood run #15, 2026-06-16): a generated entity
list silently caps at the engine's DEFAULT_LIMIT (50 rows) — the managed
`<CrudResource>` screen never requests a higher `limit` and never wires
server-side pagination, so any list that grows past 50 records drops the
overflow with NO error, NO "load more", and NO indication. The DataTable's own
pager paginates ONLY the ≤50 rows already in memory, which makes the list look
complete (e.g. "5 pages of 10") while ~10% of the data is unreachable.

LIVE PROOF (run #15, throwaway container from the deployed base image
`omnia-template-nextjs-entities:dev`, project dogfood-listcap-probe-cd7c7c,
starter `Task` entity, owner-auth via Auth.js credentials — no LLM, no gen):
  - Created 55 `Task` records (#1..#55).
  - GET /api/entities/Task            (no limit — what useEntity/CrudResource sends) → 50 rows: #6..#55
  - GET /api/entities/Task?limit=200  (engine MAX_LIMIT)                              → 55 rows: #1..#55
  - GET /api/entities/Task?page=2                                                     → 5 rows:  #1..#5
The 5 OLDEST records (#1..#5) are absent from the default list (default sort is
created_at DESC). The engine CAN page (page=2 returns the rest) — the managed UI
just never asks. So the gap is purely a wiring gap, not a server limitation.

Root cause (code-proven):
  - engine.ts: DEFAULT_LIMIT = 50, MAX_LIMIT = 200; listRecords clamps to
    `Number(limit) || DEFAULT_LIMIT` → 50 when the caller sends no `limit`.
  - use-entity.ts: loads the collection with ONE `entities[name].list(params)`
    call (params passed verbatim), reloads after each mutation, and never sets
    `limit`/`page`/`offset` nor loops pages. Its own comment: "client-side
    search/sort/paging happen in <DataTable>, not here."
  - crud-resource.tsx: `mergedParams` only injects `expand`; no default `limit`.
    The DataTable receives `rows={data.rows}` (the ≤50 already fetched) and
    paginates them client-side via `pageSize` — it never refetches a server page.
  - SYSTEM_PROMPT.md lists `entities.X.list({sort,order,limit,page})` as
    available but never directs the writer to wire pagination, and CrudResource
    is the documented "fast path for any list screen" — so the 50-cap is the
    default for virtually every generated managed list.

Class wider than CRM: any growing collection — clients, orders, products,
bookings, leads, students, messages — silently truncates at 50. Same family as
BS-14 (unreachable feed cards) and BS-16 (invisible reference column): the
generator surfaces only part of the data while looking complete.

Why this is a PROPOSAL, not a blind ship (→ P-PAGINATE):
  - The minimal band-aid (default the managed fetch to `limit: 200` = MAX_LIMIT)
    is a TEMPLATE change → base-image rebuild on prod + regen-verify, and it
    only MOVES the cliff (>200 still vanishes) while making every managed
    screen fetch 200 rows + their ref-expansions on every load.
  - The correct fix is real server pagination: the engine returns a total/has-
    more, useEntity exposes page state and refetches, and the DataTable pager
    drives it — multi-surface, changes the list data-flow contract. Larger/risky.
  - Min one fix per run, no blind template ship.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_USE_ENTITY = _ENTITIES / "src" / "components" / "omnia" / "use-entity.ts"
_CRUD = _ENTITIES / "src" / "components" / "omnia" / "crud-resource.tsx"
_SYSTEM_PROMPT = _ENTITIES / "SYSTEM_PROMPT.md"


def test_engine_caps_list_at_fifty_by_default_today() -> None:
    """EVIDENCE (green today): with no `limit` query param the engine returns at
    most DEFAULT_LIMIT (50) rows. MAX_LIMIT (200) is the hard ceiling. So the
    server hands back 50 unless the caller explicitly asks for more."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert "const MAX_LIMIT = 200;" in src
    assert "const DEFAULT_LIMIT = 50;" in src
    # the clamp: Number(limit) || DEFAULT_LIMIT, capped at MAX_LIMIT
    assert 'Number(params.get("limit")) || DEFAULT_LIMIT' in src
    assert "Math.min(\n    MAX_LIMIT," in src


def test_use_entity_loads_once_with_no_limit_or_page_today() -> None:
    """EVIDENCE (green today): the collection hook fires a SINGLE list() with the
    caller's params verbatim and never sets limit/page/offset, so it inherits the
    server's 50-row default. There is no page-state and no fetch-next loop."""
    src = _USE_ENTITY.read_text(encoding="utf-8")
    assert "entities[name].list(paramsRef.current)" in src
    # the hook itself never introduces limit/page/offset…
    assert "limit" not in src
    assert "offset" not in src
    # …and explicitly punts paging to the table, in memory:
    assert "client-side\n * search/sort/paging happen in <DataTable>, not here" in src


def test_crud_resource_does_not_request_a_limit_or_paginate_server_side_today() -> None:
    """EVIDENCE (green today): the managed screen only injects `expand` into the
    list params (never a `limit`), then paginates the already-fetched rows in
    memory via the DataTable's `pageSize`. No server page is ever requested."""
    src = _CRUD.read_text(encoding="utf-8")
    # merged params add ONLY expand — no default limit is set.
    assert "expand: [...(listParams?.expand ?? []), ...expand]" in src
    # the table paginates the in-memory data.rows, client-side.
    assert "rows={data.rows}" in src
    assert "pageSize={pageSize}" in src
    # CrudResource itself never sets a numeric limit in its list params.
    assert not re.search(r"limit:\s*\d", src)


def test_system_prompt_does_not_require_wiring_pagination_today() -> None:
    """EVIDENCE (green today): the writer is told list() accepts limit/page but is
    never directed to wire pagination or raise the cap on a managed list, and
    CrudResource is the documented fast path — so the 50-cap is the de-facto
    default for generated list screens."""
    src = _SYSTEM_PROMPT.read_text(encoding="utf-8")
    # no directive about pagination / load-more / raising the list cap.
    assert not re.search(r"(paginat|load more|показать ещё|загрузить ещё)", src, re.I)
    assert not re.search(r"limit.{0,40}(200|max|all rows|все записи)", src, re.I)


@pytest.mark.xfail(
    strict=False,
    reason="BS-18 / P-PAGINATE not yet landed: a managed entity list silently "
    "caps at DEFAULT_LIMIT=50 — useEntity/CrudResource never requests a higher "
    "limit and never wires server pagination, so records 51+ are unreachable "
    "through the UI. Flip to XPASS when the managed read path either defaults to "
    "MAX_LIMIT or wires real server-side pagination (page state + refetch).",
)
def test_managed_list_should_reach_records_beyond_the_default_cap() -> None:
    """DESIRED: a generated managed list must surface records past the 50 default
    — either by defaulting the fetch to MAX_LIMIT (200), or by wiring true
    server-side pagination so the user can page beyond the first 50. Until then,
    the 51st record onward is invisible with no error and no control to reach it.
    """
    use_entity = _USE_ENTITY.read_text(encoding="utf-8")
    crud = _CRUD.read_text(encoding="utf-8")

    # (a) the managed fetch defaults to a high limit (≥200 / MAX_LIMIT)…
    raises_default_limit = bool(
        re.search(r"limit:\s*(200|MAX_LIMIT)", crud)
        or re.search(r"limit:\s*(200|MAX_LIMIT)", use_entity)
    )
    # …or (b) real server pagination is wired (page state in the hook + refetch).
    wires_server_pagination = bool(
        re.search(r"\bpage\b", use_entity)
        and re.search(r"set[A-Z]\w*[Pp]age|setPage|nextPage|hasMore|total", use_entity)
    )
    assert raises_default_limit or wires_server_pagination, (
        "managed list still capped at DEFAULT_LIMIT=50 with no way to reach more"
    )
