# Baseline corrections before Task 5 delivery

The owner resumed refactoring on 8 September after the recorded full-suite
blocker. This separate corrective package restores the existing onboarding wire
contract and repairs baseline tests; it is not counted as a behavior-preserving
Task 5 extraction. Task 3 numeric serialization remains deferred.

## Changes

- Serialize onboarding questions as JSON objects at the event boundary. The web
  consumer already expects objects. The regression uses the real event publisher
  with only Redis I/O replaced and checks planned and fallback question batches.
- Exercise the cabinet rubric with a separate representative HTML fixture in the
  offline manifest. Evidence explicitly records its source/hash and that it is
  neither rendered, authenticated nor generated for the tested niche. Negative
  fixtures must fail. This does not prove a live customer cabinet.
- Align stale assertions with existing backend-default/provider/primitive/model
  contracts, retaining negative cases and exact column/metadata checks.
- Replace formatting-sensitive source checks with scoped AST checks. Only the
  existing signed-session bootstrap may navigate directly; failed bootstrap
  responses or missing/foreign session evidence prevent subsequent API requests.
- Pin the 0056 roundtrip test to 0056 and separately exercise upgrade to current
  head, preserving seeded records. Run only on disposable PostgreSQL.

## Verification and delivery

The new onboarding wire regression failed before the fix because questions were
strings, then passed for both planned and fallback batches. Independent review
of onboarding and cabinet changes reported no findings. Full combined review,
CI and production delivery are pending; do not treat local unit success as a
deployed release or a successful customer generation.

Combined focused API checks: 423 passed; full API Ruff clean and mypy clean
for 274 source files. One overly broad local invocation included DB-dependent
model tests with deliberately unavailable loopback services and was interrupted;
the DB-free node selection then passed. Database execution remains a CI gate.
The web consumer test uses the real stream hook, query cache and survey component:
two interaction tests passed, with typecheck and ESLint clean. It covers answer
selection, palette selection, terminal-event persistence, submission and skip
without triggering a generation. Independent API review reported no findings.

Task 5 retains its frozen template/Git/export/wheel/image checks. Production
preflight still reports API/worker release 179a3b3f, orchestrator 1011a0fd,
healthy endpoints, no active generations/operations/leases, and the five
unrelated dirty secondbrain documents. Repeat these checks at rollout.

No dependency upgrades, new user generations, production test-data writes,
database rollback feature or ledger changes belong to this package.
