# Fast and reliable MAX generation finalization

Status: owner-approved direction on 2026-09-04. This design restores the
single-pass completion behavior that existed before the portable package and
project PostgreSQL rollout while retaining both capabilities and all current
isolation, fencing, candidate, rollback, and release protections.

## Outcome

A MAX generation receives the task promptly, edits and checks the project,
performs one production build for each distinct final revision, starts that
exact artifact, proves it, snapshots it, and terminates the generation run.
Build, probe, continuation, release, and UI reconnect paths must reuse durable
evidence instead of independently repeating work.

The performance target for a normal owner-canary generation is:

- warm median: at most 15 minutes;
- cold typical duration: 12-16 minutes;
- p95: at most 20 minutes;
- one bootstrap per dependency identity;
- one successful full build per final proof identity in at least 99% of
  successful runs;
- zero active commands killed by idle hibernation;
- no run may remain indefinitely in a non-terminal state.

## Why the earlier flow completed

The last useful code-history baseline before the regression is
`07c22d750d59ac5a4cac48b915e112f5daec7f51`. It is comparison evidence, not a
revision to restore. That flow had one agent transcript followed by one source
gate and one final gate. It did not have the portable machine, arbitrary package
installation, project PostgreSQL lifecycle, environment snapshots, and
multi-segment proof recovery that were layered on later.

The important regression boundary is the combination of:

- `0d9f780c`: portable, persistent project environments and unrestricted
  project dependency installation;
- `a7d6202d`: isolated project PostgreSQL with its own lifecycle;
- the existing segmented MAX runner and later proof/lifecycle repairs.

Those changes made `build` mean bootstrap plus production build plus required
tests, classified successful `build` and `bash` calls as environment mutations
even when their content digest did not change, and allowed setup, segment
completion, release proof, rollback, and final snapshot paths to request their
own proof. The result is repeated builds, lost proof state, repeated migrations,
and lifecycle races. The capabilities themselves are not the defect and remain
enabled.

## Architectural rule

There is one proof-carrying finalization coordinator for a generation. Existing
callers ask the coordinator to satisfy a proof dimension; they do not directly
bootstrap, build, migrate, or re-probe an already proven revision.

The state flow is:

```text
PREPARE -> EDIT -> FAST_CHECK -> FINAL_BUILD -> RUNTIME_PROBE
        -> SNAPSHOT -> PROMOTE -> COMPLETE
```

If a check fails and the agent changes project content, the flow returns to
`EDIT` with a new proof identity. A provider stop or segment boundary does not
invalidate the identity or replay completed phases. Finalization is owned by the
coordinator and has a reserved non-provider budget that the agent cannot consume.

The first delivery keeps this coordinator focused on the existing MAX main
stack. It does not introduce a global build farm, cross-project artifact cache,
or a universal framework contract.

## Durable proof identity

A proof is immutable and keyed by all inputs that can affect its result:

```text
workspace content revision
+ dependency manifest and lockfile digest
+ schema and migration digest
+ portable cell manifest digest
+ base image and toolchain digest
+ resource profile version
+ build command/configuration digest
+ workspace fencing epoch
```

The proof has independent `bootstrap`, `fast_check`, `full_build`, `runtime`,
and `release` dimensions. A green dimension may be reused only when every key
component matches. A new key creates a new record; an old record is never
rewritten to represent new content.

Invalidation is based on observed content changes:

- a source write invalidates fast-check, build, runtime, and release evidence;
- a package manifest or lockfile change invalidates every dimension, including
  bootstrap;
- a schema or migration change invalidates data/runtime/release evidence and
  build evidence only when it changes build inputs;
- a cell manifest, base image, toolchain, resource-profile, or fencing change
  invalidates every dimension;
- a read, log request, clean shell command, build, test, preview, or probe does
  not by itself mutate the environment;
- shell commands are classified from before/after content, dependency, schema,
  and environment digests rather than from the command name.

## Command roles and agent loop

The portable machine exposes four distinct roles:

