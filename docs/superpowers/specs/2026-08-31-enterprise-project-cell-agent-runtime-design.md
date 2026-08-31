# Enterprise Project Cell Agent Runtime

**Status:** approved direction; written contract prepared for owner review on 2026-08-31

**Target:** a Codex/Claude-class autonomous development agent for Omnia projects, including full MAX applications

**Provider path:** Omnia LLM Gateway (`LLMGW`) remains the only model-provider boundary

**First environment:** the current production server, as an isolated single-node pilot beside the existing Docker production stack

## 1. Outcome

When a user sends a prompt, Omnia creates or wakes one isolated Project Cell for that project. Inside the cell, a resident agent receives the prompt, works with a real persistent repository, runs commands, installs dependencies, starts services, uses a browser, writes migrations, runs tests, fixes failures, and prepares a verified candidate. The accepted project is changed only after an atomic promotion step.

The system must not impose an artificial ceiling on product complexity, file count, screen count, framework choice, frontend/backend composition, migrations, background jobs, or duration of work. Physical capacity is added by joining worker servers; it is not encoded as a product limitation in the agent protocol.

The freedom boundary is explicit:

> The model-visible executor has a full shell and userland root inside its own Kata microVM sandbox. The trusted session controller, lease credential, event writer, broker client, and verifier never share that OS sandbox. Neither side can access the host, container runtime sockets, Kubernetes control plane, another project, or long-lived Omnia credentials.

This is the security boundary for all later implementation decisions.

## 2. User experience

1. The user creates a project or opens an existing one.
2. The user writes an ordinary product request, not a technical deployment instruction.
3. Omnia reserves one durable generation run and places it in the project's workspace queue. If the workspace is free, dispatch is immediate.
4. When the queue item acquires the workspace, Omnia creates one agent session and the Project Cell starts or resumes with the project's repository and prior agent state.
5. The interface streams understandable steps: planning, files, commands, tests, browser checks, fixes, and final verification.
6. A draft preview updates while the agent works. The last accepted version remains available throughout the run.
7. Refreshing the browser or restarting API, orchestrator, runner, or gateway does not lose the task.
8. Pause preserves the draft and continuation point. Resume continues the same session. Cancel kills the process group and permanently blocks promotion of that candidate.
9. After verification, Omnia atomically promotes the candidate, creates the normal repository snapshot, and switches the preview/runtime.
10. If final verification fails, the accepted project stays unchanged and the user can inspect or resume the draft.

## 3. What is wrong with the current execution path

The current revision `d0433aba640f0ccfdc55bdd23ace44119864b98c` already provides useful tools, but the ownership boundary is inverted:

- the native agent loop and transcript live in the central API process;
- each MAX shell call creates a disposable no-network container, copies the repository, runs one command, and returns a diff;
- ordinary file tools can write directly into the live preview;
- legacy shell execution runs inside the application runtime;
- API restart marks active runs failed instead of transferring a durable lease;
- Stop cancels an in-process task but does not transactionally undo files or reliably terminate all external work;
- repository objects, database pointers, and runtime promotion are separate operations;
- MAX-generated code eventually runs in a live container that has application secrets and public egress;
- production currently uses `runc`; no mandatory microVM runtime is active.

Therefore the current design is an agent loop in the platform controlling containers. The target design is an agent process inside a project-owned isolated development machine.

## 4. Chosen architecture

### 4.1 Project Cell

A Project Cell is the durable security and lifecycle unit for one project:

```text
Project Cell
├── persistent workspace and agent home
├── control runner (trusted, non-root)
│   ├── model/tool loop
│   ├── leases, operations and checkpoints
│   └── command dispatch
├── root tool executor (untrusted Kata guest)
│   ├── shell, files, Git and package managers
│   └── process supervisor
├── build and browser workers (separate Kata guests)
├── trusted verifier
├── draft application runtime
├── project-scoped data services
└── capability client
```

The cell is represented by Kubernetes resources, but every untrusted execution pod must use the `kata` RuntimeClass. Kata places the pod inside a hardware-virtualized sandbox. The cell fails closed if that runtime is unavailable; it must never silently fall back to `runc`.

The project workspace and agent home are persistent volumes. Compute can be recreated without losing source code, Git metadata, caches, checkpoints, or the task transcript. Language dependencies are pinned in project manifests. Any system-level package installed during a run is also recorded in a generated cell environment manifest so a replacement runner can reproduce the environment.

### 4.2 Cell trust domains

The control runner, root-capable tool executor, draft runtime, browser worker, build worker, and trusted verifier are separate workloads. Root is allowed only inside an untrusted executor or build guest. An executor never shares a PID namespace, network identity, service-account credential, credential mount, or writable agent home with the control runner.

The control runner is non-root, has no interactive shell, and never loads generated code into its process. It dispatches commands to disposable or session-fenced executor workloads. The verifier control/signing process also never executes or imports candidate code and never mounts a writable candidate filesystem. Builds, project tests, migrations, browsers, and application health probes run in separate untrusted Kata verification workers that have no verifier identity or signing material. Trusted scanners inspect immutable source/image digests without invoking candidate hooks. The verifier signs only controller-captured structured results bound to those immutable digests.

### 4.3 Control plane and execution plane

```text
Browser
   │ REST + WebSocket
   ▼
Omnia API ───── durable state, event replay, finalizer
   │ internal authenticated calls
   ▼
Orchestrator ── WorkspaceProvider ── Kubernetes provider
                                      │
                                      ▼
                              Project Cell (Kata)
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
               Omnia LLMGW      capability broker   artifact store
                    │
                    ▼
                  LLMGW
```

The API remains the public product boundary. It does not execute the agent loop. The orchestrator owns desired workspace lifecycle, but does not own the transcript and cannot create arbitrary pods. The control runner owns the model loop while it holds a renewable session lease; root executors own only fenced operations. The API finalizer is the only component allowed to accept a candidate into project history.

