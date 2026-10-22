# Separate included creations and edits

Final approved commercial terms replace the earlier combined-five proposal:

| Plan | Creations | Edits | Period | Price |
| --- | ---: | ---: | --- | ---: |
| Free v4 | 1 | 1 | Lifetime per account | 0 RUB |
| Pro v3 | 2 | 25 | Paid subscription month | 2,990 RUB |
| Business v3 | 5 | 50 | Paid subscription month | 6,990 RUB |

New plans grant no ruble credit and included work does not debit a wallet.
Existing monetary history and historical paid contracts remain intact. Other
project, publication, team and integration entitlements are unchanged.
New accounts start with a zero service wallet. New split plans cannot create
wallet-top-up invoices or use the optional testing top-up; old pending invoices
still replay and settle exactly once after a plan change.

## Accounting and delivery

- New immutable catalog rows retain existing paid subscriptions, renewals
  and pending orders with their accepted prices and wallet-credit terms.
- Admission reserves one slot in the creation or edit bucket before model dispatch.
  A trusted generated-app ancestor determines the bucket; a large redesign of an
  existing generated app is an edit even if its executor response mode is build.
  Paid reservations serialize by subscription; Free reservations serialize by
  owner. Reserved and consumed slots both count against the corresponding limit.
  Idempotent submission,
  internal calls, automatic repairs and persisted recovery share the run identity.
- A validated accepted build/edit consumes that reservation once. Failed,
  cancelled and non-build terminal results release it. A routing failure before
  dispatch uses the existing terminal handler, including restoration notifications.
- A committed dispatch survives a lost queue/HTTP acknowledgement. Recovery uses
  the persisted billing policy and does not admit another generation or require a
  wallet balance. A later quota/paid-period boundary cannot stop this run.
- Historical paid reservations retain their original period boundaries. Renewal
  supplies new buckets without resetting old running work. Lifetime Free counts
  do not depend on subscription identity, plan changes or project deletion. The
  ledger has no cascading project/run FK. Null paired bounds represent lifetime.
- Current counters are separate from historical spending filters. The cabinet
  shows completed, in-progress and remaining work for both buckets.
- Active Free subscriptions move to v4, removing the one-human-message fence so
  clarification and the included edit can work. Ended Free history is retained.
  A frozen 0/1 creation carryover conservatively preserves the old successful-free
  counter, including historical promotions. It is accounting carryover, not proof
  of an accepted Free run. The old counter continues to exist for legacy users but
  is never re-read as the new carryover. One lifetime edit is newly granted.

## Verification contract

Real disposable PostgreSQL acceptance covers separate bucket exhaustion,
concurrent FK-backed reservations, immutable kind/replay, terminal settlement,
release, lifetime upgrade/downgrade, old-period continuation, renewal, expiry,
deletion, account isolation and stale SQLAlchemy identity maps.
The restricted commerce worker settles existing reservations using only SELECT
and UPDATE; it cannot insert or delete allowance history. The production grant
helper and conditional migration grant use the same rights.

API tests cover both paid checkouts and repeated verified payment notifications:
the wallet remains unchanged and the appropriate two buckets become available.
Free v4 profiles return no legacy one-message blocker; generated-app redesigns
reserve edit quota and cannot consume creation quota by changing executor mode.
Historical subscription renewal and the previous payment-recovery tests remain
part of acceptance. Browser-component tests cover both old/new checkout terms,
current-period quota, error messages and cache refresh after verified payment.

Migration 0089 adds the ledger, frozen carryover and new catalog without rewriting
paid subscriptions, payments or earlier plan terms. Downgrade refuses to discard
allowance history, frozen carryover or referenced new terms. Use the canonical full production release,
including commerce billing-worker rollout and grants, after exact-revision CI.
The release's finalization checks retire the old commerce billing writer before
an idempotent, owned-drain-protected Free normalization. This closes a real
window in which the old worker could downgrade an expired subscription to the
old Free ID after migration. It changes only live legacy Free plan references;
paid and ended records, frozen usage and money stay intact. A failed check keeps
release guards closed. Normal signup migration already runs after old API
recreation; it does not require a separate signup repair path.

No real customer purchase or paid model generation is part of these offline
regressions. Their success is evidence for accounting/recovery behavior, not a
claim that external providers can never fail. A live payment remains a separate
explicit user action. Release revision and live health evidence are recorded in
the delivery checkpoint.