1. `bootstrap` materializes dependencies once per dependency/toolchain key.
   After dependency resolution, subsequent installs use the frozen lockfile.
2. `fast_check` performs type checking, linting, and targeted tests without a
   production build, database migration, or package installation.
3. `full_build` validates frozen dependencies when needed, performs the
   production build, and runs the required final tests once for the proof key.
4. `runtime_probe` starts or reuses the exact artifact from `full_build` and
   performs identity, authorization, data, and health checks once.

The agent normally calls `fast_check` after edits. It may install a dependency,
which intentionally creates a new dependency key, but ordinary edit cycles do
not rerun bootstrap. Finalization freezes the current key and invokes
`full_build` exactly once. Build failure returns a bounded diagnostic to the
agent; a retry requires a content or dependency change and therefore a new key.
An unchanged failing key is not rebuilt in a loop.

The provider/edit budget and coordinator finalization budget are separate. At a
provider stop or the 40-step segment boundary, the runner persists the transcript
cursor, current proof key, proof references, and acceptance state. It continues
only when implementation is incomplete. Otherwise it enters finalization once.
No cap path is allowed to call an unconditional fallback build.

## Database behavior

The agent retains project-admin access to its dedicated project PostgreSQL. The
machine never receives core platform credentials and cannot reach another
project database.

Migrations are signature-driven:

- unchanged schema/migration digest means no migration during bootstrap,
  fast-check, repeated probe, or release;
- changed schema/migration digest runs the migration once under the finalization
  operation and records the resulting schema proof;
- runtime/data probes reuse that schema proof for the same fenced workspace;
- restore or data rollback creates a new data/schema identity and cannot reuse
  evidence from a different snapshot.

## Lifecycle, heartbeat, and terminal watchdog

Every long command, finalization, snapshot, and promotion has a durable activity
lease containing workspace, operation ID, kind, fencing epoch, proof key,
started-at, deadline, heartbeat-at, and terminal state.

The executor persists a heartbeat every 15 seconds while a command is active.
It also records command phase, elapsed time, bounded log progress, and deadline.
After an API or worker restart, ownership is reconciled by operation ID instead
of starting the command again.

Hibernation may stop a project machine only after acquiring the lifecycle fence
and rechecking all of these conditions under the workspace lock:

- no active generation lease;
- no live activity lease;
- no running machine operation;
- no finalization, snapshot, or promotion operation;
- the last heartbeat is idle rather than an active command heartbeat.

A stale API heartbeat alone never authorizes killing a Docker exec that the
machine still reports running. Command timeout is owned by the command watchdog:
it sends `SIGTERM`, waits a bounded grace period, then uses `SIGKILL` only if the
same fenced operation remains alive.

The generation watchdog has an overall 25-minute deadline for the normal path.
Before that deadline it may recover an interrupted infrastructure operation once
by reattaching to its durable operation. It must not restart a completed phase or
repeat an unchanged failed build. If completion is impossible, it records one
specific terminal failure with the active phase, proof key, operation ID, and
redacted diagnostic. A visible failure is acceptable; an endless non-terminal
run is not.

## Resource profile and caching

The active MAX project machine receives 2 CPU cores and 2 GiB RAM. PostgreSQL,
guard, proxy, gateway, and helper reservations are counted separately before
admission. A machine queues when the complete envelope is unavailable; the host
must not silently oversubscribe memory.

The active profile also:

- permits two Next.js/build workers instead of forcing one CPU;
- limits Node heap to 1.25 GiB inside the 2 GiB machine envelope;
- limits package lifecycle-script concurrency while permitting two build
  workers;
- persists a project-scoped pnpm store and Corepack cache;
- reuses `.next/cache` only when the Next/toolchain and dependency identities
  match;
- includes the resource-profile version in the proof key.

After finalization, the machine may return to the smaller idle profile only when
no command/activity lease is active.

## Durable progress and reconnect

Generation progress is append-only and ordered by a monotonically increasing
sequence per run. The database is authoritative; Redis fanout only reduces
latency. Persisted events include coordinator phase changes and
`tool.started`, `tool.heartbeat`, and `tool.finished`.