### 4.4 Replaceable `WorkspaceProvider`

The orchestrator uses one interface instead of Kubernetes-specific calls throughout business logic:

```python
class WorkspaceProvider(Protocol):
    async def ensure(spec: WorkspaceSpec) -> WorkspaceHandle: ...
    async def wake(workspace_id: UUID) -> WorkspaceHandle: ...
    async def pause(workspace_id: UUID, checkpoint: CheckpointRef) -> None: ...
    async def destroy(workspace_id: UUID) -> None: ...
    async def status(workspace_id: UUID) -> WorkspaceStatus: ...
    async def execute_control(workspace_id: UUID, action: ControlAction) -> ControlResult: ...
```

The first production provider is Kubernetes with Kata. A Docker provider may exist only as a local developer adapter. It is not a permitted fallback for untrusted production prompts. A later dedicated VM provider can replace the execution backend without changing the API, session model, runner protocol, or promotion contract.

## 5. First-server topology

The current server runs the pilot beside the existing production Docker stack:

- K3s uses its own bundled containerd; existing Docker containers and compose project `full` remain unchanged.
- Pod CIDR is `10.42.0.0/16`; Service CIDR is `10.43.0.0/16`. These do not overlap the observed Docker ranges `172.17.0.0/16` through `172.31.0.0/16` or the host private range `10.16.0.0/16`.
- K3s Traefik and ServiceLB are disabled so ports 80/443 and the current nginx remain untouched.
- The local-path provisioner is kept only for the single-node pilot. It is durable across pod recreation and same-node reboot while that node disk survives; it provides no node failover, volume snapshot, or cross-node recovery.
- The Kubernetes API binds to the private administration interface and is firewall-restricted to the host and approved worker addresses. It is not exposed as a public product endpoint.
- The existing host orchestrator receives credentials that can submit and observe only the constrained `ProjectCell` custom resource. It has no direct access to namespaces, Pods, Jobs, Deployments, Services, RBAC, admission, NetworkPolicy, quotas, RuntimeClass, or storage resources.
- The existing API continues to call the orchestrator through the current internal path. It never receives cluster credentials.
- New preview traffic enters through the existing nginx and an explicit internal upstream; K3s does not replace current ingress during the pilot.

Future workers join only over a private or VPN transport. The firewall admits the documented K3s control, kubelet, and selected overlay ports only between approved node addresses; overlay ports are never public. `RuntimeClass` includes execution-node selectors, taints/tolerations, and measured `podFixed` overhead so the scheduler cannot place Kata workloads on an unprepared node or ignore microVM overhead.

Before any cross-node lease recovery, a quiesced migration copies and verifies the complete workspace into snapshot-capable CSI storage, atomically changes `storage_ref`, retains the old volume for rollback, and only then permits scheduling on another node. Repository, checkpoint, transcript, and candidate artifacts always have an off-runner durable copy; package caches remain disposable.

The host already exposes `/dev/kvm`, Intel virtualization support, cgroup v2, and containerd. K3s and Kata installation still require a real runtime smoke before any user prompt is routed to the cell backend.

### 5.1 Capacity gate for the pilot

Before K3s installation, controlled cleanup must leave at least:

- 6 GiB available memory;
- 60 GiB free disk;
- the existing production services healthy;
- no swap-dependent safety assumption;
- one empty port set for the private K3s control plane and agent-controller path.

These numbers are only minimum preconditions. Admission also requires measured `NodeAllocatable` and protected host headroom to exceed the complete cell requests plus measured Kata `RuntimeClass` overhead while preserving memory and CPU for the existing Docker production stack. The gate measures disk bytes, inodes, IOPS, `/dev/shm`, ports, and a production-health soak under one worst-case canary cell. A momentary free-memory reading alone can never pass the gate.

The pilot admits only one Project Cell at a time on the current server. This is a rollout guard, not a product-contract limitation. More concurrency is enabled by joining worker servers.

### 5.2 Admission and host resource fence

Project Cell resources are created only by a cluster-side cell controller from a constrained `ProjectCell` desired-state object. The host orchestrator cannot create arbitrary Pods, Jobs, Deployments, Services, policies, roles, or volumes. It may submit and observe only the constrained custom resource.

A fail-closed admission policy is owned by a different identity. It rejects every cell Pod, Deployment, Job, and ephemeral container unless the approved `kata` handler is present. It also rejects privileged mode, host namespaces, host ports, `hostPath`, host devices and sockets, automatic service-account credential mounts, unsafe volume types, and `NodePort` or `LoadBalancer` services. Cell identities cannot mutate admission policy, RBAC, NetworkPolicy, quota, RuntimeClass, namespace enforcement labels, or the cell controller. Routing remains disabled whenever admission, CNI policy enforcement, or the per-node Kata microVM smoke is unhealthy.

Every cell namespace has mandatory `ResourceQuota` and `LimitRange` for CPU, memory, ephemeral storage, PVC size, Pods, PIDs, and log/artifact throughput. K3s reserves measured host headroom for the existing Docker production stack. New cell admission stops before host memory or disk pressure; the canary is evicted before production services cross their recorded baseline thresholds.

## 6. Components and responsibilities

### 6.1 Omnia API

- creates `GenerationRun` plus a workspace queue record on prompt acceptance; creates `AgentSession` transactionally only when that queue item acquires the free workspace;
- exposes prompt, pause, resume, cancel, status, event replay, and draft endpoints;
- persists ordered agent events and maps them to the existing WebSocket contract;
- never shells into a cell and never writes draft files directly;
- issues short-lived project- and session-scoped runner identity;
- finalizes a verified candidate with compare-and-swap checks;
- maintains backward-compatible public routes while the legacy backend remains available.

### 6.2 Orchestrator

- implements the `WorkspaceProvider` lifecycle;
- submits a constrained `ProjectCell` desired-state object to the cluster-side controller;
- has no permission to create arbitrary workloads or weaken admission, identity, storage, network, or resource policy;
- starts, pauses, resumes, reconciles, and destroys cells idempotently;
- watches readiness and publishes infrastructure events;
- cannot approve a candidate or change the accepted repository pointer.

