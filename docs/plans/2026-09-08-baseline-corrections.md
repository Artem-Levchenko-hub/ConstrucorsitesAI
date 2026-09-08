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


## Final delivery evidence

Corrective commit 4960f6b9 pushed and deployed with Task 5. Full CI 34258366570
success; API 3129 passed / 12 skipped / 8 xfailed, including migration roundtrip.
Orchestrator same-SHA rerun passed after an unexplained no-output hang in its first
job. All other jobs passed. Full independent source/test review found no defects;
an isolated-image helper mount defect was fixed and re-reviewed before execution.
The new image's two onboarding wire tests passed with network disabled and only
read-only test fixtures mounted. Production API and both workers use 4960f6b9;
health/release and preserved public kit hashes passed, write gate removed.
See Task 5 verification for image identity and backup record. No live user/model
generation was launched; the UI interaction proof is a deterministic component
test, not a production browser session.
