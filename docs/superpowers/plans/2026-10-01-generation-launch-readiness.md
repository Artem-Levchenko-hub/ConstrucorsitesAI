# Generation Launch Readiness Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development with isolated worktrees. Root owns integration, commit, push and production delivery.

**Goal:** Close two reproduced source-edit budget defects and the migration/logger regression before resuming the 67-scenario launch acceptance.

**Architecture:** Reuse a consistent application-source delta predicate through native editing and finalization. Count completed malformed provider responses within the existing unchanged-edit budget. Preserve application loggers when Alembic configures its logging.

**Tech Stack:** Python, pytest, Alembic, PostgreSQL, the canonical full production Compose stack.

**Spec:** `docs/operations/generation-stability.md` Pilot execution checkpoint and the user's instruction to test and fix launch readiness. Full acceptance inventory: `docs/reports/2026-09-30-yleum-release-testing/release-test-matrix-public.json` on `codex/yleum-release-source-tests` at `62ba62e63e49706a841fedb849c27705d5a04168`.

## Global Constraints

- Four discovery responses trigger the write gate; six unchanged completed responses terminate explicit edits.
- Auxiliary notes and managed MAX files cannot satisfy an application-source edit.
- Preserve real backend, schema and test edits, shell edits and deletions; preserve inspection and continuation requests.
- Build, runtime and release proof remain mandatory. Faster failure is not successful generation.
- Never weaken frozen acceptance tests or use mocked effects as physical acceptance.
- Preserve unrelated server working-tree changes. Deploy only the exact pushed revision after successful CI, backup, collision and quiescence checks.
- Keep passwords, tokens, private keys and customer rows out of reports.

## Review Focus

- Notes written before or after the write gate cannot unlock finalization.
- Identical writes and managed-file changes cannot reset the unchanged-edit budget.
- Malformed responses count once and execute none of their rejected tool batch.
- Genuine source deletions and backend/schema/test changes still satisfy progress.
- Running a migration before integration-runtime code does not disable its logger.

### Task 1: Source delta and completed-response budget

**Files:** native agent, generation finalization, focused regression tests. Exact final paths are recorded by the implementer in its report.

**Interfaces:** preserve existing caller signatures and the existing finalization verdict contract.

- [x] Add a regression reproducing notes-only completion of an explicit History edit; prove it fails before the fix.
- [x] Add a regression with four discovery responses and two malformed writes; prove there is no seventh unchanged provider response and no rejected tool execution.
- [x] Reuse the genuine application-source delta predicate for progress and finalization; count malformed responses once.
- [x] Run focused native/finalization regressions, including genuine edits, deletions, inspections, continuation, managed files and identical writes; run lint and type checks.
- [x] Independent review of specification compliance and patch quality before integration.

### Task 2: Migration logging

**Files:** `apps/api/migrations/env.py`; a new narrowly scoped migration/logger regression.

**Interfaces:** retain the Alembic entrypoint and migration behavior; change only existing logger preservation.

- [x] Reproduce a logger disabled by migration logging configuration in the same process.
- [x] Set `disable_existing_loggers=False` on Alembic's existing `fileConfig` call.
- [x] Verify logger behavior and the original order-dependent integration-runtime test; run lint.
- [x] Independent review before integration.

### Task 3: Deliver and resume acceptance

- [x] Integrate reviewed changes and run the full API suite once with pinned pnpm 9.15 and disposable loopback PostgreSQL; report every remaining failure.
- [ ] Check diff, commit and push the candidate; require successful CI for that exact revision.
- [ ] Verify both controller collisions and current runtime identities, encrypted backup, no competing deployment and generation/publication quiescence.
- [ ] Execute the documented canonical production release; verify API, worker, generation worker, Core/Commerce orchestrators, web and billing identities and health.
- [ ] Reproduce one ordinary successful source edit with full proof, then continue the original 67 criteria in disposable QA applications.
- [ ] Produce a PDF preserving all original scenario IDs, actual screenshots and explicit failed/blocked/unrun conclusions. A 24-hour soak and unresolved security/billing gates remain open until physically verified.

## Deferred Launch Gates

Provider-spend provenance/idempotency and generated-app PostgreSQL SUPERUSER access are confirmed separate blockers. The Docker composite-restore reservation mismatch has a proposed patch and physical failing evidence; adaptive rollback remains outside the current MVP and is not silently treated as accepted.

## Verified local evidence

On 2026-10-01 the integrated candidate passed the full API suite: **3190 passed**, with 307 warnings, in 1378.15 seconds. Whole-API Ruff and mypy (257 source files) passed. Focused generation regressions passed (237 tests). Independent reviews approved both patches after preserving rendered Markdown/RST content. Production delivery and all original live acceptance criteria remain pending.
