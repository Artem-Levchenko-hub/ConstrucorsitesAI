# MAX Project Cell: extensible main stack

The approved scope is Next.js/React/TypeScript with Node22 and pnpm9.15.0 first.
The agent may add compatible libraries, edit package/lock files, install system
userland and use a necessary helper process. It is not a universal-framework
product or a fixed package list. No model generation is part of deployment proof.

## Reachable behavior

- The existing owner-canary provider advertises the capability over bootstrap.
  New empty cells seeded from the trusted template (including byte-identical
  pristine project-directory copies) receive `.omnia/cell.json`
  with frozen `pnpm install`, separate fast checks, one final build/test, and
  `pnpm start`.
  The package test script requires real `tests/*.test.mjs`; no passing dummy test
  or product page is supplied. The agent creates `src/app/page.tsx` and product UI.
- Existing no-manifest source stays on the legacy path. An intentional manifest
  transition retires the credentialed legacy draft before the first shared-source
  write. Controller-owned identity then forbids a silent downgrade if the guest
  deletes its manifest or the operator disables the provider.
- The machine has project-only source/home/data volumes and a persistent rootfs.
  It never mounts the old credential-bearing agent home. Root can install guest
  packages, but has no NET_ADMIN/NET_RAW/SYS_ADMIN, Docker socket, host paths,
  privileged mode or platform credentials.
- A separate trusted namespace guard permits only the public dependency proxy
  and explicitly authorized traffic. The proxy resolves and validates every
  destination and connects to the validated numeric address. It denies private,
  metadata, host, configured public platform addresses and cross-cell targets.
- The pristine MAX core owns managed PostgreSQL credentials and signing secrets.
  The gateway owns reserved `/api/omnia`, `/api/max`, `/auth`, `/__omnia` routes.
  Product servers receive trusted user/project/epoch headers, not signing keys.
  The portable machine separately receives `DATABASE_URL` plus matching `PG*`
  variables for a dedicated project PostgreSQL sidecar with its own volume.
  That instance is isolated from the managed MAX core and is fully
  admin-controlled by the agent inside the project machine. It shares the
  trusted guard's network namespace and listens only on loopback, so even a
  PostgreSQL `COPY PROGRAM` process inherits the core/private-network deny.
- Proof invalidation follows controller-owned source, dependency, schema,
  manifest, environment, toolchain and profile digests. A read-only command does
  not invalidate a green build; changed dependencies invalidate every later proof.
  Fresh completion checks require the actual
  product root, signed MAX bootstrap/session, protected data, negative auth and
  exact active lease epoch. Segment continuation cannot revive invalidated proof.
- Each role has one aggregate deadline: bootstrap installs once, fast checks run
  during editing, and the coordinator owns one final build/test. API build is
  600s and shell 300s; whole preview
  apply reserves870s for work including capture/readiness plus30s for cleanup,
  with930s transport timeout. Blocking helpers inherit remaining work time:
  they cannot open a fresh readiness/quiesce window at the end of the request.
  A timeout drains in-flight Docker effects before cleanup/fencing, even if the
  drained helper fails. An unavailable Docker daemon can delay that safe drain;
  a transport failure never counts as proof.

## Production configuration and rollout

Use the existing `/opt/omnia` checkout, `full` production compose at
`apps/llm-gateway/deploy/full/docker-compose.yml`, and the host
`omnia-orchestrator.service`. Do not deploy the development `infra/` stack.
The verified production host service reads `/opt/omnia/apps/orchestrator/.env`
with WorkingDirectory `/opt/omnia/apps/orchestrator`. The older
`/opt/omnia-runtime/.env.orchestrator` is a different unused file, not a symlink;
preserve it. Inspect the effective unit/environment again before editing.
Preserve all existing owner-canary lists and
secrets. No expansion to other users is authorized by this feature.

Build the base from the approved immutable Node22 image and pin the output image:

