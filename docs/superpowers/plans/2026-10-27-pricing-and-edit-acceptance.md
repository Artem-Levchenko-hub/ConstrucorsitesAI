# Pricing and full edit acceptance implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sell the starter package for 490 RUB and new Pro subscriptions for 2990 RUB, and verify scenarios 3.1–3.2 with browser evidence.

**Architecture:** Publish a new immutable Pro catalog version and a new starter-package quote version. Preserve prior subscriptions, pending checkout snapshots, receipts and credits. Use the existing isolated QA coffee app for five cumulative edits, data-preservation checks, atomic failure preservation and draft/publication separation.

**Tech Stack:** FastAPI/SQLAlchemy/Alembic/PostgreSQL, pytest, Next.js, the authenticated in-app browser.

**Spec:** User request dated 2026-10-27; `C:/Users/Артём/Downloads/yleum-testovye-scenarii.html`, scenarios 3.1 and 3.2.

## Global constraints

- New starter checkout: 490 RUB, five edit-only units; new Pro checkout: 2990 RUB/month, two initial generations and 25 edits.
- Existing paid contracts and already-created payment snapshots retain their accepted terms. No payment execution, silent repricing of renewals, fabricated MAX identity or destructive customer-data changes.
- Browser tests use QA Север 25.10 (`c56a7913-a6f3-4b0e-b72b-a22860097f24`); preserve its source and control order. Test bots were explicitly identified as disposable by the user earlier.
- Git, CI, deployment and health verification remain serial. Do not deploy during an active generation, restoration or publication.
- Save screenshots and expected/actual results for every accepted step; report external blockers and untested branches separately.

## Review focus

- Pending one-ruble payments must replay their saved amounts after prices change.
- Active one-ruble Pro contracts must not receive an unapproved renewal-price increase.
- One successful edit consumes one unit; rejection/cancellation must not charge twice.
- Five edits must preserve earlier edits, navigation, basket arithmetic and the saved order.
- A failed or unpublished update must leave the previous public version and business data usable.

### Task 1: Versioned commercial prices

**Files:** billing model seeds; payments package catalog; new migration after 0096; existing checkout/catalog tests and new migration-preservation tests.

**Interfaces:** Existing `/api/billing/plans`, `/api/payments/config`, `/api/payments` and `/api/payments/subscription`; no response shape changes.

- [x] Change consumer-facing checkout expectations first; prove RED on the old amounts.
- [x] Add Pro v4 at 2990 RUB, retain historical Pro v3 at 1 RUB, and starter quote v3 at 490 RUB.
- [ ] Test real migration preservation, saved-order replay, catalog/checkout consistency and duplicate webhook settlement.
- [ ] Run appropriate API tests, lint/type checks and exact-head CI; obtain independent review.
- [ ] Commit, push and deploy through the canonical release driver; verify live amounts and health.

### Task 2: Full browser acceptance

**Files:** Screenshots and state receipts under the task evidence directory outside the checkout; Russian HTML report under `output/`.

**Interfaces:** User-facing editor, version history/restoration, publication flow, MAX business settings and the actual public mini-app.

- [x] Baseline: 22 edits; four drinks; 190×2+250=630 RUB; empty-name rejection; control order survives reload.
- [ ] Five sequential accepted edits; capture submitted/running/completed states, remaining units, cumulative preview changes and data preservation.
- [ ] Verify failed/cancelled generations preserve the preceding accepted source, preview, public version and data; verify credit reservation release. User explicitly excludes manual version rollback from the product.
- [x] Establish a public baseline and verify it opens in a real authenticated MAX session.
- [ ] Make remaining draft edits, verify old public/new draft separation, publish and verify the new public/MAX result.
- [ ] Exercise publication rejection/failure safely and prove the preceding public version remains available.
- [ ] Produce a Russian evidence report linking screenshots and exact expected/actual results; mark only fully verified cases passed.

## Checkpoint

- Base: `7d6a236b`; clean existing linked worktree reused on `fix/pricing-and-edit-acceptance-20261027`.
- Browser sessions are already authenticated in Yleum and MAX for business. QA app is not published and has no bot connected yet.
- First edit accepted after 6m26s: caption `Забрать с собой` → `Кофе с собой каждый день`; edits 22→21; preview order 630 RUB survives reload.
- Baseline published at `https://qa-sever-25-10-5ab9f4.apps.yleum.ru`; test Coffee bot `se13771437_bot` points there and remains published. Previous test URL was `https://qa-zametki-20-10-51aaaa.apps.yleum.ru`.
- Real MAX account already authenticated. Mini-app opens successfully; MAX actor does not see the preview-owner order. Separate MAX control order `QA MAX 27.10 — до обновления`, 630 RUB, saved at 10:38 Moscow.
- Edit 2 submitted: filter price 190→200, preserve first caption and existing order amounts. It is currently running; do not submit edit 3 until terminal verification.
- User ruling: no manual version history/rollback; leave `MAX_CUSTOMER_VERSIONING=false`. Acceptance requirement is atomic success versus preservation of the preceding version on failure.
- Pricing tests: baseline6 PASS; intended RED3 failures; GREEN7 PASS; scoped Ruff and mypy PASS. Independent reviewer found a stale migration-parent assertion (fix with RED→GREEN) and requested a realistic pending Pro v3 payment fixture (promote to required coverage).
- Live service revision before changes: `dc5aaf055537982a174a627d3d2c94a135356919`; HTTP health green. Local/GitHub base is `7d6a236b`. SSH attempts on Windows direct and configured jumps time out/close; testing the previously established strict loopback transport is next. No deployment attempted.
- Local test interpreter: `.qa-tools/api-timeout/Scripts/python.exe`; 13 selected unit/static migration checks pass. Independent review's migration-parent finding reproduced RED and fixed GREEN; pending-checkout preservation coverage now uses real frozen provider parameters and renewal-consent fields.
- The established strict loopback SSH helper works. Read-only Core inspection succeeded: checkout `7d6a236b`, no deploy lock, services healthy. Keep this transport and the documented canonical release driver; do not revert to direct Windows SSH attempts.
