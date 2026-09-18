# Versioning v4 — baseline (AV00.R) and package G1

Plan: `MAX_Studio_Versioning_Completion_Plan_v4` (18.09.2026). Machine-readable
twin of this file: `versioning-v4-evidence-index.json` (checked by
`apps/api/tests/test_versioning_v4_baseline_contract.py`). Package 1 and the
route-only AV06 stay as the regression baseline; nothing here re-implements them.

## Baseline

| Item | Value |
|---|---|
| Git base | `594cddcf` (local == origin/main at the start of G1) |
| api / worker / generation-worker | source `21cdaf65`, image `sha256:10e102ed…` |
| web | source `ff18c258`, image `sha256:42796b89…` |
| orchestrator | source `2636a2fc` (systemd from `/opt/omnia`, checkout `594cddcf`) |
| Preserved fixes | `18b3550b` resume, `32932948` baked templates, `ff18c258` package 1, `21cdaf65` AV06, `b6579652` verification capacity, `2636a2fc` CHECKs from migrations |

The live run `3cad3a6c` (version 5, 6 routes, 5 clients / 3 visits / 4 840,49)
is a positive example. It proves neither `source_repair`, browser checks nor
production — those are listed as `not_run` in the index, not as done.

## What G1 changed

| Card | Change | Where |
|---|---|---|
| AV19.1 | The API worker re-observes restorations that wait on the controller (`preparing/checking/applying/reconciling`) through the same guarded path as a client GET, with lease + backoff (3→30 s) and a lost-dispatch resend; owner-facing `ready/needs_changes` are never polled. A controller that cannot be observed (offline, 5xx, dropped connection) is no evidence: the row keeps its state and error, only the backoff advances; observations run four at a time, each bounded by the lease. Migration `0063` adds `next_reconcile_at`, `reconcile_lease_until`, `reconcile_attempts`. | `services/restoration_reconciliation.py`, `services/restorations.py`, `workers/run.py` |
| AV19.1 | The controller honours a cancel recorded before preparation without creating a candidate, and stops a running preparation at safe checkpoints (before candidate, after install, before build, before start); cleanup runs, state becomes `cancelled`, never `failed`. A cancel that lands after a running drive's last check makes the controller look once more when that drive ends. | orchestrator `code_restorations.py`, `code_restoration_engine.py` |
| AV23.1 | The production canary retries its own project's DELETE after 409/503 with jittered backoff, a 180 s / 12-attempt deadline, cancels only its own generation/restoration on 409, refuses foreign ids and keeps the primary error. | `ops/production_canary.py` |
| AV06.1 | Route extraction blanks comments/strings, follows `export { x as GET }` and re-exports, treats groups/`@slots` as transparent, normalizes `[param]` while keeping catch-all vs optional distinct, reports `export * from` as a coverage gap (`export * as ns` is a namespace, not a gap), counts multi-declarator / destructured / no-semicolon exports (`export const GET = h, POST = h`, `export const { GET } = handlers`), and counts platform kit routes only when their file changed. A recognised adaptation whose baseline is missing/foreign/unreadable fails with `baseline_unavailable` (no repair, no promotion). Both extractors are held to one golden corpus. | `services/versioning_capabilities.py`, orchestrator `versioning/compatibility.py`, `max_finalization.py`, `tests/fixtures/versioning_v4/routes/corpus.json` |
| AV06.3 (deterministic half) | Stub repair loop: a missing GET → NEEDS_EDIT → real fix → build; a comment-only "fix" is rejected until attempts run out; a provider error aborts without a build. Mutation catalog states which mutations are covered and which wait for AV06.2/AV10. | `tests/test_versioning_repair_loop.py`, `tests/fixtures/versioning_v4/mutations.json` |

## Explicitly not done in G1

- AV06.2: real API responses, ownership and screens (FV033–FV036).
- AV06.3 live: a model run with a fault-injected candidate (needs the isolated candidate of AV10 and a QA-only hook).
- AV23.1: a durable cleanup job that survives the runner's death (FV018) and reservation observation separate from compute (FV019).
- AV07+: business decisions (the phone-derived email is still `legacy_unapproved` by convention only).

## Operating notes

- A restoration cancel now reaches `cancelled` within ~30 s on a healthy stand without any client polling; a controller outage leaves each row exactly as it was (state, error) while `next_reconcile_at` advances with backoff, and the worker logs `restoration.reconcile_backlog` when the oldest pending observation is older than 120 s.
- `restoration_reconcile_poll_seconds` (5) and `restoration_reconcile_lease_seconds` (60) are settings of the api worker.
