# Account mail and personal Pro offer implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Primary integrates Git/migrations/deployment serially.

**Goal:** Give the user one payment/email test for 1 RUB without changing the existing Pro and complete branded account-security mail.
**Architecture:** Independent account and pricing slices share existing transactional mail and billing infrastructure; primary integrates one additive migration and one verified release.
**Tech Stack:** Existing FastAPI/SQLAlchemy/PostgreSQL, Next.js/React, SMTP and YooKassa. No new dependencies.
**Spec:** `docs/superpowers/specs/2026-10-27-account-mail-personal-offer-design.md`.

## Global constraints
One user's one-off purchase only; public Pro remains2990RUB. No real charge or actual user password changes by agents. No tokens/passwords in logs/outbox. Keep GreenSMS changes from base3e7799ed. Preserve other edits; each edited file has one owner.

## Review focus
- Changed/stale principal during email confirmation must not expose or log out another account.
- SMTP failures must not roll back completed credentials or acknowledge an unconfirmed address change.
- Two concurrent confirmation/checkout requests must not consume twice.
- Payment-test receipt and notice agree; no subscription/credit/limit field changes.
- Mobile data-loading states must settle before viewport assertions; forms must remain scrollable with keyboard-sized windows.

## Task1: Account backend and mail (API worker)
Files: routers/auth.py; new routers/account_credentials.py, schemas/account_credentials.py, services/account_credentials.py, services/account_security_email.py, models/account_security.py; services/email_templates.py; focused auth/account mail tests.
Interfaces: exact HTTP/schema contracts in spec; `account_credentials.router`; `run_account_security_email_forever()` owns its async engine. Models `EmailChangeChallenge`, `AccountSecurityEmail`.
- [ ] Add meaningful RED tests for branded reset, current-password change, two-address confirmation and completed notices.
- [ ] Implement the smallest guarded transaction helpers and worker; preserve reset/phone/OAuth contracts.
- [ ] Run targeted auth/email/session tests, PostgreSQL rollback/race cases, Ruff and mypy. Report exact metadata to primary.

## Task2: Personal purchase backend and billing UI (pricing worker)
Files: new models/billing_offer.py and services/billing_offers.py; affected billing/payment router/schema/lifecycle services and tests; existing billing components/API modules and tests. Do not edit auth UI, shared templates or migration.
Interfaces: `issue_billing_purchase_offer(...)` and `/api/billing/purchase-offer` plus dedicated POST `/api/payments/test` as specified.
- [ ] Reproduce ordinary fulfillment must remain untouched and no-offer denial in RED; add concurrent/retry/cross-user assertions.
- [ ] Implement persisted account-bound payment-only test, one success, safe recovery and no subscription side effects.
- [ ] Show the personal quote only to the eligible account; explicitly preserve the current subscription; preserve public plans.
- [ ] Run focused billing/payment/renewal/recovery and frontend tests, lint/types. Report exact DDL.

## Task3: Account UI (account UI worker)
Files: components/account/AccountProfile.tsx, AccountSecurity.tsx; new lib/api/account-credentials.ts; `(auth)/confirm-email-change/page.tsx` and client component; focused tests. Existing reset page may receive only scoped referrer/security UI fixes.
- [ ] RED tests for forms, password mismatch/current-password errors, OAuth-only recovery, pending/completed/expired confirmation and account switching.
- [ ] Implement controls in the existing style, pending/error states, accessible labels, explicit confirm button and memory-only fragment handling.
- [ ] Run focused Vitest, TypeScript and ESLint. Avoid sending real email or changing credentials.

## Task4: Integrate and deliver (primary)
Files: frozen migration0098 after0097, model/router registration, general worker integration, CI critical PostgreSQL gates, docs and scoped export integration only if required.
- [ ] Review model metadata and create the additive migration with data-preserving downgrade guards; validate one head and upgrade/downgrade against disposable PostgreSQL.
- [ ] Run independent read-only review of the complete relevant diff; fix concrete findings.
- [ ] Run required full gates and diff/data/secret sanity. Use a settled local/browser UI with representative data at390px, tablet and desktop.
- [ ] Commit intended files; push configured upstream; wait exact-SHA greenCI; canonical production deployment and service/public-health/smoke proof.
- [ ] Identify current account privately and issue only its one-off offer; verify public2990/personal1, unchanged existing subscription/limits and no other owner access. No provider charge.
- [ ] Record sent Olga clarification separately; save cropped proof without unrelated private data. Report completed flows, exact revisions and manual payment/real-credential-test limits.

Final clarification: user explicitly chose payment/email test only; the existing Pro, periods, limits and packages must remain unchanged. Earlier first-invoice-discount design is superseded.
