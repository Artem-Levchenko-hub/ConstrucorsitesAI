# MAX Restoration Pre-Implementation Test Contract

**Status:** immutable acceptance contract for implementation agents
**Evidence and implementation plan:** `docs/superpowers/plans/2026-09-21-adaptive-rollback-production-readiness.md`
**Earlier live result:** `docs/operations/2026-09-21-max-studio-rollback-live-result.md`

## Purpose

These tests are designed and committed before production code is changed. Their job is
to define observable rollback behavior, reproduce the failures found in live testing,
and prevent an implementation agent from declaring success by weakening assertions,
skipping hard cases, or changing the expected result.

The implementation loop may add regression tests. It may not edit, delete, rename,
skip, quarantine, loosen, or replace any frozen contract test without a new independent
Astra review and a recorded change to this contract.

## Product outcomes

All three outcomes must pass on the same release SHA:

| ID | Business data | Schema relation | Required behavior | AI | Data strategy |
|---|---|---|---|---|---|
| R0 | Absent, including unused sequences and hidden objects | Any supported historical schema | Automatic rollback after one confirmation | Forbidden | `replace_verified_empty` |
| R1 | Present | Compatible | Automatic exact rollback after one confirmation | Forbidden | `preserve_current` |
| R2 | Present | Incompatible | Explicit `Адаптировать и восстановить`, code-only adaptation of historical code to the current DB | One admitted generation run; bounded repair calls inside it are allowed | `preserve_current` |

`R0` is allowed only after complete inventory proves that every business object is
empty. Zero visible rows alone is insufficient. `R1` requires a compatibility proof.
`R2` may change application code but must not change the current schema digest.

## Frozen rules

1. Tests use synthetic data and disposable projects/databases only.
2. No contract test is marked `skip`, `xfail`, `todo`, `only`, `flaky`, or conditionally
   bypassed in the release gate.
3. A network mock may prove client behavior. It cannot replace PostgreSQL, Docker,
   process-restart, or browser acceptance where those effects are the subject.
4. Every retry assertion also proves uniqueness of operation, run, version, controller
   effect, settlement, and charge.
5. Every data-preservation assertion compares canonical SQL digests and checks IDs,
   owner IDs, hidden columns, nullability, relations, sequences, and timestamps.
6. Tests never inspect or store credentials, cookies, signed URLs, or raw production
   business rows in artifacts.
7. A passing result on a different release SHA is not evidence for the candidate.
8. The pre-implementation RED baseline uses Local mode with a controller-owned digest
   of baseline product code plus the uncommitted Astra test layer. Release mode begins
   only after a verified candidate is committed and the tree is clean.

## Test layers and mandatory cases

### A. Unit and state-machine tests

- `UNIT-CAP-001`: `CellCapacityUnavailable(insufficient_cpu)` becomes owner-safe HTTP
  429 `capacity_wait`, `Retry-After`, `effect_applied=false`; it is never mapped to 500.
- `UNIT-CAP-002`: capacity wait retains operation ID, request digest, fence, retry time,
  and never creates another wake intent.
- `UNIT-STATE-001`: every allowed restoration state/phase transition is accepted;
  every other transition is rejected without external effects.
- `UNIT-STATE-002`: cancellation is possible before activation effects admission and
  impossible after it.
- `UNIT-IDEM-001`: same logical key returns the same operation; a competing key returns
  `restoration_active` with the existing ID.
- `UNIT-EMPTY-001`: all business tables empty and all relevant sequences pristine
  produces R0.
- `UNIT-EMPTY-002`: zero rows with an advanced sequence, hidden object, incomplete
  inventory, or unavailable inventory does not produce R0.
- `UNIT-COMP-001`: identical supported schema contracts produce R1.
- `UNIT-COMP-002`: incompatible column, constraint, relation, ownership, migration
  ambiguity, or incomplete descriptor produces `needs_changes`.
- `UNIT-ADAPT-001`: R2 prompt says code-only, current schema immutable, current data
  preserved, and contains both source and current contracts.
- `UNIT-ADAPT-002`: candidate schema digest change returns bounded repair feedback and
  never reaches activation.
- `UNIT-DELTA-001`: added, modified, and deleted files survive every repair pass relative
  to the immutable source baseline.
- `UNIT-PROMPT-001`: user instruction `без демо-данных` cannot be contradicted by
  generated boilerplate that requires seed/demo data.
- `UNIT-DELETE-001`: active or admitted restoration fences project deletion; a terminal
  project becomes deletable only after a durable redacted receipt exists outside the
  project cascade.
