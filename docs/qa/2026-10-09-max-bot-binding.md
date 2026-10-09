# Exclusive MAX bot binding

Ordinary connection must not attach the same identifiable MAX bot to another
project. This change does not transfer ownership, change any existing production
binding, or delete credentials/data.

## Contract

- PostgreSQL `uq_max_integrations_bot_id` reserves each non-null bot ID across
  verified, active and error bindings. Error status does not silently release it.
- Connect and verify reject a missing/empty provider bot ID before saving it.
- Another project's committed binding returns HTTP 409 `max_bot_already_bound`,
  without exposing its project or owner. The wizard explains that the bot must
  first be explicitly disconnected there.
- A concurrent connection returns HTTP 409 `max_bot_binding_busy` without waiting
  for provider latency to exceed PostgreSQL's command timeout. A later same-project
  retry remains supported. Project row locking preserves public lifecycle lock
  order; the unique constraint remains protection against other database writers.
- The reservation is flushed before unsubscribe/configuration. A rejected
  replacement leaves old credentials/webhook untouched. Failed unsubscribe rolls
  back the reservation; runtime sync failure after the DB commit preserves the claim
  for a same-project retry. This is not an atomic owner-transfer workflow.

## Release precondition

The serial production owner must run
`docs/qa/2026-10-09-max-bot-binding-preflight.sql` in the existing authorized DB
context under the documented drain. It returns counts only. It has not been run
against production by the implementing agent.

If duplicate groups exist, stop delivery and resolve them through explicit
authorized disconnection. Do not choose a winner, remove rows, clear credentials,
or restore tokens automatically. Legacy NULL bot IDs are counted and preserved;
they cannot be newly created through connect/verify.

Migration 0081 locks the table and repeats the assessment transactionally before
installing UNIQUE. It aborts with a sanitized count if duplicates exist, without
updating records. Downgrade removes only the constraint.

## Verification scope

New regressions use isolated PostgreSQL 16, synthetic provider responses and
runtime transport stubs. They cover own/foreign projects, all binding statuses,
direct DB uniqueness, concurrent claims/replacements, stale conflict prechecks,
missing IDs, commit/sync/unsubscribe failures, actual public lifecycle locks and
12-second provider latency with the production 10-second DB command timeout.
The real migration is executed against clean and duplicated predecessor tables;
credential preservation and its downgrade are checked.

The complete Web suite, lint/type checks, focused API suite, independent review
and full CI receipts belong to the release handoff. They do not prove a live
ownership transfer or a successful CRM/MAX provider transaction. Production
activation and live acceptance remain serial-owner responsibilities after the
active generation window closes.
