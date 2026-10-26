# Yleum transactional email implementation plan

**Goal:** Four branded Russian event emails using existing SMTP and verified billing.
**Architecture:** Pure renderers + MIME transport; durable notification outbox;
payment transaction enqueues, independent background sender delivers.
**Spec:** docs/superpowers/specs/2026-10-26-transactional-email-design.md

## Constraints and review focus

Preserve verification TTLs, payment authority and rollback, data isolation, TLS,
all unrelated changes. No real payment or mass historic notification for testing.
Review: concurrent claims; ambiguous SMTP outcome; stale period; HTML escaping;
mail outage independence from billing. Existing branch/worktree reused at4ee95c15.

## Tasks

- [x] RED: renderer and MIME tests; implement shared branded templates, CID logo,
  optional HTML transport and auth code integration; run auth/SMTP suite.
- [x] RED: real PostgreSQL outbox tests for unique events, transactional rollback,
  period windows and stale suppression, claims and delivery failures. Add model,
  additive migration0094, payment enqueue and billing notification sender.
- [x] Verify focused suite, lint, mypy, migration graph/DDL, logo byte identity,
  independent branch review and rendered preview; address material findings.
- [ ] Commit/push, wait exact SHA CI, deploy canonical production revision after
  quiescence; verify roles, migration, configured SMTP and safe scoped delivery.

## Progress

- Branded mail renderers, transport and durable billing queue implemented. Preview
  uses actual templates; four variants, tabs/mobile/source DOM passed.
- Expanded authorized scope: one-time phone registration through SMS Aero,
  optional email proof afterwards, normal email/password login and recovery.
- Isolated PostgreSQL run: 273 passed, one SQL-comment parser test failure fixed;
  subsequent restricted-role/runtime/payment run: 72 passed. No production DB,
  SMS, payment or external AI request used by these tests.
- Web:89 focused tests, typecheck and lint passed (one existing apple-icon warning).
  API: full Ruff and mypy --platform linux src passed (345 source files).
- Independent review found no remaining P1/P2. SMS Aero support inquiry accepted
  by SMTP; mandatory phone flag remains disabled until an approved sender and
  immediate code delivery are available. No paid sender or contract accepted.
- First exact-SHA CI exposed Next PageProps defaults (unit/typecheck alone did
  not generate route contracts) and an incomplete payment quote user double.
  Removed whole-props defaults, ran next typegen plus tsc and 12 auth UI tests;
  quote test now uses a real phone-verified User, six quote tests passed.
