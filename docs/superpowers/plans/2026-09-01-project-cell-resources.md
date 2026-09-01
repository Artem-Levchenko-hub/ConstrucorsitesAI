# Project Cell Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the disabled-by-default Docker Project Cell resource bundle: deterministic private storage and networks, dedicated PostgreSQL and Redis, capacity admission, fenced/idempotent lifecycle, and a private resource-level checkpoint/restore proof, without exposing a model-visible executor or changing public generation.

**Architecture:** The already-delivered fencing-hardening prerequisite makes the API the durable owner of committed operation identity and epochs. This plan changes only the orchestrator resource layer: `WorkspaceProvider` delegates a mutation-bearing contract to a focused Docker manager that journals intent before every side effect and reconciles only exact immutable labels; it never calls the legacy provisioner. A private named checkpoint volume stores workspace, agent-home, and logical PostgreSQL artifacts; Redis is cleared on restore because it is cache, not durable product truth.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Docker SDK 7, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-01-docker-project-cell-owner-canary-design.md`

## Global Constraints

- Mandatory prerequisite: `docs/superpowers/plans/2026-09-01-project-cell-fencing-hardening.md` is implemented, committed, pushed, deployed, and healthy first. This plan consumes its committed `operation_id`, `fencing_epoch`, unchanged `request_digest`, and typed `ProjectCellOrchestratorClient`; it does not modify API models, services, clients, control code, migrations, or tests.
- `WORKSPACE_PROVIDER=disabled` and `DOCKER_OWNER_CANARY_ENABLED=false` remain the defaults; Docker resources are reachable only when both are explicitly enabled.
- Public `messages`/prompt routing, resident runner, LLMGW calls, model-visible shell, root executor, generated runtime, browser worker, preview, candidate, promotion, and publish behavior remain out of scope and disabled.
- `DockerOwnerCanaryProvider` never imports or calls the legacy `provisioner`; the legacy runtime and Project Cell bundle are separate resource families.
- No request-supplied environment is accepted. Images, names, labels, mounts, networks, capabilities, limits, and database credentials are selected only by trusted orchestrator code.
- No Project Cell resource uses a host port, host network/PID/IPC namespace, host device, bind mount, Docker/containerd socket, privileged mode, or arbitrary capability.
- Every resource carries exact immutable labels for managed ownership, workspace, project, owner, provider, resource kind, and profile version. A same-name resource with any identity-label mismatch is an error and is never adopted or deleted.
- The bundle has two labeled networks. The data network is Docker-internal. The egress network is also sealed (`internal=True`) in this subproject; Subproject 3 may activate it only by adding the controlled egress service and a new profile version.
- PostgreSQL and Redis have dedicated named volumes and no published ports. Workspace, agent home, and private checkpoints are separate named volumes.
- PostgreSQL credentials are randomly generated into an orchestrator-owned `0600` file below `secrets_root`; they are never returned by an API, written to labels, logged, or committed.
- Images must be configured by immutable `name@sha256:<64 lowercase hex>` references before the Docker provider can mutate resources.
- Admission is fail closed, allows at most one non-retained bundle on the current host, and checks configured CPU, available memory, free disk bytes, and free inodes while preserving protected host headroom.
- Every mutation requires a durable operation UUID and fencing epoch. Lower epochs and a reused operation UUID with a different canonical request are rejected before Docker mutation.
- OS lock acquisition is cancellation-safe: fcntl/msvcrt make one nonblocking attempt per short worker-thread call, bounded async backoff owns the deadline, and a cancelled waiter cannot leave a worker that acquires later.
- Interrupted running mutations become `indeterminate`; recovery never blindly requeues them. Reconciliation observes exact labels and state before a later operator chooses a new fenced operation.
- Checkpoints are resource-level only: workspace archive, agent-home archive, and logical PostgreSQL dump plus hashes and manifest. They do not claim runner/session/candidate semantics. Redis is cleared on restore and is never a source of durable truth.
- Destroy creates a final checkpoint, removes sidecar containers and networks, and retains all named volumes. Physical volume purge is not implemented in this subproject because the retention/deletion authority belongs to the later project-deletion workflow.
- Public MinIO buckets are not used for Project Cell checkpoints.
- Docker is an owner-only shared-kernel canary boundary, not Kata, a microVM, or an arbitrary multi-tenant security boundary. Model-visible execution remains disabled throughout this plan.
- Cross-service apply/rollback state is a permission-protected durable transaction keyed by the pushed SHA. A restarted command validates and rolls back any unfinished transaction before it may begin another apply; process memory is never rollback authority.
- No implementation or test in this subproject may mutate production Project Cell Docker resources. Live Docker lifecycle tests run only against an explicitly selected non-production daemon and unique test UUIDs.
- Repository `AGENTS.md` delivery rules apply: verify, update `otchet/data.json`, hand the exact intended files to `luna_delivery`, push `origin/main`, deploy with both flags still disabled, and prove production health. Do not use the development `infra/` compose stack.

## File Map

- `apps/orchestrator/src/omnia_orchestrator/core/cell_resources.py` — immutable profile, deterministic names/labels, lifecycle envelopes, manifests, and validation.
- `apps/orchestrator/src/omnia_orchestrator/core/config.py`, `apps/orchestrator/.env.example` — dark resource settings and protected-headroom configuration.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_admission.py` — host-capacity snapshot and one-bundle admission decision.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_lock.py` — per-workspace asyncio + cancellation-safe nonblocking process lock with one owner token per acquisition.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_state.py` — write-ahead lifecycle state machine plus permission-protected PostgreSQL credential store.
- `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py` — isolated Docker SDK adapter and desired-state bundle reconciliation.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_checkpoint.py` — private staged checkpoint, verified restore, and pre-restore rollback checkpoint.
- `apps/orchestrator/src/omnia_orchestrator/core/workspace_provider.py`, `apps/orchestrator/src/omnia_orchestrator/services/docker_owner_canary_provider.py`, `apps/orchestrator/src/omnia_orchestrator/services/workspace_provider_factory.py` — provider integration without legacy fallback.
- `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`, `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py` — authenticated internal lifecycle contract.
- `apps/orchestrator/tests/test_cell_resources.py`, `test_cell_admission.py`, `test_cell_lock.py`, `test_cell_state.py`, `test_docker_cell_resources.py`, `test_cell_checkpoint.py`, `test_workspace_provider.py`, `test_workspace_router.py`, `test_project_cell_docker_integration.py` — unit, cross-process lock, crash-state, contract, security, and opt-in real-Docker lifecycle proof.
- `apps/orchestrator/scripts/orchestrator_release.py`, `apps/orchestrator/scripts/project_cell_rollout.py`, `apps/orchestrator/deploy/omnia-orchestrator-current.conf`, `apps/orchestrator/tests/test_orchestrator_release.py`, `apps/orchestrator/tests/test_project_cell_rollout.py` — immutable SHA releases, first adoption, durable crash-recoverable cross-service rollout/rollback, and retention.
- `otchet/data.json` — live hypothesis/evidence update; V4 remains incomplete.

---

### Task 1: Prerequisite acceptance gate

**Files:**
- Read: `docs/superpowers/plans/2026-09-01-project-cell-fencing-hardening.md`
- Read: `apps/api/src/omnia_api/services/project_cell_lifecycle.py`
- Read: `apps/api/src/omnia_api/services/orchestrator_client.py`
- No API file is modified by this resource plan.

**Interfaces:**
- Consumes committed `ClaimedCellOperation(operation_id, workspace_id, project_id, owner_id, kind, request, request_digest, fencing_epoch)` and `ProjectCellOrchestratorClient.ensure/control/observe_resources` typed requests carrying those three mutation identity fields unchanged.
- Requires the prerequisite release to persist canonical digest + committed fence before outbound calls and to reconcile unknown outcomes only through a new higher fence.

- [ ] **Step 1: Prove the prerequisite revision is delivered and healthy**

```bash
git log -1 --format=%H -- apps/api/src/omnia_api/services/project_cell_lifecycle.py
git merge-base --is-ancestor "$(git log -1 --format=%H -- apps/api/src/omnia_api/services/project_cell_lifecycle.py)" origin/main
cd apps/api
uv run pytest tests/test_project_cell_lifecycle.py tests/test_orchestrator_client.py tests/test_project_cells.py -q
```

Expected: a non-empty revision, ancestor check exit 0, and all fencing/client tests pass. Production evidence must show that same revision deployed with Project Cell routing still dark.

- [ ] **Step 2: Prove the resource diff does not alter API control code**

```bash
git diff --name-only origin/main...HEAD -- apps/api/src apps/api/tests apps/api/migrations
```

Expected before resource implementation: no output. Repeat at the final review; any API path is a scope violation and returns to the separate hardening plan.

**Review gate:** Do not start Task 2 if the prerequisite is uncommitted, undeployed, unhealthy, permits pre-commit outbound calls, or replays indeterminate operations.

---

### Task 2: Immutable resource contract, deterministic identity, and admission

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/core/cell_resources.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/cell_admission.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/config.py`
- Modify: `apps/orchestrator/.env.example`
- Create: `apps/orchestrator/tests/test_cell_resources.py`
- Create: `apps/orchestrator/tests/test_cell_admission.py`

