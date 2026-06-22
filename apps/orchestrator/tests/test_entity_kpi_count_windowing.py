"""Acceptance-lock for BS-26 (dogfood run #23, 2026-06-17).

UPDATE (post-wave): the windowing-remediation wave LANDED the client-side half of
the fix. The SDK now auto-paginates any unpinned `list()`/`filter()` — it walks
the server's offset pages (fetchAll) up to AUTO_FETCH_CAP=10000 and concatenates
them — so a dashboard KPI written as `entities[X].list().length` / `.reduce()`
now reflects the WHOLE table, not the newest 50. This file has been updated to
assert that ACTUAL behaviour:
  - test_sdk_list_auto_paginates_so_length_is_the_full_count — locks the fetchAll
    fix (the count is now correct up to 10000).
  - test_use_entity_rows_length_is_full_count_via_unpinned_list — the hook rides
    that contract: its unpinned load now full-fetches.
  - test_kit_jsdoc_length_over_loaded_arrays_is_now_a_sound_kpi_pattern — the
    `.length` pattern the kit teaches is now correct, not a trap.
The SERVER half is STILL open and held by a tight xfail at the bottom: there is
no engine count/aggregate, no `{rows,total}` envelope, no SDK `count()`, no
useEntity `total`. So tables >10k rows still undercount and there is no server
SUM/AVG (avgCheck/conversion stay client-computed over the fetched window).

ORIGINAL FINDING (pre-wave, for history): a generated entity app's DASHBOARD KPIs
— the headline numbers an owner sees on the very first screen after login
("Активных сделок: N", "Клиентов: N", "Средний чек") — are computed CLIENT-SIDE
as `.length` / `.reduce()` over the entity arrays that `useEntity` /
`entities[X].list()` loads. At the time, those loads carried no `limit`, so the
engine returned its DEFAULT_LIMIT=50 page. The entity runtime exposed NO
count/aggregate primitive anywhere: `listRecords` returns a bare page with
no `total`, the SDK `list` returns `Row[]`, `useEntity` exposes only `rows`, and
there is no count/stats route. So once a business passed 50 records per entity,
every headline KPI silently capped at 50 — and the average/sum/ratio KPIs
(avgCheck, conversion) were computed over the NEWEST 50, i.e. statistically
skewed, not merely truncated.

Worse than BS-18 (in-app list cap) and BS-24 (export cap): BS-18 hides old rows
in a table the user can sense paging through; BS-26 reports an AUTHORITATIVE
TOTAL that looks exact, freezes at 50 as the business grows, and is the FIRST
thing shown on login. Same "looks complete, isn't" family as BS-14/16/18/20/21/24,
at the aggregate/headline-metric surface.

LIVE PROOF (run #23, throwaway container from the DEPLOYED base image
`omnia-template-nextjs-entities:dev`, schema proj_dogfood_kpi_*, starter `Task`
entity, owner-auth via Auth.js credentials — no LLM, no gen, wallet-empty
bypassed exactly like runs #14–#22):
  - Created 60 `Task` records.
  - GET /api/entities/Task        (no params — what useEntity/list sends, and
    exactly what a `rows.length` KPI counts)              → 50 rows
  - The response is a bare `{"data":[...]}` array — NO `total`/`count` field,
    and NO `X-Total-Count` header.
  - GET /api/entities/Task?limit=200  (engine MAX_LIMIT)             → 60 rows
  - No count/aggregate route: GET .../Task/count, .../Task/stats,
    .../Task/aggregate → 500 (the dynamic [entity] segment treats "count" as an
    entity name → unknown-entity error path); ?count=true is ignored (still 50).
So the only number a dashboard can derive client-side is ≤50, while 60 exist.

WRITER-PATTERN PROOF (real generated CRM, demo-crm-dlia-prosmotra, dashboard
page.tsx read from the running base binary):
  - loads `entities.Deal.list({sort,order})`, `entities.Client.list()`,
    `entities.Task.list()` — all WITHOUT `limit` (≤50 each).
  - `value={activeDeals.length}`, `<CountUp value={newClientsThisMonth.length}/>`,
    `<CountUp value={overdueTasks.length}/>` — counts over the ≤50 window.
  - `avgCheck = deals.reduce((s,d)=>s+Number(d.amount),0) / deals.length` and the
    revenue funnel `deals.forEach(...)` — sums/averages over the ≤50 window.
The kit's OWN JSDoc teaches this exact pattern, so it is not writer noise:
  - dashboard-hero.tsx: `{ label: "Клиентов", value: <CountUp value={clients.length}/> }`,
    `{ label: "Открытых сделок", value: open.length }`.
  - count-up.tsx: `<StatCard accent label="Выручка" value={<CountUp value={total} .../>} />`.

Root cause (code-proven on the template source):
  - engine.ts: `listRecords` returns the shaped ≤50 page (`return expand.length
    ? await expandRecords(...) : shaped`) — no `total`, no count. There is no
    `countRecords` / SQL `count(*)` anywhere. DEFAULT_LIMIT=50, MAX_LIMIT=200.
  - sdk/index.ts: `list(params?): Promise<Row[]>` — returns a bare array, no
    count method, no `{rows,total}` envelope.
  - use-entity.ts: `UseEntity` exposes only `rows` (no `total`); one `list()`
    with no `limit`/`page` → inherits the BS-18 cap.
  - api/entities/[entity]/route.ts is the only collection route — no count/stats
    sibling.
  - dashboard-hero.tsx + count-up.tsx JSDoc instruct `.length` KPIs; SYSTEM_PROMPT
    lists StatCard (L99) with no directive to compute counts against the server.

Class wider than CRM: every dashboard headline — clients, orders, products,
bookings, leads, students, revenue sums, averages, conversion ratios.

Why this is a PROPOSAL, not a blind ship (→ P-KPICOUNT):
  - The fix is a NEW server count/aggregate primitive (engine `countRecords` +
    a `/count` route or a `{rows,total}` list envelope + SDK `count()` + useEntity
    `total`), plus kit guidance to source KPIs from server counts. That changes
    the list-data-flow contract and is multi-surface — tightly coupled to
    P-PAGINATE / P-SEARCH / P-SORT / P-EXPORT (the whole windowing remediation).
  - A count alone does NOT fix avgCheck/conversion — those need server SUM/AVG
    aggregation; computing them over the loaded window stays skewed even with a
    correct total. The right fix is a small aggregate surface, not a band-aid.
  - TEMPLATE+engine change → base-image rebuild on prod + regen-verify. Larger /
    risky. Min one fix per run, no blind template ship.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_SDK = _ENTITIES / "src" / "lib" / "sdk" / "index.ts"
_USE_ENTITY = _ENTITIES / "src" / "components" / "omnia" / "use-entity.ts"
_DASH_HERO = _ENTITIES / "src" / "components" / "omnia" / "dashboard-hero.tsx"
_COUNT_UP = _ENTITIES / "src" / "components" / "omnia" / "count-up.tsx"
_API_DIR = _ENTITIES / "src" / "app" / "api" / "entities"


def test_engine_still_has_no_server_count_primitive_only_a_bounded_page() -> None:
    """STILL-OPEN at the SERVER layer (the fix landed in the SDK, not here):
    `listRecords` still returns the shaped page directly — no `total`, no count
    envelope — and there is still NO count/aggregate function in the engine. The
    page is clamped to MAX_LIMIT per request. So the engine itself cannot answer
    "how many records total?"; the client gets the true count only by walking
    every page (the SDK's fetchAll). This is why the desired xfail below — which
    demands a real SERVER count primitive — stays red."""
    src = _ENGINE.read_text(encoding="utf-8")
    # The list returns a bare rows array (optionally expanded) — never {rows,total}.
    assert "return expand.length ? await expandRecords(def, shaped, expand, user) : shaped;" in src
    # The default page is 50; a single request is clamped to MAX_LIMIT.
    assert "const DEFAULT_LIMIT = 50;" in src
    assert "const MAX_LIMIT = 500;" in src
    assert "Math.min(\n    MAX_LIMIT," in src
    # No count primitive exists anywhere in the engine.
    assert "countRecords" not in src
    assert not re.search(r"count\(\s*\*\s*\)", src)
    assert not re.search(r"sql`\s*count", src, re.IGNORECASE)


def test_sdk_list_auto_paginates_so_length_is_the_full_count() -> None:
    """BS-26 FIX (landed in the wave): the SDK `list` still returns a bare
    `Promise<Row[]>` (no `{rows,total}` envelope, no dedicated `count` method),
    but it no longer hands back a single ≤50 page. When the caller does NOT pin a
    window (`limit`/`page`/`offset`), `list`/`filter` route through `fetchAll`,
    which walks the server's offset pages (AUTO_PAGE per step) until it gets a
    short page or hits the safety cap — concatenating into ONE array. So a
    dashboard KPI written as `list().length` / `reduce()` now sees the WHOLE table
    (up to AUTO_FETCH_CAP), not the newest 50.

    NOTE: the count primitive is still client-side brute-fetch — there is no
    server count/aggregate. That residual gap is held by the xfail below. This
    test locks the fix that DID land: full-fetch makes `.length` correct."""
    src = _SDK.read_text(encoding="utf-8")
    # The list still returns a bare array — the fix is in HOW it's filled, not a
    # new {rows,total} envelope.
    assert "list(params?: ListParams): Promise<Row[]>;" in src
    # The old single-page implementation is gone…
    assert 'list: (params) => safeCollection(req<Row[]>("GET", base + qs(params))),' not in src
    # …replaced by: pin a page → one request; otherwise auto-complete via fetchAll.
    assert "isPinnedPage(params)" in src
    assert 'req<Row[]>("GET", base + qs(params))' in src  # honoured only when pinned
    assert "fetchAll(base, params)" in src
    # fetchAll walks offset pages, concatenating until a short page or the cap.
    assert "async function fetchAll(" in src
    assert "for (let offset = 0; offset < AUTO_FETCH_CAP; offset += AUTO_PAGE)" in src
    assert "if (page.length < AUTO_PAGE) break;" in src
    # The walk mirrors the engine's MAX_LIMIT per step and is hard-capped at 10000.
    assert "const AUTO_PAGE = 500;" in src
    assert "const AUTO_FETCH_CAP = 10000;" in src
    # Still no server-backed count/aggregate method on the collection SDK —
    # the cardinality is derived by fetching every row, not asking the server.
    assert not re.search(r"\bcount\s*[:(]", src)


def test_use_entity_rows_length_is_full_count_via_unpinned_list() -> None:
    """BS-26 FIX (hook side): useEntity still exposes `rows` only (no `total`) and
    still loads with a single `list(paramsRef.current)` carrying no
    `limit`/`page`/`offset` — but because that call is now UNPINNED, the SDK
    auto-paginates it (fetchAll). So `useEntity(X).rows.length`, the only way the
    hook lets a KPI count, now reflects the WHOLE table (up to AUTO_FETCH_CAP)
    instead of capping at 50. The fix needed no hook change — it rides the SDK's
    list() contract change."""
    src = _USE_ENTITY.read_text(encoding="utf-8")
    assert "rows: Row[];" in src
    # The hook still surfaces no server total — counts come from rows.length,
    # which is now correct because the underlying list() auto-paginates.
    assert "total" not in src
    # The load is a bare, UNPINNED list() — no limit/page/offset — which is
    # precisely what triggers the SDK's full-fetch path.
    assert "entities[name].list(paramsRef.current)" in src
    assert "limit" not in src
    assert "page" not in src
    assert "offset" not in src


def test_kit_jsdoc_length_over_loaded_arrays_is_now_a_sound_kpi_pattern() -> None:
    """The kit still teaches the `.length`-over-loaded-arrays KPI pattern — and
    that is now CORRECT, not a trap. Because every unpinned `list()` the writer
    calls auto-paginates the whole table, `clients.length` / `open.length` count
    all records (up to AUTO_FETCH_CAP) rather than the newest 50. So the guidance
    the template hands the writer no longer produces a silently-capped headline.
    (This test just pins that the taught pattern is unchanged; the SDK test above
    proves the pattern is now sound.)"""
    hero = _DASH_HERO.read_text(encoding="utf-8")
    count_up = _COUNT_UP.read_text(encoding="utf-8")
    assert "value={clients.length}" in hero
    assert "value: open.length" in hero
    # CountUp is positioned as the dashboard KPI value wrapper.
    assert "StatCard" in count_up


def test_no_count_or_stats_route_exists_still() -> None:
    """STILL-OPEN at the ROUTE layer: the only collection routes are the list
    route and the single-record route; there is still no count/stats/aggregate
    sibling the dashboard could hit for a server-side total. The wave solved the
    headline-count problem on the CLIENT (the SDK walks every page), not by adding
    a count endpoint — so this absence is real and the desired xfail stays red."""
    route_files = {p.name for p in _API_DIR.rglob("route.ts")}
    # the list/collection route exists…
    assert "route.ts" in route_files
    # …but nothing named count/stats/aggregate.
    names = {p.parent.name for p in _API_DIR.rglob("route.ts")}
    assert not (names & {"count", "stats", "aggregate", "summary"})


@pytest.mark.xfail(
    strict=False,
    reason="PARTIALLY closed. The ≤50 headline cap is FIXED on the client: the "
    "SDK auto-paginates unpinned list()/filter() (fetchAll), so `.length`/reduce "
    "KPIs now see the whole table up to AUTO_FETCH_CAP=10000 (asserted green in "
    "test_sdk_list_auto_paginates_so_length_is_the_full_count). What is STILL "
    "missing — and what this test demands — is a true SERVER count/aggregate "
    "primitive: an engine `countRecords`/`count(*)`, a `{rows,total}` list "
    "envelope, an SDK `count()`, or a useEntity `total`. None exist; the count is "
    "still derived by brute-fetching every page. So tables >10k rows still "
    "undercount, and SUM/AVG (avgCheck/conversion) over a 10k window can still "
    "skew. Flip to XPASS when a server count/aggregate primitive lands.",
)
def test_runtime_should_expose_a_server_count_primitive_for_kpis() -> None:
    """DESIRED (genuinely-remaining part of the gap): a dashboard must get a TRUE
    total/aggregate from the SERVER without the client fetching every row. Today
    the count is correct only because the SDK walks all pages (fetchAll, capped at
    AUTO_FETCH_CAP) — which silently undercounts beyond 10k rows and pays N round
    trips, and gives no server SUM/AVG so avgCheck/conversion stay client-computed
    over the fetched window. The real fix is one of: an engine count/aggregate
    function, a `{rows,total}` list envelope, an SDK `count()`, or a useEntity
    `total`. None has landed — so this stays xfail."""
    engine = _ENGINE.read_text(encoding="utf-8")
    sdk = _SDK.read_text(encoding="utf-8")
    use_entity = _USE_ENTITY.read_text(encoding="utf-8")

    # (a) engine grows a count/aggregate function or returns a total envelope…
    engine_has_count = bool(
        re.search(r"countRecords|count\(\s*\*\s*\)", engine)
        or re.search(r"sql`\s*count", engine, re.IGNORECASE)
        or re.search(r"return\s*\{[^}]*\btotal\b", engine)
    )
    # …or the SDK exposes a count method (client-side fetchAll does NOT count;
    # it is a row-walk, not a count() the kit can point a KPI at)…
    sdk_has_count = bool(re.search(r"\bcount\s*[:(]", sdk))
    # …or useEntity surfaces a server total for KPIs to read.
    hook_has_total = "total" in use_entity

    assert engine_has_count or sdk_has_count or hook_has_total, (
        "no SERVER count/aggregate primitive yet; the full count is derived by "
        "brute-fetching every page (capped at AUTO_FETCH_CAP=10000), so >10k "
        "tables undercount and there is no server SUM/AVG for skewed KPIs"
    )
