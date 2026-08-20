# Production E2E Hardening Design

Date: 2026-08-20

## Context

The current production surface is healthy, but the repository does not yet provide enough evidence to call a newly deployed builder revision production-safe end to end.

Observed on 2026-08-20:

- `https://constructor.lead-generator.ru/api/health` reports the API, Postgres, Redis, worker heartbeat, deploy control plane, and preview storage as healthy.
- `https://constructor.lead-generator.ru/web-health` reports the web process as healthy.
- The permanent MAX canary and its unauthenticated webhook boundary are healthy.
- The current off-host smoke probes those existing resources every five minutes, but it does not create a new project or exercise generation.
- Health responses do not identify the deployed source revision, so a green check cannot prove which code is running.
- Repository HEAD is `a7c4fc227855bb5e4cb044194b5f31e39ab83344`. The latest documented production deployment is `59bf18ca`; the exact live revision still has to be captured before rollout.
- Project memory migration `0046_project_memory` is additive, but the production Compose default currently enables the feature globally.
- A clean local baseline passes the targeted orchestrator and web suites. The API acceptance baseline has four stale tests whose implicit flag assumptions no longer match the intentional code defaults. Production Compose explicitly keeps those optional reference gates off.
- The web lockfile is reproducible with the CI/Docker pnpm version (`9.15.0`), while an unpinned local pnpm 11 install rejects the frozen lockfile because pnpm 11 no longer reads the repository's package-level override configuration.

## Goals

1. Make every release prove the exact revision running in the web, API, worker, and orchestrator paths.
2. Make the production builder update reproducible from a clean checkout.
3. Turn the current static smoke into a bounded synthetic golden path that exercises a fresh generation.
4. Ship project memory dark first, enable it for a dedicated canary account, and retain an immediate no-data-loss kill switch.
5. Define a release and rollback procedure that is safe for active generations and migration `0046`.
6. Leave a repeatable release gate in the repository rather than relying on an operator's memory.

## Non-goals

- This work does not add product features or redesign the builder UI.
- It does not configure third-party production accounts such as SMTP, YooKassa, or the MAX Partner cabinet.
- It does not expose internal LLM gateway or orchestrator credentials to GitHub Actions.
- It does not downgrade migration `0046` during rollback. The schema is additive and remains in place while code and flags roll back.
- It does not deploy to production without an explicit final confirmation from the owner after the branch, CI, and release evidence are ready.

## Chosen architecture

### 1. One public-safe release identity

Use `OMNIA_RELEASE_SHA` as the shared environment variable for deployable services. Its normalized value is either a 7-40 character lowercase hexadecimal Git revision or `unknown`; invalid values are reported as `unknown` rather than reflected verbatim.

Expose the normalized revision as `release_sha` in:

- API `/health` and `/api/health`;
- web `/web-health`;
- orchestrator `/health`.

The web health route becomes runtime-evaluated so the container's environment, rather than the image build host's environment, is authoritative. The API readiness probe captures the orchestrator health payload and adds a public-safe `dependencies.orchestrator_release_sha` field. This lets the off-host monitor verify the host systemd service as well as the Compose services without publishing a new orchestrator endpoint.

The API and worker reuse the same image and receive the same `OMNIA_RELEASE_SHA` from Compose. The worker heartbeat value is changed from a timestamp string to a small JSON document containing the timestamp and normalized release. API readiness reports `dependencies.worker_release_sha`; legacy timestamp-only heartbeats remain healthy but report `unknown` during a rolling upgrade.

Production injection rules:

- the release command obtains the revision once with `git rev-parse HEAD` from the validated checkout;
- it rejects a dirty checkout and a revision other than the reviewed release candidate;
- it exports `OMNIA_RELEASE_SHA` to Compose for web, API, and worker;
- it updates the existing orchestrator `EnvironmentFile` with the same revision through an atomic, permission-preserving helper before restarting the systemd unit;
- it never writes secrets or a production `.env` into the repository.

The production smoke requires all four revisions to be non-`unknown` and equal. Equality detects partial rollout and drift; the release gate additionally requires them to equal its explicit `EXPECTED_RELEASE_SHA`. The scheduled smoke compares them with the protected production-environment variable `PRODUCTION_EXPECTED_RELEASE_SHA`, which the release procedure updates only after the candidate is approved. During the first rolling deployment only, the operator may run the old compatibility probe until every service has restarted; the final release gate never accepts mixed or unexpected revisions.

