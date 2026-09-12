# Client code restoration

MAX Studio restores a historical source version into a **new current draft**.
It keeps the current business database. Publication is a separate explicit action.
The screenshot identifies a version visually; immutable Git source and activation
evidence identify the code that actually runs.

## Customer flow

1. Select a historical ready version and prepare restoration.
2. Wait for the compatibility report. Preparation builds an isolated candidate
   against a copy of current data; it does not replace the current draft or public app.
3. Read the retained fields, unavailable actions and warnings. Choose
   **«Сделать текущей в редакторе»** to apply a ready candidate.
4. The confirmed outcome creates a new version in history. Closing the panel,
   refreshing the page or a lost HTTP response does not start another activation.
5. Test the draft, then publish explicitly. Its public database is independent;
   republishing preserves writes made by public users.

When compatibility needs changes, **«Адаптировать и восстановить»** saves the
explicit request, confirms cancellation and submits one adaptation generation.
It preserves any typed composer draft. Refreshing never submits a generation;
an explicit retry reconciles the exact cancelled operation and reuses its
idempotency key. An active generation, publication or changed draft blocks submission.
A structured reference binds the selected historical version and expected draft.
The API provides verified historical source and the accepted compatibility report
in an immutable private bundle. Historical contents are reference data, never
higher-priority instructions. The agent adapts historical code to current data,
preserving values, meaning and ownership. Unsupported semantic changes remain a
reported limitation; a prompt alone cannot guarantee lossless conversion.

The report also records whether the inspected application database has rows:
`empty`, `present` or `unknown`. This is an actual existence check on the isolated
current-data copy, not a table-size estimate. Unsupported objects or a failed
inspection return `unknown`. This observation does not classify external files,
other databases or all project data. New writes may arrive after inspection.
An empty database never permits a reset or bypasses compatibility checks. Both
empty and populated compatible applications use ordinary restoration without AI;
incompatible applications require explicit adaptation.

An adaptation run requests database protection during agent bootstrap, using the
server-saved adaptation reference. The controller checks the current schema and
retained volumes, stops existing writers, and journals the transition to restricted
database access. It retains the current database and business volumes. The agent
receives an executor only after the controller confirms protection; legacy runtimes
and unsupported schemas fail before adaptive tools run. Interrupted transitions
keep the original generation lease for reconciliation and never restore owner
credentials as a shortcut. Publication remains separate.

## Runtime boundary

- API operations live in `restorations` (migration `0061_code_restorations`).
  A project admits one active restoration; generation, deletion and publication
  use the same admission boundary.
- Preparation binds the project, owner, source/target commits, source inventory,
  runtime identity and controller epoch. Binary/empty files and executable modes
  are preserved in the activation bundle.
- The candidate uses separate owned resources and a current-data copy. Its
  outbound proxy is stopped before importing business data. It receives no live
  integration credentials. A bounded dump excludes the private signing identity.
- The app connects as a non-owner PostgreSQL role. Controller-owned authentication,
  grants, row policies and signed request identity enforce data access. No
  administrative credential is supplied to historical application code.
- Application switches replace the code volume, not database or business volumes.
  The controller records intent before effects and reports the observed running
  revision. An uncertain response is reconciled before API history moves.
- Failed activation restores the prior code at the admitted epoch, retaining
  current rows. It never imports an old database checkpoint.
- Cold resume validates retained storage and immutable runtime identity. Missing
  data volumes are not silently recreated. Pending migrations keep their original
  generation lease until recovery proves the outcome.

After restoration, agent instructions describe the protected database capability.
The controller accepts the standalone command
`omnia-db apply .omnia/data-contract.json`. It admits supported additive declarations
under the current generation lease and source revision; arbitrary shell SQL does
not obtain owner privileges. Initial support includes nullable scalar columns and
directly owned UUID tables. Applied SQL and policy rotation have durable recovery
stages. Cancellation reconciles admitted work before releasing the lease.

## Supported boundary and remaining risks

