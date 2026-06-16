"""Acceptance-lock for BS-26 (dogfood run #23, 2026-06-17): a generated entity
app's DASHBOARD KPIs — the headline numbers an owner sees on the very first
screen after login ("Активных сделок: N", "Клиентов: N", "Средний чек") — are
computed CLIENT-SIDE as `.length` / `.reduce()` over the entity arrays that
`useEntity` / `entities[X].list()` loads. Those loads carry no `limit`, so the
engine returns its DEFAULT_LIMIT=50 page. The entity runtime exposes NO
count/aggregate primitive anywhere: `listRecords` returns a bare ≤50 page with
no `total`, the SDK `list` returns `Row[]`, `useEntity` exposes only `rows`, and
there is no count/stats route. So once a business passes 50 records per entity,
every headline KPI silently caps at 50 — and the average/sum/ratio KPIs
(avgCheck, conversion) are computed over the NEWEST 50, i.e. statistically
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


def test_engine_list_returns_a_bare_capped_page_with_no_total_today() -> None:
    """EVIDENCE (green today): listRecords returns the shaped ≤50 page directly —
    no `total`, no count envelope — and there is no count/aggregate function in
    the engine. So nothing can answer "how many records total?" short of fetching
    them all (capped at MAX_LIMIT)."""
    src = _ENGINE.read_text(encoding="utf-8")
    # The list returns a bare rows array (optionally expanded) — never {rows,total}.
    assert "return expand.length ? await expandRecords(def, shaped, expand, user) : shaped;" in src
    # The default page is 50.
    assert "const DEFAULT_LIMIT = 50;" in src
    # No count primitive exists anywhere in the engine.
    assert "countRecords" not in src
    assert not re.search(r"count\(\s*\*\s*\)", src)
    assert not re.search(r"sql`\s*count", src, re.IGNORECASE)


def test_sdk_list_returns_bare_array_with_no_count_method_today() -> None:
    """EVIDENCE (green today): the SDK `list` returns `Promise<Row[]>` — a bare
    array, no total — and there is no `count` method to ask the server for a
    cardinality."""
    src = _SDK.read_text(encoding="utf-8")
    assert "list(params?: ListParams): Promise<Row[]>;" in src
    assert 'list: (params) => safeCollection(req<Row[]>("GET", base + qs(params))),' in src
    # No count/aggregate method on the collection SDK.
    assert not re.search(r"\bcount\s*[:(]", src)


def test_use_entity_exposes_only_a_capped_rows_array_today() -> None:
    """EVIDENCE (green today): useEntity exposes `rows` only (no `total`), and
    loads with a single list() and no `limit`/`page` → inherits the ≤50 cap. So
    a KPI written the only way available — `useEntity(X).rows.length` — caps."""
    src = _USE_ENTITY.read_text(encoding="utf-8")
    assert "rows: Row[];" in src
    assert "total" not in src  # the hook never surfaces a server total
    assert "entities[name].list(paramsRef.current)" in src
    assert "limit" not in src  # never raises the cap for the load


def test_kit_jsdoc_teaches_length_over_loaded_arrays_for_kpis_today() -> None:
    """EVIDENCE (green today): the kit's own dashboard guidance instructs the
    writer to compute KPI values as `.length` over the loaded entity arrays — so
    the capped-count behaviour is structural (the template teaches it), not
    per-writer noise."""
    hero = _DASH_HERO.read_text(encoding="utf-8")
    count_up = _COUNT_UP.read_text(encoding="utf-8")
    assert "value={clients.length}" in hero
    assert "value: open.length" in hero
    # CountUp is positioned as the dashboard KPI value wrapper.
    assert "StatCard" in count_up


def test_no_count_or_stats_route_exists_today() -> None:
    """EVIDENCE (green today): the only collection route is the list route; there
    is no count/stats/aggregate sibling the dashboard could call for a true
    total."""
    route_files = {p.name for p in _API_DIR.rglob("route.ts")}
    # the list/collection route exists…
    assert "route.ts" in route_files
    # …but nothing named count/stats/aggregate.
    names = {p.parent.name for p in _API_DIR.rglob("route.ts")}
    assert not (names & {"count", "stats", "aggregate", "summary"})


@pytest.mark.xfail(
    strict=False,
    reason="BS-26 / P-KPICOUNT not yet landed: dashboard KPI counts/sums are "
    "computed over the ≤50 loaded rows because the entity runtime exposes no "
    "count/aggregate primitive. Flip to XPASS when the engine/SDK gain a server "
    "count (and ideally SUM/AVG) primitive — a `/count` route, a `{rows,total}` "
    "list envelope, or an SDK `count()` — so headline KPIs reflect the full "
    "dataset, not the newest 50.",
)
def test_runtime_should_expose_a_server_count_primitive_for_kpis() -> None:
    """DESIRED: a dashboard must be able to display a TRUE total without fetching
    every row. Either the engine returns a `total` alongside the page (a
    `{rows,total}` envelope), or there is a count/aggregate route, or the SDK
    exposes a `count()` the kit guidance points KPIs at. Until then every
    generated dashboard headline silently caps at 50 (and averages skew to the
    newest 50)."""
    engine = _ENGINE.read_text(encoding="utf-8")
    sdk = _SDK.read_text(encoding="utf-8")
    use_entity = _USE_ENTITY.read_text(encoding="utf-8")

    # (a) engine grows a count/aggregate function or returns a total envelope…
    engine_has_count = bool(
        re.search(r"countRecords|count\(\s*\*\s*\)", engine)
        or re.search(r"sql`\s*count", engine, re.IGNORECASE)
        or re.search(r"return\s*\{[^}]*\btotal\b", engine)
    )
    # …or the SDK exposes a count method…
    sdk_has_count = bool(re.search(r"\bcount\s*[:(]", sdk))
    # …or useEntity surfaces a server total for KPIs to read.
    hook_has_total = "total" in use_entity

    assert engine_has_count or sdk_has_count or hook_has_total, (
        "no server count/aggregate primitive yet; dashboard KPIs still cap at "
        "the ≤50 loaded window"
    )