### 6.3 Agent runner

- lives inside the cell and owns the model/tool loop;
- restores the latest confirmed checkpoint before accepting a lease;
- calls the Omnia LLM Gateway for every model turn;
- dispatches shell, package, browser, build, and other untrusted execution to separate root-capable executor boundaries;
- never executes generated code in its credential-bearing process or pod;
- fences each executor and its process tree by session, lease epoch, and operation id;
- records an operation id before every side effect;
- checkpoints after every completed model turn and tool result;
- produces an immutable candidate manifest; a separate trusted verifier recomputes and signs mandatory evidence;
- never receives cluster or host credentials.

### 6.4 Draft runtime

- runs the generated frontend, backend, workers, and project-scoped services;
- reads the draft workspace but has a separate runtime identity;
- receives only project runtime credentials;
- exposes readiness and logs to the runner and preview gateway;
- can be restarted independently of the agent runner.

### 6.5 Capability broker

The broker performs operations that require Omnia-owned credentials or controlled external access. Initial capabilities are:

- Context7 documentation lookup;
- MAX API test actions and webhook validation;
- controlled publication and domain operations;
- object storage operations outside the project volume;
- operation-scoped registry credential minting for the per-session build worker; the broker never builds, deploys, changes a `ProjectCell`, or activates a runtime;
- allowlisted access to existing Omnia integrations.

Each request includes project, run, session, operation, capability, and expiry claims. The broker verifies ownership and policy, records an audit event, redacts secret values, and returns only the minimum result. Generated code cannot call the broker with the runner identity.

The capability broker is a standalone deployment and operating-system identity. It has no Docker or containerd socket, kubeconfig, host mount, sudo access, or access to the orchestrator secrets root. It receives only capability-specific credentials. No network-reachable cell endpoint runs inside the legacy Docker-socket process or the Kubernetes controller process.

Candidate runtime materialization is performed only by the cell controller from constrained `ProjectCell` desired state. Traffic activation is performed only by the API finalizer's versioned active pointer; no broker endpoint exposes deployment authority.

A broker call requires authenticated workload identity plus a single-use API-issued grant bound to `project_id`, `run_id`, `session_id`, `lease_epoch`, `cancel_epoch`, `workspace_fencing_epoch`, `operation_id`, normalized request digest, audience, and expiry. Runner identity alone is insufficient. Draft runtimes and executor pods have no route to the broker. The existing shared `X-Internal-Token` is not valid for Project Cell identities.

### 6.6 Omnia LLM Gateway

- remains the sole route to LLMGW;
- accepts native tool-use messages from the runner;
- preserves `project_id`, `run_id`, `session_id`, `operation_id`, model name, and trace id;
- streams model output and tool calls without buffering the complete turn;
- closes the upstream request when a valid cancel epoch is observed;
- never receives workspace or cluster credentials.

## 7. Durable data model

The existing `generation_runs` table remains the public compatibility record. Full agent state is stored separately.

### `agent_workspace_queue`

- `id`, `generation_run_id` (unique), `project_id`, requested backend, created time;
- state `queued`, `dispatching`, `dispatched`, `cancelled` and a stable queue order;
- no lease, credentials, worktree, or `agent_sessions` row exists while the request is queued.

Dispatch locks the free workspace and queue head in one transaction, marks the item dispatched, increments the workspace fencing epoch, and creates the single non-final `agent_sessions` row. A cancelled queued item never creates execution identity or storage.

### `agent_workspaces`

- `id`, `project_id` (unique), `backend`, `provider_ref`;
- `state`, `desired_state`, `image_digest`, `workspace_revision`;
- authoritative `workspace_fencing_epoch`, incremented before every new writable owner;
- `storage_ref`, `node_ref`, `last_ready_at`, `last_error`;
- timestamps and optimistic `version`.

### `project_data_revisions`

- `id`, `project_id`, `parent_revision_id`, backend/storage reference, state;
- immutable schema version/digest, migration digest, data snapshot/CDC cursor, restore evidence;
- states `preparing`, `replicating`, `sealed`, `active`, `retained`, `rejected`;
- a data revision becomes accepted only through an active `project_releases` record.

Every candidate uses its own data revision cloned from the current active revision. It may receive logical changes from the active revision until the final write fence, but it never writes into the accepted data revision. The first MAX pilot supports this contract for its PostgreSQL project schema; other stateful services must provide the same snapshot/sync/seal interface before the agent may promote them.

### `project_releases`

- `id`, `project_id`, accepted snapshot id, immutable runtime revision;
- data revision id, deployment identity/digest, job-owner release id, schema/data digest;
- state, verification digest, promotion id, activation and retention timestamps;
- `projects.active_release_id` is the single user-visible source/runtime/data/job pointer.

The runtime gateway, project-data gateway, and job controller resolve one active release record. They refuse requests whose runtime, data, or job identity does not match that record; no independent pointer can expose a mixed release.

### `agent_sessions`

- `id`, `generation_run_id` (unique), `workspace_id`;
- `state`, `desired_state`, `base_snapshot_id`;
- immutable `base_active_release_id` and `base_data_revision_id`, captured when the queue item acquires the workspace;
- `lease_owner`, `lease_epoch`, `lease_expires_at`;
- `cancel_epoch`, current `workspace_fencing_epoch`, `last_event_seq`, `last_checkpoint_seq`;
- `candidate_id`, `started_at`, `finished_at`, `last_error`.

The pilot permits at most one non-final session per workspace, enforced by a database partial unique constraint and a workspace fencing epoch. Additional prompts queue. Session worktrees, scratch paths, process supervisors, agent state, and credentials are session-specific; pause or cancel can address only the matching fenced process tree.

### `agent_checkpoints`