### 2. Reproducible web package manager

Keep pnpm `9.15.0`, matching the existing Dockerfile and CI. Add the exact `packageManager` declaration to `apps/web/package.json` and make repository commands use Corepack or `pnpm@9.15.0` explicitly. Do not move the override block: commit `0d28e11f` deliberately put it in `package.json` for the current Docker build.

CI verifies all of the following from a clean install:

- the active pnpm version is `9.15.0`;
- `pnpm install --frozen-lockfile` changes neither the lockfile nor the manifest;
- typecheck and the existing web tests pass.

### 3. Explicit optional acceptance gates

The code defaults for `acceptance_gauntlet_reference_gate` and `reference_ceiling_enforced` stay on because their flip was an intentional product milestone. Production Compose remains the production policy and explicitly passes both as off until a separate reference-corpus rollout.

The four stale acceptance tests are changed to set optional reference gates off when testing the base acceptance path. Separate tests continue to prove the enabled path. A deployment-policy test asserts that the production Compose defaults remain off, preventing either a code-default or test-default change from silently changing production behavior.

### 4. Project-memory canary semantics

Add `PROJECT_MEMORY_CANARY_USERS`, a comma-separated set of user UUIDs, alongside the existing global `USE_PROJECT_MEMORY` switch. A single helper owns the policy:

```text
memory enabled for request = USE_PROJECT_MEMORY
                          OR current user is in PROJECT_MEMORY_CANARY_USERS
```

Both memory reads and memory revision compilation use this helper. The decision is based on the authenticated `GenerationRun.user_id`, not on a caller-supplied value. Invalid allowlist entries are ignored and logged without exposing their raw content.

The production rollout starts with:

```text
USE_PROJECT_MEMORY=false
PROJECT_MEMORY_CANARY_USERS=<dedicated canary user UUID>
```

This keeps all normal users on the pre-memory behavior while allowing the synthetic account to prove write-then-read behavior. Turning off both values immediately stops memory injection and compilation without deleting revisions.

The truth table is covered by unit and route/service tests:

| Global flag | User in allowlist | Read memory | Compile revision |
| --- | --- | --- | --- |
| false | false | no | no |
| false | true | yes | yes |
| true | false | yes | yes |
| true | true | yes | yes |

### 5. Disposable generation canary

Add one repository-owned Python canary client that uses only the public API. It authenticates with a dedicated, verified, funded production canary account using `PRODUCTION_CANARY_EMAIL` and `PRODUCTION_CANARY_PASSWORD`. The API session cookie remains in memory and is never printed.

One run performs this bounded path:

1. Read web and API health and record the expected release identity.
2. Log in to the dedicated account.
3. Create a uniquely named disposable `max_miniapp` project.
4. Submit a deterministic, sufficiently detailed build prompt with a unique idempotency key and `skip_clarify=true`.
5. Poll `GET /api/projects/{id}/generation` with a hard overall deadline and bounded backoff until `completed` or a terminal failure.
6. Assert response mode is `build`, the project now points at a snapshot different from the seed snapshot, and the latest snapshot contains generated files.
7. Start or inspect the runtime, obtain the signed MAX preview session, and verify the returned bootstrap URL is HTTPS and on the configured preview host. Follow its expected `307` relative redirect with an in-memory cookie jar and require the final project page to return `200`, without logging the signature, cookie, or page body.
8. Submit one deterministic edit prompt with a second idempotency key; assert a second completed run and a third snapshot.
9. Confirm web, API, worker, and orchestrator release identities remain equal to `PRODUCTION_EXPECTED_RELEASE_SHA` and to the value observed at the start.
10. Delete the disposable project in `finally`, which also tears down the runtime. Cleanup failure makes the canary fail and prints only the project UUID for manual recovery.

The canary emits structured, secret-redacted step results and elapsed times. It never prints cookies, passwords, signed preview URLs, response bodies that may contain user data, or model prompts beyond the fixed repository-owned canary text.

There are two workflow layers:

- the existing five-minute smoke remains cheap and verifies availability, dependencies, release consistency, permanent MAX canary health, and the webhook authentication boundary;
- a new serialized generation-canary job runs on manual dispatch first and then once daily after production credentials are provisioned and the owner approves scheduling. Missing credentials are a hard configuration failure, not a skipped green job.