On reconnect the client supplies its last sequence. The server replays through a
captured high-water mark, subscribes to live events, and fills the race gap. The
client merges by sequence/event identity and never treats the presence of local
steps as proof that its transcript is complete. Secret values and unbounded logs
must not enter event payloads.

## Security boundaries retained

Package and system-library installation stays inside the credential-free project
machine with CPU, memory, PID, disk, inode, time, download, subprocess, and log
bounds. The machine has no Docker socket, host mounts, privileged mode, host
network, platform secrets, or core database credentials. Public dependency
traffic continues through the guarded egress path; private, metadata, host,
platform, cross-cell, `file:`, `link:`, git/SSH, and unauthorized direct-HTTP
dependencies remain denied. Resolved package versions and integrity are captured
in the lockfile and proof key.

Project PostgreSQL remains separately named, stored, networked, quota-bound, and
restored with integrity checks. Freedom inside the project boundary does not
grant access outside it.

## Delivery sequence

1. Add regression metrics for phase duration, bootstrap/build/probe counts,
   proof-key changes, hibernation decisions, heartbeat gaps, and UI/database
   sequence mismatch.
2. Separate bootstrap, fast-check, full-build, and runtime-probe roles; correct
   digest-based mutation classification; remove starter, segment, release, and
   snapshot duplicate full builds.
3. Persist proof and activity identities, make finalization idempotent, restore
   proof state across continuations, and add command heartbeat/lifecycle vetoes.
4. Raise the active profile to 2 CPU/2 GiB with truthful aggregate admission and
   enable compatible project caches.
5. Add ordered durable events and reconnect replay.
6. Deploy behind independent flags, run authored deterministic integration
   fixtures, then enable for the owner MAX canary. Do not test deployment by
   spending a model generation.

## Required verification

Automated tests must prove:

- exactly one bootstrap for an unchanged dependency key;
- exactly one successful full build for a final proof key;
- setup, segment continuation, release proof, snapshot, and reconnect reuse the
  same green evidence;
- source, dependency, schema, manifest, toolchain, resource, and fence changes
  invalidate only their specified proof dimensions;
- clean build/bash/test/probe commands do not claim mutation;
- an unchanged failing build cannot be retried automatically;
- provider stop and segment continuation preserve checkpoint and proof state;
- hibernation refuses to stop a machine during command or finalization activity;
- command heartbeat survives API/worker restart and permits reattachment;
- watchdog timeout produces one terminal state and no orphan operation;
- the 2 CPU/2 GiB envelope is fully included in capacity admission;
- project PostgreSQL remains isolated from core and cross-project databases;
- package egress and credential boundaries remain unchanged;
- WebSocket reconnect produces the same final sequence high-water mark as the
  database without duplicates or gaps.

The repository's targeted API, orchestrator, and web suites must pass. Existing
baseline failures unrelated to this change remain explicitly reported and may
not be represented as regressions or silently ignored. Delivery is complete only
after commit, push, the documented `/opt/omnia` production deployment, service
health checks, and an authored no-model generation fixture that reaches a
terminal successful release using the new single-pass flow.

## Rollout and rollback

Roll out independently gated features in this order: observability, duplicate
build removal, durable proof/activity leases, resource profile v2, ordered event
replay, and coordinator-owned finalization. Begin with the existing owner canary.
Expand only when the following hold:

- median at most 15 minutes and p95 at most 20 minutes;
- at least 99% of successful runs have one successful full build for the final
  key;
- hibernation kills zero active commands;
- heartbeat gaps remain at most 20 seconds;
- reconnect high-water matches the database;
- false green-proof reuse remains zero;
- OOM and command-timeout rate remains below 1%;
- successful generation rate exceeds 98%.

Rollback disables the new coordinator/reuse/resource/event flags independently
without deleting proof, activity, event, candidate, snapshot, or project-data
records. It does not disable package installation or project PostgreSQL and does
not revert the isolation changes introduced after the historical baseline.
