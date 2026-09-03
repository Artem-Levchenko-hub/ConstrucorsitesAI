# Docker Project Machine

Status: implementation direction approved by the owner on 2026-09-03. Docker is the selected runtime now; K3s/Kata work is outside this change. The first independently delivered slice removes `see` from all generation paths. Arbitrary-stack machine runtime tasks remain unimplemented; existing legacy restrictions remain until their replacements pass verification.

## Outcome and boundaries

The agent develops a real MAX mini-app in its own persistent Linux machine: installs language packages and system tools, creates frontend/backend/worker processes, owns application schemas, runs functional tests, survives service restarts, and publishes a verified release. MAX is the output/authentication/integration contract, not a framework or language restriction. A Next.js-only dependency prototype is an intermediate result, never completion.

- Retain the existing authenticated, verified, allowlisted owner-canary rollout boundary. Do not enable arbitrary public accounts.
- Use Docker now; do not install K3s/Kata or weaken their existing installation guards.
- No generated workload receives privileged mode, host namespaces, devices, sockets, host mounts, platform database credentials, runner credentials, or control-plane authority.
- Docker uses a shared host kernel. This owner canary is not represented as microVM isolation or a qualified hostile multi-tenant service.
- No framework, language, package-name, package-registry, table-count, file-count, or service-count allowlist. Linux userland compatibility, available resources, MAX compatibility and isolation are the boundaries.
- Public dependency/application egress is permitted only through externally enforced destination controls; private/host/platform/metadata/cross-project destinations remain blocked.
- `see` is removed from every generation path, including native, legacy and MAX: no exposed tool, dispatch, prompt requirement or automatic retry. Manual live preview remains available. Existing build/runtime/auth/data checks remain; no replacement visual gate is introduced.
- No user/model generation runs during implementation verification. Use authored deterministic fixtures.
- Source changes are verified, reviewed and handed to delivery for commit, push, documented production deployment and health confirmation. Existing unrelated work is preserved.

## Architecture

Retain ProjectCellWorkspace, lifecycle operations, capacity reservations/FIFO, ownership locks, source revision checks, signed owner preview and candidate fencing. Keep the existing fixed MAX cell as a compatibility adapter while the machine provider is dark.

The new path has four separate responsibilities:

1. A trusted non-root runner owns durable model turns, leases, tool operations and events. It never executes generated code or shares its writable home with project processes.
2. A project-owned Docker development machine has a writable Linux rootfs and project-only repository/home/data volumes. Userland root is allowed only here, with a bounded Docker capability set and no host-control capabilities. Commands and long-running services are owned by a fenced machine session, not an HTTP request.
3. A trusted Docker controller owns machine lifecycle, network attachments, resource accounting, immutable environment snapshots and runtime materialization. Model-controlled manifests cannot supply raw Docker options.
4. A release coordinator owns immutable candidate verification, data cutover and the active-release pointer. Generated code cannot approve or publish itself.

Use dedicated modules for machine manifests, environment artifacts, services, egress and releases. Do not enlarge `messages.py` with another executor implementation; it dispatches, reports and replays machine sessions.

## Portable manifest

`.omnia/cell.json` is versioned, JSON-only and digestable. It describes environment identity, tasks, services, routes, mounts, data stores and requested resources without a framework enum.

Each service declares `name`, `argv`, `cwd`, dependencies, named persistent mounts, readiness, restart policy and requested resources. A task declares argv/cwd and its role (`bootstrap`, `build`, `test`, `migrate`, `quiesce`, `restore_check`). Commands are passed as argv; shell syntax is explicit through a shell argv, never interpolated on the host. Routing refers to service names and container ports; no host ports are model-controlled. Mount sources are project-local names and guest destinations cannot overlap controller/secret mounts. Graph cycles, duplicate routes, unknown services and malformed identities fail closed.

Admission accounts for the aggregate machine/service/database/verification resource envelope. Operational request/log/artifact size bounds protect the host, but do not impose product-complexity quotas.

Existing MAX cells without a manifest keep their current adapter. The new agent receives capabilities from the selected provider, so prompts do not advertise unavailable installs or database APIs.

## Environment and package installation

The development machine uses a project-owned writable rootfs. Package scripts and system installers run only there. Project repository, installed dependencies, virtual environments and agent-visible home are retained without the current text-only copy/symlink exclusions.

At a stable checkpoint the controller stops mutable execution, snapshots the rootfs to an immutable Docker image ID, exports an image artifact and records its SHA-256, base-image identity, manifest digest, installed package/lock metadata and volume checkpoint digests. Private artifacts belong to one project. Docker `restart` is not recreation proof: destroy/recreate must import and hash-check the recorded environment artifact, restore the matching volumes, then restart declared services. Caches are optional acceleration, not the recovery source.

