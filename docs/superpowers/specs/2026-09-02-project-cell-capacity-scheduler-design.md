# Capacity-Based Project Cell Scheduling

**Status:** approved in chat on 2026-09-02; written contract awaiting owner review

**Purpose:** let the approved owner create any number of durable projects without an arbitrary numerical cap. The current production server may run only as much compute as its CPU, memory, disk, and inode headroom safely allow; excess work waits in a durable queue and starts automatically instead of failing.

**Relationship to the owner canary:** this document amends `2026-09-01-docker-project-cell-owner-canary-design.md`. It replaces the one-active-bundle rollout guard and its count-based admission rule. All owner authentication, isolation, persistence, safety, completion, and later Docker-to-Kata migration requirements remain in force.

## 1. Owner outcome

The owner can create projects one after another from the normal Omnia/MAX flow. Every accepted project immediately receives its own durable project identity and persistent state. Omnia then creates or wakes that project's isolated workspace, PostgreSQL, and Redis when capacity is available and runs the requested generation without requiring the owner to repeat the prompt.

There is no product setting such as “one project”, “three projects”, or another fixed total. The only limit is the capacity the server can safely provide at that moment. A temporarily full server changes execution timing, not whether the project is accepted: the run becomes `queued_for_capacity`, remains visible, and starts automatically after resources are released.

“No project-count limit” does not mean unlimited simultaneous compute. The scheduler must protect the host, existing Omnia services, active generations, and persistent data. Adding server capacity increases concurrency without changing product code or user-facing limits.

## 2. Current production defect

The production attempt for project `БарберПро` failed before generation because the orchestrator returned `active_bundle_limit`. The current configuration constrains `cell_max_active_bundles` to exactly `1`. One older ready project already owned a draft, PostgreSQL, and Redis bundle, so a second workspace could not be ensured.

The orchestrator exposed that deterministic admission refusal as HTTP 500. The API consequently classified the `ensure` operation as `indeterminate`, and the UI showed `Project Cell ensure failed: indeterminate`. No resources for the new project were actually created, but the run ended instead of waiting.

This design corrects both problems:

1. count-based admission is replaced with atomic capacity admission;
2. temporary capacity pressure is a durable wait state, not a failed or indeterminate operation.

## 3. Capacity contract

The scheduler reasons about resources, not project count. Each runnable cell profile declares reservations for:

- memory for executor, draft runtime, PostgreSQL, Redis, runner, browser, and required overhead;
- CPU shares or quotas;
- persistent and temporary disk bytes;
- inode headroom;
- process/file descriptor ceilings where they are relevant to host safety.

The host publishes allocatable capacity after subtracting a protected reserve for Omnia control-plane services, the operating system, Docker overhead, backups, and recovery operations. A cell can start only when an atomic reservation fits inside every required resource dimension.

Admission uses configured reservations for correctness and measured usage for observability. It must not rely only on a momentary `free` memory reading, because concurrent starts and delayed container allocation could otherwise overcommit the host. Operators may tune resource profiles and protected reserves, but cannot configure a user-visible project-count limit.

Disk admission considers both bytes and inodes. When persistent storage approaches the protected threshold, new work stays queued and the UI explains that server storage is the constraint. The scheduler never deletes a project, database, snapshot, or volume to create capacity unless the owner has explicitly initiated the normal project-deletion lifecycle.

## 4. Durable project versus active compute

A project is durable even when none of its containers are running. Its control record, source repository, Git history, workspace volume, database volume or restorable backup, accepted release, snapshots, and queued prompts survive sleep, service restarts, and host restart.

Compute has three relevant states:

- `active`: a generation or owner-visible runtime operation is in progress;
- `idle_ready`: the bundle is usable but has no active fenced operation;
- `hibernated`: mutable compute is stopped and its capacity reservation is released while durable data remains.

An idle bundle may be hibernated automatically after its idle grace period or immediately when another queued project needs capacity. Hibernation stops compute only after all operations have known outcomes and a durable checkpoint exists. It does not remove volumes or accepted releases. Opening an old project or submitting a new prompt wakes the same project state automatically.

Active generation is never evicted to make room for another project. If no safe idle victim exists, the new run waits.

## 5. Queue and scheduling policy

Capacity waiting is represented by a durable database record linked to the immutable project, generation run, prompt, requested cell profile, and idempotency key. The initial policy is FIFO by admission time, with these safeguards:

- at most one writable generation run per project;
- a project already holding a compatible reservation may continue its queued work before another wake is attempted;
- cancelled or superseded runs are removed transactionally;
- a run too large for the current host cannot block every smaller runnable item forever;
- bounded fairness prevents one frequently active project from monopolizing all admissions;
- a restart reconstructs the queue from durable state rather than in-memory order.

