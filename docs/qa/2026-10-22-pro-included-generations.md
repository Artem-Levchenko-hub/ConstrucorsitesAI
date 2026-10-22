# Pro: five included generations per paid month

Approved commercial terms: 2,990 RUB/month, five generations in total across
the account's applications. Creation and edits share the allowance. These runs
do not debit the ruble wallet. There is no 500 RUB subscription credit in the
new Pro version. Project/publication slots, Free and Business are unchanged.

## Accounting and delivery

- Pro v3 is a new immutable catalog row. Existing v1/v2 subscriptions, renewals
  and pending orders retain their accepted prices and wallet-credit terms.
- Admission reserves one slot before model dispatch, serialized by subscription.
  Reserved and consumed slots both count against five. Idempotent submission,
  internal calls, automatic repairs and persisted recovery share the run identity.
- A validated accepted build/edit consumes that reservation once. Failed,
  cancelled and non-build terminal results release it. A routing failure before
  dispatch uses the existing terminal handler, including restoration notifications.
- A committed dispatch survives a lost queue/HTTP acknowledgement. Recovery uses
  the persisted billing policy and does not admit another generation or require a
  wallet balance. A later quota/paid-period boundary cannot stop this run.
- Historical reservations retain their original paid-period boundaries. Renewal
  supplies a new period, without resetting old running work. Deleting a project
  cannot restore spent allowance: the ledger has no cascading project/run FK.
- The current paid-period counter is separate from historical spending filters.
  The cabinet shows completed, in-progress and remaining included generations.

## Verification contract

Real disposable PostgreSQL tests cover six admissions, concurrent FK-backed
reservations, replay, terminal settlement, release, old-period continuation,
renewal, expiry, deletion, account isolation and stale SQLAlchemy identity maps.
The restricted commerce worker settles existing reservations using only SELECT
and UPDATE; it cannot insert or delete allowance history. The production grant
helper and conditional migration grant use the same rights.

API tests cover a new Pro checkout and repeated verified payment notifications:
the wallet remains unchanged and five included generations become available.
Historical subscription renewal and the previous payment-recovery tests remain
part of acceptance. Browser-component tests cover both old/new checkout terms,
current-period quota, error messages and cache refresh after verified payment.

Migration 0089 adds the ledger and v3 catalog without rewriting subscriptions,
payments or earlier plan terms. Downgrade refuses to discard any allowance
history or referenced v3 contract. Use the canonical full production release,
including commerce billing-worker rollout and grants, after exact-revision CI.

No real customer purchase or paid model generation is part of these offline
regressions. Their success is evidence for accounting/recovery behavior, not a
claim that external providers can never fail. A live payment remains a separate
explicit user action. Release revision and live health evidence are recorded in
the delivery checkpoint.
