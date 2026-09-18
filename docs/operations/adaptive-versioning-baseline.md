# Adaptive versioning — baseline and package 1

Plan: `MAX_Studio_adaptive_versioning_v3` (18.09.2026), tasks AV00–AV29.
This file records the verified starting point (AV00) and what package 1
(AV00–AV05 + AV06 groundwork) changed. It does not declare adaptive versioning
complete.

## AV00 — baseline

| Item | Value |
|---|---|
| Git base | `a33ffe9a` (main, 18.09.2026) |
| Runtime before package 1 | api / worker / generation-worker `21d2b98e`, orchestrator `18b3550b`, web `6e6f11fd` |
| Kept as-is | `Restoration` durable operation + idempotency, isolated candidate with a current-data copy, code-volume-only activation, resume fix `18b3550b`, ordinary project PostgreSQL, templates baked via `--build-context templates` |

Existing implementations (no parallel engine was added):
`services/code_restoration_engine.py` (prepare/apply/observe),
`services/restoration_catalog.py` (live catalog + historical Drizzle extractor),
`services/restoration_data_contract.py` (contract + assessment),
API `schemas/restoration.py`, `services/restoration_adaptation.py`,
web `components/max/MaxRestorationPanel.tsx`.

Known pre-existing test failures on a clean `a33ffe9a` checkout (not caused by
this work): 8 × `test_docker_machine_backend.py::test_retained_receipt_binds_trusted_runtime_and_still_fences_product`.

## The four problems of the owner's test and what package 1 did

| Problem (screenshot) | Cause | Package 1 |
|---|---|---|
| Data shown as "unknown" | presence was measured only after the schema check passed | `versioning/inventory.py`: read-only per-table counts on the source **before** any analysis, then again on the isolated copy; a failing relation is `not_measured`, never zero |
| Vague reason ("custom constraints") | table-level flags and raw tokens | precise checks (`versioning/compatibility.py`): code, operation, object, explanation, resolution — e.g. `required_field_missing_on_create` on `public.clients.email` |
| Ordinary CHECK/DEFAULT forced adaptation | CHECK counted as custom constraint; tiny DEFAULT whitelist; Drizzle extractor threw on any CHECK | named CHECKs compared by normalized expression on both sides; literal/`nextval`/`now()`/`gen_random_uuid()`/IDENTITY defaults are ordinary; user-function defaults, triggers, expression indexes, NOT VALID/deferrable constraints are still blockers, now named |
| Visits GET lost after adaptation | nothing tracked functions | groundwork only: route capability diff (`capabilities.lost`) in the report and in the adaptation brief; **not yet enforced** (AV06 proper / AV13) |

## Report format 2

Additive fields on the existing report (`format: 2`, `inventory`, `checks`,
`capabilities`). Format 1 reports keep parsing; missing fields mean "not
measured". `database_state` must equal `inventory.presence`. The adaptation
bundle trims per-table counts and passed checks if the report exceeds 64 KB.

## Tests

- `apps/orchestrator/tests/test_versioning_inventory.py` — real PostgreSQL via `psql -qAt`
- `apps/orchestrator/tests/test_versioning_catalog_native.py`
- `apps/orchestrator/tests/test_versioning_customer_visits_scenario.py` — the owner's v1→v2→v1 scenario on synthetic fixtures (`tests/fixtures/versioning/customer_visits`), incl. the real Drizzle extractor when template `node_modules` exist
- `apps/api/tests/test_restoration_report_v2.py`
- web `src/lib/__tests__/max-restoration.test.tsx` (format-2 rendering)

Real-DB tests need `RESTORATION_TEST_DATABASE_URL=.../restoration_policy_test`
(CI provides it).

## Remaining limits (not done in package 1)

- A changed CHECK with the same name is compared by a normalized text form; two
  differently parenthesized boolean expressions can normalize equal. PostgreSQL
  still enforces the live CHECK, so the worst case is a rejected write, not bad data.
- Business defaults on new required columns still need confirmation (`required_field_default_needs_confirmation`).
- Capabilities are HTTP route methods only (no server actions, UI scenarios, response shape, owner filter checks).
- No behavioural rehearsal, writer barrier, migration plan, decisions, production path (AV07+).
