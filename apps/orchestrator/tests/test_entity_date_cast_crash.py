"""Acceptance-lock for BS-22 (dogfood run #19, 2026-06-16): a single
JS-valid-but-Postgres-invalid date value 500s the ENTIRE list the moment anyone
sorts by that column — total denial-of-view from realistic sloppy date entry.

THE DIVERGENCE (code-proven, two halves):
  - WRITE accepts too much: a `date` field is validated only by
    `z.string().refine((s) => !Number.isNaN(Date.parse(s)))` (registry.ts:139),
    i.e. JS `Date.parse`, which is very lenient — "2025", "2025-06", "June 2025",
    "Jun 2025", "2025/06", "2025-02-30" all PASS — and the raw string is stored
    in JSONB unnormalized (createRecord stores `parsed.data` as-is).
  - SORT reads too strictly: ordering a date column casts the stored text with
    Postgres' `::timestamptz` (engine.ts fieldExpr), whose parser is STRICTER and
    DIFFERENT from JS — it REJECTS every one of those values
    ("invalid input syntax for type timestamp with time zone").
  - The raw Postgres error is not an `EngineError`, so `run()` (http.ts) maps it
    to a generic 500 "internal error" — opaque, logged only as
    "[entities] unexpected error".
  - `<CrudResource>` makes every column sortable by default (BS-21,
    crud-resource.tsx:168), so the date column is one click from this 500.
  - The equality FILTER path (`data->>key = value`, no cast) is SAFE — the poison
    record is still filterable; only SORT crashes.

LIVE PROOF (run #19, throwaway container from the deployed base image
`omnia-template-nextjs-entities:dev`, container
omnia-dev-dogfood-datecast-probe-2fe2f6 port 3399, starter `Task` entity with its
built-in `due` date field, owner-auth via Auth.js credentials — no LLM, no gen):
  - POST /api/entities/Task {title, due:"June 2025", priority:"high"}     → 201 (write accepts it)
  - GET  /api/entities/Task            (no sort — what useEntity sends)    → 200, record present
  - GET  /api/entities/Task?sort=due&order=asc                            → 500 "internal error"  ← THE BUG
  - GET  /api/entities/Task?sort=due&order=desc                           → 500 (both directions)
  - GET  /api/entities/Task?due=June 2025   (equality filter, no cast)    → 200, finds the record (safe)
  - CONTROL: DELETE the poison record, re-GET ?sort=due&order=asc         → 200 with sorted rows
        → proves it was EXACTLY the "June 2025" value, not a general sort failure.
  - Container log: `[entities] unexpected error error: invalid input syntax for
        type timestamp with time zone: "June 2025" at async listRecords
        (engine.ts:194)` — the `::timestamptz` sort cast is the exact failure point.

Worse than its siblings: BS-21 returns a wrong-but-present top; BS-18 hides rows
silently; here the WHOLE list view crashes with an opaque error for EVERY user,
triggered by one malformed-but-typeable date (a year, a year-month, "June 2025").
Class wider than CRM: any growing list with a date column — invoices by due date,
orders by date, bookings, leads, tasks.

FIX (shipped this run — crash-proof date sort, BS-22):
  - init-db.mjs defines `safe_to_timestamptz(text)` — a STABLE PL/pgSQL function
    that returns NULL on any cast error (only exception-catching is truly
    crash-proof: a regex guard can't catch range errors like "2025-02-30").
  - engine.ts fieldExpr sorts a date column THROUGH `safe_to_timestamptz(...)`
    instead of a raw `::timestamptz` cast, so a poison row becomes NULL (sorts to
    the NULL end) and can never 500 the whole view.
  - Deterministic, fail-soft, non-regressive: `safe_to_timestamptz(valid) ===
    valid::timestamptz`, so healthy ISO dates sort identically; it changes no
    read/write contract (unlike P-PAGINATE/P-SEARCH/P-SORT). Verified by base-image
    rebuild + re-running the live probe (the poison value no longer 500s the list).

NOT fixed here (left as the larger, semantics-changing follow-up — see the
ledger's P-DATECAST note): the WRITE side is still lenient — it accepts "June 2025"
and stores it raw, so the value still displays oddly and sorts to the NULL end.
Tightening the write validator to reject/normalize non-ISO dates changes the
write-validation semantics of every generated app (cf. BS-17 P-CLEAR) and risks
over-rejecting legitimate writer/seed output → not shipped blind.

Deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_HTTP = _ENTITIES / "src" / "lib" / "entities" / "http.ts"
_INIT_DB = _ENTITIES / "scripts" / "init-db.mjs"


def test_date_write_validator_is_lenient_js_dateparse_today() -> None:
    """EVIDENCE (green): the write side accepts any string JS `Date.parse` parses —
    far more than Postgres' timestamptz parser will later accept. This is the root
    of the divergence and is intentionally LEFT in place this run (tightening it
    changes write semantics across all apps)."""
    src = _REGISTRY.read_text(encoding="utf-8")
    assert "Date.parse(s)" in src
    assert "Number.isNaN(Date.parse(s))" in src


def test_engine_filter_path_is_uncast_string_equality_today() -> None:
    """EVIDENCE (green): the equality filter uses `data->>key = value` with NO cast,
    so a poison date is still filterable — only the SORT path (which casts) crashes.
    This is why the fix targets the sort cast, not the filter."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert "sql`${records.data} ->> ${key} = ${value}`" in src


