# Safe nullable-column changes during publication

## Acceptance and evidence

The owner must be able to create orders in the published QA coffee app, generate a real feature, confirm a safe schema change, republish, and retain all historical orders and their original amounts while creating new orders. Current production is safely blocked with `publication_migration_required`: the UI button is explanatory only and no executor exists. The live public target is Kubernetes; the source cell is Docker. Existing accepted app commit is `6ef296d6f4b8de7d0f2440d298266ec0f628e157`.

This delivery supports only adding nullable columns without defaults to existing ordinary public tables. It reuses `resolve_additive` and `authorize_plan` unchanged. Docker production targets, new tables, removals, renamed objects, changed types/defaults/constraints/indexes/RLS/policies/triggers and changed data contracts remain unsupported. Client SQL is never accepted.

Existing orders to preserve in actual MAX: 630 RUB at10:38, 650 RUB at13:04, 650 RUB at19:26, and270 RUB at19:35 Moscow. The unpublished preview also has a250 RUB scheduled order at20:10; its pickup/comment survive reload.

## Rulings and alternatives

- Keep the schema guard. Manual SQL or raw source edits outside controller proof are not alternatives.
- Add a bounded Kubernetes-only path rather than claiming all migration kinds work.
- Verify a logical backup by restoring into a new network-none PostgreSQL Docker sandbox. Never restore into the live PostgreSQL instance or mount its volumes into a verifier.
- Use a durable controller intent plus the exact PostgreSQL transaction ID and `pg_xact_status` for unknown COMMIT outcomes. Do not add a receipt table to the business schema: the existing full schema digest would then differ from the accepted source. Record the XID before sending any DDL and record validated postconditions before sending COMMIT. Never infer successful execution from the presence of columns alone.
- No automatic restoration of an older backup over later orders.
- External model request3 remains `ambiguous/indeterminate` after HTTP422. No further paid model request, financial rewrite, or source-lease bypass is authorized by this controller work.

## Trusted identities and serialization

API owner authorization and existing `load_publication_evidence` select the exact accepted candidate. Store only the safe evidence projection; no runtime env, tokens, credentials, request transcript or customer values in operation journals.

Bind owner/project/snapshot/Git commit, workspace source digest, build digest, source fence, active release/epoch, data contract, live/target schema digests, target namespace/StatefulSet/PVC UIDs, bound claim, database name/OID and PostgreSQL system identifier. A pod UID and backend PID/start time identify a particular SQL session; storage identity remains separately verifiable across a pod restart. Revalidate immediately before an effect.

Acquire publication project lock before source workspace operation lock consistently. Reject an active publication; a durable migration in preparing/applying/reconciling blocks a new publication and must be represented in deployment drain. Physical generation/restoration/deletion effects serialize through the source lock; API admission must reject an active migration under its normal project lock before reserving new generation work.

Source catalog must be observed from the verified idle source backend and its full schema digest must match the accepted sealed schema. A later source/fence/snapshot or target release/PVC/schema change makes a prepared plan stale. Never treat a mutable preview URL as an accepted source.

## Backup and catalog proof

Use a production catalog observer extracted from the existing PostgreSQL integration test. Observe ordinary tables, column order/types/nullability, constraints/indexes/RLS/policies/triggers and absence of event triggers. Keep all resolver guards.

Capture a consistent read-only exported PostgreSQL snapshot. Read catalog and original-column row counts/digests from this snapshot, run bounded `pg_dump --snapshot` of the business DB, then release the read-only session. Concurrent live inserts are allowed and are not compared to an earlier backup as if they were lost.

Limit catalog/dump/fingerprint bytes, rows, execution time and verifier resources. Store backup encrypted and authenticated with a purpose-separated key derived from the existing private controller key, a fresh nonce and bound AAD. No plaintext backup in logs or public/task artifacts. Enforce disk/project budgets; unsupported capacity fails before DDL. Keep backup retention/cleanup bounded and never delete another operation's artifacts or live data.

Restore the backup into an exact-owned new sandbox using the actual running immutable PostgreSQL image. This first version accepts only one non-template database named postgres and default tablespaces. Exported-snapshot business data plus observed globals without role passwords must reproduce the entire current pg_dumpall schema context. Handle only the expected bootstrap CREATE ROLE postgres conflict explicitly; all other restore errors abort. Globals are outside the business snapshot and require unchanged repeated observations under controller locks.