- `UNIT-DELETE-002`: deletion cannot cascade immutable restoration receipts, actor,
  timestamp, reason, release SHA, proof digest, or settlement evidence.
- `UNIT-COPY-001`: owner-facing copy distinguishes `replace_verified_empty` from
  `preserve_current` and never claims that the DB was preserved when it was replaced.

### B. PostgreSQL integration tests

- `PG-R0-001`: materialize an incompatible historical schema into a separately verified
  empty database, switch once, create the first row, refresh, restart, update, and read it.
- `PG-R1-001`: preserve a populated compatible database through exact code rollback;
  CRUD and SQL digest remain valid after refresh and restart.
- `PG-R2-001`: adapt historical code to a populated incompatible current schema; schema
  digest and the original-row witness remain identical immediately after activation.
  CRUD probes run only after that comparison and use separate synthetic IDs.
- `PG-R2-002`: nullable, non-null with default, relation, index, unique constraint,
  owner reference, UUID key, integer key, composite key, read-only entity, and
  platform-managed entity each produce their declared supported/unsupported outcome.
- `PG-ISO-001`: signed identity A can use its records after every branch; identity B is
  denied at the API and RLS layers.
- `PG-MIG-001`: upgrade, downgrade, upgrade leaves one Alembic head and preserves
  restoration receipts outside project cascade.
- `PG-LOCK-001`: concurrent create/delete/reconcile/activate transactions cannot create
  a split-brain current version or lose the journal.

### C. Orchestrator and real Docker tests

- `ORCH-CAP-001`: an exhausted reservation ledger returns retryable capacity wait while
  the host still has free physical CPU; releasing a reservation resumes the same wake.
- `ORCH-R0-001`: R0 creates one candidate bundle and one activation effect, then removes
  temporary resources after the receipt is sealed.
- `ORCH-R1-001`: R1 runs historical code against the preserved current DB and passes
  readiness plus business probes before activation.
- `ORCH-R2-001`: R2 uses an isolated DB copy for build/probe, forbids live-DB writes,
  then activates code-only against the unchanged current DB.
- `ORCH-CRASH-001`: restart before wake response, during proof, after proof, during
  activation, and after activation response loss resumes the same durable intent.
- `ORCH-FENCE-001`: stale fence, duplicate callback, and reordered callback are ignored
  or reconciled without a second effect.
- `ORCH-CLEAN-001`: success cleans temporary resources; failure retains only documented
  forensic artifacts and no runnable stale candidate.

### D. API contract and web component tests

- `API-CREATE-001`: restoration row is committed before source wake; a wake failure
  returns the operation in `preparing/source_wake` instead of 503 without an ID.
- `API-STATUS-001`: public status exposes stable reason codes, progress, retry time, and
  owner-safe next action for every state and phase.
- `API-SEC-001`: another user cannot list, read, cancel, retry, adapt, or delete a
  restoration or infer its project/version existence.
- `WEB-CAP-001`: capacity wait shows a durable queued state, survives refresh, polls the
  same operation, and offers no duplicate start action.
- `WEB-R0-001`: empty rollback explains DB replacement and completes without adaptation
  or AI billing copy.
- `WEB-R1-001`: compatible rollback starts automatically and never shows an AI charge.
- `WEB-R2-001`: incompatible populated rollback requires an explicit adaptation action,
  explains AI usage, and shows proof/activation progress.
- `WEB-ERR-001`: terminal failure remains diagnosable after refresh and offers only safe
  retry/recovery actions.
- `WEB-A11Y-001`: keyboard, focus, dialog semantics, touch targets, and 390 px layout pass
  for all confirmation, progress, error, and completion states.

### E. Full browser E2E and manual QA

Each scenario runs through the real owner UI and records redacted network/console logs,
operation timeline, release SHA, screenshots at desktop and 390 px, and a synthetic SQL
witness. Direct API calls may prepare fixtures but may not perform the user action under
test.

- `E2E-R0-001`: generate two schema-incompatible versions with no business data, select
  the old version, confirm once, wait through wake/activation, then create/update/reload
  the first business record.
- `E2E-R1-001`: create records including hidden/nullable/relation fields, generate a
  UI-only version, exact-rollback, then verify IDs and CRUD after reload/restart.
- `E2E-R2-001`: create data in the new incompatible schema, select old code, explicitly
  adapt, verify the agent run, proof, one activation, unchanged schema/data digests,
  CRUD, reload, restart, and cross-user denial.
- `E2E-REFRESH-001`: refresh/close/reopen during each non-terminal phase; the same
  operation remains visible and completes or fails deterministically.
