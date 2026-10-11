# Catalog and legal correction for amoCRM moderation

Baseline production: `164a40abc93f2a784351e704cdcdeb810f024364`.

Implemented in this change:
- Immutable new public terms: Free v3 (one project, one publication, one lifetime generation message), Pro v2 (2990 RUB/month, 500 RUB credit), Business v2 (6990 RUB/month, 1500 RUB credit).
- Existing paid subscriptions and payment snapshots retain historical terms. Only live historical Free subscriptions move to the current Free plan.
- Anonymous pricing comes from the active server catalog; failed requests show unavailable status instead of invented prices. Monthly credit is described as conditional on successful payment.
- Privacy and personal-data consent describe actual amoCRM data flows; new document version 2026-10-11. Prior deployed pages are archived without rewriting acceptance records.

Verification before commit:
- Web: 1081 tests pass; TypeScript and ESLint pass.
- API: Ruff passes; mypy with the production Linux target passes (282 source files).
- Disposable PostgreSQL: 53 migration, billing, payment, entitlement and renewal tests pass. Temporary internal-only Docker database; fsync disabled during the run because durability/recovery is outside these tests. No production database used.
- Independent review: no remaining introduced findings; stale payment fixtures were corrected and legacy renewal is covered separately.
- Local Windows standalone build compiled and generated pages, but packaging failed with EPERM creating symlinks. Successful Linux CI build remains a release gate.

Still required for delivery:
1. Exact revision CI, push/merge, coordinated production release, new backup and preservation checks, exact service identities and fresh smoke.
2. Confirm public prices and both legal pages on production; update and republish the demo policy through the normal product flow.
3. Complete actual restricted moderator access and two independent CRM-account isolation evidence.
4. Deliver refreshed PDF, HTML/TXT, separate 1188x616 JPGs and ZIP manifest from actual live evidence.

Live demo rechecked on 2026-10-11: MAX form -> one CRM lead -> changed stage -> own MAX history -> close/reopen. No personal contact details are stored in this report. Public OAuth was previously verified for officeyleumru; this is distinct from unproved cross-account isolation.

UI consent refusal is verified. Server enforcement against a direct request is NOT established: see `consent-enforcement-followup.md`. Do not claim general enforcement or moderation readiness until its compatible rollout and tests are completed. Payment acceptance is not enabled by this catalog release.