**Interfaces:**
- Produces `CellResourceProfile.from_settings(settings) -> CellResourceProfile`, `CellResourceNames.for_workspace(workspace_id, *, namespace: Literal["prod", "test"] = "prod") -> CellResourceNames`, `identity_labels(spec, resource_kind) -> dict[str, str]`, `LifecycleMutation(operation_id: UUID, fencing_epoch: int, request_digest: str)`, `DockerDaemonIdentity(id: str, name: str, docker_root_dir: str, operating_system: str)`, `HostCapacitySnapshot`, and `CellAdmissionGate.check(snapshot, *, existing_bundle: bool, running_bundle: bool) -> AdmissionDecision`.
- Names are `omnia-cell-{workspace_id.hex}-{suffix}` with suffixes `internal`, `egress`, `workspace`, `agent-home`, `postgres`, `redis`, and `checkpoints`.

- [ ] **Step 1: Write RED tests for exact names, labels, image pins, and headroom**

```python
def test_resource_identity_is_deterministic_and_secret_free():
    spec = WorkspaceSpec(
        workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )
    names = CellResourceNames.for_workspace(spec.workspace_id)
    assert names.internal_network == "omnia-cell-00000000000000000000000000000001-internal"
    assert names.checkpoint_volume.endswith("-checkpoints")
    labels = identity_labels(spec, "postgres")
    assert labels["omnia.workspace_id"] == str(spec.workspace_id)
    assert labels["omnia.resource_kind"] == "postgres"
    assert all("token" not in key and "password" not in key for key in labels)


def test_admission_preserves_all_headroom(profile, admission_gate):
    decision = admission_gate.check(
        HostCapacitySnapshot(
            cpu_count=8, load_1m=1.0,
            memory_available_bytes=profile.host_memory_reserve_bytes + profile.bundle_memory_bytes - 1,
            disk_free_bytes=10**12, disk_free_inodes=10**7, active_bundle_count=0,
        ), existing_bundle=False, running_bundle=False,
    )
    assert decision == AdmissionDecision(False, "insufficient_memory")


def test_capacity_uses_selected_daemon_root_not_projects_root(capacity_reader, docker):
    docker.info.return_value = {"ID": "test-daemon", "DockerRootDir": "/daemon-disk"}
    capacity_reader.statvfs.side_effect = lambda path: {
        "/daemon-disk": capacity_reader.free(100 * 1024**3),
        "/different-projects-disk": capacity_reader.free(1 * 1024**3),
    }[path]
    snapshot = capacity_reader.read()
    assert snapshot.disk_path == "/daemon-disk"


def test_remote_or_unverifiable_daemon_fails_closed(capacity_reader, docker):
    docker.info.return_value = {"ID": "remote", "DockerRootDir": "/var/lib/docker"}
    capacity_reader.local_daemon_root_is_verifiable = False
    assert capacity_reader.read().failure_reason == "daemon_filesystem_unverifiable"
```

- [ ] **Step 2: Confirm RED**

```bash
cd apps/orchestrator
uv run pytest tests/test_cell_resources.py tests/test_cell_admission.py -q
```

Expected: collection fails because the resource and admission modules do not exist.

- [ ] **Step 3: Implement the immutable contract and dark settings**

Add these exact settings; byte values use multiplication in code so their meaning stays reviewable:

```python
cell_profile_version: str = "docker-owner-cell-resources-v1"
cell_postgres_image: str = ""
cell_redis_image: str = ""
cell_backup_image: str = ""
cell_max_active_bundles: int = Field(default=1, ge=1, le=1)
cell_bundle_cpu_cores: float = Field(default=2.0, gt=0)
cell_bundle_memory_bytes: int = Field(default=4 * 1024**3, gt=0)
cell_host_cpu_reserve_cores: float = Field(default=2.0, ge=0)
cell_host_memory_reserve_bytes: int = Field(default=4 * 1024**3, ge=0)
cell_required_free_disk_bytes: int = Field(default=20 * 1024**3, gt=0)
cell_host_disk_reserve_bytes: int = Field(default=10 * 1024**3, ge=0)
cell_required_free_inodes: int = Field(default=100_000, gt=0)
cell_host_inode_reserve: int = Field(default=50_000, ge=0)
cell_state_path: str = "/opt/omnia-runtime/state/project-cells.json"
```

`CellResourceProfile.from_settings` rejects any enabled-provider image that does not match `^[^\s@]+@sha256:[0-9a-f]{64}$`. `.env.example` documents the three empty image variables and both disabled provider switches without supplying a mutable tag.

The exact immutable labels are:

```python
{
    "omnia.managed": "true",
    "omnia.project_cell": "true",
    "omnia.workspace_id": str(spec.workspace_id),
    "omnia.project_id": str(spec.project_id),
    "omnia.owner_id": str(spec.owner_id),
    "omnia.provider": "docker_owner_canary",
    "omnia.profile_version": spec.profile_version,
    "omnia.resource_kind": resource_kind,
}
```

The capacity reader first queries `DockerClient.info()` on the selected daemon and requires non-empty immutable daemon ID plus `DockerRootDir`. It verifies that root is a local path on the same host, then uses `/proc/meminfo`, `os.getloadavg()`, `os.cpu_count()`, and `os.statvfs(docker_root_dir)` for bytes/inodes. It never substitutes `projects_root`; a test mounts/fixtures those paths on different filesystems. TCP/SSH/remote contexts, missing root, inaccessible `statvfs`, or malformed evidence return `AdmissionDecision(False, "daemon_filesystem_unverifiable")` before mutation.

- [ ] **Step 4: Verify contracts and defaults**

```bash
uv run pytest tests/test_cell_resources.py tests/test_cell_admission.py -q
uv run ruff check src/omnia_orchestrator/core/cell_resources.py src/omnia_orchestrator/services/cell_admission.py src/omnia_orchestrator/core/config.py tests/test_cell_resources.py tests/test_cell_admission.py
uv run mypy src/omnia_orchestrator/core/cell_resources.py src/omnia_orchestrator/services/cell_admission.py
```

Expected: all pass; default settings cannot mutate; existing running bundles are observable without consuming another admission slot, while a stopped bundle must pass headroom again before wake.

**Review gate:** Reject mutable dataclasses, request-derived names, non-digest images, optimistic capacity fallback, or any admission path allowing a second active bundle.

---

