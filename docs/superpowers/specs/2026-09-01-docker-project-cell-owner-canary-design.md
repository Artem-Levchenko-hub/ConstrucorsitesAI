# Docker Project Cell Owner Canary

**Status:** approved in chat on 2026-09-01; written contract awaiting owner review

**Purpose:** give one authenticated owner account a Claude Code/Codex-class full-stack agent immediately, using an isolated persistent Docker Project Cell on the current production server, while the stronger K3s + Kata runtime remains the public multi-tenant target.

**Model path:** Claude Sonnet through the existing Omnia LLM Gateway (`LLMGW`). No provider credential is placed in generated code or a project container.

**Relationship to the enterprise contract:** this document adds a narrow owner-only Docker canary to `2026-08-31-enterprise-project-cell-agent-runtime-design.md`. It supersedes that document's “Docker provider is local-only” sentence solely for this authenticated owner canary. It does not replace the Kata design and does not authorize arbitrary external accounts on a shared-kernel Docker executor.

## 1. Outcome

The owner opens the normal public Omnia interface, creates a real MAX project, and writes an ordinary product request. Omnia creates or wakes one persistent Project Cell for that project. A resident agent works on the server as a development team would: it plans, edits a real Git repository, installs dependencies, starts frontend and backend processes, creates and migrates PostgreSQL data, uses Redis, runs browser checks, diagnoses failures, repairs the product, and prepares a verified release.

The result is a complete application, not a throwaway demonstration or a static mock. A MAX application is not complete unless its real user flows, server logic, database schema, migrations, MAX integration boundary, responsive interface, error states, and deployment health all work.

The model inference remains at the configured provider behind LLMGW. The Project Cell supplies the equivalent of the owner's computer: persistent disk, shell, Git, processes, network, browser, databases, tools, and durable task state.

## 2. Exact canary scope

The first release is externally reachable through the normal production UI, but the new runtime is selected only when all of the following are true:

1. `PROJECT_CELL_DOCKER_CANARY_ENABLED=true` is set in production configuration.
2. The request has a valid authenticated Omnia user.
3. The account email is verified.
4. The normalized verified account email is present in the server-side `PROJECT_CELL_CANARY_EMAILS` allowlist.
5. The project belongs to that authenticated account.

The allowlist value is production configuration and is never committed to the repository or accepted from a prompt, request body, project file, cookie field, or generated application. Routing resolves the authenticated database user first and records the immutable user and project identifiers in the generation run.

All other users remain on the existing generation backend. Unknown external accounts never reach the Docker Project Cell executor. The first canary permits one active Project Cell generation at a time on the current host; that is a rollout guard, not a product complexity limit.

## 3. Meaning of “full freedom”

Inside its own Project Cell, the agent may:

- create, read, edit, rename, and delete any project file;
- initialize and use Git branches and commits inside the project repository;
- install Node.js, Python, system, browser, and build dependencies in the cell;
- run shell commands and long-lived frontend, backend, worker, scheduler, and queue processes;
- expose multiple internal application ports through the cell preview router;
- create PostgreSQL databases, schemas, roles, tables, indexes, migrations, triggers, and seed data owned by the project;
- use project-scoped Redis for queues, caches, locks, and realtime work;
- call public package registries, documentation, MAX endpoints, and product APIs over controlled public egress;
- run Chromium against the draft application, inspect screenshots, console output, network failures, and responsive layouts;
- run builds, unit tests, integration tests, migrations, health checks, and repair loops;
- retain source, Git history, dependency caches, database state, and agent checkpoints across prompts and restarts.

There is no protocol limit on file count, screen count, table count, migration count, framework composition, or product complexity. Physical CPU, memory, disk, process, and execution-time guards protect the shared production host; they stop unsafe admission or a runaway process but do not downgrade the requested product into a template or mock.

The agent may not:

- access the Docker or containerd socket;
- use privileged mode, host PID/IPC/network namespaces, host devices, or arbitrary host mounts;
- read Omnia source, production secrets, another project's workspace, database, Redis, network, or artifacts;
- bind host ports directly or change host firewall, routes, systemd, kernel, packages, users, or files;
- contact host, metadata, loopback, link-local, private, Docker, Kubernetes, or other reserved address ranges from project-visible execution;
- choose its own container image, mounts, capabilities, network attachments, or control-plane parameters.