- `session_id`, monotonically increasing `seq`;
- transcript object key and digest;
- workspace Git revision plus `workspace_artifact_ref` and digest for a content-addressed Git bundle/filesystem delta containing every dirty and untracked byte;
- completed operation cursor and active process metadata;
- runner image digest, exact environment OCI layer/image digest, repository snapshot metadata, created time.

Checkpoint payloads are immutable objects in MinIO. Checkpoint completion is acknowledged only after the workspace artifact and transcript are uploaded outside the executor and digest-verified. Active processes are not claimed to survive; they are terminated and restarted from declared supervisor state. Orphan objects are safe and garbage-collected later.

Object keys are derived server-side by the artifact service from authenticated project/session identity; the runner cannot choose another tenant's key. The service enforces size and media-type limits and verifies the digest on every read used for recovery or promotion.

### `agent_events`

- `session_id`, monotonically increasing `seq` with a unique constraint;
- `event_type`, safe JSON payload, visibility, operation id, created time;
- payloads never contain credentials, raw environment dumps, or unrestricted command output.

The database event log is the recovery source for UI replay. Redis remains a delivery accelerator, not the source of truth.

### `agent_operations`

- unique `(session_id, operation_id)`;
- tool name, normalized request digest, state, result reference, side-effect class;
- started, heartbeat, completed, and failure metadata.

Repeated delivery returns the stored result or safely resumes the same operation when the downstream system durably accepts the same idempotency key. If a runner dies after an external effect but before result persistence and downstream state cannot be queried, the operation becomes `indeterminate`; it is not automatically replayed and must be reconciled or compensated by a new audited operation.

### `agent_candidates`

- `session_id`, `base_snapshot_id`, `base_active_release_id`, `base_data_revision_id`, candidate Git revision;
- complete file manifest, content-addressed Git object bundle `artifact_ref`, and artifact digest;
- `prepared_lease_epoch`, `prepared_cancel_epoch`, `prepared_workspace_fencing_epoch`, stable `promotion_id`;
- candidate `data_revision_id`, migration operation id, expected/applied schema digests, applied schema version, and verified restore-point reference;
- verification manifest, independent verifier identity/digest, runner/verifier image digests, admission/network/broker policy bundle digest, egress-proxy configuration digest, created time;
- state `prepared`, `verified`, `promoting`, `promoted`, `rejected`.

## 8. State machines

### 8.1 Workspace

```text
absent → provisioning → ready → busy
                     ↘ failed
busy → pausing → hibernated → waking → ready
busy/ready/hibernated → destroying → absent
```

Every transition is driven by `desired_state` and reconciled repeatedly. A repeated request is a no-op when the desired state is already reached.

### 8.2 Agent session

```text
starting → running → verifying → promoting → completed
               │          │
               └→ pausing → paused → resuming → running
               └→ cancelling → cancelled

starting/running/verifying → interrupted → resuming
any non-final state → failed only after a non-recoverable recorded error
```

`pause` and `cancel` are different commands:

- Pause increments the control epoch, ends the current model request at a safe boundary, deletes the executor pod/cgroup and verifies its death, marks any unfinished side-effecting operation `indeterminate`, writes a checkpoint, preserves the draft, and only then moves to `paused`.
- Cancel and finalize serialize on the same `agent_sessions` row. Cancel locks a non-final session, increments `cancel_epoch`, changes state, commits, then closes model/broker requests and kills the fenced process group. Finalize locks the same row. The first committed transaction is the linearization point: if cancel wins, finalize and all old-epoch consumers reject the candidate; if finalize wins, cancel returns `409 already_promoted` and the UI does not report a cancellation.
- Resume acquires a new lease epoch and continues from the last confirmed checkpoint.

Closing a request does not claim to undo an external side effect already accepted by another system. Compensation is a separate audited idempotent operation. Cancel guarantees that an unpromoted candidate cannot become accepted after the cancel transaction commits.

The runner renews a 30-second lease every 10 seconds. A replacement may acquire it only after expiry and must increment `lease_epoch`. Every event, operation, checkpoint, and finalize request carries that epoch; stale runners are rejected.

Lease expiry fences control-plane writes but does not by itself fence a workspace. Epoch N+1 may start only after the cell controller confirms termination of every runner, executor pod, cgroup, and network identity from epoch N, confirms no prior operation is still active, and obtains exclusive writable ownership of the session worktree. An expired lease never authorizes concurrent writers. The local-path pilot relies on controller-enforced pod termination plus the one-non-final-session database constraint; cross-node recovery requires CSI storage with exclusive writer and fencing support.

## 9. Agent loop

1. Acquire or renew the session lease.
2. Restore transcript, operation cursor, repository state, and environment manifest.
3. Ask the LLM Gateway for the next model turn.
4. Persist the complete model response and proposed tool calls before execution; a partial stream can never authorize a tool call.
5. Derive each stable operation id from the persisted turn id and tool-call id, then reserve it.
6. Dispatch untrusted work to a fenced executor or use a single-use capability grant.
7. Persist the result, safe output reference, and workspace revision.
8. Write a checkpoint.
9. Continue until the model declares completion or verification requests another repair loop.
10. Ask the verifier controller to schedule isolated untrusted verification workers for build, tests, browser, migration, and application probes; trusted scanners inspect immutable artifacts only.
11. Produce the candidate manifest and attach evidence signed over controller-captured structured results and immutable digests.
12. Request finalization from the API.

The loop has no fixed product-step count. Operational guards stop runaway process trees, disk exhaustion, log flooding, or repeated identical failures, but do not cap legitimate application complexity. Long work continues through multiple durable leases and checkpoints.

## 10. Workspace, Git, and processes