### Task 3: Labeled Docker bundle manager and durable control state

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/services/cell_state.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/cell_lock.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py`
- Create: `apps/orchestrator/tests/test_cell_lock.py`
- Create: `apps/orchestrator/tests/test_cell_state.py`
- Create: `apps/orchestrator/tests/test_docker_cell_resources.py`

**Interfaces:**
- Produces frozen opaque `FileLockOwnerToken`, injectable `OSFileLockBackend.try_acquire(fd: int) -> FileLockOwnerToken | None` and `.release(owner: FileLockOwnerToken) -> None`, platform implementations `FcntlFileLockBackend` and `MsvcrtFileLockBackend`, and `WorkspaceOperationLock.hold(workspace_id) -> AsyncContextManager[None]`. `WorkspaceOperationLock` accepts positive bounded `acquire_timeout_seconds` and `retry_interval_seconds` constructor values for deterministic tests.
- Produces `CellStateStore.begin/advance/complete/mark_indeterminate`, `CellCredentialStore.load_or_create`, `DockerCellResourceManager.ensure(spec, mutation)`, `.wake(workspace_id, mutation)`, `.pause_services(workspace_id, mutation)`, `.destroy_compute(workspace_id, mutation)`, `.inspect_by_project(project_id)`, and `.reconcile(workspace_id, mutation)`.
- Produces immutable `CellBundleHandle(workspace_id, provider_ref, state, fencing_epoch, resource_names)` and `CellBundleObservation(state, identity_valid, containers, networks, volumes, detail)`.
- Journal phases are `planned`, `volumes_created`, `networks_created`, `postgres_initialized`, `sidecars_started`, `checkpoint_sealed`, `containers_removed`, `networks_removed`, `completed`, and `indeterminate`.
- Durable inventory is always exactly five retained volumes: workspace, agent-home, PostgreSQL, Redis, and checkpoints. Init/client secret staging volumes and all helper containers are temporary labeled resources, never members of `CellResourceNames.retained_volumes` or a completed bundle observation.

- [ ] **Step 1: Write fake-Docker RED tests**

```python
async def test_ensure_creates_exact_private_bundle(manager, docker, spec, mutation):
    handle = await manager.ensure(spec, mutation)
    completed = docker.completed_inventory(spec.workspace_id)
    assert set(completed.retained_volume_names) == {
        handle.resource_names.workspace_volume,
        handle.resource_names.agent_home_volume,
        handle.resource_names.postgres_volume,
        handle.resource_names.redis_volume,
        handle.resource_names.checkpoint_volume,
    }
    assert completed.helper_container_ids == []
    assert completed.secret_staging_volume_ids == []
    assert docker.networks[handle.resource_names.internal_network].internal is True
    assert docker.networks[handle.resource_names.egress_network].internal is True
    assert docker.containers[handle.resource_names.postgres_container].ports == {}
    assert docker.containers[handle.resource_names.redis_container].ports == {}


async def test_same_name_wrong_labels_is_never_adopted_or_removed(manager, docker, spec, mutation):
    docker.seed_volume(CellResourceNames.for_workspace(spec.workspace_id).workspace_volume,
                       labels={"omnia.workspace_id": "different"})
    with pytest.raises(CellIdentityConflict):
        await manager.ensure(spec, mutation)
    assert docker.removed_resources == []


async def test_empty_postgres_volume_bootstraps_with_removed_one_shot_helper(
    manager, docker, spec, mutation
):
    await manager.ensure(spec, mutation)
    helper = docker.last_container(kind="postgres-init")
    assert helper.labels["omnia.resource_kind"] == "postgres-init"
    assert helper.ports == {}
    assert helper.user == "0:0"
    assert helper.cap_add == ["CHOWN"]
    assert helper.removed_in_finally is True
    steady = docker.containers[CellResourceNames.for_workspace(spec.workspace_id).postgres_container]
    assert steady.user == "999:999"
    assert steady.cap_drop == ["ALL"]
    assert steady.read_only is True


async def test_crash_after_network_create_is_indeterminate_and_not_replayed(
    manager, docker, state_store, spec, mutation
):
    docker.crash_after = "network_create"
    with pytest.raises(SimulatedProcessCrash):
        await manager.ensure(spec, mutation)
    record = state_store.load(spec.workspace_id)
    assert record.phase == "indeterminate"
    with pytest.raises(CellIndeterminateOperation):
        await manager.ensure(spec, mutation)


@pytest.mark.parametrize("outcome", ["success", "init_failure", "cancelled", "restart"])
async def test_final_inventory_has_only_five_retained_volumes(
    manager, docker, spec, mutation, outcome
):
    await exercise_bootstrap_outcome(manager, docker, spec, mutation, outcome)
    inventory = docker.inventory_for_workspace(spec.workspace_id)
    assert set(inventory.retained_volume_names) == set(
        CellResourceNames.for_workspace(spec.workspace_id).retained_volumes
    )
    assert len(inventory.retained_volume_names) == 5
    assert inventory.helper_container_ids == []
    assert inventory.secret_staging_volume_ids == []
    assert inventory.persistent_container_env_secret_matches == []


async def test_two_manager_process_representatives_serialize_before_docker(
    manager_factory, shared_lock_backend, spec
):
    first = manager_factory(shared_lock_backend)
    second = manager_factory(shared_lock_backend)
    results = await asyncio.gather(
        first.ensure(spec, LifecycleMutation(uuid4(), 8, "a" * 64)),
        second.ensure(spec, LifecycleMutation(uuid4(), 8, "b" * 64)),
        return_exceptions=True,
    )
    assert sum(isinstance(item, CellBundleHandle) for item in results) == 1
    assert first.docker.begin_operation_calls + second.docker.begin_operation_calls == 1


async def test_cancellation_releases_lock_but_keeps_journal(manager, lock_backend, spec, mutation):
    manager.docker.block_next_side_effect = True
    task = asyncio.create_task(manager.ensure(spec, mutation))
    await manager.docker.wait_until_blocked()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lock_backend.can_acquire_from_other_process(spec.workspace_id) is True
    assert manager.state_store.load(spec.workspace_id).phase == "indeterminate"


def test_spawned_processes_contend_on_same_lock_file(tmp_path):
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    side_effects = context.Queue()
    args = (str(tmp_path / "same-workspace.lock"), gate, side_effects)
    contenders = [context.Process(target=lock_contender, args=args) for _ in range(2)]
    for process in contenders:
        process.start()
    gate.set()
    for process in contenders:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(read_queue(side_effects)) == ["entered", "rejected_equal_fence"]


async def test_cancelled_waiter_never_late_acquires_or_mutates(
    tmp_path, manager_factory, shared_state_store, fixed_spec
):
    context = multiprocessing.get_context("spawn")
    holder_ready = context.Event()
    release_holder = context.Event()
    lock_path = tmp_path / f"{fixed_spec.workspace_id}.lock"
    holder = context.Process(
        target=hold_real_platform_lock,
        args=(str(lock_path), holder_ready, release_holder),
    )
    holder.start()
    assert holder_ready.wait(timeout=10)

    cancelled_mutation = LifecycleMutation(uuid4(), 8, "c" * 64)
    cancelled = manager_factory(lock_path=lock_path)
    waiter = asyncio.create_task(cancelled.ensure(fixed_spec, cancelled_mutation))
    await cancelled.lock_backend.wait_for_first_attempt()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release_holder.set()
    holder.join(timeout=10)
    assert holder.exitcode == 0

    fresh_mutation = LifecycleMutation(uuid4(), 9, "d" * 64)
    fresh = manager_factory(lock_path=lock_path)
    await asyncio.wait_for(fresh.ensure(fixed_spec, fresh_mutation), timeout=1)
    await asyncio.sleep(0)
    assert shared_state_store.operation_ids(fixed_spec.workspace_id) == [fresh_mutation.operation_id]
    assert cancelled.docker.begin_operation_calls == 0
    assert fresh.docker.operation_ids == [fresh_mutation.operation_id]
```

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_docker_cell_resources.py -q
```

Expected: collection fails because the manager and stores do not exist.

- [ ] **Step 3: Implement exact desired state**

`ensure` first checks admission, credentials, and all existing identity labels, then creates exactly five retained named volumes and two internal bridge networks. A fresh PostgreSQL volume is initialized by a labeled one-shot ownership helper running fixed UID/GID `0:0`, `cap_drop=["ALL"]`, and only `cap_add=["CHOWN"]`; it may create/chown `PGDATA` for steady UID/GID `999:999` and has no network, port, host mount, or persistent environment secret. Database initialization itself runs in a second labeled one-shot PostgreSQL container as `999:999`, `cap_drop=["ALL"]`, with the digest-pinned Postgres entrypoint. The password arrives only as a mounted read-only file from a temporary labeled staging volume. Both helpers and staging volume are removed in one outer `finally`, including non-zero exit/cancellation. The steady PostgreSQL server starts/restarts from initialized `PGDATA` without any password environment variable, secret file, or secret attachment; later database clients receive the password only through their own temporary secret staging. PostgreSQL and Redis run as fixed non-root users, `cap_drop=["ALL"]`, `cap_add=[]`, `privileged=False`, `no-new-privileges`, read-only root, bounded CPU/memory/PIDs/logs, private retained volumes, and tmpfs scratch.