Transient credentials are injected through controller-owned ephemeral mounts and excluded from environment snapshots. No platform/runner credential enters the machine. Project integration secrets remain project-scoped; production credentials are absent from development and verification machines.

## Network and platform identity

Keep project/data networks internal. A small dedicated public-egress proxy/resolver has no Docker socket, host mounts or platform credentials. An externally enforced project network fence permits only its proxy/resolver, project data and the narrowly scoped MAX boundary; merely setting HTTP_PROXY is not sufficient. The proxy resolves destination addresses itself and rejects all forbidden addresses after DNS/redirect normalization, including IPv6 and mapped addresses. Direct IP bypass, alternate DNS and host gateway access are negative acceptance tests. Project destination and byte/rate logs redact secrets.

MAX session/launch validation and platform integrations stay behind a language-neutral HTTP boundary. Preserve existing verified behavior first through the managed core adapter, then route reserved platform paths to that boundary and product paths to manifest services. Strip caller-supplied identity headers and bind verified identity to project, release and user. Custom backend code is allowed to use its project database; another project's or the platform database is unreachable. Independent two-user tests verify in-app authorization. Removing source-level raw-DB restrictions is gated on this boundary, not on a prompt assertion.

## Commands, services and recovery

Every command has a durable operation ID, request digest, lease/fencing/cancel epochs, bounded output reference and state. Re-delivery returns a stored result; an unknown side-effect outcome becomes `indeterminate` and is reconciled instead of blindly retried. Services expose stable IDs, state, logs, readiness and restart through the supervisor. Background children cannot survive machine cancellation/lease transfer: stop/remove the matching container and verify death before allowing another writer.

Runner checkpoints persist transcript/turn cursor, completed operations, event sequence, source/environment/volume digests and manifest. API/runner restart resumes from durable state, not an in-memory task. One active writable session per workspace remains database-enforced. Pause retains a checkpoint; cancel fences promotion permanently and retains the draft for inspection. Neither deletes accepted data.

## Data and publication

Development and accepted data are separate revisions. PostgreSQL/Redis are provided conveniences, not mandatory product storage. Other userland data services declare persistent volumes and quiesce/restore checks; publication requires verified recovery of all durable facts.

First publication uses a bounded maintenance cutover, not a promise of generic zero-downtime replication:

1. Build immutable environment/source/service artifacts and verify against draft data.
2. Acquire project/session promotion fences; block new old-release requests and jobs, drain in-flight work and stop old writers.
3. Snapshot final accepted data into a new candidate revision. Run migrations and restore/functional checks there; never migrate accepted data in place.
4. Materialize the exact inactive candidate with its final runtime/data identity; validate health and acceptance evidence.
5. In one transaction compare base release and all epochs, advance one active runtime/data/jobs pointer, and persist an idempotent outbox.
6. Reconcile gateway/runtime activation from that pointer. Before commit, any failure resumes untouched old runtime/data. After commit, recovery finishes activation of the committed pair.

Code rollback does not imply data rollback. After new accepted writes, restoring an older data snapshot requires explicit data-loss authorization or independently verified compatible reverse migration. Keep old artifacts/volumes through the rollback retention window.

## Functional completion

Acceptance is bound to the immutable source/environment/manifest/data digests and captured by a verifier outside generated code. Project tests execute in isolated verification containers without signing credentials; independent black-box checks must cover real required behavior. No fixed `pnpm typecheck`, TypeScript source-path scan or `/api/omnia/actions` requirement applies to arbitrary product services.

Required evidence includes stack-appropriate build/tests; MAX signed-session validity and negative cases; real API mutation/readback; required user flows; two-user authorization; migration from empty and previous accepted data; service restart and whole-machine recreation persistence; cross-cell/platform isolation; cancelled/stale candidate rejection; publish/health and failed-cutover preservation. Visual screenshots may aid debugging but missing vision never blocks completion.

## Delivery slices and final acceptance

1. Portable manifest and resource/service validation, with the existing MAX adapter unchanged.
2. Persistent execution/environment artifacts, service supervisor and enforced public egress.
3. Durable runner/session integration and framework-neutral MAX identity/custom data boundary.
4. Functional verifier and candidate/data publication with public lifecycle controls.
5. Deterministic live integration matrix and production delivery.

Final acceptance runs three isolated authored cells without model calls. At least two unlike stacks are recreated from captured artifacts (one JavaScript-based, one Python-based; a compiled/system tool is installed), each has a real frontend/API/database write and worker, and all persistence/isolation/cancel/publish cases pass. A passing fake-provider suite or restart of an existing container alone is not final acceptance. If required Linux Docker, namespace guard, credentials or test data infrastructure is unavailable, report that precise gate and keep the remaining checklist open.