```sh
cd /opt/omnia/apps/orchestrator
docker build --build-arg NODE_BASE=sha256:APPROVED_NODE22_IMAGE_ID \
  -f scripts/Dockerfile.project-machine -t omnia-project-machine:main-stack .
docker build --build-arg BASE_IMAGE=sha256:APPROVED_PYTHON_IMAGE_ID \
  -f scripts/Dockerfile.project-machine-guard -t omnia-project-machine-guard:main-stack .
```

Use real immutable IDs, never the placeholders above. The base includes Node/npm,
pnpm9.15.0 and Python3 for internal command/archive helpers. Node's bundled trust
roots bootstrap HTTPS apt before installing distro CA certificates; TLS and apt
signature verification remain enabled. Guard source is copied with deterministic
readable modes. Neither image includes project or platform secrets.

Add these **host orchestrator** settings (JSON syntax for the CIDR list):

```dotenv
CELL_MACHINE_ENABLED=true
CELL_MACHINE_BASE_IMAGE=sha256:APPROVED_BUILT_MAIN_STACK_IMAGE_ID
CELL_MACHINE_GUARD_IMAGE=sha256:APPROVED_BUILT_GUARD_IMAGE_ID
CELL_NETWORK_POOL=10.253.0.0/16
CELL_MACHINE_DENIED_CIDRS=["170.168.72.200/32"]
CELL_PROFILE_VERSION=docker-owner-cell-resources-v2
CELL_ACTIVE_MACHINE_CPU_CORES=2
CELL_ACTIVE_MACHINE_MEMORY_BYTES=2147483648
CELL_PROJECT_POSTGRES_CPU_CORES=0.15
CELL_PROJECT_POSTGRES_MEMORY_BYTES=268435456
CELL_HELPER_CPU_CORES=0.2
CELL_HELPER_MEMORY_BYTES=134217728
CELL_MANAGED_CORE_CPU_CORES=0.35
CELL_MANAGED_CORE_MEMORY_BYTES=805306368
```

The pool was free in the scoped 2026-09-03 inventory; recheck all Docker networks
and host routes immediately before rollout. New networks use bounded explicit
/28 allocation and refresh/retry Docker overlap conflicts. Never prune other
networks or change daemon address pools. The public host IP is upstream NAT and
is not discoverable from host interfaces; explicitly deny every relevant public
platform IP in trusted configuration, including future addresses.

API/worker require the changed code; capabilities and the dedicated project
PostgreSQL runtime environment come from the orchestrator. Preserve existing `PROJECT_CELL_*`,
`WORKSPACE_PROVIDER=docker_owner_canary`, `DOCKER_OWNER_CANARY_ENABLED=true`,
owner allowlists and pinned PG/Redis/backup settings. Rebuild/restart API/worker
using canonical production compose, restart the orchestrator, verify status,
health and canary capability/bootstrap. Record pushed/deployed revision and image
IDs. Do not claim delivery until that loop is complete.

Add these **API and worker** switches initially disabled, then enable one at a
time for the existing owner canary only:

```dotenv
USE_MAX_FINALIZATION_COORDINATOR=false
USE_PROJECT_CELL_ACTIVITY_WATCHDOG=false
USE_GENERATION_EVENT_REPLAY=false
USE_CELL_RESOURCE_PROFILE_V2=false
MAX_GENERATION_DEADLINE_SECONDS=1500
PROJECT_CELL_HEARTBEAT_SECONDS=15
PROJECT_CELL_WATCHDOG_GRACE_SECONDS=20
```

Rollout order is observability, coordinator, activity watchdog, resource profile
v2, durable event replay, then coordinator ownership for the owner MAX canary.
After each step check API/worker/orchestrator health and the authored no-model
acceptance fixture. Each switch rolls back independently. Rollback retains proof,
activity and event records and never removes installed project libraries or the
dedicated project PostgreSQL database.