The credential store creates every parent with mode `0700`, rejects symlinks with `lstat`/`O_NOFOLLOW`, creates the file using `O_CREAT|O_EXCL` mode `0600`, writes/fsyncs the file, fsyncs the parent, and reopens without following links for reads. Credentials never appear in persistent Docker `Config.Env`. Only init/client one-shot helpers receive a read-only file from a temporary secret staging volume; steady PostgreSQL/Redis have no secret attachment or password environment. Every one-shot helper and temporary staging volume is removed in `finally`.

`WorkspaceOperationLock` first awaits a process-local `asyncio.Lock`, opens `state/locks/{workspace_id}.lock` without following symlinks, then retries only nonblocking OS attempts until a `time.monotonic()` deadline. A newly created lock file is initialized to one byte and fsynced; each msvcrt attempt seeks to byte zero. `FcntlFileLockBackend.try_acquire` makes exactly one `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` call; `EACCES`/`EAGAIN` means contention. `MsvcrtFileLockBackend.try_acquire` makes exactly one `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` call on that fixed first byte; its documented contention errors mean contention. No backend contains a retry loop or blocking lock mode.

Each attempt is a short `asyncio.to_thread(backend.try_acquire, fd)` task awaited through `asyncio.shield`. If the outer task is cancelled while that thread is finishing, the cancellation handler awaits that one attempt to completion, shield-releases any returned owner token, and re-raises without scheduling another attempt. A `None` result waits with cancellable `asyncio.sleep(min(retry_interval, deadline-now))`; deadline expiry raises `workspace_lock_timeout`. A successful result is stored as the sole owner token before the next cancellation point and is consumed exactly once by `backend.release(owner)` in an awaited shielded `finally`; double/foreign-token release is an error. Thus no cancelled await abandons a thread that can later acquire the lock.

The combined lock spans journal validation/begin, every fsynced phase write, the corresponding Docker side effect, observed-ID fsync, and terminal write. If fcntl/msvcrt support cannot be loaded, the lock file cannot be opened safely, or the selected daemon is remote/unverifiable, live provider mutation fails closed with `workspace_lock_unavailable`; there is no in-process-only live fallback. Cancellation/failure never deletes or truncates the journal or lock file. Cancellation while waiting for the OS lock occurs before `CellStateStore.begin` and therefore produces no journal or Docker mutation; cancellation after journal begin preserves the existing `indeterminate` behavior.

The state store uses symlink-safe write-to-temp, file `fsync`, `os.replace`, directory `fsync`, and mode `0600`. Before every individual Docker side effect, while holding the combined lock, it persists `operation_id`, canonical `request_digest`, fence, `status="running"`, next phase, and exact expected resource IDs/names. After the side effect it fsyncs the observed ID and advances phase; only the final write is `completed`. Process restart turns a lingering `running` phase into `indeterminate` before any Docker call.

Replay rules are exact:

```python
if mutation.fencing_epoch < state.fencing_epoch:
    raise CellFenceRejected("stale fencing epoch")
if mutation.operation_id in state.operations:
    return state.operations[mutation.operation_id].replay_completed_same_envelope(
        mutation.request_digest, mutation.fencing_epoch
    )
if mutation.fencing_epoch == state.fencing_epoch and state.last_operation_id is not None:
    raise CellFenceRejected("epoch already consumed by another operation")
```

Only a completed same-UUID/same-digest/same-fence operation replays its result. Mismatch rejects. An indeterminate operation accepts only a new higher-epoch `reconcile`; ordinary ensure/wake/pause/destroy never resumes it.

Reconcile explicitly handles: volumes/networks created without sidecars; a finalized checkpoint with only one sidecar/container removed; an exited/leaked one-shot helper; and one of two networks already removed. It verifies exact resource IDs plus identity labels, removes only the leaked exact-labeled helper, records observed partial state, and returns the next safe desired-state operation without executing it.

`pause_services` stops only the two sidecars. `destroy_compute` removes only exactly labeled sidecars and networks, retaining all five volumes and credentials. Missing correctly labeled resources are idempotent success; unknown or mismatched resources fail closed.

- [ ] **Step 4: Verify idempotency, fencing, and security kwargs**

```bash
uv run pytest tests/test_cell_lock.py tests/test_cell_state.py tests/test_docker_cell_resources.py -q
uv run ruff check src/omnia_orchestrator/services/cell_lock.py src/omnia_orchestrator/services/cell_state.py src/omnia_orchestrator/services/docker_cell_resources.py tests/test_cell_lock.py tests/test_cell_state.py tests/test_docker_cell_resources.py
uv run mypy src/omnia_orchestrator/services/cell_lock.py src/omnia_orchestrator/services/cell_state.py src/omnia_orchestrator/services/docker_cell_resources.py
```

Expected: all pass; same-process managers and two truly spawned contenders against one lock file allow only one equal/stale-fence request to reach the side effect; fcntl uses `LOCK_EX|LOCK_NB`, msvcrt uses `LK_NBLCK`, and both backends perform one short attempt per worker call. A waiter cancelled behind a separately spawned holder leaves no late owner, journal entry, or Docker call, and a fresh contender acquires immediately after holder release. Cancellation after journal begin still releases the sole owner token while preserving the indeterminate journal. Empty-volume bootstrap, restart, init-helper failure, crashes at every phase, partial ensure/destroy, leaked helper, partial network removal, exact completed replay, and higher-fence reconcile are covered. Destroy never removes retained volumes, and an unsupported live lock backend fails closed.

**Review gate:** Inspect every Docker SDK kwarg. Reject host publication/mount/socket, privileged/capability broadening, label adoption, secret serialization, or best-effort deletion after identity mismatch.

---

### Task 4: Private resource-level checkpoint and verified restore

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/services/cell_checkpoint.py`
- Create: `apps/orchestrator/tests/test_cell_checkpoint.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py`
- Modify: `apps/orchestrator/tests/test_docker_cell_resources.py`

**Interfaces:**
- Produces `CellCheckpointManager.create(workspace_id, checkpoint_ref, mutation) -> CheckpointManifest` and `.restore(workspace_id, checkpoint_ref, mutation) -> CheckpointManifest`.
- `CheckpointManifest` contains only workspace/project IDs, profile version, fencing epoch, checkpoint reference, UTC creation time, artifact names, SHA-256 digests, PostgreSQL image digest, and `redis_policy="clear_on_restore"`.

- [ ] **Step 1: Write staged checkpoint and rollback RED tests**

```python
async def test_checkpoint_is_private_atomic_and_secret_free(checkpoints, helper, workspace_id, mutation):
    manifest = await checkpoints.create(workspace_id, "before-migration-1", mutation)
    assert set(manifest.artifacts) == {"workspace.tar", "agent-home.tar", "postgres.dump"}
    assert manifest.redis_policy == "clear_on_restore"
    assert helper.finalized_paths == ["before-migration-1"]
    assert helper.remaining_tmp_paths == []
    assert "password" not in manifest.model_dump_json().casefold()


async def test_failed_restore_rolls_back_pre_restore_state(checkpoints, helper, workspace_id, mutation):
    helper.fail_after_workspace_extract = True
    with pytest.raises(CellRestoreFailed):
        await checkpoints.restore(workspace_id, "accepted-1", mutation)
    assert helper.created_refs == [f"pre-restore-{mutation.operation_id}"]
    assert helper.rollback_completed is True
```

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_cell_checkpoint.py -q
```

Expected: collection fails because `cell_checkpoint` does not exist.

- [ ] **Step 3: Implement the exact resource-level boundary**

Validate checkpoint references with `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$`. Checkpoint creation first reaches a quiescent resource state. A workspace archiver mounts only workspace, agent-home, and checkpoint volumes. A separate pinned PostgreSQL client mounts only checkpoint + ephemeral secret volumes and reaches the sole PostgreSQL owner over the internal network to run `pg_dump --format=custom`; it never mounts raw `PGDATA`. Both helpers write under `{checkpoint_ref}.tmp-{operation_id}`, are removed in `finally`, and the manager hashes all artifacts, writes canonical `manifest.json`, fsyncs, then atomically renames the staging directory. No host directory or MinIO endpoint is mounted or contacted.

Restore is an explicit journaled state machine:

```text
verify_target_identity_and_hashes_while_paused
start_single_isolated_maintenance_postgres_owner_on_existing_PGDATA
create_and_verify_logical_pre_restore_dump_and_workspace/home_archives
apply_workspace_and_agent_home
drop/recreate_project_database_and_pg_restore_target
verify_workspace_hashes_and_pg_restore_list/smoke_query
clear_stopped_redis_volume
stop_and_remove_maintenance_postgres
leave_bundle_paused
```

