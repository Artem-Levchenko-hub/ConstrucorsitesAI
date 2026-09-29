# Generation State Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete recoverable generation attempts with coherent portable database, source, and proof contracts.

**Architecture:** Keep the existing content-addressed workspace proofs and publication permits. Add explicit portable database guidance, safe controller SQL diagnostics and bounded source repair; close independently reproduced boundary gaps without replacing the generation architecture.

**Tech Stack:** Python API/orchestrator, PostgreSQL 16, Docker controller, Next.js managed kit, pytest and real disposable PostgreSQL.

**Spec:** `docs/superpowers/plans/2026-09-29-generation-state-consistency-design.md`.

## Global Constraints

- Preserve accepted source, business data, unrelated server changes, and immutable applied migration checksums.
- Original project storage is read-only during diagnosis; SQL reproduction uses only the isolated audit clone/disposable test databases.
- No generated application patch, journal adoption, provider write, secret exposure, or guard bypass.
- One implementation owner; independent reviewers are read-only. Terra owns serial commit/push/CI/deploy after review.
- Production compose includes both `docker-compose.yml` and `docker-compose.hostdb.yml`; health alone is insufficient.

## Review Focus

- A legacy schema export is not proof of a physical product relation; an existing local parent must remain supported.
- SQL errors may contain secret row values; only trusted typed diagnostics can become model/public feedback.
- A stopped client and an unknown running SQL operation require different retry decisions.
- Retained failed candidate files are not accepted/applied migration authority.
- A red or stale proof must never consume quota or advance the accepted snapshot.

### Task 1: Reproduce and record product database mismatch

**Files:** external sanitized audit evidence; this design and plan.

- [x] Reproduce exact pending SQL against an isolated cold clone: empty product DB, SQLSTATE `42P01`.
- [x] Record exact safe relation failure, original before/after archive hash, and zero residual clone tables.
- [x] Merge fresh 14-day audit: 105 retained unique runs, per-run evidence and limitations.
- [x] Resolve independent release/admission findings against actual current guards.

### Task 2: Align portable schema guidance and early feedback

**Files:** `apps/api/src/yleum_api/services/portable_cell_contract.py`, `max_project_kit.py`; orchestrator controller migration services if catalog feedback is required; focused API/orchestrator tests.

**Interfaces:** Existing portable final guide and seed renderer remain the model input. Any early prerequisite check consumes controller-owned read-only catalog plus ordered pending SQL, never TypeScript exports as DB authority.

- [x] Write and observe failing tests for product/core separation in the actual portable final guide and starter.
- [x] Implement explicit identity/storage guidance preserving legacy-compatible exports and accepted source.
- [x] Add an early missing-prerequisite regression using a real catalog, with existing-parent and prior-local-migration controls. Ambiguous SQL must defer to actual PostgreSQL.
- [x] Run focused tests and record results.

### Task 3: Safe SQL diagnostics and same-run repair

**Files:** `apps/orchestrator/src/yleum_orchestrator/services/restoration_database.py`, `project_migrations.py`; `apps/api/src/yleum_api/services/max_finalization.py`; their focused tests and `test_project_migrations_postgres.py`.

**Interfaces:** A typed controller SQL error exposes bounded SQLSTATE and known command termination. `run_project_migrations` translates only allowlisted source errors to a versioned repair marker. The finalizer maps that marker to `NEEDS_EDIT`; all other failures keep existing fail-closed behavior.

- [x] Write failing tests for missing table, syntax/type errors, secret-bearing stderr, exit timeout/running/unknown, and journal conflicts.
- [x] Implement bounded SQLSTATE extraction without changing stdout consumers.
- [x] Implement marker translation and bounded finalizer repair; require changed workspace source and fresh proof.
- [x] Run real disposable PostgreSQL cases: missing FK rolls back whole batch/journal, corrected SQL succeeds, rerun is idempotent, checksum change fails and preserves data.
- [x] Run focused orchestrator/API tests, lint/types, and existing boundary regressions.

### Task 4: Verify phase consistency and complete delivery

**Files:** only independently proven additional boundary gaps; delivery manifest includes exact final paths and both plan files.

- [x] Test full checked filesystem equals final candidate including retained SQL and deletions; preserve current accepted/applied immutability tests.
- [x] Verify publication permit refusal before snapshot/quota effects and document any declined audit hypothesis.
- [x] Close proven deployment-admission race with a durable fence and concurrent admission regression, if existing platform mechanism does not cover it.
- [x] Independent whole-diff review; fix substantive findings with regression tests.
- [ ] Terra commits/pushes exact intended files; full Linux CI and canonical production rollout; compare runtime revisions, effective pins, DB target, and active leases.
- [ ] Parent performs ordinary UI recovery and complete native amoCRM acceptance. Record actual run IDs separately from historical audit.

### Task 5: Bound and attribute provider recovery

**Files:** `apps/api/src/yleum_api/services/agent_native.py`, its tests;
`apps/llm-gateway/src/yleum_gateway/routers/messages_native.py`, its tests.

- [x] Reproduce an active provider request exceeding the 210-second retry budget; bound the request and sleep by remaining wall time without replay after exhaustion.
- [x] Reproduce native upstream fallback misattribution. Canonical provider aliases keep the requested model; a known different model is recorded/billed as actual. An unknown actual model returns a terminal contract error before wrong-model charging, never an automatic retry loop.
- [x] Verify auth failures remain bounded/terminal and no tool side effect is replayed. No paid model call is required for these tests.
- [ ] Include gateway full tests and canonical gateway deployment when this task changes it.

## Execution ledger

- 2026-09-29: preflight supplied by parent at 12:09:56 UTC: local/remote/server `7e43`, clean local, known unrelated server dirt preserved.
- Ruling: execute inline without another approval pause because the user explicitly authorized implementation and production delivery. Commits/push remain assigned to Terra.
- Ruling: no rollback-only rehearsal on original DB; nontransactional effects make that an invalid isolation claim.
- Independent release-proof bypass hypothesis retracted: `require_promotion_permit` precedes commit and requires green full build/runtime/release proof. No reachable red-proof publication witness was found; existing regression remains authoritative.
- PostgreSQL verification: 26 real cases passed in a disposable database on the isolated network-none audit clone; empty catalog early feedback identifies `public.max_users`. Original project storage was not mounted writable.
- Final original read-only archive SHA matches before/after: `e1baecc7f6182173437ba159cb655ea0265c3361d9090a6f29af21dd10d544c9`.
- Local verification: 122 API focused; 106 gateway full; 72 orchestrator focused plus 6 publication-gate cases; 18 web. Ruff/mypy/type checks pass. Full Windows orchestrator: 2292 passed, 88 skipped, six unchanged POSIX-mode/path/socket assumptions failed; Linux CI remains mandatory. API PostgreSQL suites require CI.
- Portable golden update: all six changed outputs reverse to their exact old digest when only the new schema annotation is removed. No baseline/render assertions were weakened.
- Controller publication drain checked read-only against live PID settings: core two terminal journals, commerce one; no mutation.
- Final review deltas: explicit executable first-install `--bootstrap-admission` and rejection of `--web-only --gateway`; two old-model API tests and two CLI flag tests passed. The readonly target helper executes on the old production API and correctly refuses a nonzero operation count.
- Independent final rereview: both release findings closed; ready for delivery. No new code edits planned.