- `E2E-DOUBLE-001`: double click and two tabs produce one operation. R0/R1 produce zero
  AI charges/settlements; R2 produces one final billing result according to the frozen
  free/paid fixture, even when its admitted run performs bounded repair calls.
- `E2E-DELETE-001`: deletion during active/admitted restoration is blocked; terminal
  deletion leaves immutable proof/tombstone evidence.
- `E2E-CAP-001`: paused workspace under capacity pressure remains queued and resumes
  automatically when capacity is released.
- `E2E-FAIL-001`: forced agent, probe, activation, callback, and settlement failures
  expose a safe user message and preserve recovery evidence.

Manual QA repeats R0/R1/R2 without mocks on a production-like disposable environment.
It checks loading, empty, error, cancellation, refresh, navigation, mobile layout,
browser console/network, service logs, and persistence after a full service restart.

## Coverage gate

The frozen allowlist is:

- API: `services/restorations.py`, `services/project_cell_lifecycle.py`,
  `services/project_cell_runtime.py`, `services/restoration_adaptation.py`,
  `routers/restorations.py`, and the restoration guard/receipt paths in
  `routers/projects.py`.
- Orchestrator: `routers/workspace.py` capacity mapping,
  `services/code_restoration_engine.py`, `services/restoration_adaptation_workspace.py`,
  `services/restoration_adaptation_probe.py`, and `services/versioning/`.
- Web: `use-max-restoration.ts`, `lib/api/restorations.ts`, and
  `components/max/MaxRestorationPanel.tsx`.

The numerical gate is strict but avoids trapping the implementation agent on unrelated
legacy utility lines:

- 100% branch coverage for branch selection, admission, cancellation/point-of-no-return,
  fence validation, activation recovery, billing idempotency, deletion guard, and owner
  authorization functions.
- At least 95% line and 90% branch coverage for each allowlisted module separately; no
  averaging across modules.
- 100% executable changed-line coverage and a test for every newly added decision branch.
- A frozen mutation corpus must be killed 100%: remove the deletion fence, disable schema
  equality, allow post-admission cancel, remove owner filtering, duplicate settlement,
  and omit a hidden column from the witness.
- Any exclusion must be an unreachable/type-only/generated line, named line-by-line in
  a machine-readable allowlist, and approved by independent Astra review.
- The full repository test, lint, type, migration, build, image, and secret gates still
  run. Critical-module coverage cannot replace them.
- Missing coverage tools, missing PostgreSQL/Docker/browser prerequisites, or a skipped
  critical test fails the gate.

Required reports: Cobertura XML, LCOV, HTML coverage, JUnit for each layer, Playwright
trace/video/screenshots on failure, redacted service/browser logs, migration output,
and one `acceptance.json` tied to the tested release SHA.

The agent pipeline has two identities. `Local` binds every gate artifact to the
controller-computed working-tree digest and proves only a local candidate. `Release`
requires a clean committed SHA, immutable images and the exact deployed SHA. A local
tree digest can never satisfy the production release gate.

## Anti-loop policy for agents

1. Maximum implementation iterations: four by default, eight only by an explicit
   release-owner setting.
2. Each iteration may change production code and add regression tests, but frozen tests
   are hash-checked before and after the run.
3. The deterministic gate runs outside the implementation agent. The agent cannot
   self-report green.
4. A normalized failure fingerprint is computed from failed test IDs, state/phase,
   exception type, and top application frames.
5. The same fingerprint twice without a reduced failing set stops the loop as `blocked`.
6. A new P0/P1 Astra finding also stops release. P2 must be fixed or explicitly deferred
   by the release owner; the agent cannot defer it itself.
7. Timeout, unavailable dependency, missing credential, unavailable capacity, and test
   defect are separate terminal classifications; they are not fed back forever as code
   repair prompts.
8. Exit `verified` requires all deterministic gates and independent Astra review to pass.
   Exit `blocked` includes the smallest reproduction, logs, diff, last green gate, and
   exact human action needed.

## Exit gates

The candidate is production-ready only when:

1. R0, R1, and R2 pass on the same commit and release SHA.
2. Unit, PostgreSQL, Docker/orchestrator, API, web, E2E, security, concurrency,
   restart/recovery, and fault-injection suites are green with zero critical skips.
3. Coverage and diff-coverage gates are green.
4. Astra reports no P0/P1/P2 defect in the full diff and surrounding recovery code.
5. Production-like acceptance proves one operation/version, zero AI runs/charges/
   settlements for R0/R1, one admitted AI run and one final billing result for R2,
   preserved original-row data, and cross-user denial.
6. Commit, push, exact-SHA deploy, health, rollback acceptance, and evidence archive are
   all recorded. CI success alone is insufficient.