- `/workspace/repo` is a real Git working tree created from the accepted snapshot.
- Each run uses a draft branch/worktree rooted at its `base_snapshot_id`.
- `/workspace/cache` stores project package caches; `/home/agent` stores the agent's durable configuration and session artifacts.
- The accepted branch is read-only to the runner. Only the API finalizer can advance it.
- Shell commands run in an executor pod/cgroup with tracked descendants, output limits, heartbeat, cancellation, and structured exit evidence. A process group alone is not a fence: pause, cancel, and lease transfer delete the old executor sandbox and verify cgroup/pod death before replacement.
- Background services are registered with the session-fenced supervisor; unregistered detached processes are terminated at checkpoint or pause.
- Rootless BuildKit runs in a per-project, per-session Kata build workload with no host socket, host mount, privileged mode, `security.insecure`, `network.host`, or shared writable cross-project cache. Build traffic uses the egress proxy. Registry authorization derives the project repository server-side from workload identity. Build credentials are operation-scoped, never persisted in layers/cache, and unavailable to the generated runtime. Output is referenced only by immutable digest; build identity cannot deploy or promote it.
- Browser automation runs in its own untrusted Kata workload against the draft runtime and stores screenshots, traces, console output, and network failures as artifacts. Preview content cannot access runner or broker identity.
- System-package reproducibility is captured as an immutable OCI layer/image digest with repository snapshot metadata; a package-name list alone is not accepted as a recoverable environment.
- Context7 is exposed as a runner tool through the existing Omnia client contract.

## 11. Draft preview and atomic promotion

```text
accepted snapshot
        │
        ▼
isolated draft worktree ──► draft preview
        │
        ▼
verification manifest
        │
        ▼
candidate object
        │ compare-and-swap
        ▼
accepted snapshot + runtime outbox
```

Finalization follows a prepare/activate/commit pattern:

1. The runner uploads a complete immutable Git bundle and candidate manifest. A separate build workload creates an immutable candidate runtime.
2. A trusted data-revision controller clones the session's immutable `base_data_revision_id`, whose parent release is `base_active_release_id`, into a new candidate data revision and records that exact parent plus its snapshot/change-capture cursor. It never imports or executes candidate migration code. Active accepted data remains untouched.
3. An untrusted migration worker receives credentials only for the candidate data revision and applies the project's arbitrary migration there. Trusted scanners normalize and inspect the resulting schema/digest; no candidate hook runs with an accepted-data identity. For the low-downtime path, independently normalized declarative expand operations may use continuous change capture. A destructive/incompatible migration requires a final fresh copy after the write fence and is never applied in place to the active accepted revision.
4. The cell controller materializes the exact immutable release target behind a candidate-only endpoint with its final release identity and configuration. A versioned project-data gateway binds that identity only to the candidate data revision. User traffic and background-job leases remain disabled. The release will keep the same identity, data revision, configuration, image digest, and deployment identity after promotion; it needs no credential injection, rebinding, or restart.
5. Isolated untrusted verification workers check build, application health, browser flow, and migrations against that exact runtime/data pair. Trusted scanners inspect immutable source/image/policy digests without running candidate hooks; the verifier controller signs only captured structured results bound to the release and data-revision digests.
6. Finalization acquires the project promotion fence. The runtime gateway stops new mutating requests to the old release, the job controller stops issuing old-release leases, existing project-data connections drain, and the data-revision controller advances the candidate revision to the exact accepted-data barrier. Expand-only revisions catch up through change capture; destructive revisions are rebuilt from the fenced final copy and rerun migration/verification entirely inside the candidate boundary. The candidate revision is then sealed and the exact inactive release is health-checked again against it.
7. In the final serializable transaction the API locks the same session and project and checks:
   - session is still `promoting`;
   - lease, cancel, and workspace fencing epochs match the prepared candidate;
   - current accepted snapshot equals `base_snapshot_id`;
   - `projects.active_release_id` still equals `base_active_release_id`;
   - the current active release still references `base_data_revision_id`, and the candidate parent/CDC source matches that same data revision;
   - candidate and independent verification are valid;
   - the sealed candidate data revision matches the fenced snapshot/change cursor and verified schema/data digest;
   - the already materialized inactive release deployment identity/image/config digest is healthy against that same data revision;
   - the promotion id has not already completed.
8. The final transaction creates the accepted snapshot and atomically advances one active-release record containing `accepted_snapshot_id`, `active_runtime_revision`, `active_data_revision_id`, deployment identity/digest, and job-owner release id. Runtime, data, and job gateways resolve this same versioned record and refuse mismatched pairs. The candidate runtime/data pair thereby becomes accepted without receiving new credentials or restarting. The transaction marks the candidate promoted and writes cleanup/audit outbox events.

If preparation, migration, fencing, or finalization fails, the active-release record does not change, the old runtime/data pair is unfenced, and the rejected candidate revision is retained for evidence or discarded later. Post-commit outbox work cleans up old resources and records additional evidence; it is not the first activation of an unverified runtime. A rollback-window policy may keep a compatible change stream to the retained prior data revision. Runtime rollback changes the full runtime/data pair only when that stream and schema compatibility are proven. Snapshot rollback alone never claims to roll back project data; data restore remains a separate fenced operation with verified backup evidence.

## 12. Security model

### Isolation

- pinned, verified Kata release and mandatory RuntimeClass for runner, executor, draft runtime, browser, verifier, and build steps;
- dangerous runtime annotations and guest/host path overrides are rejected by admission;
- no privileged pods, host PID/IPC/network namespaces, hostPath mounts, host devices, or Docker/containerd sockets;
- no automatic service-account credential mount inside runner or generated workloads;
- dedicated namespace, identities, volumes, and network policy per project;
- root inside the microVM-backed container is allowed; that root has no authority on the host.

### Network