The API returns the durable run as soon as the prompt is accepted. The UI can display `Waiting for server resources` and, when reliable, the current queue position. It must not promise an exact start time. The client follows the same run stream through `queued_for_capacity`, provisioning, generation, verification, and completion; no resubmission or duplicate prompt is needed.

## 6. Race-free admission

A single logical capacity coordinator is implemented with durable database transactions and leases, not a process-local mutex. Every admission attempt:

1. locks the allocatable-capacity ledger or obtains an equivalent serializable transaction;
2. reconciles live reservations and expired leases;
3. selects a runnable queue item according to the scheduling policy;
4. reserves its complete resource profile atomically;
5. records the reservation epoch and provisioning operation before any Docker side effect;
6. commits, then invokes the idempotent workspace provider;
7. confirms the reservation only after the expected labeled resources are reconciled.

Two API workers, two scheduler ticks, repeated WebSocket delivery, browser refresh, or an orchestrator retry must resolve to the same run, reservation, workspace, and resource bundle. They cannot create duplicate containers, networks, volumes, database mutations, or agent executions.

Every provider operation retains the existing idempotency and fencing contract. A stale scheduler or runner epoch cannot provision, write, promote, release another run's reservation, or resume after cancellation.

## 7. Failure semantics and automatic recovery

Temporary lack of resources is a normal pre-side-effect state:

- it is reported as `queued_for_capacity`, not HTTP 500;
- it is safe to retry internally;
- it never becomes `indeterminate` merely because the host is full;
- it does not mark generation failed or charge the prompt twice.

`indeterminate` remains reserved for a provider call whose side effect may have happened but cannot yet be proven. In that case, reconciliation searches immutable project/workspace labels and operation identifiers before any retry creates resources.

If provisioning fails after a reservation is recorded, the scheduler reconciles actual resources, records a precise retryable or terminal reason, and releases only capacity that is proven unused. Retryable infrastructure failures remain attached to the same run and use bounded backoff. Product, model, build, migration, and policy failures stay distinct from capacity waiting.

On API, orchestrator, worker, or host startup, reconciliation must:

- compare durable reservations with labeled Docker resources;
- recover or expire abandoned scheduler and runner leases;
- preserve reservations for operations whose outcome is still unknown;
- return proven-unused capacity to the ledger;
- restore the queue in deterministic order;
- wake the scheduler automatically.

## 8. Lifecycle policy

The scheduler may release capacity through the following safe sequence:

1. choose only a bundle with no active fenced operation;
2. persist a checkpoint and process manifest;
3. confirm PostgreSQL consistency and preserve its volume or backup contract;
4. stop application, browser, executor, Redis, and database compute through idempotent provider operations;
5. mark the bundle hibernated only after actual container state is reconciled;
6. release the matching reservation epoch;
7. admit queued work.

Wake follows the reverse contract and restores the same workspace and project data. It may recreate containers, but immutable labels and the workspace identity continue to refer to the same project. An accepted public release stays available through the normal serving path while its editable cell sleeps.

The existing older owner project must be handled by this lifecycle, not removed manually. After rollout it can be checkpointed and hibernated when idle, freeing capacity for `БарберПро` and subsequent projects while retaining its data.

## 9. Isolation and security

Scheduling must not weaken the owner-canary isolation contract. Every project retains separate workspaces, PostgreSQL and Redis storage, networks, secrets, process supervision, and immutable Docker labels. A capacity reservation grants no authority to select host paths, images, ports, capabilities, or networks.

The current Docker backend remains restricted to the verified owner allowlist. Removing a project-count cap does not authorize arbitrary external accounts or change the future K3s + Kata boundary for public multi-tenant model-visible execution.

## 10. API and UI behavior

The normal prompt endpoint should expose one stable generation run and explicit phases:

```text
accepted
  -> queued_for_capacity (zero or more times across recovery)
  -> provisioning | waking
  -> generating
  -> verifying
  -> completed | failed | cancelled
```

The UI must:

- confirm that the project and prompt were saved immediately;
- distinguish waiting for server resources from a generation failure;
- continue receiving progress after refresh or reconnect;
- allow cancellation while queued without starting the cell later;
- start automatically when admitted;
- preserve the same project page, run timeline, and prompt message;
- show a precise terminal error only when recovery is no longer safe.

The public API stays backward compatible. Older clients that do not render the new phase still see an in-progress run, not a false failure.

## 11. Observability and operator controls

The control plane records and exposes:

- total and allocatable host resources by dimension;
- confirmed and provisional reservations;
- active, idle-ready, and hibernated cell counts as observations only;
- queue depth, oldest wait, admission latency, and wake latency;
- admission decisions and the limiting resource;
- hibernation, wake, retry, reconciliation, and stale-lease events;
- project, workspace, run, operation, reservation epoch, and profile version on every related event.

Operator controls are limited to resource profiles, protected host reserves, idle grace periods, scheduler enablement, and an emergency fail-closed switch. There is no `max_active_bundles` product gate. If the scheduler is disabled or unhealthy, new runs remain durably queued and existing active runs are not silently duplicated.

## 12. Rollout and migration

1. Add failing concurrency, queue, restart, capacity, and hibernation tests around the existing admission path.
2. Add durable queue/reservation state and explicit lifecycle statuses through a reversible database migration.
3. Implement the transactional capacity coordinator and scheduler reconciliation loop with cell routing still disabled.
4. Change orchestrator capacity refusal from a generic 500 into the typed, retryable pre-side-effect outcome used by the API.
5. Implement safe idle hibernation/wake and connect reservation release to proven provider state.
6. Add API event mapping and MAX Studio waiting/cancellation/reconnect UI.
7. Remove the count-based `cell_max_active_bundles` constraint after all code paths use capacity reservations.
8. Deploy with the owner canary enabled, reconcile the existing cell, and verify health before admitting new work.
9. Run the production acceptance scenario with at least three rapidly submitted real projects.
10. Keep rollback data-compatible: disabling scheduling stops new admissions but preserves the queue, projects, volumes, and accepted releases.

## 13. Verification matrix

### Admission and concurrency

- submit three or more new owner projects rapidly; all prompts are accepted once and none ends with `active_bundle_limit` or `indeterminate`;
- each project receives a unique workspace, network, workspace volume, PostgreSQL storage, Redis storage, run, operation, and reservation epoch;
- simultaneous scheduler ticks and duplicate deliveries produce one bundle and one generation per run;
- aggregate confirmed plus provisional reservations never exceed allocatable capacity in any dimension;
- when capacity is unavailable, runs remain queued and start automatically after safe hibernation or completion releases capacity;
- a queued cancellation never provisions resources later.

### Persistence and application completeness

- each admitted project produces a real application with frontend, server-side logic, PostgreSQL migrations and writes, Redis-backed behavior where requested, build evidence, runtime health, and browser checks;
- refresh, WebSocket reconnect, API restart, worker restart, orchestrator restart, and scheduler restart preserve the same run and queue order;
- hibernate/wake preserves source, Git history, database data, accepted snapshot, agent checkpoint, and project identity;
- waking an old project does not mutate or stop another project's resources.

### Failure and recovery

- injected failure before provider side effects releases the reservation safely and retries the same run;
- injected ambiguous provider response reconciles labels before retry and creates no duplicate resource;
- stale leases and stale reservation epochs cannot write or release current state;
- full disk, inode pressure, memory pressure, and protected-host-reserve pressure produce typed waiting states and clear telemetry;
- active generation is never preempted; only proven-idle cells hibernate automatically.

### Production proof

- test through the real approved account `undj00x03@gmail.com` and public MAX Studio path;
- submit at least three projects back-to-back with substantially different full-stack briefs;
- confirm all three complete without a second user action, resource collision, duplicate generation, lost prompt, or lost data;
- reopen each project and exercise a real frontend-to-backend-to-PostgreSQL path;
- record exact deployed revision and HTTP 200 health for web, API, gateway, and orchestrator;
- confirm the existing project remains recoverable after hibernation and the platform services stay inside protected host headroom.

## 14. Non-goals

- promising infinite simultaneous generations on finite hardware;
- bypassing CPU, memory, disk, inode, process, or host-reserve safety checks;
- deleting old projects or their data as an admission shortcut;
- opening the Docker owner canary to arbitrary external accounts;
- weakening per-project single-flight, fencing, isolation, or completion evidence;
- considering generated files alone a complete application;
- claiming real MAX bot publication without the required moderated bot credential and live signed integration proof.

## 15. Final acceptance

This change is complete only when:

1. no fixed project or active-bundle count participates in normal owner-canary admission;
2. capacity is reserved atomically from real host resource dimensions with a protected reserve;
3. excess projects wait durably and begin automatically without prompt resubmission;
4. idle cells release compute without losing workspace, PostgreSQL, Redis durability, snapshots, or accepted releases;
5. duplicate requests, concurrent schedulers, retries, cancellation, and component restarts cannot create duplicate resources or generations;
6. at least three back-to-back production projects complete as separate full-stack applications through the approved account;
7. the previous project remains recoverable, all isolation probes pass, and platform health remains green;
8. the exact implementation revision is committed, pushed, deployed through the documented production compose project, and verified live.