def test_non_engine_errors_become_a_generic_500_today() -> None:
    """EVIDENCE (green): a raw Postgres cast error is not an `EngineError`, so the
    wrapper maps it to an opaque 500 "internal error" — the whole list, not just the
    bad row, fails."""
    src = _HTTP.read_text(encoding="utf-8")
    assert "e instanceof EngineError" in src
    assert '{ status: 500 }' in src


def test_date_sort_uses_crash_proof_cast() -> None:
    """FIX-LOCK (BS-22): the date sort must go through `safe_to_timestamptz(...)`,
    NOT a raw `::timestamptz` cast that throws on a JS-valid-but-PG-invalid value.
    Would have FAILED before the fix; guards against regressing back to the raw cast."""
    src = _ENGINE.read_text(encoding="utf-8")
    assert "safe_to_timestamptz(${records.data} ->> ${field})" in src, (
        "date sort must use the crash-proof safe_to_timestamptz wrapper"
    )
    # the raw, throwing date cast must be gone from the sort/filter expression
    assert "(${records.data} ->> ${field})::timestamptz" not in src, (
        "the raw ::timestamptz cast (which 500s the whole list on one bad row) "
        "must no longer be used for the date sort expression"
    )
    # the number sort keeps its (safe — write-validated) numeric cast.
    assert "(${records.data} ->> ${field})::numeric" in src


def test_safe_to_timestamptz_is_defined_in_schema_bootstrap() -> None:
    """FIX-LOCK (BS-22): the schema bootstrap that runs on every container boot must
    define `safe_to_timestamptz` (idempotent CREATE OR REPLACE) that swallows cast
    errors and returns NULL, so the engine's sort expression can resolve it."""
    src = _INIT_DB.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION safe_to_timestamptz(t text)" in src
    assert "RETURNS timestamptz" in src
    # must actually catch the cast error and return NULL (not just re-throw).
    assert "EXCEPTION WHEN others THEN" in src
    assert "RETURN NULL;" in src


@pytest.mark.xfail(
    strict=False,
    reason="BS-22 write-side half (P-DATECAST follow-up) not landed: the date field "
    "validator still accepts any JS-Date.parse-able string ('2025', 'June 2025') and "
    "stores it raw, so the value displays oddly and sorts to the NULL end. Tightening "
    "the validator to reject/normalize non-ISO dates changes write-validation "
    "semantics across all apps (cf. BS-17 P-CLEAR) → not shipped blind. Flip to XPASS "
    "when the date write validator enforces a Postgres-castable (ISO-8601) date.",
)
def test_date_write_validator_should_reject_non_iso_dates() -> None:
    """DESIRED: the date write validator should only accept values Postgres can later
    cast (strict ISO-8601), so a malformed date is rejected loudly at write time (400)
    rather than silently stored to misbehave on sort/display."""
    src = _REGISTRY.read_text(encoding="utf-8")
    # A real write-side fix replaces the lenient Date.parse refine with a strict ISO
    # check (e.g. z.iso.date()/z.string().date()/.datetime(), or a normalize step).
    lenient_dateparse_gone = "Number.isNaN(Date.parse(s))" not in src
    strict_iso_present = (
        ".date()" in src
        or ".datetime()" in src
        or "z.iso" in src
        or "toISOString()" in src
    )
    assert lenient_dateparse_gone and strict_iso_present, (
        "date write validator still accepts non-ISO, Postgres-uncastable strings"
    )