- default-deny ingress and egress is enforced for IPv4 and IPv6; policy-enforcement readiness is a routing prerequisite;
- Kubernetes NetworkPolicy is not treated as the host-isolation boundary because node traffic needs separate enforcement;
- executor, build worker, browser worker, and runner reach public HTTPS only through an authenticated egress proxy plus host-aware CNI/firewall policy; direct Internet IP traffic, UDP/QUIC, alternate DNS, node addresses, Docker/Kubernetes ranges, RFC1918, loopback, link-local, multicast, reserved ranges, metadata endpoints, and IPv6 equivalents are denied;
- the proxy resolves destinations itself and repeats address classification after every DNS answer and redirect, including IPv4-mapped IPv6 and numeric/obfuscated forms;
- package and documentation destinations are policy allowlists at the proxy; draft runtime uses a separate, narrower identity and policy;
- private-destination access runs broker-side under a single-use grant bound to hostname/IP, port, protocol/method, request digest, response limit, and expiry; it never creates a direct pod egress exception;
- Phase 3 proves node, host, Docker, control-plane, metadata, and cross-project denial from inside Kata, not only from an ordinary pod.

### Credentials

- long-lived Omnia and provider credentials stay only in gateway/broker services;
- runner identity is short-lived, session-scoped, audience-bound, and rotated on lease change;
- draft, migration, verifier, and accepted runtime credentials are distinct and project-scoped; accepted credentials never mount in runner, executor, browser, or verifier, and draft credentials are revoked at cancel/destroy;
- generated runtimes use a short-lived release identity and the project-data gateway endpoint, never a reusable accepted-database secret; the gateway target is the release's candidate revision before promotion and the same revision after that release becomes active;
- command output, environment listings, events, traces, and model context pass through redaction;
- every broker action is auditable by project, session, operation, capability, and result in an append-only audit store with explicit retention, separate from the session event log.

### Supply chain

- runner and base images are digest-pinned and signed;
- package lifecycle scripts run only inside the cell boundary;
- candidate evidence includes dependency lockfiles, image digests, SBOM, and vulnerability scan result;
- a failed mandatory security check rejects promotion but leaves the draft available for repair.

## 13. Controlled server cleanup

Cleanup is a data-preserving migration step, not a broad Docker prune.

This procedure supersedes the cleanup and optional-isolation instructions currently present in `docs/08-vps-setup.md`. That runbook must be corrected before Phase 1. For this rollout, `docker system prune`, global `docker builder prune`, global `docker image prune`, and every `--volumes` cleanup are forbidden on the production host.

### Inventory and proof

1. Record current Git revision, compose project, service health, container/image/volume/build-cache inventory, disk, memory, and Docker networks.
2. Reconcile every `omnia.project_id` container against the platform database, accepted snapshot, runtime directory, and named volumes.
3. Create an explicit JSON manifest with one decision per container: preserve, hibernate, remove envelope, or investigate.
4. Prove that every hibernated or removed development envelope can be reconstructed from accepted source and retained project data.
5. Take and verify the normal production backup before the first removal.
6. Acquire a global production deploy/build/garbage-collection maintenance lock and prove that no release, build, backup, restore, hibernate, project deletion, or image promotion is active.

### Allowed cleanup

- hibernate reconstructible paused development previews through the orchestrator so their memory is released;
- re-inspect each target immediately before removal, then remove stopped/created obsolete container envelopes listed by exact immutable id in the reviewed manifest;
- remove only a dedicated inactive builder cache whose exact objects are listed in the reviewed manifest; global build cache is not pruned;
- remove an exact image id only when it is unused and not referenced by any running container, compose configuration, deployment record, pending rollback manifest, retained snapshot, or registry retention rule, and remains reproducible from a recorded registry digest or repository revision;
- remove explicitly identified temporary files created by previous controlled releases.

### Forbidden cleanup

- no Docker system/image/builder blanket prune and no volume prune;
- no deletion of named Postgres, Redis, MinIO, project-data, or unknown volumes;
- no removal or restart of active production containers during inventory;
- no glob-based deletion of runtime roots;
- no deletion of a stopped project merely because its container is not running;
- no mutation of the dirty production Git files already present on the server.

After cleanup, production health, accepted previews, database counts, storage counts, and backup restore metadata are compared with the baseline. Only then may K3s installation begin.

## 14. Compatibility and rollout flag

The public API and existing project records remain compatible. Routing is controlled per project:

```text
AGENT_EXECUTION_BACKEND=api|cell
project.agent_execution_backend overrides the default for canaries
```

- Existing projects stay on `api` until explicitly migrated.
- The first `cell` project belongs to an internal MAX canary account.
- No production prompt is routed to `cell` until Kata isolation, durable recovery, cancel, and promotion tests pass.
- Failure to provision a cell returns a controlled unavailable state; it never silently runs the prompt in a weaker sandbox.
- The legacy path stays available for rollback during the canary, then is retired only after production evidence.

## 15. Repository integration map

### API

- `apps/api/src/omnia_api/routers/messages.py`: keep public orchestration; remove ownership of the worker loop for cell sessions.
- `apps/api/src/omnia_api/services/agent_native.py`: protocol source to extract into the runner package.
- `apps/api/src/omnia_api/services/agent_builder.py`: reusable tool contracts and validation adapters.
- `apps/api/src/omnia_api/services/generation_runs.py`: replace restart-to-failed behavior with lease recovery.
- `apps/api/src/omnia_api/models/generation_run.py`: compatibility model plus new workspace/session/checkpoint/event/operation/candidate models.
- `apps/api/src/omnia_api/routers/ws.py` and `services/ws_hub.py`: durable event replay by sequence.
- `apps/api/src/omnia_api/services/repo.py`: idempotent candidate finalizer and snapshot promotion.
- `apps/api/src/omnia_api/services/context7_client.py`: existing documentation capability adapter.

### New runner

- `apps/agent-runner/`: non-root control process and model/tool loop;
- executor protocol for shell, Git, filesystem, process, test, browser, build, and artifact tools in separate Kata workloads;
- checkpoint client, event client, broker client, and LLM Gateway client;
- separate runner, executor, browser, build, and verifier images with environment bootstrap.

### Cell controller and broker