The ordinary PostgreSQL sidecar must be stopped and its death verified before maintenance PostgreSQL starts; no other helper may mount `PGDATA`. Failure injection at every phase restores the already verified pre-restore workspace/home archives and logical PostgreSQL dump, verifies that rollback, removes helpers in `finally`, and leaves the bundle paused. If either restore or rollback verification fails, journal state becomes `degraded` and wake is denied.

This task does not create runner checkpoints, accepted revisions, candidates, or promotion claims. The returned reference is the exact resource checkpoint boundary consumed by Subproject 3.

- [ ] **Step 4: Verify checkpoint failures and no-public-storage rule**

```bash
uv run pytest tests/test_cell_checkpoint.py tests/test_docker_cell_resources.py -q
uv run ruff check src/omnia_orchestrator/services/cell_checkpoint.py src/omnia_orchestrator/services/docker_cell_resources.py tests/test_cell_checkpoint.py
uv run mypy src/omnia_orchestrator/services/cell_checkpoint.py src/omnia_orchestrator/services/docker_cell_resources.py
rg -n "MINIO|S3|public.*bucket|hostPath|bind" src/omnia_orchestrator/services/cell_checkpoint.py
```

Expected: tests and static checks pass; the final search has no matches. Corrupt hash, wrong workspace, stale fence, competing PostgreSQL owner, and failure at each verify/dump/apply/check/cleanup phase either restore the verified pre-restore checkpoint or leave `degraded`; success always leaves the bundle paused.

**Review gate:** Reject a restore that mutates before hash/identity verification, raw-mounts `PGDATA` into a non-PostgreSQL helper, allows two PostgreSQL owners, leaves the bundle running, stores outside the private volume, treats Redis as truth, or reports success after incomplete rollback.

---

### Task 5: Provider integration, authenticated lifecycle, and reconciliation

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/workspace_provider.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_owner_canary_provider.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/workspace_provider_factory.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`
- Modify: `apps/orchestrator/tests/test_workspace_provider.py`
- Modify: `apps/orchestrator/tests/test_workspace_router.py`

**Interfaces:**
- Consumes the one frozen `LifecycleMutation(operation_id: UUID, fencing_epoch: int, request_digest: str)` defined in `core/cell_resources.py` by Task 2 and requires it on every mutator, with no default: `ensure(spec, mutation)`, `wake(workspace_id, mutation)`, `pause(workspace_id, checkpoint_ref, mutation)`, `destroy(workspace_id, mutation)`, and `execute_control(workspace_id, action, mutation)`.
- Defines `ControlAction(kind: Literal["wake", "pause", "stop", "destroy", "restore", "reconcile"], checkpoint_ref: str | None = None)`; resource-level `stop` is an idempotent alias of checkpoint-then-pause and does not delete data.
- Produces Pydantic request/response types `WorkspaceEnsureRequest`, `WorkspaceControlRequest`, and `WorkspaceResourceResponse`; each uses `extra="forbid"` and contains no free-form environment or Docker kwargs.
- `DisabledWorkspaceProvider`, `DockerOwnerCanaryProvider`, protocol conformance tests, routes, and every caller use those exact mutation-bearing signatures. `DockerOwnerCanaryProvider` delegates only to the resource/checkpoint managers. Factory construction still requires exactly `workspace_provider == "docker_owner_canary"` and `docker_owner_canary_enabled is True`; otherwise it returns `DisabledWorkspaceProvider` without constructing Docker dependencies.

- [ ] **Step 1: Write RED provider and router tests**

```python
async def test_internal_ensure_authenticates_before_provider(monkeypatch, client, spec_payload):
    called = False
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: mark_called())
    response = await client.post("/internal/workspaces/ensure", json=spec_payload)
    assert response.status_code == 401
    assert called is False


async def test_resource_provider_never_calls_legacy_provisioner(provider, manager, spec):
    mutation = LifecycleMutation(uuid4(), 4, "a" * 64)
    handle = await provider.ensure(spec, mutation)
    assert handle.provider == "docker_owner_canary"
    assert manager.ensure_calls == [(spec.workspace_id, mutation.operation_id, 4)]


def test_every_provider_mutator_requires_lifecycle_mutation():
    for name in ("ensure", "wake", "pause", "destroy", "execute_control"):
        assert "mutation" in inspect.signature(WorkspaceProvider.__dict__[name]).parameters
```

- [ ] **Step 2: Confirm RED**

```bash
cd apps/orchestrator
uv run pytest tests/test_workspace_provider.py tests/test_workspace_router.py -q
```

Expected: failures because Docker provider remains unsupported and lifecycle routes do not exist.

- [ ] **Step 3: Implement only the authenticated internal resource lifecycle**

Add:

```text
POST /internal/workspaces/ensure
POST /internal/workspaces/{workspace_id}/control
POST /internal/workspaces/{workspace_id}/resources/observe
GET  /internal/workspaces/{workspace_id}/resources
```

All four call `verify_internal_token` before factory construction or parsing a mutation into a Docker call. `ensure` accepts only UUID identity, exact profile version, operation UUID, positive fencing epoch, and 64-lowercase-hex prerequisite digest. `control` accepts the exact `ControlAction` plus the same required mutation envelope; `checkpoint_ref` is required only for pause/stop/restore and forbidden for wake/destroy/reconcile. Fenced `resources/observe` accepts the exact typed observation DTO consumed by higher-fence reconciliation; GET is authenticated human/read-only diagnostics and cannot complete an API operation. Responses contain state, provider reference, fencing epoch, checkpoint reference, and boolean resource-presence facts, never Docker socket paths, credentials, environment, raw labels, or helper output.

`pause` creates the named checkpoint before stopping sidecars. `destroy` creates `final-{fencing_epoch}-{operation_id.hex}` before retaining volumes/removing compute. `reconcile` compares journal with exact Docker labels: missing compute in a ready state becomes `degraded`, an extra same-label resource is reported but not deleted, and an identity mismatch is `conflict`. It never replays an incomplete mutation.

The existing capability route remains byte-compatible: `ready=False`, `state="unsupported"`, and its existing detail. Resource states `resources_ready`, `resources_paused`, `retained`, `partial`, and `degraded` appear only on the authenticated `/resources` and `/control` responses. Therefore public readiness remains legacy/fail-closed without any API change.

- [ ] **Step 4: Verify auth, provider selection, and compatibility**

```bash
uv run pytest tests/test_workspace_provider.py tests/test_workspace_router.py -q
uv run ruff check src/omnia_orchestrator/core/workspace_provider.py src/omnia_orchestrator/services/docker_owner_canary_provider.py src/omnia_orchestrator/services/workspace_provider_factory.py src/omnia_orchestrator/schemas/workspace.py src/omnia_orchestrator/routers/workspace.py tests/test_workspace_provider.py tests/test_workspace_router.py
uv run mypy src/omnia_orchestrator/core/workspace_provider.py src/omnia_orchestrator/services/docker_owner_canary_provider.py src/omnia_orchestrator/routers/workspace.py
```

Expected: all pass; missing/wrong token produces zero manager calls; default factory produces zero Docker client construction; no mutator can be called without `LifecycleMutation`; capability remains exactly unsupported while resource routes expose internal state.

**Review gate:** Reject any public router registration, implicit Docker fallback, legacy provisioner import, readiness `true`, auth-after-side-effect ordering, or response that leaks resource secrets.

---

### Task 6: Opt-in real-Docker lifecycle and isolation proof

**Files:**
- Create: `apps/orchestrator/tests/test_project_cell_docker_integration.py`
- Modify: `apps/orchestrator/pyproject.toml`
- Modify: `apps/orchestrator/README.md`

**Interfaces:**
- Adds pytest marker `docker_cell_integration`; tests skip unless `RUN_DOCKER_CELL_INTEGRATION=1` and the configured immutable images are inspectable on the selected Docker daemon.
- The fixture constructs names with `CellResourceNames.for_workspace(id, namespace="test")`, yielding `omnia-cell-test-{workspace_id.hex}-{suffix}`. It refuses to run unless `ENV != "prod"`, queried `Docker.info()["ID"]` is in `CELL_TEST_ALLOWED_DAEMON_IDS`, and queried daemon labels contain `omnia.project-cell-test-daemon=true`; endpoint strings are not treated as identity.

- [ ] **Step 1: Write the integration harness with cleanup-by-exact-label**

```python
@pytest.mark.docker_cell_integration
async def test_bundle_survives_pause_restore_wake(cell_harness):
    cell = await cell_harness.ensure_unique()
    await cell.write_workspace("proof.txt", b"accepted")
    await cell.insert_postgres("accepted")
    await cell.redis_set("cache", "discard-me")
    checkpoint = await cell.pause("accepted-1")
    await cell.mutate_after_checkpoint()
    await cell.restore(checkpoint.ref)
    assert await cell.read_workspace("proof.txt") == b"accepted"
    assert await cell.read_postgres() == ["accepted"]
    assert await cell.redis_get("cache") is None
    await cell.stop("stopped-1")
    cell = await cell_harness.recreate_manager_from_disk(cell.workspace_id)
    await cell.wake()
    assert await cell.sidecars_healthy() is True