V2 admission counts each concurrent component once: legacy cell PostgreSQL
1 CPU/2 GiB, Redis .5 CPU/1 GiB, active project machine 2 CPU/2 GiB, dedicated
project PostgreSQL .15 CPU/256 MiB, guard/proxy/gateway helpers .2 CPU/128 MiB,
and managed core .35 CPU/768 MiB. Total: **4.2 CPU and 6.125 GiB** plus the
configured disk/inode envelope. If the complete envelope is unavailable, the
request queues; Docker is not started with a smaller hidden limit. The machine
uses two Next workers, a 1280 MiB Node heap, lifecycle concurrency 1, and
project-only named pnpm/Corepack/Next cache volumes. Profile v1 remains an
explicit rollback value.

Useful diagnostics (run against the production application database with the
normal operator account; do not paste secrets or full logs):

```sql
select dimension, outcome, operation_id, created_at
from project_cell_proof_results order by created_at desc limit 50;
select workspace_id, operation_id, kind, state, phase, heartbeat_at, deadline_at
from project_cell_activity_leases order by heartbeat_at desc limit 50;
select generation_run_id, seq, event_type, created_at
from generation_events order by created_at desc limit 100;
```

Inspect a known command through
`GET /internal/workspaces/{workspace_id}/agent/operations/{operation_id}`.
Alert when an active lease has no heartbeat for more than 20 seconds. A terminal
watchdog failure includes phase, proof key and operation ID; use those fields to
correlate the generation row, activity lease and command journal.

## Recovery and limitations

Environment snapshots are private immutable sanitized rootfs images plus named
volume archives with workspace/base/manifest identity, sizes and SHA256 hashes.
All nested artifacts are validated before restoring source/home/dedicated project PostgreSQL.
Source checkpoints seal the matching environment reference. No log/tmpfs/secrets
enter the captured image config. Restoration is durably fenced until imports and
declared recovery checks succeed. Dedicated PostgreSQL restore also boots a
temporary local-only Postgres process against the restored volume and requires a
controller-side `select 1` smoke before activation. Owned restore/archive helpers
are discovered, identity-checked and confirmed dead before pause, imports and activation.
Legacy machine artifacts without the dedicated database volume remain restorable;
that restore initializes an empty project database instead of reusing newer data.

Declared datastore quiesce failure/pending state cannot be bypassed by retrying a
stopped container. Pause retains that stopped rootfs but does not certify it as a
new checkpoint. Explicit restore can replace the failed state with a fully
validated requested checkpoint; in this recovery case the rollback baseline is
that requested envelope, **not** the unquiesced current data. Recovery failure
remains fenced/degraded. No automatic merge or salvage is claimed.

- CPU/RAM/pids are enforced; disk admission and snapshot byte bounds are **not**
  hard rootfs/volume quotas. Retained artifact history still needs operational
  storage monitoring/retention. No shared-host hostile-tenant qualification.
- Egress is HTTP(S)/CONNECT, not arbitrary raw TCP/UDP. Installers must honor the
  proxy. Private registries and destinations are not automatically authorized.
  PostgreSQL shares this guard: a database-side program can deliberately use the
  same public-only proxy known to project code, so this is bounded egress rather
  than a zero-egress database process.
- Gateway strips product `Set-Cookie`, incoming product Cookie/Authorization,
  spoofed identity/forwarded headers and Upgrade. Arbitrary app-cookie auth and
  WebSockets/HMR are not supported; default Next serves a production build.
- The existing managed PostgreSQL API foundation is preserved. Product code gets
  its own dedicated PostgreSQL, not broader grants into the managed platform DB.
  Payments, booking/roles or new provider actions still require their separate
  product/platform work; this change only isolates and delegates the project DB.
  Public product publication remains rejected separately (409); it is not part
  of this iteration. Real user/MAX launch and generated product behavior require
  the user's later testing; authored owner-preview tests are narrower evidence.
- Disabling the provider while portable cells exist is fail-closed, not automatic
  cleanup/downgrade. Restore them to an explicit legacy checkpoint or complete
  controlled lifecycle cleanup before disabling. Do not delete controller state.