No network, live PVC, env/secret mounts, host mount, Docker socket, privilege or unbounded resources. Validate restored catalog and snapshot row digests, not only SELECT1. Then compare the sandbox full schema digest with bound live schema, execute the exact canonical plan in the sandbox and compare its full schema with the accepted target using the existing unchanged inspector. Both full comparisons are required before live DDL; unsupported policy/function/view/global changes fail here. The production observer must include policy expressions, stable role names, actual persistence and inheritance/partition identity. Confirm termination and remove only exact-owned ephemeral resources; cleanup uncertainty blocks preparation. Return a receipt bound to backup hash, storage identity and verifier image/digests.

## Transaction and recovery protocol

Use bounded framed PostgreSQL sessions over the existing authenticated Kubernetes exec transport. A read timeout is not proof that a server command stopped.

1. Begin, set finite statement/lock/idle-transaction timeouts, force synchronous commit, take the project advisory transaction lock and lock affected tables. Read the exact XID, server/session identity, catalog and original-column row fingerprints in that same session.
2. Validate all bindings and `authorize_plan`; fsync controller intent containing that XID before sending canonical additions.
3. Execute only the canonical plan inside this transaction. Reobserve catalog and original columns/row counts under the same table locks. On any mismatch, rollback.
4. Fsync validated pre-COMMIT evidence, then send COMMIT. On confirmed commit, verify target schema through the normal inspector and seal the migration receipt.
5. On lost acknowledgement or restart, revalidate storage/system identity and unchanged PostgreSQL timeline/history, then query only the recorded xid8 without truncation. `committed` plus bound pre-COMMIT evidence and target schema confirms the effect; `aborted` means failed with no changes; in-progress/unknown remains reconciling. Never resend DDL or COMMIT while outcome is unresolved. A missing/old/unverifiable XID or changed timeline fails closed; pg_xact_status is bounded historical evidence, not an indefinite receipt. A durable effect_possible flag excludes new effects regardless of a blocked/failed display status until a proven outcome clears it. Startup restores durable exclusion before admitting effects and deployment drain examines journals.

The private durable journal has versioned states preparing, prepared, applying, reconciling, applied, failed, stale and blocked. Prepared plans have a finite lifetime. Repeated prepare/confirmation are idempotent; GET only projects state. Startup recovery resumes observation of existing intents rather than creating new work.

## Owner API and UI

The API forwards typed accepted evidence to authenticated orchestrator routes. Endpoints: POST prepare, GET latest/status, POST confirm with operation ID, exact plan hash and explicit intent. Owner/project checks and no-active-generation/restoration checks remain in API; internal controller checks the physical identities again. SQL is never a request field.

The UI replaces its explanatory button with prepare/poll, a human-readable added-field summary and verified-backup result, then a separate explicit confirmation. Editing/source changes invalidate old confirmation. After applied, use the existing publication runner and keep both its preflight and activation schema checks. On failure, preserve the preceding public release and report a safe error.

## Verification gates

- Existing resolver/plan baseline:103 local PASS;3 cases deselected because an oversized test ID exceeds the Windows32767-character environment limit. The complete cases must run in Linux CI.
- Fast failing tests for currently missing endpoints/executor; unit negatives for forged/stale ownership/fence/PVC/backup, confirmation, idempotency and lock/recovery states.
- Real disposable PostgreSQL16 tests for three columns, concurrent inserts/lock timeout, rollback, delayed commit ACK and exact-XID recovery. No production fallback in tests.
- Isolated sandbox restore/integrity/cleanup tests and byte/time limits.
- Independent full diff review, repository API/orchestrator/web checks and exact-head CI. Then focused commit/push, merge according to repository policy, canonical production driver and exact serving revision/health.
- Repeat actual MAX old-record comparison, confirmed migration, republication, old and newly created scheduled/immediate orders after re-entry. Preserve per-attempt evidence and do not claim generation completeness from a green build.

## Ownership

Primary owns API/UI, dependency/CI files, design, integration, Git and deployment. Transport/observer/verifier worker owns its new orchestrator IO/catalog/backup modules and tests. Controller worker owns migration operation/service/schema/router integration and controller tests. Review is read-only. Shared-file changes require explicit coordination.

## First-version operational bounds

The adapter supports the locked Kubernetes33.1.0 client, literal-IP API endpoints and literal-IP explicit proxies only. Without an explicit configured proxy, nonempty ambient HTTP(S)_PROXY/ALL_PROXY variables are rejected before connection; the environment is never rewritten. TLS validation/SNI remain unchanged. The verifier uses the exact running immutable PostgreSQL16 Alpine image and UID70, matching the current published runtime. At most four private encrypted backup artifacts per project and64 operation journals are retained; capacity exhaustion fails closed before new DDL. There is no automatic backup deletion policy in this delivery. The exact managed migration ledger is validated as an ordinary controller-compatible heap with expected columns/ownership/security before any administrator row read; it remains outside business ALTER authorization and inside old-row preservation proof.