The workflow uses `concurrency` to prevent overlapping paid generations and a job timeout above the client's own deadline. It creates/updates one generation-canary incident issue, separate from the availability-smoke issue, and closes that issue after recovery.

### 6. Release gate and rollout

The repository release gate produces a machine-readable evidence directory containing command, revision, start/end time, exit code, and redacted output for:

- clean-tree and exact-revision checks;
- Python lock sync for API and orchestrator;
- API lint/typecheck/test suites, including migrations from a clean database;
- orchestrator lint/typecheck/test suites;
- exact-pnpm frozen install, web typecheck, unit tests, and production build;
- Compose config validation;
- production image builds under temporary revision tags;
- local container health and migration smoke.

No production mutation occurs in this gate.

After the branch and CI are green, the production sequence is:

1. Ask the owner for explicit production confirmation and state the exact revision and rollback target.
2. Capture the live revision, service state, image IDs, Compose/nginx configuration, orchestrator environment file, and a Postgres backup.
3. Confirm no `pending`, `running`, or `cancel_requested` generation exists.
4. Set project memory dark with only the dedicated canary UUID allowed.
5. Validate Compose and build/test temporary revision-tagged images.
6. Deploy API first so Alembic applies `0046`, then worker and web; update/restart orchestrator with the same release identity.
7. Require local host health, off-host health, exact revision equality, permanent smoke, and one successful disposable generation canary.
8. Run ten successful canary-account build/edit cycles with no project-memory regression before enabling memory globally. Global enablement is a separate small configuration change with the same health and generation checks.
9. Enable the daily scheduled generation job only after its first manual production run succeeds.

“Ten successful cycles” means ten consecutive terminal `completed` build/edit pairs, each with new snapshots and healthy signed previews, with no intervening memory, release-identity, cleanup, or runtime failure. A failure resets the counter.

## Failure handling and rollback

- Before migration: keep the current production containers and systemd service untouched.
- During a rolling restart: if health or revision consistency fails, stop the rollout and restore previous image tags and the previous orchestrator checkout/environment revision.
- After migration `0046`: disable both project-memory controls, restore previous image tags and orchestrator code, and leave the additive table/column in place. Do not run Alembic downgrade in production.
- If generation fails but static health is green: keep memory dark, restore previous images, verify the permanent canary, and retain the disposable project's UUID until teardown is confirmed.
- If cleanup fails: retry the idempotent public project delete; if it still fails, record the UUID in the incident and remove it only through the documented operator path after checking ownership and active runtime state.
- Never use `docker compose down -v`.

## Security and cost controls

- Production credentials exist only as protected GitHub Actions secrets and in the operator's secret store.
- The canary account has only the minimum product entitlement and balance needed for its fixed daily budget; no administrator role is required.
- Login and prompt calls retain normal production rate limiting.
- Preview signatures and auth cookies are redacted by construction, not with a post-processing regex alone.
- The daily job is serialized and limited to one build plus one edit. Manual dispatch uses the same concurrency group.
- Health release metadata contains only a Git revision and no hostname, environment file, image digest, or secret.

## Verification strategy

Implementation follows test-driven changes:

- unit tests for release normalization and the project-memory policy truth table;
- API tests for health metadata, legacy/new worker heartbeat parsing, and orchestrator release propagation;
- web route tests for runtime release metadata;
- orchestrator health tests;
- deployment-policy tests for Compose release injection and dark optional gates;
- canary client tests with a fake HTTP server covering success, timeout, terminal failure, release drift, unsafe preview URL, and cleanup failure;
- workflow/static checks proving schedules, concurrency, secret names, and incident separation;
- the full local release gate from a clean worktree;
- one independent code review after all suites pass;
- one manual production canary only after the owner's explicit deployment confirmation.

## Exit criteria

The builder release is considered stable end to end only when:

- the reviewed branch is clean, pushed, and green in CI;
- a clean checkout passes the repository release gate;
- migration `0046` has applied successfully without destructive changes;
- web, API, worker, and orchestrator report the same expected non-`unknown` revision;
- all static health dependencies are green;
- the permanent MAX canary remains healthy;
- the disposable build-and-edit canary completes, creates the expected snapshots, serves a signed safe preview, and cleans itself up;
- rollback inputs were captured before deployment and the rollback procedure was not needed or was itself successfully proven;
- project memory remains limited to the canary account until ten consecutive green cycles have completed.
