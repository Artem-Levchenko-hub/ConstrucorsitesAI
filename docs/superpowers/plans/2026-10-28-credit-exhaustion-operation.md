# Generation-credit exhaustion operation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set one explicitly identified owner's available generation and edit units to zero for Pro exhaustion testing.

**Architecture:** Add account-serialized, append-only administrative debit/reversal operations. Debit the current available units of specific grants in all four buckets. Shared inventory supplies both displayed balances and normal generation admission. Preserve original grants, subscriptions, payments, wallet and accepted runs; future purchases and renewals remain usable. No new public endpoint.

**Tech Stack:** FastAPI/SQLAlchemy, Alembic/PostgreSQL, pytest, the canonical production release driver, native operational service invocation.

**Spec:** User request dated 2026-10-28 identifying the target account privately. No target email or credentials belong in this repository.

## Global constraints

- Only the named account is changed. Require an active administrator actor and a metered active target.
- Serialize with the existing billing-account lock; reject held reservations or nonterminal runs.
- Immutable original grants and financial facts must remain unchanged. Never fabricate a generation, payment or accepted snapshot.
- Operation UUID is an idempotency key, including an operation that debits zero units. Replay must not consume later purchases.
- Reversal appends a separately authorized record; no deletion or rewriting of debit history.
- Primary owns migration, migration tests, this plan, Git, live operations and deployment. Worker owns credit models/services/inventory and service tests. Reviewer is read-only.

## Review focus

- All four current buckets reach zero; both initial and edit admission reject normally with no provider call.
- Account and grant bindings, administrator permission, zero-op idempotency and lock-wait freshness hold.
- Later purchases/renewals and another account remain unaffected.
- Reversal restores only original rights still applicable to their grant period; replay never re-debits.
- Database forbids UPDATE/DELETE of administrative operations and refuses downgrade with evidence.

### Task 1: Credit operation and migration

**Files:** credit models/registration, `services/generation_credits.py`, new `services/generation_credit_admin.py`, `0100_credit_adjustments.py`, focused service/migration/head tests.

- [x] Inspect live target without mutation: active Pro v5, 2 initial/25 edit, no package units, held reservations or running jobs.
- [x] Implement debit/reversal journal and shared inventory; focused cleanup and regression coverage.
- [ ] Freeze migration schema; verify append-only trigger and preservation/downgrade on disposable PostgreSQL.
- [ ] Independent review, focused local checks and full exact-revision CI.

### Task 2: Delivery and authorized operation

- [ ] Commit intended files, push configured upstream, canonical FULL deployment and exact-revision health proof.
- [ ] Invoke one idempotent debit for the privately identified account; capture before/after and unchanged financial/grant hashes.
- [ ] Confirm zero available units and normal exhaustion decisions, without a paid generation or test payment.

## Checkpoint

- Base/local/live: 76a672d1f89c20dedceeee93718bf079272db0fd; worktree clean before this task.
- Safe existing administrative debit capability was independently confirmed absent. Direct grant mutation would break immutable payment replay; fake consumed holds would falsify accepted-generation facts.
- No live credit mutation has occurred. Private baseline receipt lives outside the repository.
- Local verification: 15 pure/head checks pass; changed-file Ruff, strict mypy (three source modules), exact model/frozen DDL comparison and diff sanity pass. PostgreSQL is unavailable locally; 28 service cases plus two migration scenarios require the exact-revision CI.
- Independent review closed stale-identity-map handling after account-lock waits: refresh mutable subscription/plan/hold/payment state and re-check actor authorization. Five two-session regressions cover the race; final source review has no findings.