These are cell boundaries, not product restrictions.

## 4. Approaches considered

### A. Owner-only Docker Project Cell — chosen

This reaches the required full-stack behavior on the current server without changing its kernel or installing K3s. The owner can test through the public product while all other users stay on the proven backend. The trade-off is that Docker uses the host kernel, so this backend is not approved for arbitrary untrusted accounts.

### B. Open Docker Project Cell to every account — rejected

It is fast to expose but makes a shared-kernel runtime the security boundary for arbitrary model-visible shell execution. That is inconsistent with the enterprise isolation contract and is not accepted.

### C. Wait for K3s + Kata before any full-cell test — retained as the public target

This supplies a hardware-virtualized boundary suitable for multi-tenant rollout, but it is blocked on independently verified recovery and provider-console evidence for the current production server. The Docker owner canary lets product work proceed without pretending that this infrastructure gate has passed.

## 5. Cell composition

One project maps to one durable cell bundle:

```text
Project Cell
├── resident agent runner
│   ├── Claude Sonnet loop through LLMGW
│   ├── project/session lease
│   ├── ordered events and checkpoints
│   └── tool dispatch
├── root-capable development executor
│   ├── shell, files, Git and package managers
│   └── supervised long-lived processes
├── draft application runtime
├── PostgreSQL with a project-owned volume
├── Redis with a project-owned volume
├── browser worker
├── persistent workspace volume
├── persistent agent-home volume
└── project-private networks
```

The trusted resident runner and the root-capable executor are different containers. The runner is non-root, does not import generated code, and holds only a short-lived identity for LLMGW and Omnia control calls. The executor receives no LLMGW, Omnia database, Docker, deployment, or long-lived provider credential. It sees only the project workspace and operation-scoped environment required for the current action.

The draft runtime is also separate. Generated code never executes inside the runner. Browser checks run against the draft runtime from a separate worker so preview content cannot read runner state.

## 6. Replaceable provider boundary

Business logic addresses the cell through the provider interface already approved by the enterprise design:

```python
class WorkspaceProvider(Protocol):
    async def ensure(spec: WorkspaceSpec) -> WorkspaceHandle: ...
    async def wake(workspace_id: UUID) -> WorkspaceHandle: ...
    async def pause(workspace_id: UUID, checkpoint: CheckpointRef) -> None: ...
    async def destroy(workspace_id: UUID) -> None: ...
    async def status(workspace_id: UUID) -> WorkspaceStatus: ...
    async def execute_control(
        workspace_id: UUID,
        action: ControlAction,
    ) -> ControlResult: ...
```

`DockerOwnerCanaryProvider` implements this contract with Docker networks, containers, and volumes. The later Kubernetes provider implements the same contract with Kata workloads. Public API routes, generation records, runner protocol, tool schemas, and promotion semantics do not change when the backend moves.

The Docker provider is selected only by the authenticated owner canary policy. It is never a silent fallback from a failed Kata or policy check.

## 7. Request and agent flow

1. The API authenticates the owner and creates one durable generation run.
2. A transaction acquires the per-project workspace lease. A second prompt waits in the project queue.
3. The API chooses `docker-cell` only through the server-side owner-canary policy.
4. The orchestrator idempotently creates or wakes the project's cell bundle.
5. The runner restores the latest confirmed checkpoint and receives the prompt.
6. The runner calls Claude Sonnet through LLMGW with the existing native tool contract.
7. Every tool operation is recorded before execution with project, run, session, lease epoch, and operation identifier.
8. File, shell, Git, package, process, database, build, and browser actions execute only in the project cell.
9. Observations return to the model. The loop continues until the product is verified or needs an explicit owner decision.
10. A candidate is assembled from immutable source, migration, database-backup, build, and verification references.
11. Promotion uses a compare-and-swap check against the accepted project revision. A stale or cancelled candidate cannot replace the active release.
12. The normal snapshot, preview, and publish paths receive the accepted candidate.

The existing `bash`, file, build, runtime, media, probe, and `see` tool names remain stable. The execution target changes beneath their internal adapter rather than teaching the model a second incompatible tool language.

## 8. Workspace and process behavior

The project workspace is mounted at one fixed path and contains a real Git repository. The agent home is a separate persistent volume for tool configuration, command history, checkpoints, and safe package caches. Neither volume is shared with another project.