- `apps/cell-controller/`: cluster-side reconciler for the constrained `ProjectCell` resource; it alone materializes approved workloads and enforcement resources;
- `apps/capability-broker/`: standalone least-privilege service with capability-specific adapters and append-only audit;
- `apps/project-data-controller/`: trusted snapshot/change-capture/seal controller that never imports or executes candidate code;
- `apps/project-data-gateway/`: versioned connection boundary that binds release-scoped runtime identity to exactly one candidate or active data revision;
- none of these services shares the legacy orchestrator process or its Docker/host credentials.

### Orchestrator

- new `routers/agent_sessions.py` for internal lifecycle/control endpoints;
- new `services/workspace_provider.py` protocol;
- new `services/kubernetes_workspace_provider.py`;
- new `services/agent_cells.py` reconciler;
- constrained client for the cluster-side `ProjectCell` resource;
- `core/docker_client.py` remains legacy and local-development infrastructure, not the cell implementation.

### Gateway

- `apps/llm-gateway/src/omnia_gateway/routers/messages_native.py`: true streaming tool-use and upstream cancellation;
- `apps/llm-gateway/src/omnia_gateway/providers/llmgw.py`: remains the provider transport;
- structured trace metadata and safe cancellation propagation.

### Infrastructure

- K3s installation and rollback automation;
- Kata installation, containerd template, RuntimeClass, and mandatory smoke;
- custom resource, admission policy, namespace, host-aware egress, quota, PVC, runner, executor, build, browser, verifier, draft runtime, service, and broker policy manifests;
- production monitoring and backup additions;
- no use of the development `infra/docker-compose.yml` for production deployment.

## 16. Delivery phases and rollback gates

### Phase 0 — baseline and written contract

- freeze this design, inventory current production, confirm correct health endpoints, and verify backups;
- rollback: no runtime change.

### Phase 1 — safe capacity cleanup

- correct the conflicting old runbook, acquire the maintenance lock, build the exact manifest, hibernate reconstructible dev previews, remove approved obsolete envelopes and exact reproducible cache/image objects, then compare health/data;
- rollback: recreate envelopes from recorded image/source; retained volumes and accepted data remain untouched.

### Phase 2 — isolated K3s

- before installation, create a permission-protected rollback bundle containing package versions, K3s/Kata/containerd configuration, systemd units, sysctls, modules, nftables/iptables rules, routes, DNS, CNI state, Docker networks, and production-health evidence;
- encrypt the rollback bundle, copy it off-host, and verify its checksum and restore instructions independently;
- before the first network mutation, test provider console/rescue access and arm a host-local dead-man rollback unit from a protected path outside the repository; unless explicitly disarmed after the production-health soak, it restores the recorded firewall, routes, DNS, services, and container-runtime configuration;
- keep the production maintenance lock held throughout every host networking mutation;
- install K3s with separate networks, protected host reserves, and disabled ingress components; deploy only a trusted hello workload;
- rollback: restore and byte-compare the recorded host state, routes, firewall, services, and Docker health; the K3s uninstall script alone is not accepted as rollback evidence.

### Phase 3 — mandatory Kata boundary

- install a pinned supported Kata release, register a scheduled/overhead-accounted RuntimeClass, deploy the constrained cell controller and independent fail-closed admission policy, run microVM/host-egress/escape-boundary probes, then reject ordinary `runc` for every Project Cell resource;
- rollback: remove the cell namespace and Kata/K3s configuration; no prompt routing changes.

### Phase 4 — durable control plane

- add database models, migrations, internal session API, event replay, leases, operations, checkpoints, and provider abstraction behind a disabled flag;
- rollback: disable the flag; additive tables remain inert.

### Phase 5 — resident runner and Project Cell

- extract the agent loop, ship the runner image, persistent volumes, local tools, process supervisor, Context7, broker, and LLMGW path;
- rollback: route the canary project back to the legacy backend; preserve the draft for inspection.

### Phase 6 — pause, cancel, resume, and recovery

- prove restart at every model/tool boundary, process-tree termination, stale-lease rejection, and durable WebSocket replay;
- rollback: keep cell routing disabled until the full matrix is green.

### Phase 7 — draft preview and atomic promotion

- add portable candidate bundles, independent verification, versioned candidate data, project-data gateway/controller, exact inactive release materialization, promotion fence, single active-release pointer, and guarded rollback to the prior compatible runtime/data pair;
- rollback: disable promotion from cells; accepted legacy repository remains authoritative.

### Phase 8 — MAX canary

- run one internal end-to-end project with real LLMGW model calls and the existing MAX preview/session boundary;
- rollback: disable the project override and destroy only the canary cell after retaining its artifacts.

### Phase 9 — horizontal workers

- migrate quiesced pilot workspaces to snapshot-capable CSI storage with digest verification and retained old-volume rollback, join additional servers over private/VPN transport, label execution pools, and run scheduling/fencing/failure-domain tests;
- rollback: cordon and drain the new worker; the provider contract and existing sessions stay unchanged.

Each phase is a separate verified delivery. A failed gate stops the next phase; it does not weaken the required boundary.

## 17. MAX end-to-end proof

The canary prompt asks the agent to create a non-trivial MAX application as one product task:

- responsive multi-screen MAX Mini App;
- signed-session authentication adapter;
- catalog/service selection, booking flow, user history, and owner administration;
- FastAPI or Next.js backend with PostgreSQL migrations;
- background job and Redis-backed event delivery;
- webhook handler and project-scoped MAX capability;
- validation, access control, fixtures, and rollback-safe migrations;
- unit, integration, and browser tests;
- live draft preview at phone, tablet, and desktop widths;
- README and operational health endpoint.

The proof intentionally interrupts the run at these points:

1. during a streamed model response;
2. during a long shell command;
3. after file writes but before checkpoint acknowledgement;
4. during browser verification;
5. immediately before promotion.