```

The fixture records every created Docker resource ID at creation. Cleanup fetches each exact ID, verifies the complete expected workspace/project/owner/provider/profile/kind label set, then removes only that ID. Missing is idempotent; any label mismatch aborts cleanup for human inspection. It never removes by name/prefix search and never calls Docker prune.

- [ ] **Step 2: Run unit tests first, then the opt-in lifecycle on a non-production daemon**

```bash
uv run pytest tests/test_cell_resources.py tests/test_cell_admission.py tests/test_docker_cell_resources.py tests/test_cell_checkpoint.py tests/test_workspace_provider.py tests/test_workspace_router.py -q
RUN_DOCKER_CELL_INTEGRATION=1 uv run pytest tests/test_project_cell_docker_integration.py -m docker_cell_integration -v
```

Expected unit result: PASS. Expected integration result: immutable daemon ID+sentinel guard passes; test-only names are valid; one bundle is reused idempotently; five volumes and two internal networks have exact labels; PostgreSQL/Redis expose no host ports; fresh bootstrap/restart/helper failure and checkpoint/restore/wake pass; a helper attached only to project A cannot resolve or connect to project B; inspection shows no privileged mode, host namespace, host bind/device/socket, or forbidden capability; exact-ID cleanup leaves no fixture resource.

- [ ] **Step 3: Run full orchestrator suite and prove API paths remain untouched**

```bash
cd apps/orchestrator
uv run pytest -q
uv run ruff check src tests
uv run mypy src
cd ../..
git diff --name-only origin/main...HEAD -- apps/api/src apps/api/tests apps/api/migrations
```

Expected: all orchestrator checks pass and the API path command has no output. API hardening was already verified/delivered by the prerequisite and is not changed by this plan. If repository-wide unrelated failures exist, record their exact isolated reproduction; no Project Cell failure may be waived.

- [ ] **Step 4: Perform the security invariant scan**

```bash
rg -n "privileged.?=.?(True|true)|network_mode.?=.?(host|\"host\")|pid_mode|ipc_mode|/var/run/docker.sock|/run/containerd|ports.?=" src/omnia_orchestrator/services/cell_*.py src/omnia_orchestrator/services/docker_cell_resources.py
rg -n "initial_env|provisioner|MINIO|S3|PROJECT_CELL_CANARY_EMAILS" src/omnia_orchestrator/services/docker_owner_canary_provider.py src/omnia_orchestrator/services/docker_cell_resources.py src/omnia_orchestrator/services/cell_checkpoint.py
```

Expected: the first command finds only explicit safe `privileged=False`/empty-port assertions if present; the second has no matches.

**Review gate:** Review the whole Subproject 2 diff independently. Reject if the integration guard relies on endpoint strings or lacks daemon ID+sentinel, cleanup is name/prefix/prune based, cross-project isolation is asserted without a live probe, or the public generation path changed.

---

### Task 7: Report, atomic delivery, and dark production proof

**Files:**
- Modify: `otchet/data.json`
- Create: `apps/orchestrator/scripts/orchestrator_release.py`
- Create: `apps/orchestrator/scripts/project_cell_rollout.py`
- Create: `apps/orchestrator/deploy/omnia-orchestrator-current.conf`
- Create: `apps/orchestrator/tests/test_orchestrator_release.py`
- Create: `apps/orchestrator/tests/test_project_cell_rollout.py`
- No files beyond the exact Task 1–7 file map.

**Interfaces:**
- Produces H128 evidence for the resource-only milestone. H128 remains `testing`, `score: null`, and V4 owner canary remains incomplete because no resident agent, model-visible execution, browser, or promotion exists.
- Keeps the existing `project_cell_rollout.py apply --prior-sha <40hex> --pushed-sha <40hex>` and `rollback --prior-sha <40hex> --pushed-sha <40hex>` CLI. Internally it produces `RolloutTransactionStore.create/load/find_unfinished/write_intent/write_observed/mark_terminal` and a versioned `RolloutJournal` keyed by pushed SHA; process-local objects are never accepted as rollback input.

- [ ] **Step 1: Update the live report truthfully**

Raise `meta.updated`, add the exact verified test counts and fill the revision field from `git rev-parse HEAD` at delivery time, and state: dedicated bundle resources, private resource checkpoint proof, fencing/admission/isolation evidence completed; public routing and model-visible generation still disabled. Do not add an owner address, image credential, PostgreSQL credential, server secret, or raw Docker observation containing secrets.

- [ ] **Step 2: Run final data/diff sanity**

```bash
python -m json.tool otchet/data.json > NUL
git diff --check
git status --short
git diff --stat
rg -n "@gmail\.com|PROJECT_CELL_CANARY_EMAILS=.*@|postgres_password|PGPASSWORD=" apps/api apps/orchestrator otchet/data.json
```

Expected: JSON and diff checks pass; only intended files are present; secret scan has no committed value or personal address.

- [ ] **Step 3: Implement and test immutable orchestrator releases**

`orchestrator_release.py` exposes `stage --repo --sha --root`, `bootstrap --prior-sha --pushed-sha`, `activate --sha`, `rollback --sha`, and `prune --keep 3`. `stage` validates a full 40-hex commit reachable from the repo, creates `/opt/omnia-runtime/releases/orchestrator/<sha>.staging`, extracts `git archive <sha>` without using the mutable checkout as runtime, creates `<release>/apps/orchestrator/.venv` with `uv sync --frozen`, writes `.release.env` containing only `OMNIA_RELEASE_SHA=<sha>`, `WORKSPACE_PROVIDER=disabled`, and `DOCKER_OWNER_CANARY_ENABLED=false`, fsyncs, then atomically renames staging to `<sha>`. Failure removes only that exact staging directory and leaves `current` untouched.

The installed systemd drop-in target is `/etc/systemd/system/omnia-orchestrator.service.d/20-immutable-release.conf`; its exact content is:

```ini
[Service]
WorkingDirectory=/opt/omnia-runtime/releases/orchestrator/current/apps/orchestrator
EnvironmentFile=
EnvironmentFile=/opt/omnia-runtime/.env.orchestrator
EnvironmentFile=/opt/omnia-runtime/releases/orchestrator/current/.release.env
ExecStart=
ExecStart=/opt/omnia-runtime/releases/orchestrator/current/apps/orchestrator/.venv/bin/uvicorn omnia_orchestrator.main:app --host 127.0.0.1 --port 8003
```

`activate` creates `current.next` as a relative symlink to the immutable SHA directory, fsyncs its parent, atomically `os.replace`s it over `current`, then runs fixed-argv `systemctl daemon-reload` and `systemctl restart omnia-orchestrator`. It calls local `/health` and requires exact `OMNIA_RELEASE_SHA`; failure atomically restores the prior symlink and restarts/health-checks the prior SHA. Code and release flags switch together through the one `current` symlink; stable secret env remains outside releases.

First adoption is executable, not assumed. `bootstrap` requires both currently running `PRIOR_SHA` and pushed `PUSHED_SHA` already staged from exact Git archives. It verifies live health reports `PRIOR_SHA`, captures `systemctl cat/show` plus the original unit/drop-in bytes into a protected bootstrap backup, creates `current` pointing to the prior immutable release, and proves the prior release imports/starts from that path before changing systemd. It writes the reviewed drop-in to a sibling temp file, fsyncs, atomically replaces the drop-in, daemon-reloads/restarts the prior immutable release, and requires exact prior health SHA. Only that success authorizes later pushed activation. Any failure restores the original drop-in/unit state byte-for-byte, daemon-reloads/restarts the original checkout service, proves its prior health, and leaves `/opt/omnia` untouched.

Tests use a temporary release root and fake fixed-argv command runner to prove exact Git archive SHA, frozen independent venv path, atomic code+env switch, first-adoption prior+pushed staging, original-unit backup/restore, failure before/after drop-in install, failed-health prior-current preservation, prior-SHA rollback, and pruning. A drop-in test parses exact lines and proves inherited `EnvironmentFile` is first reset, stable secrets load only from `/opt/omnia-runtime/.env.orchestrator`, and `current/.release.env` loads last so SHA/dark flags override deterministically. `prune --keep 3` never removes `current`, the immediately prior release, bootstrap backups, any `.staging` it did not create in this invocation, or any data/state/secret path.

`project_cell_rollout.py` owns the cross-service release transaction for production compose `/opt/omnia/apps/llm-gateway/deploy/full/docker-compose.yml` and exact env `/opt/omnia/apps/llm-gateway/deploy/full/.env`. Its durable root is `/opt/omnia-runtime/state/project-cell-rollouts`, mode `0700`; each transaction is the child directory named by the exact pushed 40-hex SHA, also `0700`. Directory traversal opens each existing component with `O_DIRECTORY|O_NOFOLLOW`, rejects symlinks/non-owner-writable modes, and creates new components with an exact `0700` mode. Journal and backup files are created with `O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`. Every write fsyncs the file, and every create/rename fsyncs the opened parent directory. Journal replacement writes a uniquely named `0600` sibling with `O_EXCL`, fsyncs it, atomically `os.replace`s it, then fsyncs the transaction directory; no predictable temp name or symlink-following open is permitted.

Before the first production side effect, the transaction directory contains a canonical `transaction.json` with `schema_version=1`, exact distinct `prior_sha` and `pushed_sha`, `status="prepared"`, the fixed ordered phase list, and the following validated recovery facts. `status` is exactly `prepared|applying|rollback_required|rolling_back|completed`; `phase_state` is exactly `none|intent|observed`, and `terminal_outcome` is null until a verified terminal write:

```text
backups.full_compose_env = {path, sha256}
backups.orchestrator_stable_env = {path, sha256}
backups.prior_release_env = {path, sha256}
backups.orchestrator_current_target = {path, sha256, exact_relative_target}
backups.orchestrator_drop_in = {existed, path_or_null, sha256_or_null}
backups.rollback_compose_override = {path, sha256}
prior_image_ids = {api: sha256:..., worker: sha256:..., web: sha256:...}
pre_project_cell_manifest = {path, sha256}
pre_health = {path, sha256}
next_phase = "stop_api"
observed_phases = []
```

The protected backup files, not the JSON journal, hold exact prior bytes. The journal stores only verified absolute backup paths below its own transaction directory and SHA-256 hashes; it never serializes env/secret bytes. The current-target backup file contains only the exact relative symlink target and must resolve to `<release-root>/<prior_sha>` without escaping the release root. `orchestrator_drop_in.existed=false` is an explicit absence record; when true, its protected backup hash is required. `rollback_compose_override` pins API/worker/web to the recorded image IDs so rollback does not depend on a moved tag. `pre_project_cell_manifest` is the canonical exact resource ID+name+identity-label manifest. Loading reopens every file without following links, proves it is a regular `0600` file owned by the expected account, checks its real path remains below the transaction directory, recomputes every hash, validates all image IDs and SHAs, and rejects unknown schema, phase, duplicate/out-of-order observation, or path before any command runs.

The forward phases are exactly `stop_api`, `drain_generations`, `replace_full_env`, `recreate_worker_web`, `activate_orchestrator`, `recreate_api`, `verify_forward`, and `rolled_forward`. Before each external side effect, the store fsyncs `next_phase=<phase>` and `phase_state="intent"`. After the fixed-argv runner returns, code independently observes the actual env hash, container image IDs/health, symlink target/drop-in hash, or manifest/health result and fsyncs `phase_state="observed"` plus that evidence before advancing. A crash between effect and observation is therefore an unfinished intent, never assumed success. Only an observed `verify_forward` may fsync the terminal pair `status="completed", terminal_outcome="rolled_forward"`. Read-only drain polls are bounded and the exact query is `SELECT count(*) FROM generation_runs WHERE status IN ('pending','running','cancel_requested')`; only an observed zero advances. `cancel_requested` is draining work because cancellation cleanup may still own mutable state.

On process start, `apply` scans the protected root before preflight or any new transaction creation. If any journal has `status != "completed"` (including an `intent` or `observed` phase state), it fully validates that journal and its backups, performs only its idempotent validated rollback, records `status="completed", terminal_outcome="rolled_back"`, and exits with a distinct recovered-rollback result; it never starts or resumes a forward apply in the same invocation. An unreadable, unverifiable, ambiguous, or multiple-conflicting unfinished journal fails closed without a service/env mutation. Re-running `apply` for a journal already marked `status="completed", terminal_outcome="rolled_forward"` verifies its recorded pushed state and returns idempotently; it does not apply twice.

An explicit later `apply` after verified `status="completed", terminal_outcome="rolled_back"` appends `attempt_no + 1` inside the same pushed-SHA transaction directory, snapshots fresh attempt-scoped protected backups, and retains the prior terminal attempt record unchanged. This is the only same-SHA reapply path and is never entered by the invocation that recovered an unfinished attempt.

Standalone `rollback` loads the transaction by pushed SHA from disk, validates that its CLI prior/pushed SHAs exactly equal the journal, and needs no in-memory state from the apply process. Its ordered phases are `rollback_stop_api`, `rollback_full_env`, `rollback_orchestrator`, `rollback_worker_web`, `rollback_api`, `verify_rollback`, and `rolled_back`. It uses the same fsynced intent/observed protocol and resumes idempotently after interruption: before acting it observes whether the intended prior bytes, exact relative current target/drop-in state, or recorded image IDs are already present, records that result, and proceeds without duplicating a completed phase. Verification requires byte-identical full/stable/release env hashes, exact prior current target/drop-in state, API/worker/web image IDs, prior release health plus legacy smoke, and a Project Cell manifest hash equal to `pre_project_cell_manifest.sha256`. Only an observed `verify_rollback` may fsync the terminal pair `status="completed", terminal_outcome="rolled_back"`.

Rollout still uses only fixed argv in the full compose directory: stop API, drain to exact zero, atomically replace the full compose release env with `OMNIA_RELEASE_SHA=PUSHED_SHA`, recreate worker/web with `--no-build --force-recreate`, activate orchestrator, then recreate API last. Preflight verifies recorded images immediately before recreation. Signals other than `SIGKILL` request the same guarded durable rollback; `SIGKILL` recovery occurs on the next invocation from the fsynced journal. The helper never deploys `infra/`, resets Git, or deletes data.

Transaction cleanup or retention pruning may inspect only journals with `status="completed"`, `terminal_outcome` exactly `rolled_forward` or `rolled_back`, their corresponding verification phase observed, and all hashes revalidated. It never deletes the newest rolled-forward transaction, the transaction needed to roll back the active release, an unfinished/invalid journal, bootstrap backup, release referenced by `current`, or any data/state/secret outside that exact transaction directory. Until an explicit tested prune, terminal journal/backups remain available for standalone rollback.

Add RED tests using a persistent fake fixed-argv runner and a real child process. The fake runner rejects every argv not in the production allowlist and persists simulated env bytes, current symlink target, drop-in bytes/absence, service image IDs, health, command counters, and Project Cell manifest outside the rollout process. Parameterize all forward and rollback phases: the parent waits until the requested fsynced `intent` or `observed` marker, sends `SIGKILL` on POSIX (or `Process.kill()`/TerminateProcess on Windows), then starts a fresh process against the same state. A restarted `apply` must restore the exact prior state and exit without a pushed forward command; a restarted standalone `rollback` must resume and reach the same result. Assert prior env files byte-for-byte, current target/drop-in exact, API/worker/web IDs exact, manifest hash exact, no secret byte appears in `transaction.json`, no forward phase counter increases during recovery, and every phase counter is at most one after an already-observed result. Include corrupt hash, symlink substitution, wrong CLI SHA, missing backup, duplicate unfinished journal, moved image tag, and `cancel_requested` drain rows as fail-closed cases.

```python
FORWARD_PHASES = (
    "stop_api", "drain_generations", "replace_full_env", "recreate_worker_web",
    "activate_orchestrator", "recreate_api", "verify_forward",
)
ROLLBACK_PHASES = (
    "rollback_stop_api", "rollback_full_env", "rollback_orchestrator",
    "rollback_worker_web", "rollback_api", "verify_rollback",
)