Long-lived processes are owned by a cell supervisor, not by an HTTP request. Starting a server returns a stable process reference. Logs, status, restart, and termination address that reference. Stop or cancel terminates the entire session process group; detached children cannot survive outside the owning session fence.

Pause stops mutable executor work only after the current operation has a known outcome and a checkpoint is durable. Wake restores the same workspace, database volumes, process manifest, and runner state. A process is restarted from the recorded manifest rather than assumed to have survived.

## 9. Project database and Redis

Each canary project receives a dedicated PostgreSQL container and persistent volume. The agent owns the project database and may create real schemas, tables, migrations, extensions from an approved set, roles, indexes, and data. It never receives credentials for the Omnia platform database or another generated project.

Before a generation session can mutate the database, the cell creates a restorable PostgreSQL backup bound to the accepted source revision. Migrations run against the draft database. A failed or cancelled run keeps the accepted release and its backup reference intact. Promotion records the exact migration digest and database-backup reference used by the accepted candidate.

Redis is project-scoped. Durable product facts must remain in PostgreSQL; Redis is used for cache, queues, locks, and realtime state. Redis persistence may survive cell sleep, but restoration correctness cannot depend on cached values.

Destroy first seals a final database backup and workspace archive, then removes compute and network resources. Project volumes are deleted only after explicit project deletion reaches the retention step. Stop and hibernate never delete project data.

## 10. Network model

Each cell receives two orchestrator-owned networks:

- an internal project network for runner-adjacent control, draft application, PostgreSQL, Redis, and browser reachability;
- a controlled egress path for public HTTPS, package installation, documentation, MAX, and application APIs.

The executor and generated runtime cannot attach themselves to another network. They have no Docker API with which to request a new attachment.

Public egress is broad enough for real development, not restricted to a fixed package list. The egress layer denies the host, Omnia control services, Docker/Kubernetes ranges, private and reserved ranges, metadata endpoints, alternate DNS paths, and direct cross-project traffic. DNS is supplied by the controlled resolver. Public requests are logged by project and session without logging secrets.

Only the preview gateway publishes application traffic. Generated processes cannot bind arbitrary host ports. The gateway routes an authenticated draft preview and the normal accepted public application separately.

## 11. Container hardening for the owner canary

Every model-visible container uses:

- no privileged mode;
- no host namespaces, devices, sockets, or arbitrary bind mounts;
- no Docker/containerd API;
- `no-new-privileges`;
- a restrictive seccomp/AppArmor profile where supported;
- a bounded capability set with no `SYS_ADMIN`, `NET_ADMIN`, `SYS_MODULE`, `SYS_PTRACE`, `SYS_BOOT`, or host-control equivalent;
- explicit CPU, memory, process, open-file, temporary-storage, and log limits;
- a project-specific network and volume set selected only by trusted orchestrator code;
- immutable labels for project, runtime kind, profile version, and owner-canary backend.

The executor may have userland root inside its own container because package managers and development tools require it. This is accepted only for the authenticated owner canary. It is not described as a microVM or approved as a multi-tenant boundary.

## 12. Real-product completion contract

The agent cannot finish a MAX project merely because files exist or a landing page renders. Completion requires evidence appropriate to the requested product:

- frontend screens implement the real primary and secondary flows;
- backend endpoints execute real validation and business logic;
- PostgreSQL migrations apply from an empty database and from the previous accepted revision;
- the generated application reads and writes real project data;
- authentication and authorization protect private actions;
- MAX launch data and webhook boundaries use the existing verified integration path when required;
- loading, empty, success, validation, permission, offline, and server-error states exist;
- desktop and mobile browser checks cover the main flow;
- build, type, unit, integration, migration, runtime, and browser checks pass;
- console errors, failed required requests, broken navigation, and horizontal mobile overflow are absent;
- no required feature is replaced by hard-coded success, fake records, or a static mock.

If a real external credential must be supplied by the owner, the agent must implement and verify the full credential boundary, fail honestly while the credential is absent, and identify that single external requirement. It may not claim the integration works by returning fabricated success.

## 13. Failure and recovery rules