The same session must resume once. Platform-mediated operations with downstream idempotency must not duplicate; interrupted local/external operations with unknown outcome must be reconciled or surfaced and never blindly replayed. Cancel that wins the session-row race must leave the accepted snapshot unchanged; a completed promotion makes later cancel return conflict. Cross-project filesystem, network, database, broker, metadata, host, Docker socket, and Kubernetes API probes must all be denied. The final accepted application must pass its build, migrations, tests, browser flow, runtime health, snapshot rollback, and publish checks.

## 18. Verification matrix

### Functional

- create/wake/pause/resume/cancel/destroy workspace;
- shell, files, Git, package installation, processes, browser, Context7, build, and artifacts;
- multi-service application and persistent data;
- draft preview, verification, promotion, rollback;
- candidate runtime/data pair stays isolated before promotion; one active-release pointer switches source, exact deployment, data revision, and job ownership without credential rebinding or restart;
- old projects and public endpoints unchanged.

### Recovery

- restart API, gateway, orchestrator, runner, draft runtime, containerd, and K3s node at every state boundary;
- expire and transfer a lease; prove old runner/executor pods, cgroups, and network identities are dead and exclusive writable storage is fenced before the new epoch starts;
- retry idempotent broker/finalization operations; force an `indeterminate` local/external effect and prove it is reconciled or surfaced without replay;
- recover UI events from the database when Redis is empty;
- delete the execution pod and restore dirty/untracked workspace bytes from the off-runner checkpoint artifact plus accepted repository state;
- race pause/cancel/finalize and prove the session-row linearization result in every ordering.

### Security

- cross-project file, network, database, and artifact denial;
- host, metadata, Docker/containerd socket, Kubernetes API, and private-network denial;
- admission denial for `runc`, privileged mode, host namespaces/ports, hostPath/devices/sockets, unsafe volumes, service-account mounts, NodePort, policy/RBAC changes, and dangerous Kata annotations;
- egress-proxy denial for DNS rebinding, redirects to private addresses, numeric/obfuscated addresses, IPv4-mapped IPv6, alternate DNS, QUIC, and direct-IP bypass;
- root executor cannot read runner/verifier/broker identity; draft runtime cannot reach broker or accepted database credentials;
- candidate release identity is denied every non-candidate data revision, and no candidate migration code ever runs with accepted-data authority;
- path traversal, symlink escape, environment dump, malicious package lifecycle script;
- fork/process storm, disk fill, log flood, oversized artifact, and background-process escape;
- broker replay, wrong audience, expired identity, changed lease epoch, and generated-app impersonation.

### Production safety

- local, origin, and server revisions compared before every repository operation;
- existing compose services and correct `/api/health`, orchestrator `/health`, gateway `/health`, web health, database, Redis, worker, and canary probes recorded before and after each infrastructure phase;
- K3s rollback-bundle restore and byte-comparison rehearsal performed before canary routing; uninstall alone is not sufficient;
- no existing named volume, accepted snapshot, user project, or production route changes during phases 1–3.

## 19. Operational evidence

Every session exposes:

- workspace and session state;
- current lease owner and epoch without secrets;
- last checkpoint and event sequence;
- active operation and process-group state;
- model, broker, shell, build, test, browser, and promotion timings;
- runner image, candidate, accepted snapshot, and deployed revision digests;
- safe structured logs linked by project/run/session/operation trace ids.

Initial service objectives for the canary are:

- no lost confirmed tool result after restart;
- no duplicated platform-mediated side effect after lease transfer when the downstream accepts the stable idempotency key;
- no automatic replay of an `indeterminate` local or external effect;
- cancellation prevents promotion in every tested race;
- durable events appear in the UI within two seconds under normal load;
- hibernated workspace begins recovery within 45 seconds on the pilot;
- existing production health remains unchanged through infrastructure installation;
- adding a worker requires node join and labels, not application-contract changes.

## 20. Non-goals

- replacing the existing user-facing workspace UI in the first infrastructure phase;
- migrating every existing project before the MAX canary succeeds;
- exposing Kubernetes, Docker, or host administration to the model;
- allowing one project to weaken another project's or the platform's boundary;
- making current-server capacity the permanent architecture;
- silently falling back to a weaker runtime when Kata or policy enforcement fails.

## 21. Final acceptance

The system is considered raised only when all of the following are true:

1. the current production stack is still healthy and revision-matched;
2. cleanup evidence proves no user or platform data was removed;
3. K3s and Kata runtime smokes pass on the current server;
4. one persistent Project Cell is created from Omnia through the normal control path;
5. the runner reaches the model only through Omnia LLM Gateway and LLMGW;
6. the MAX canary is built, tested, previewed, interrupted, resumed, promoted, rolled back, and re-promoted;
7. all isolation probes fail closed;
8. API/orchestrator/runner restarts preserve confirmed state, fence stale epochs, avoid duplicate idempotent operations, and surface indeterminate effects without replay;
9. legacy projects remain functional;
10. the same provider contract can schedule the next cell on a newly joined worker.

## 22. Authoritative implementation references

- [K3s quick start and node joining](https://docs.k3s.io/quick-start)
- [K3s containerd configuration](https://docs.k3s.io/advanced)
- [K3s networking requirements](https://docs.k3s.io/installation/requirements#networking)
- [K3s local storage limitations](https://docs.k3s.io/add-ons/storage)
- [Kata Containers installation](https://github.com/kata-containers/kata-containers/blob/main/docs/installation.md)
- [Kata RuntimeClass with Kubernetes and containerd](https://github.com/kata-containers/kata-containers/blob/main/docs/how-to/how-to-use-k8s-with-containerd-and-kata.md)
- [Kubernetes RuntimeClass scheduling and overhead](https://kubernetes.io/docs/concepts/containers/runtime-class/)
- [Kubernetes persistent-volume access modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes)
- [Kubernetes NetworkPolicy behavior and limits](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)

This document is the approved architecture boundary. Implementation plans may split the phases into independently deliverable blocks, but may not weaken the cell freedom, isolation, durability, or atomic-promotion contracts without a new owner-reviewed design change.