This is a bounded compatibility system, not a proof for arbitrary SQL or business
semantics. A preparation report must stay actionable when a case is unsupported.

| Case | Current behavior / remaining limit |
| --- | --- |
| Runtime | Next.js, a saved pnpm lockfile, one standard `next start` service and pg8.22 transport are verified. Other runtimes, custom launch chains and background services need adaptation. |
| Dependencies | The saved lockfile is rebuilt. Missing registry packages, incompatible native libraries or unavailable images can prevent preparation. This is not a historical image restored bit for bit. |
| Data shape | Simple scalar columns and known ownership relationships are checked. SERIAL defaults, custom types/triggers, partitioning, unknown ownership and cyclic relationships require additional support. |
| Hidden fields | Values remain in the database but old screens may stop filling them. Re-enabling the new screen does not invent missing values. |
| Deletes and relationships | Deletes that could lose hidden or dependent data are blocked and reported. They need compatible business logic before becoming available again. |
| JSON and meanings | Supported top-level JSON keys are preserved. Nested unknown structures, unit changes and changed field meanings cannot be inferred safely from SQL types alone. |
| External actions | Payments, notifications, messages and other actions already performed are not undone by code restoration. |
| Business files | Unknown persistent files mixed into source require classification. This flow never replaces an unclassified business-file volume. |
| Capacity and time | Preparation needs an additional isolated runtime, bounded dump space and build time. Capacity failure leaves the current app intact. |
| Storage retention | Temporary code archives are removed after terminal outcomes or cancellation. Cleanup failures are logged and retried by observation; historical mounted code/images and receipts still need operational retention/capacity management. |
| Legacy history | Missing Git source, missing lockfiles and unsupported historical contracts need preparation/adaptation. A screenshot alone cannot reconstruct source. |
| Adaptation | Text context is bounded to 128 files / 256 KiB, excluding secrets, lockfiles and generated assets. Adaptation is a new generation subject to ordinary verification; it is not a guaranteed automatic semantic conversion. |
| Infrastructure failure | A missing/corrupt volume, hardware loss or exhausted disk still requires tested backups and operator recovery. Container isolation shares the host kernel. |
| Security proof | Tested ownership, SQL grants and signed identity reduce known bypasses. They are not a universal security audit of arbitrary generated code. |

## Verification and operations

Local unit tests cover durable admission, exact identities, retries, lost responses,
crash reconciliation, schema constraints, protected lifecycle and UI refresh/mobile
states. The CI orchestrator job has a disposable PostgreSQL 16 database named
`restoration_policy_test`; tests refuse an arbitrary database name. The Node/pg
bridge has a separate real database fixture.

`apps/orchestrator/scripts/smoke_code_restoration.py` exercises a disposable real
Next.js/Docker/PostgreSQL app: v2→v1→v2, two actors, writes after preparation,
hidden surname retention, lost-response observation, cold resume and the next
generation's additive migration. `--publication` additionally verifies explicit
public releases and independent public writes. It prepares the second candidate
before allocating the public fixture, so at most two runtime bundles overlap.
Only script-created private QA identities are accepted and cleanup checks ownership.
This runtime canary does not claim that a paid model generation was tested.

Enable `MAX_CODE_RESTORATION_ENABLED=true` through the documented `full` Compose
API environment only after acceptance. Deploy API migration before workers and
verify the API, workers, web and host orchestrator's actual release identities.
Gate all unfinished generations (including queued work), Cell operations, leases
and restorations; preserve server edits and back up SQL/config/controller metadata.

When reverting platform code after 0061, do not point an old Alembic installation
at the new revision and expect its startup migration to work. The migration is
additive; recovery must retain schema history and use a verified compatible old
API entrypoint without rerunning old Alembic. Keep the maintenance gate closed
until service health **and worker heartbeats** confirm the intended recovery.

Release-specific test counts, exact Git/image revisions and deployment health
belong in the delivery evidence, not in this operating contract.