- `ensure`, `wake`, `pause`, `stop`, and `destroy` are idempotent.
- Repeated delivery of a control request returns the recorded operation result instead of repeating the side effect.
- A runner lease has an epoch. A replaced runner cannot continue writing or promoting.
- Cancel increments a cancellation epoch, blocks new operations, terminates the session process group, and prevents candidate promotion.
- A server or container restart recovers from the last confirmed checkpoint and reconciles operations whose outcome was unknown.
- The legacy backend may be selected only before a canary run obtains a writable cell lease. It is not started in parallel after cell writes begin.
- A cell failure leaves the accepted project revision and accepted database backup unchanged.
- Infrastructure errors are shown separately from model, build, migration, browser, and product errors.

## 14. Compatibility with the existing system

The current public generation, preview, snapshot, stop, project deletion, and publish routes remain compatible. The new runtime is an internal backend selection.

The first implementation changes these internal seams:

- `apps/api/src/omnia_api/routers/messages.py` — canary policy, queue/lease dispatch, and runner handoff;
- `apps/api/src/omnia_api/services/agent_native.py` — reusable runner loop without changing model-visible schemas;
- `apps/api/src/omnia_api/services/agent_builder.py` — execution adapter selection rather than direct live-container assumptions;
- `apps/api/src/omnia_api/services/orchestrator_client.py` — cell lifecycle and operation methods;
- API persistence models and migrations — workspace, session, operation, checkpoint, event, and candidate state required for restart-safe ownership;
- `apps/orchestrator/src/omnia_orchestrator/services/provisioner.py` — bundle provisioning behind `DockerOwnerCanaryProvider`;
- `apps/orchestrator/src/omnia_orchestrator/core/docker_client.py` — versioned cell containers, volumes, networks, labels, limits, and sidecars;
- `apps/orchestrator/src/omnia_orchestrator/routers/runtime.py` — idempotent lifecycle and label-based cell lookup;
- a focused resident runner entrypoint packaged from existing agent logic;
- production compose/config — runner image, internal connectivity, disabled-by-default flags, and owner allowlist configuration.

Legacy name-based lookups such as `omnia-dev-<slug>` move to label-based resolution by immutable project identifier and runtime kind, with the old name retained only as a compatibility fallback during migration.

## 15. Rollout sequence

1. Add persistence and provider interfaces with the canary flag disabled.
2. Add Docker cell bundle provisioning, version labels, project networks, workspace and agent-home volumes.
3. Add PostgreSQL and Redis sidecar lifecycle with backup and restore evidence.
4. Package the resident runner and route existing native tools through the cell operation protocol.
5. Add process supervision, browser worker, draft routing, checkpoints, cancellation, and candidate promotion.
6. Run local lifecycle, recovery, security, database, browser, and compatibility suites.
7. Deploy with `PROJECT_CELL_DOCKER_CANARY_ENABLED=false`; verify all existing production health endpoints and a legacy generation.
8. Configure the approved owner allowlist on the server and enable the canary.
9. Create one real owner project through the public UI and complete the full MAX acceptance proof.
10. Keep the owner canary enabled only after the production evidence is stored and the old backend remains healthy.

Every step is reversible. Disabling the canary flag routes new owner prompts to the legacy backend only when no writable cell run is active. Existing cell volumes and drafts are retained for inspection.

### 15.1 Implementation decomposition

The complete canary spans several independently risky subsystems and is not implemented as one undifferentiated change. It is delivered through four focused subprojects. The routing flag remains disabled until all four satisfy their contracts:

1. **Control foundation:** authenticated owner policy, durable workspace/session/operation records, replaceable provider interface, idempotent lifecycle, and legacy compatibility. No model-visible cell shell is enabled.
2. **Cell resources:** project workspace and agent-home volumes, versioned Docker bundle, private networks, PostgreSQL, Redis, backup/restore, resource admission, and isolation probes. The bundle is exercised only by internal lifecycle tests.
3. **Resident agent:** runner packaging, LLMGW identity, existing native tool schemas, executor protocol, process supervisor, checkpoints, cancellation, restart recovery, build, and browser worker. Generated code still cannot be promoted.
4. **Draft and owner canary:** draft routing, candidate evidence, atomic promotion, server allowlist enablement, real MAX project, recovery exercise, security proof, and rollback verification.

Each subproject receives its own reviewed implementation plan, tests, commit, push, production-safe deployment, and health evidence. A later subproject consumes only the explicit interfaces produced by the previous one. Partial completion cannot accidentally route a real prompt because the owner canary flag and readiness check are both required.

## 16. Verification matrix

### Functional

