# Generation state consistency design

Date: 2026-09-29. Baseline: `7e43f698244e039a4b12be9d86728c6853ca2cbe`.

## Objective and evidence

Complete recoverable generations against a coherent source/runtime/database contract, preserve the last accepted app on failure, and expose actionable safe failures. This implements the user's request for a full two-week audit and a working native amoCRM app. Execution and production delivery are already authorized; the parent owns live browser acceptance.

The refreshed inventory covers 105 unique retained runs between 2026-09-15 12:09:56 UTC and 2026-09-29 12:09:56 UTC, merging current database records and available backups. Snapshot archives cannot prove coverage of rows created and deleted between backups. External audit artifacts retain per-run evidence and uncertainty; historical errors are not attributed to today's revision.

Three live acceptance witnesses distinguish separate boundaries:

- `eca3582e`: portable prompt replacement discarded the live integration guide; source migration feedback was absent. Released fixes compose the final prompt and guard initial/repair turns.
- `b763ecac`: early checks saw a complete workspace but final candidate assembly used a diff against a different baseline and lost an existing pending SQL file. Released `7e43` assembles the exact complete checked tree, including deletions.
- `ebbd63fd`: the corrected candidate reached PostgreSQL. An isolated, network-disabled cold clone of the empty product database failed with SQLSTATE `42P01`: pending `0002` references `max_users`. The controller deliberately excludes core `0000/0001`; product seed exports still suggest those tables exist. The generic journal reconciliation message obscured the actual SQL error. Original storage remains untouched.

## Authoritative state

Run ID, project ID, accepted snapshot/commit, fixed accepted-plus-seed migration baseline, workspace ID/revision/fencing epoch, configuration, runtime revision, and controller database identity have distinct owners. Existing content-addressed build/runtime/release proofs and publication permits remain authoritative. Final source must equal the checked workspace; configuration writes remain serialized against an active run. Retry starts a fresh run and transcript against retained candidate source; it cannot convert failed or unknown external effects into accepted evidence.

Do not introduce a competing ledger merely to duplicate these identities. Close demonstrated gaps and test their composition. Preserve existing checks for immutable accepted/applied SQL, data-preserving adaptation, source deletions, stale permits, and cancellation.

## Product database contract

The portable product database is separate from the managed MAX core. `DATABASE_URL` never points to core data. `getMaxUser()` supplies an authenticated subject; products can store this subject as text and scope every query by it. A TypeScript `maxUsers` export or legacy SQL file does not establish a physical product table. Product migrations must explicitly own any local prerequisite they require. Native integration history should use the managed authenticated SDK, not a duplicate local CRM mirror or signing secrets.

Keep legacy source compatibility. Do not create core tables in product databases, replay skipped core migrations, alter accepted SQL, fabricate journal entries, or rewrite generated app files. Make the portable guide and seed annotations explicit. Controller early dependency feedback must use a real read-only catalog and ordered pending SQL; uncertain syntax must defer to PostgreSQL rather than inventing a proof.

## SQL failure and recovery contract

Controller SQL diagnostics retain only a PostgreSQL SQLSTATE from bounded stderr; never expose SQL, data values, connection strings, or raw exception text. A terminated psql command is distinguishable from an operation still running or whose outcome is unknown. Known transactional source errors become a versioned repairable marker. Journal/checksum/adoption conflicts, missing controller state, timeouts, cancellation, and unknown effects remain terminal/reconciliation cases.

The finalizer returns a known source error to its existing bounded same-run repair loop. A changed source revision and remaining repair/finalization budget are mandatory. Rebuild and runtime proofs are invalidated and recomputed. No error is relabeled complete. An early source/catalog dependency rejection performs no SQL mutation. A general SQL rehearsal must use an isolated database; `ROLLBACK` on the original is not a universal no-side-effect sandbox.

## Audit follow-through

Independently verify suspected generic release-proof and deployment-drain gaps before changing them. A missing promotion permit already blocks publication and must not be reported as a proven bypass. A deployment lock alone does not fence user admission; a durable drain must close that race if no existing admission fence does so. Deployment must retain both canonical compose files and the known production database target.

## Acceptance

Meaningful tests cover missing prerequisite versus an existing/local prerequisite, safe SQLSTATE versus secret-bearing stderr, actual PostgreSQL rollback and journal immutability, source repair within one run, unknown/timeout non-replay, complete filesystem snapshots/deletions, stale identity and publication refusal. Full CI precedes canonical deployment; effective API/worker/orchestrator revisions and core pins must match the release contract.

Then the parent uses ordinary UI Retry, verifies a genuinely accepted app and preserved source/data, uses ordinary integration attachment if needed, publishes, and tests invalid fields, pending/double submission, one CRM lead with comment/email/source, owner-scoped history after reload, and live CRM status refresh. No manual generated-source patch or technical rescue prompt substitutes for this acceptance.
