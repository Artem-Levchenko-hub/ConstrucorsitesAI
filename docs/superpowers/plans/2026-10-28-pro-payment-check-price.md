# Pro payment-check price implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Pro at 1 RUB/month in production for the owner's payment checks.

**Architecture:** Add immutable Pro v5 with the existing Pro entitlements. Switch the public catalog only; retain v3/v4 subscriptions, renewal terms, payment snapshots and receipts. Both website pricing and checkout already consume the server catalog.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic/PostgreSQL, pytest; canonical production release driver.

**Spec:** User request, 2026-10-28: urgently set Pro to 1 RUB and deploy to production for tariff-payment testing.

## Global constraints

- Pro v5: 1.00 RUB/month, two initial generations and 25 edits, unchanged limits.
- Historical Pro v4 remains 2990.00 RUB; historical contracts and pending payments remain byte-for-byte unchanged.
- Other tariffs and starter packages retain their current prices.
- Primary owns all edited files, Git, migration and deployment. Reviewer is read-only.
- No payment execution; owner completes the real checkout after deployment.

## Review focus

- Catalog, provider amount and receipt agree on 1.00 RUB for a new Pro checkout.
- Pending Pro v4 payment keeps its frozen 2990.00 RUB submission and renewal consent.
- Switching the catalog does not alter existing subscriptions, credits or receipts.
- Migration has one head and downgrade refuses referenced v5 terms.

### Task 1: Catalog and migration

**Files:** `apps/api/src/yleum_api/models/billing.py`, new `apps/api/migrations/versions/0099_pro_payment_check_price.py`, catalog/checkout/migration tests.

**Interfaces:** `/api/billing/plans`, `/api/payments/subscription`; response shapes unchanged.

- [x] Add price and preservation coverage; observe old-price failure.
- [x] Add frozen Pro v5 migration and matching model seed; update current-price expectations.
- [ ] Run focused API tests, Ruff/mypy and the exact-revision full CI; independent defect-first review.

### Task 2: Delivery

- [ ] Commit intended files, push current branch to configured `origin/main` without force.
- [ ] Confirm no competing production work; deploy the green pushed revision with `infra/release/deploy-prod.sh`.
- [ ] Verify exact live revision, service health, public catalog and visible Pro price. Leave checkout to the owner.

## Checkpoint

- Worktree clean and fast-forwarded from 11dedffb to 1b817800 before edits.
- Current public Pro is v4 at 2990 RUB; historical v3 is 1 RUB. New v5 avoids changing accepted terms.
- Local: price RED confirmed; 9 non-DB tests GREEN, changed-file Ruff and billing-model mypy pass. DB tests require CI: this workspace has no local PostgreSQL listener/configuration.
- Independent review: two stale test expectations corrected; final review has no findings.
- Live baseline freshly checked: 1b8178001d75391c7b6dd1d357d393f688198fe6. Exact target CI and canonical deployment remain required.
