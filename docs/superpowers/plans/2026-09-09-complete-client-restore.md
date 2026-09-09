# Complete client restore implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Follow the ownership boundaries below; the primary agent alone owns integration, commits, migrations and deployment.

**Goal:** Deliver preparation, evidence, explicit draft activation and recovery of a historical MAX version without replacing current business storage.

**Architecture:** A separate durable API operation drives an isolated orchestrator candidate. Preparation never activates an old checkpoint. Activation switches verified code, preserves live volumes and current identity, records its intent before external effects and reconciles the actual outcome before committing history. Unsupported data semantics produce an actionable preparation report rather than a false compatibility claim.

**Tech stack:** Existing Python 3.12+ API/orchestrator, PostgreSQL 16, Docker, Next.js/React, TanStack Query. Reuse the existing repository, ProjectVersion and lifecycle fencing primitives.

**Spec:** [Accepted client versioning design](../specs/2026-09-09-client-versioning-design.md).

## Global constraints

- Preserve current business rows, files, credentials and the published pointer during draft restore.
- Do not reinterpret the existing full-checkpoint `restore` operation.
- No real customer/model generation is a deployment test. Use isolated disposable fixtures.
- Instructions are not enforcement. A green build or schema hash alone is not a data-safety proof.
- Missing historical data contracts or ambiguous conversions must be reported; never manufacture values or silently import an old database.
- No automatic publication. The chosen restored snapshot must be pinned on explicit publication.
- Binary and empty source files and executable modes are part of the immutable source bundle.
- Complete tests, independent review, commit, push, documented production deployment and health verification before claiming delivery.

## Ownership and interfaces

- API worker: restoration model, migration, schemas, service, router, runtime transport, detached Git helpers and their tests. No change to legacy MAX restore admission.
- Frontend worker: restoration API/types, durable hook/panel, history/Shell integration and focused tests.
- Primary: orchestrator candidate/data boundary, common generation/publication admission, publication provenance, release verification and serial delivery.
- Independent reviewer: complete stable diff and negative safety scenarios; read-only.

Public endpoints:

```text
POST /projects/{project_id}/restorations
  {target_version_id, expected_draft_snapshot_id, idempotency_key}
GET /projects/{project_id}/restorations -> {items, enabled}
GET /projects/{project_id}/restorations/{operation_id}
POST /projects/{project_id}/restorations/{operation_id}/apply
  {report_revision, expected_draft_snapshot_id, idempotency_key}
POST /projects/{project_id}/restorations/{operation_id}/cancel
```

States: `preparing`, `checking`, `ready`, `needs_changes`, `applying`, `completed`,
`cancelled`, `failed`, `reconciling`. A report carries its revision, exact/adapted
mode, changes, retained data, unavailable features, warnings, blockers and next
actions. Only a confirmed external activation supplies `applied_snapshot`.

Internal runtime identity:

```text
operation_id, workspace_id, project_id, owner_id, expected_source_head,
target_commit_sha, planned_commit_sha, fencing_epoch,
files: [{path, content_base64, mode}]
```

Internal endpoints use `/workspaces/{workspace_id}/code-restorations/{operation_id}`
with `prepare`, `apply`, `cancel` POST actions and GET status. Completed observation
must identify candidate, planned source commit and applied fence. API SQL owns the
canonical project HEAD; a detached Git commit alone is not activation.

## Task 1 — durable API and user operation

Files: `apps/api/src/omnia_api/{models,schemas,services,routers}/restoration[s].py`,
`services/restoration_runtime.py`, `services/repo.py`, migration and tests.

- [ ] Write failing tests for cross-owner denial, identical retry/different envelope,
  detached binary source preservation and no HEAD mutation during preparation.
- [ ] Implement durable operations and row-locked admission. Every status and mutation
  rechecks ownership; no fake GenerationRun is created for a restore.
- [ ] Implement prepare/status/apply/cancel transport with strict observed identity.
- [ ] Test lost response, repeated apply, SQL commit failure and one restored version.
- [ ] Integrate the same project-row admission into generation and publication.

## Task 2 — isolated preparation and storage enforcement

Files: new `apps/orchestrator/src/omnia_orchestrator/{schemas,services}/code_restoration*.py`,
data-compatibility/runtime helpers, existing machine backend/adapter/boundary seams,
router and tests.

- [ ] Write failing source-bundle tests: traversal, collisions, duplicate paths,
  secret paths, oversized payloads, binary/empty bytes and mode preservation.
- [ ] Implement private durable requests/journal and independently owned candidate
  resources; reserve them without reclaiming an active production environment.
- [ ] Clone current data for checking; never expose live credentials or integrations.
- [ ] Build the historical code/dependencies in the candidate and retain the artifact.
- [ ] Implement a non-owner runtime boundary with controller-owned authentication;
  old credentials and open writer connections cannot bypass activation fencing.
- [ ] Assess actual read/write/delete contracts. Additive fields are retained;
  JSON replacement, unknown values, foreign-key cascades and semantic conversions
  require specific evidence or an adaptation report.
- [ ] Exercise real PostgreSQL grants/authentication and two-user CRUD on isolated
  fixtures; verify old writes retain a newly added surname and new rows.

## Task 3 — activation and recovery

Files: orchestrator restoration coordinator/backend, API restoration service,
machine adapter and publication evidence integration.

- [ ] Write intent before switching code; recheck expected HEAD, schema/contract,
  candidate digest, fence and active generation/publication.
- [ ] Stop former writers, activate only code and dependencies, keep live database
  and declared business volumes; never run an historical migration on live startup.
- [ ] Confirm the actual running revision before completing the operation and audit.
- [ ] Inject failure before switch, after switch and before API commit; reconcile
  without duplicate history or blindly repeating an external action.
- [ ] Preserve current code on candidate failure and restore prior code on activation
  failure without restoring prior business data.
- [ ] Make explicit publication consume restored provenance and its exact snapshot;
  check public data independently from draft preparation.

## Task 4 — complete client flow

Files: `apps/web/src/lib/api/restorations.ts`, `lib/use-max-restoration.ts`,
`components/max/MaxRestorationPanel.tsx`, history/Shell and tests.

- [ ] Test preparation without an image, F5 discovery, two clicks, lost POST and
  late responses from a different project before implementing the hook.
- [ ] Keep observation outside the panel; closing the panel does not cancel work.
- [ ] Show verified consequences, explicit draft activation and actionable blocked
  preparation; only completed updates the HEAD cache.
- [ ] Test desktop and 390px layout, keyboard/focus, errors, current/published labels
  and a separate publication action against the pinned restored snapshot.

## Task 5 — full acceptance and delivery

- [ ] Run the accepted spec's 16 scenarios against applicable real isolated services;
  record unavailable cases honestly and resolve blockers before enabling activation.
- [ ] Independent full-diff review; fix concrete P0/P1/P2 findings.
- [ ] Complete API/orchestrator/web regression gates and inspect intended diff only.
- [ ] Push reviewed code. Guard production against active work, back up, deploy exact
  revisions using `/opt/omnia` / compose `full` / host orchestrator service.
- [ ] Verify service revisions, readiness, actual restoration flow on disposable
  resources and preservation of unrelated production identities/data.
- [ ] Record delivery evidence and all identified remaining risks; synchronize docs.

This plan is an execution checklist, not evidence that restore is ready.