@pytest.mark.parametrize("phase", FORWARD_PHASES)
@pytest.mark.parametrize("edge", ("intent", "observed"))
def test_sigkill_forward_recovers_prior_without_second_apply(rollout_process, phase, edge):
    crashed = rollout_process.start_apply_and_kill_at(phase, edge)
    before = crashed.external_state.command_counters.copy()
    recovered = rollout_process.run_fresh_apply()
    assert recovered.exit_reason == "unfinished_transaction_rolled_back"
    assert recovered.external_state.exact_prior_snapshot() == crashed.prior_snapshot
    assert recovered.external_state.command_counters.forward_delta(before) == 0
    assert (recovered.journal.status, recovered.journal.terminal_outcome) == (
        "completed", "rolled_back",
    )


@pytest.mark.parametrize("phase", ROLLBACK_PHASES)
@pytest.mark.parametrize("edge", ("intent", "observed"))
def test_sigkill_rollback_resumes_idempotently(rollout_process, phase, edge):
    crashed = rollout_process.start_rollback_and_kill_at(phase, edge)
    recovered = rollout_process.run_fresh_rollback()
    assert recovered.external_state.exact_prior_snapshot() == crashed.prior_snapshot
    assert (recovered.journal.status, recovered.journal.terminal_outcome) == (
        "completed", "rolled_back",
    )
    assert recovered.external_state.max_observed_phase_execution_count <= 1


