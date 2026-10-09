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

## Adjacent generation repair guard

The same release also corrects a false repeated-read stop. Previously the global
action count survived successful edits of the same file, so the fourth read could
be refused even after three real source changes and different compiler errors.

Only the read signatures of the exact safe canonical file are reset, after a
successful write/edit proves a different before/after content hash. The Cell
executor produces the full-content receipt after persistence succeeds, including
for files longer than its 16,000-character read output. Empty content and an absent
file are distinct. Failed writes, no-op writes, another file, traversal and invalid
receipts do not earn a reset. Safe path aliases share the unchanged-read limit.

For other injected executors without receipts, the loop uses a bounded fresh
before-read and full returned after-content; truncated/unavailable evidence earns
no reset. Once the executor demonstrates valid receipt support, the extra
before-read is omitted. The unchanged-action, exploration, write-pressure and step
limits remain in force. This is not proof of a successful generated feature;
full build, runtime and promotion checks remain mandatory.