- owner account selects `docker-cell`; a non-allowlisted account selects legacy;
- one project creates one repeatable cell bundle;
- source, Git state, database, Redis, and agent checkpoint survive stop/wake and service restart;
- package installation, frontend, backend, worker, PostgreSQL migration, Redis queue, Chromium, and public HTTPS all work;
- repeated prompts continue the same repository rather than regenerating a replacement project;
- a complete MAX application builds and publishes through the normal product flow.

### Isolation

- no Docker/containerd socket, host mount, host namespace, host port, or forbidden capability;
- project A cannot read, resolve, connect to, or mutate project B;
- executor cannot reach host, Omnia control services, platform databases, private ranges, or metadata endpoints;
- generated code cannot read runner or provider credentials;
- an unverified or non-allowlisted account cannot select the cell backend.

### Recovery

- duplicate lifecycle requests are idempotent;
- API, orchestrator, runner, executor, database, and Redis restarts are reconciled;
- cancel terminates descendant processes and blocks promotion;
- failure after a database migration restores or retains the accepted backup as specified;
- stop/wake resumes the same workspace and checkpoint;
- stale runner and stale candidate writes are rejected.

### Production safety

- API, worker, gateway, web, orchestrator, platform PostgreSQL, Redis, and existing preview health are recorded before and after deployment;
- canary admission stops when protected host headroom is unavailable;
- only one canary generation is active on the current server;
- legacy generation remains healthy while the owner canary is enabled;
- disabling the flag requires no database rollback and does not delete cell data.

## 17. First real MAX acceptance project

The first owner project is retained as a real application, not discarded as a synthetic smoke. The owner supplies a product request requiring at least:

- a mobile-first MAX interface with multiple connected screens;
- authenticated user and administrator roles;
- a PostgreSQL domain model with related tables and real migrations;
- frontend forms that create, update, list, search, and validate real records;
- backend authorization and business rules;
- one background or queued operation using project Redis;
- one real MAX launch/session or webhook boundary supported by available owner credentials;
- loading, empty, permission, validation, and failure states;
- responsive browser evidence at desktop and mobile widths;
- successful build, migration, runtime, integration, browser, snapshot, publish, stop, and resume checks.

The proof includes one interrupted session that resumes without losing source or data, one rejected invalid action, one database backup/restore exercise, and explicit denial of host, Docker, metadata, Omnia-control, and cross-project access.

## 18. Migration to Kata

The Docker owner canary is deliberately shaped like the final Project Cell:

- identical workspace/session/candidate state;
- identical runner and operation protocol;
- identical model-visible tools;
- identical project and lease identifiers;
- identical draft and promotion contract;
- identical public API and UI behavior;
- replaceable `WorkspaceProvider` only.

Moving to K3s + Kata replaces Docker containers, networks, and volumes with the approved Kubernetes/Kata resources. It does not require the model, UI, prompts, runner logic, database revision contract, or project lifecycle to be redesigned.

## 19. Non-goals for this canary

- opening model-visible Docker execution to arbitrary accounts;
- claiming Docker `runc` is equivalent to Kata or a dedicated VM;
- changing the production host kernel, Docker daemon runtime, firewall, or K3s state as part of the owner-canary feature;
- migrating every existing project before the first real MAX project succeeds;
- replacing LLMGW or exposing provider credentials to the cell;
- allowing the agent to administer Omnia, the host, container runtime, or other projects;
- declaring success from generated files without live database, runtime, browser, and publish evidence.

## 20. Final acceptance

The owner canary is complete only when:

1. the approved owner account reaches the new backend through the public product and other accounts cannot;
2. one real project receives a persistent cell with runner, executor, frontend/backend runtime, PostgreSQL, Redis, browser, workspace, and agent home;
3. Claude Sonnet through LLMGW completes and repairs a non-trivial full-stack MAX application in that cell;
4. the project survives interruption, stop/wake, component restart, and a database recovery proof;
5. host, Docker, metadata, Omnia-control, secret, and cross-project probes are denied;
6. the accepted version remains available during work and cannot be replaced by a failed, cancelled, or stale candidate;
7. build, migrations, tests, browser flows, runtime health, snapshot, publish, and rollback evidence pass;
8. existing production services and legacy generation remain healthy;
9. the owner can disable new canary routing without deleting the cell or its data;
10. the exact implementation revision is committed, pushed, deployed through production compose, and confirmed by live health checks.