def test_drain_counts_cancel_requested(fake_database, rollout):
    fake_database.generation_statuses = ["cancel_requested"]
    with pytest.raises(DrainDeadlineExceeded):
        rollout.apply(prior_sha="1" * 40, pushed_sha="2" * 40)
    assert rollout.external_side_effects_after_stop_api == []
```

```powershell
cd apps/orchestrator
uv run pytest tests/test_orchestrator_release.py -q
uv run pytest tests/test_project_cell_rollout.py -q
uv run ruff check scripts/orchestrator_release.py scripts/project_cell_rollout.py tests/test_orchestrator_release.py tests/test_project_cell_rollout.py
uv run mypy scripts/orchestrator_release.py scripts/project_cell_rollout.py
```

Expected: PASS; no test invokes real systemd or writes outside its temporary release/state roots. Crash tests cover every forward and rollback phase with a fresh process and prove exact prior env/image/symlink/drop-in/manifest restoration, idempotent resume, and no second apply.

- [ ] **Step 4: Hand the exact reviewed file list to `luna_delivery`**

Commit message:

```text
feat(project-cell): add isolated cell resources
```

Required trailer:

```text
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

`luna_delivery` commits only the intended Task 1–7 files and pushes the current branch to `origin/main`; it must not include `.superpowers/`, server backups, environment files containing values, or unrelated work.

- [ ] **Step 5: Deploy dark and prove no production cell mutation**

Before deployment, repeat mandatory fetch/local/upstream/server revision comparison and set `PUSHED_SHA` from verified `origin/main`; read `PRIOR_SHA` from current live health (or the resolved `current` symlink after adoption). Build the canonical exact Project Cell identity manifest. Stage both `PRIOR_SHA` and `PUSHED_SHA` from `/opt/omnia` Git objects. If no immutable `current` exists, run tested `orchestrator_release.py bootstrap --prior-sha "$PRIOR_SHA" --pushed-sha "$PUSHED_SHA"` and prove prior exact service health through the immutable path. Then execute `project_cell_rollout.py apply --prior-sha "$PRIOR_SHA" --pushed-sha "$PUSHED_SHA"`; it owns admission close/drain, atomic full-env backup/update, worker/web-first recreation, orchestrator switch, API-last recreation, exact health, and automatic unified rollback. Never reset/merge `/opt/omnia` for runtime and never build unchanged API/web images.

Verify:

```text
API /api/health = 200 with the pushed release SHA
worker running at the pushed release SHA
orchestrator /health = 200 with the pushed release SHA
gateway /health = 200
web /web-health = 200
external /api/health and /web-health = 200
existing legacy generation smoke remains healthy
Project Cell capability reports provider=disabled, enabled=false, ready=false
pre/post exact Project Cell resource ID+name+identity-label manifests are identical
active generation count was zero before first env/service mutation
recorded API/worker/web image IDs are unchanged because their code did not change
```

Expected: dark deployment is healthy, prerequisite migration head remains 0053, no Project Cell Docker resource was created/adopted/renamed, and disabling requires no data deletion.

- [ ] **Step 6: Prove operational rollback without deleting data**

Run `project_cell_rollout.py rollback --prior-sha "$PRIOR_SHA" --pushed-sha "$PUSHED_SHA"`; it closes admission, restores exact backed-up full compose env, restores prior orchestrator symlink/drop-in state, recreates worker/web first and API last from recorded prior image IDs, and requires prior exact cross-service health plus legacy smoke. It never resets `/opt/omnia` and never removes a container/network/volume/secret/state entry. Then re-run `project_cell_rollout.py apply` and repeat exact release + legacy health. Only after both releases are proven may `orchestrator_release.py prune --keep 3` remove older unreferenced code releases. A rollback that changes image identity, retained data, exact resource identity, or service order fails this gate.

**Review gate:** Do not claim Subproject 2 complete until revision, push, dark deploy, rollback/redeploy, health, byte-identical resource identity manifests, and legacy compatibility evidence are all recorded. Do not mark the overall owner canary or V4 complete.

---

## Self-Review Record

- **Spec coverage:** §§5–6 map to Tasks 2–5; PostgreSQL/Redis and retention in §9 map to Tasks 3–4; two-network boundary in §10 maps to Tasks 2–3; hardening and ordinary-Docker limitation in §11 map to Tasks 3 and 6; fencing/recovery in §13 comes from the Task 1 prerequisite and is enforced again by Tasks 3–5; decomposition/disabled rollout in §15.1 maps to Tasks 5–7; functional, isolation, recovery, and production-safety resource rows in §16 map to Tasks 6–7.
- **Intentional boundary:** This plan proves resources only. It does not claim source acceptance, runner checkpoint, application migration digest, browser evidence, candidate, promotion, or public owner routing. Those require Subprojects 3–4.
- **Placeholder scan:** No deferred implementation instruction, unspecified error handling, or undefined neighboring interface remains. Runtime image values are operator configuration and are deliberately empty while the provider is disabled; enabled mutation requires immutable digests.
- **Type/signature consistency:** Task 2 defines the single `LifecycleMutation` value object from the prerequisite's committed UUID/epoch/digest fields; every provider mutator requires that exact type and passes it unchanged to resource and checkpoint managers; manifests and lifecycle responses never contain credentials.
- **Secret/inventory consistency:** Completed bundles contain exactly five retained volumes. Secret staging and helper resources are temporary labeled inventory, removed after success/failure/cancellation; steady PostgreSQL restart needs no secret env or attachment.
- **Serialization:** One per-workspace asyncio lock plus one OS-exclusive file lock spans journal validation, every fsynced phase, and its Docker side effect; unsupported lock backends fail closed.
- **Release safety:** First adoption stages prior+pushed immutable archives, proves prior through the new path, and restores original unit/drop-in on failure. One atomic `current` symlink switches code plus dark release env; rollback never rewrites `/opt/omnia` or deletes Project Cell data.
- **Cross-service release:** A tested helper closes API admission, drains active generations, atomically backs up/updates full release env, recreates worker/web first and API last from unchanged verified image IDs, switches orchestrator between them, and rolls every component back together.
- **Security invariants:** No public route, arbitrary env, host port/mount/socket/device/namespace, privileged mode, public MinIO, broad cleanup, secret label, or legacy provisioner mixing is permitted.
- **Rollback:** Feature flags remain off; Docker mutations are journaled/idempotent/label-fenced; destroy retains all data volumes; dark rollback restarts the prior orchestrator without data removal; exact resource identity manifests must remain equal.
