# Project Cell Capacity Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-bundle Project Cell guard with race-free physical-capacity admission, durable FIFO waiting, automatic idle-cell hibernation, restart-safe prompt resumption, and a live proof that at least three owner projects submitted back-to-back complete without prompt replay or data loss.

**Architecture:** The orchestrator remains the authority for physical host evidence and Docker side effects; it serializes cross-workspace admission and returns a signed pre-effect capacity response instead of a generic 500. The API remains the durable workflow authority: PostgreSQL stores capacity-wait state, FIFO order, retry metadata, prompt dispatch data, and fenced lifecycle operations; queued workers hibernate only workspaces whose generation lease has been explicitly released. Existing `generation_runs`, Project Cell fencing, immutable labels, and provider interfaces are extended rather than replaced.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Docker SDK 7, asyncio, Redis/WebSocket progress, Next.js 15, React 19, TypeScript, Vitest, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-02-project-cell-capacity-scheduler-design.md`

## Global Constraints

- There is no fixed project-count or active-bundle-count product limit. Only verified CPU, memory, disk-byte, and inode headroom may defer admission.
- `undj00x03@gmail.com` remains the only enabled production owner canary account; this work does not widen Docker execution to other accounts.
- A capacity shortage before provider side effects becomes a durable `queued_for_capacity` generation state and `waiting_capacity` cell-operation state, never `failed` or `indeterminate`.
- A prompt is accepted and stored once. Retries reuse the same `GenerationRun`, assistant message, Project Cell workspace, and lifecycle operation; the browser never has to submit the prompt again.
- Per-project single-flight remains enforced. Cross-project scheduling is FIFO by `GenerationRun.created_at, GenerationRun.id` for the one current cell profile.
- Only a workspace with no active generation lease may be selected for capacity hibernation. An active generation is never preempted.
- Hibernation uses the existing fenced `pause` lifecycle and preserves workspace, agent-home, PostgreSQL, Redis, checkpoint volumes, snapshots, and accepted releases.
- Every lifecycle retry uses a newly claimed fencing epoch. A stale scheduler, worker, runner, or request cannot mutate or release current state.
- Cross-workspace admission must be serialized around host evidence, durable reservation intent, and provisioning. A per-workspace lock alone is insufficient.
- Existing Project Cell identity labels, no-host-access rules, digest-pinned images, preview routing, candidate promotion, and public API compatibility remain unchanged.
- Queued runs survive API restart through a validated dispatch envelope stored in `GenerationRun.agent_state`; startup resumes only `queued_for_capacity`, while genuinely interrupted running generations keep the existing honest failure behavior.
- Configuration may tune protected host reserves, retry delay, queue scan interval, and idle hibernation behavior. It may not reintroduce `max_active_bundles`.
- TDD is mandatory: each behavior begins with a focused test that is observed failing for the intended missing behavior before production code is changed.
- Local baseline evidence on 2026-09-02: orchestrator focused suite `71 passed`; web lifecycle suite `16 passed`. Local API execution is unavailable because this Windows host has no PostgreSQL listener on 5432 and Docker Desktop is stopped; API unit tests that do not require DB may run locally, but database/migration suites must run against an isolated disposable PostgreSQL 16 before delivery.
- Repository delivery rules apply: update `otchet/data.json`, verify, hand exact files to `luna_delivery`, push `origin/main`, deploy only through `apps/llm-gateway/deploy/full`, and prove exact revision plus public/internal health. Never deploy `infra/`.

## File Map

- `apps/orchestrator/src/omnia_orchestrator/core/config.py`, `apps/orchestrator/.env.example` — remove the numerical bundle cap; add bounded retry/admission settings only.
- `apps/orchestrator/src/omnia_orchestrator/core/cell_resources.py` — typed `CellCapacityUnavailable` and count-free resource profile.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_admission.py` — physical-dimension admission decisions with no count gate.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_lock.py` — secure named cross-process lock used by all workspace admissions.
- `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py` — cross-workspace admission critical section and pre-effect capacity exception.
- `apps/orchestrator/src/omnia_orchestrator/services/cell_state.py` — explicit release of the active generation lease while retaining ready compute.
- `apps/orchestrator/src/omnia_orchestrator/core/workspace_provider.py`, `apps/orchestrator/src/omnia_orchestrator/services/docker_owner_canary_provider.py` — fenced `release` lifecycle operation.
- `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`, `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py` — typed capacity-wait response and internal release operation.
- `apps/api/migrations/versions/0055_project_cell_capacity_queue.py` — generation/operation status constraints and retry metadata.
- `apps/api/src/omnia_api/models/generation_run.py`, `apps/api/src/omnia_api/models/project_cell.py` — durable queue and retry fields.
- `apps/api/src/omnia_api/services/orchestrator_client.py` — parse the exact capacity pre-effect envelope.
- `apps/api/src/omnia_api/services/project_cells.py` — park/reclaim lifecycle operations, FIFO admission turn, idle victim claim, workspace release state.
- `apps/api/src/omnia_api/services/project_cell_lifecycle.py` — persist `waiting_capacity` rather than failed/indeterminate and retry the same operation safely.
- `apps/api/src/omnia_api/services/project_cell_capacity.py` — bounded FIFO wait loop, idle hibernation, retry wakeup, and metrics/log events.
- `apps/api/src/omnia_api/services/project_cell_executor.py` — wait instead of failing, report queue progress, and expose fenced generation-lease release.
- `apps/api/src/omnia_api/services/generation_runs.py`, `apps/api/src/omnia_api/routers/messages.py`, `apps/api/src/omnia_api/main.py` — validated dispatch persistence, queued status, startup resumption, cancellation, and final lease release.
- `apps/api/src/omnia_api/schemas/message.py`, `apps/api/src/omnia_api/schemas/max_studio.py` — expose `queued_for_capacity` without breaking older clients.
- `apps/web/src/lib/api/types.ts`, `apps/web/src/lib/generation-lifecycle.ts`, `apps/web/src/components/workspace/AgentTranscript.tsx` — active queued status and explicit Russian waiting copy.
- Focused tests listed in each task prove RED→GREEN behavior, races, recovery, and UI state.
- `otchet/data.json` — H129 implementation evidence; V4 stays false until live production acceptance succeeds.

---

### Task 1: Remove the numerical admission gate and serialize host admission

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/config.py`
- Modify: `apps/orchestrator/.env.example`
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/cell_resources.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/cell_admission.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/cell_lock.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/workspace_provider_factory.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py`
- Test: `apps/orchestrator/tests/test_cell_resources.py`
- Test: `apps/orchestrator/tests/test_cell_admission.py`
- Test: `apps/orchestrator/tests/test_cell_lock.py`
- Test: `apps/orchestrator/tests/test_docker_cell_resources.py`

**Interfaces:**
- Remove `cell_max_active_bundles` from `CellResourceSettings` and `max_active_bundles` from `CellResourceProfile`.
- Add `CellCapacityUnavailable(reason: str)` as a distinct pre-effect `CellResourceError` subclass with allowed reasons `insufficient_cpu|insufficient_memory|insufficient_disk|insufficient_inodes|daemon_filesystem_unverifiable`.
- Add `WorkspaceOperationLock.hold_named(name: str) -> AsyncContextManager[None]`; `hold(workspace_id)` delegates to the named key `workspace-{workspace_id}`. The admission key is exactly `host-capacity-admission`.
- `DockerCellResourceManager` receives `capacity_lock: WorkspaceOperationLock` and holds `capacity_lock.hold_named("host-capacity-admission")` around capacity observation through completed sidecar start or a proven pre-effect rejection.

- [ ] **Step 1: Write RED tests for count-free admission**

```python
def test_admission_ignores_bundle_count_when_physical_headroom_exists(profile):
    decision = CellAdmissionGate(profile).check(
        HostCapacitySnapshot(
            cpu_count=8,
            load_1m=1.0,
            memory_available_bytes=12 * 1024**3,
            disk_free_bytes=200 * 1024**3,
            disk_free_inodes=1_000_000,
            active_bundle_count=999,
            disk_path="/var/lib/docker",
        ),
        existing_bundle=False,
        running_bundle=False,
    )
    assert decision == AdmissionDecision(True, "admitted")
```

Also assert `Settings.model_fields` and `CellResourceProfile.__dataclass_fields__` contain no `cell_max_active_bundles`/`max_active_bundles` entry.

- [ ] **Step 2: Run RED and confirm the old count gate fails the test**

```powershell
cd apps/orchestrator
uv run pytest tests/test_cell_resources.py tests/test_cell_admission.py -q
```

Expected before implementation: failure reports `active_bundle_limit` or the removed settings field still exists.

- [ ] **Step 3: Write RED concurrency tests for two workspaces**

Create two `ensure` calls with different workspace UUIDs and a fake capacity reader that reports capacity for one bundle until the first fake Docker start becomes observable. Assert the fake backend's maximum concurrent admission section is `1`, one call completes, and the other receives `CellCapacityUnavailable("insufficient_memory")` before `begin_operation`.

```python
assert fake_capacity.max_parallel_reads == 1
assert docker.begin_operation_ids == [winner_mutation.operation_id]
assert loser_state_store.load(loser_spec.workspace_id) is None
```

- [ ] **Step 4: Implement count-free profile, named lock, and critical section**

`CellAdmissionGate.check` keeps running-bundle reuse and the four physical checks but deletes the `active_bundle_count >= max_active_bundles` branch. The active count remains telemetry in `HostCapacitySnapshot`; it has no admission authority.

```python
if decision.allowed is False:
    raise CellCapacityUnavailable(decision.reason)
```

Raise only before `stateful_begin_or_replay` and `docker.begin_operation`, so the typed exception is always a proven pre-effect outcome. Keep the named capacity lock held until `_ensure_sidecars` completes and state is durably completed; this deliberately serializes cold starts on the single current host and prevents two optimistic snapshots from overcommitting it.

- [ ] **Step 5: Verify focused orchestrator behavior**

```powershell
uv run pytest tests/test_cell_resources.py tests/test_cell_admission.py tests/test_cell_lock.py tests/test_docker_cell_resources.py -q
uv run ruff check src/omnia_orchestrator/core/config.py src/omnia_orchestrator/core/cell_resources.py src/omnia_orchestrator/services/cell_admission.py src/omnia_orchestrator/services/cell_lock.py src/omnia_orchestrator/services/docker_cell_resources.py tests/test_cell_resources.py tests/test_cell_admission.py tests/test_cell_lock.py tests/test_docker_cell_resources.py
uv run mypy src/omnia_orchestrator/core/cell_resources.py src/omnia_orchestrator/services/cell_admission.py src/omnia_orchestrator/services/cell_lock.py src/omnia_orchestrator/services/docker_cell_resources.py
```

Expected: all focused tests pass; `rg "active_bundle_limit|cell_max_active_bundles|max_active_bundles" apps/orchestrator` finds no runtime reference.

---

### Task 2: Return a typed, verifiable capacity-wait response

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`
- Modify: `apps/api/src/omnia_api/services/orchestrator_client.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_lifecycle.py`
- Test: `apps/orchestrator/tests/test_workspace_router.py`
- Test: `apps/api/tests/test_orchestrator_client.py`
- Test: `apps/api/tests/test_project_cell_lifecycle.py`

**Interfaces:**
- Orchestrator returns HTTP `429` with code `capacity_wait` and exact details `{operation_id, fencing_epoch, request_digest, effect_applied: false, reason, retry_after_seconds}`.
- Add API value object `ProjectCellCapacityRejection(operation_id: UUID, fencing_epoch: int, request_digest: str, effect_applied: Literal[False], reason: str, retry_after_seconds: int)`.
- Add `ProjectCellCapacityWait` exception carrying that value object.
- `ProjectCellOperationOutcome.status` may now be `waiting_capacity`.

- [ ] **Step 1: Write the orchestrator RED contract test**

```python
response = client.post("/internal/workspaces/ensure", headers=token, json=request_payload)
assert response.status_code == 429
assert response.json()["error"]["code"] == "capacity_wait"
assert response.json()["error"]["details"] == {
    "operation_id": request_payload["operation_id"],
    "fencing_epoch": request_payload["fencing_epoch"],
    "request_digest": request_payload["request_digest"],
    "effect_applied": False,
    "reason": "insufficient_memory",
    "retry_after_seconds": 2,
}
```

- [ ] **Step 2: Confirm RED**

```powershell
cd apps/orchestrator
uv run pytest tests/test_workspace_router.py -k capacity_wait -q
```

Expected: current route returns `500 container_failure`.

- [ ] **Step 3: Implement the typed route response and strict parser**

Catch `CellCapacityUnavailable` before the broad `CellResourceError` handler and construct the exact pre-effect identity details from `LifecycleMutation`. In the API client, parse this envelope only when status is 429; reject missing, extra, wrongly typed, mismatched, or non-allowlisted fields as `OrchestratorUnavailable`, never as safe capacity wait.

- [ ] **Step 4: Write and pass API RED→GREEN lifecycle tests**

The test client raises `OrchestratorBadRequest(status_code=429, details=valid_capacity_details)`. Assert `execute_cell_operation` persists `waiting_capacity`, leaves `finished_at is None`, stores the allowlisted reason and retry deadline, and does not hash it into a terminal error. A malformed envelope must still produce `indeterminate`.

```powershell
cd apps/api
$env:DATABASE_URL='postgresql+asyncpg://omnia:omnia@127.0.0.1:5432/omnia'
$env:JWT_SECRET='test-secret-change-me-please-32-bytes'
uv run pytest tests/test_orchestrator_client.py tests/test_project_cell_lifecycle.py -q
```

Expected: all tests pass against the isolated test database.

---

### Task 3: Persist queue status and retry metadata through migration 0055

**Files:**
- Create: `apps/api/migrations/versions/0055_project_cell_capacity_queue.py`
- Modify: `apps/api/src/omnia_api/models/generation_run.py`
- Modify: `apps/api/src/omnia_api/models/project_cell.py`
- Modify: `apps/api/src/omnia_api/models/__init__.py` only if new mapped types are introduced
- Modify: `apps/api/src/omnia_api/services/generation_runs.py`
- Modify: `apps/api/src/omnia_api/services/project_cells.py`
- Test: `apps/api/tests/test_migrations_single_head.py`
- Test: `apps/api/tests/test_project_cell_models.py`
- Test: `apps/api/tests/test_generation_runs.py`
- Test: `apps/api/tests/test_project_cells.py`

**Interfaces:**
- `GenerationRun.status` adds `queued_for_capacity`; active statuses become `pending|queued_for_capacity|running|cancel_requested`.
- `ProjectCellOperation.status` adds `waiting_capacity`; active-operation partial index includes `pending|waiting_capacity|running`.
- Add nullable `capacity_reason TEXT`, nullable `next_attempt_at TIMESTAMPTZ`, and non-null `attempt_count INTEGER DEFAULT 0 CHECK (attempt_count >= 0)` to `project_cell_operations`.
- `park_cell_operation_for_capacity(session, operation_id, *, reason, retry_after_seconds)` moves only `running -> waiting_capacity` and clears `finished_at/error`.
- `claim_cell_operation_committed` accepts `pending|waiting_capacity`, increments `attempt_count` and workspace fencing epoch, and clears capacity metadata when moving to `running`.
- `recover_interrupted_cell_operations` leaves `waiting_capacity` untouched and still marks only dispatched `running` operations indeterminate.

- [ ] **Step 1: Write migration and model RED tests**

Assert migration head is `0055_project_cell_capacity_queue`, the check constraints contain the two new statuses, the partial unique index contains `waiting_capacity`, and upgrade/downgrade/upgrade preserves all pre-existing Project Cell rows.

```python
assert operation.status == "waiting_capacity"
assert operation.capacity_reason == "insufficient_memory"
assert operation.next_attempt_at is not None
assert operation.finished_at is None
```

- [ ] **Step 2: Confirm RED on current 0054 schema**

```powershell
cd apps/api
uv run pytest tests/test_migrations_single_head.py tests/test_project_cell_models.py tests/test_generation_runs.py tests/test_project_cells.py -q
```

Expected: failures identify missing status values/columns and head `0054_project_cell_candidates`.

- [ ] **Step 3: Implement migration and state transitions**

Use named constraints matching repository convention:

```text
ck_generation_runs_status_allowed
ck_project_cell_operations_status_allowed
ck_project_cell_operations_attempt_count_nonnegative
uq_project_cell_operations_one_active_per_workspace
```

The downgrade refuses to collapse live `queued_for_capacity`/`waiting_capacity` rows silently: first convert them to honest failed terminal states with `error='capacity queue migration downgraded'` and `finished_at=now()`, then restore the prior constraints and remove the new columns.

- [ ] **Step 4: Verify migration parity and focused services**

```powershell
uv run pytest tests/test_migrations_single_head.py tests/test_project_cell_models.py tests/test_generation_runs.py tests/test_project_cells.py -q
uv run ruff check migrations/versions/0055_project_cell_capacity_queue.py src/omnia_api/models/generation_run.py src/omnia_api/models/project_cell.py src/omnia_api/services/generation_runs.py src/omnia_api/services/project_cells.py tests/test_migrations_single_head.py tests/test_project_cell_models.py tests/test_generation_runs.py tests/test_project_cells.py
uv run mypy src/omnia_api/models/generation_run.py src/omnia_api/models/project_cell.py src/omnia_api/services/generation_runs.py src/omnia_api/services/project_cells.py
```

Expected: upgrade/downgrade/upgrade and ORM catalog parity pass on disposable PostgreSQL 16.

---

### Task 4: Add FIFO coordination and safe idle-cell hibernation

**Files:**
- Create: `apps/api/src/omnia_api/services/project_cell_capacity.py`
- Modify: `apps/api/src/omnia_api/services/project_cells.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_lifecycle.py`
- Test: `apps/api/tests/test_project_cell_capacity.py`
- Test: `apps/api/tests/test_project_cells.py`

**Interfaces:**
- `CapacityTurn(run_id: UUID, is_head: bool, position: int, reason: str | None, retry_after_seconds: int)`.
- `claim_capacity_turn(session, run_id) -> CapacityTurn` obtains `pg_advisory_xact_lock(hashtext('project-cell-capacity-scheduler'))` and orders active queued runs by `(created_at, id)`.
- `claim_idle_hibernation_victim(session, *, requesting_run_id: UUID) -> ProjectCellWorkspace | None` selects the oldest `state='ready' AND generation_run_id IS NULL` workspace with `FOR UPDATE SKIP LOCKED`, excluding the requesting workspace.
- `hibernate_one_idle_workspace(session_factory, *, requesting_run_id) -> bool` reserves one deterministic fenced pause operation with checkpoint ref `capacity-{requesting_run_id.hex[:12]}` and executes it outside the selection transaction.
- `wait_for_capacity(...)` loops with bounded delay `1..10` seconds, emits a progress callback only when reason/position changes or 15 seconds elapsed, checks cancellation each iteration, and retries the same waiting operation.

- [ ] **Step 1: Write FIFO and victim-selection RED tests**

Create three queued runs in non-UUID creation order. Assert only the earliest `(created_at, id)` receives `is_head=True`. Create ready workspaces with and without `generation_run_id`; assert only the oldest lease-free workspace is chosen.

```python
assert [turn_a.is_head, turn_b.is_head, turn_c.is_head] == [True, False, False]
assert victim.id == idle_workspace.id
assert active_workspace.id != victim.id
```

- [ ] **Step 2: Write RED race tests**

Run two coordinator sessions concurrently against PostgreSQL. Assert one pause operation is created for the chosen victim, its unique idempotency key is reused on replay, and the second waiter either observes the first operation or chooses a different idle victim. No workspace receives two active lifecycle operations.

- [ ] **Step 3: Implement the coordinator with no process-local authority**

The loop sequence is exact:

```text
park current ensure operation as waiting_capacity
set generation run queued_for_capacity
wait until current run is FIFO head
try one safe idle pause through normal lifecycle
claim and execute the same ensure operation again
on waiting_capacity: persist reason/deadline and repeat
on completed: set generation running and return response
on cancelled/failed/indeterminate: exit through existing terminal semantics
```

Never hold a PostgreSQL transaction across an HTTP/Docker call. Commit the victim lifecycle reservation first, execute it, then update workspace `state='stopped'` only from a completed pause response.

- [ ] **Step 4: Verify focused capacity service**

```powershell
cd apps/api
uv run pytest tests/test_project_cell_capacity.py tests/test_project_cells.py tests/test_project_cell_lifecycle.py -q
uv run ruff check src/omnia_api/services/project_cell_capacity.py src/omnia_api/services/project_cells.py src/omnia_api/services/project_cell_lifecycle.py tests/test_project_cell_capacity.py tests/test_project_cells.py tests/test_project_cell_lifecycle.py
uv run mypy src/omnia_api/services/project_cell_capacity.py src/omnia_api/services/project_cells.py src/omnia_api/services/project_cell_lifecycle.py
```

Expected: FIFO, cancellation, duplicate scheduler, no-idle-victim, retry deadline, and one-pause-only cases pass.

---

### Task 5: Release generation leases without stopping the finished preview

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/workspace_provider.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/cell_state.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_cell_resources.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_owner_canary_provider.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`
- Modify: `apps/api/src/omnia_api/services/project_cells.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_executor.py`
- Test: `apps/orchestrator/tests/test_cell_state.py`
- Test: `apps/orchestrator/tests/test_workspace_provider.py`
- Test: `apps/orchestrator/tests/test_workspace_router.py`
- Test: `apps/api/tests/test_project_cell_executor.py`

**Interfaces:**
- Add lifecycle kind `release` to API and orchestrator allowlists.
- `CellStateStore.release_generation(workspace_id, mutation, *, generation_run_id)` clears `active_generation_run_id` and `active_generation_fencing_epoch` only when both match the mutation's current fenced lease; it leaves `bundle_state='resources_ready'` and all containers unchanged.
- `ProjectCellExecutorHandle.release() -> Awaitable[None]` reserves and executes `generation:{run_id}:release:{profile_version}`, then clears `ProjectCellWorkspace.generation_run_id` only after the provider confirms the same workspace/fence.

- [ ] **Step 1: Write RED release-fencing tests**

```python
released = store.release_generation(workspace_id, mutation, generation_run_id=run_id)
assert released.active_generation_run_id is None
assert released.bundle_state == "resources_ready"
assert docker.stop_calls == []
```

Wrong run ID, stale fence, duplicate different envelope, or release before ensure must be rejected before state change.

- [ ] **Step 2: Confirm RED and implement the provider/API lifecycle**

```powershell
cd apps/orchestrator
uv run pytest tests/test_cell_state.py tests/test_workspace_provider.py tests/test_workspace_router.py -k release -q
```

Expected before implementation: unsupported lifecycle kind `release`.

- [ ] **Step 3: Add executor release and API workspace update tests**

Assert successful release retains the draft response and clears only the active generation owner. A failed, indeterminate, or mismatched release keeps `generation_run_id` so the hibernation selector cannot preempt uncertain work.

- [ ] **Step 4: Verify both service boundaries**

```powershell
cd apps/orchestrator
uv run pytest tests/test_cell_state.py tests/test_workspace_provider.py tests/test_workspace_router.py -q
cd ../api
uv run pytest tests/test_project_cell_executor.py tests/test_project_cells.py -q
```

Expected: release is idempotent, fenced, leaves compute ready, and makes only proven-finished workspaces hibernation candidates.

---

### Task 6: Wait inside the original run and resume queued prompts after API restart

**Files:**
- Modify: `apps/api/src/omnia_api/services/project_cell_executor.py`
- Modify: `apps/api/src/omnia_api/services/generation_runs.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Modify: `apps/api/src/omnia_api/main.py`
- Test: `apps/api/tests/test_project_cell_executor.py`
- Test: `apps/api/tests/test_generation_runs.py`
- Test: `apps/api/tests/test_messages_project_cell.py`
- Test: `apps/api/tests/test_main_lifespan.py`

**Interfaces:**
- `maybe_create_project_cell_executor(..., agent_emit: Callable[[dict[str, object]], Awaitable[None]])` delegates `waiting_capacity` to `wait_for_capacity` and returns only after the same operation completes.
- Store `agent_state["dispatch"]` before `_spawn_process_prompt` with exact keys: `schema_version`, `project_id`, `user_id`, `user_message_id`, `assistant_message_id`, `current_snapshot_id`, `prompt_text`, `model_id`, `force_model`, `is_free`, `free_business_id`, `orchestrate`, `selected_elements`.
- `load_generation_dispatch(run) -> GenerationDispatch` validates exact keys, UUID ownership, JSON-native selected elements, maximum prompt length equal to `PromptRequest.prompt`, and `schema_version == 1`.
- `resume_capacity_queued_generations() -> int` selects only `queued_for_capacity` runs, validates their dispatch, skips any run already in `_PROMPT_TASKS`, and invokes `_spawn_process_prompt` once per run.
- `_run_tracked_prompt` must not overwrite `queued_for_capacity` with `running` until capacity is actually admitted; the executor sets `running` immediately before agent bootstrap.

- [ ] **Step 1: Write RED executor waiting tests**

Fake lifecycle outcomes `waiting_capacity`, `waiting_capacity`, `completed`. Assert the executor returns one handle, calls the same operation ID three times with increasing fences, emits one Russian waiting step and one ready step, and never invokes legacy runtime.

```python
assert emitted[0]["action"] == "Ожидаю ресурсы сервера"
assert lifecycle.operation_ids == [operation_id, operation_id, operation_id]
assert legacy_calls == []
```

- [ ] **Step 2: Write RED dispatch recovery tests**

Assert startup leaves `queued_for_capacity` runs non-terminal, marks ordinary `running` runs failed as before, resumes a valid queued dispatch exactly once, fails closed on invalid ownership/schema, and cancellation while queued becomes `cancelled` without an ensure retry.

- [ ] **Step 3: Implement exact dispatch serialization and startup order**

Lifespan order is:

```text
initialize engine
recover genuinely interrupted running generation runs
recover dispatched running cell operations as indeterminate
start Redis hub listener
resume capacity-queued generation dispatches
serve traffic
```

The queued dispatch is stored in the existing JSONB `agent_state`; `record_run_artifacts` must merge rather than replace it. A resumed `_process_prompt` reaches Project Cell ensure before any model call, billing consumption, source commit, or snapshot mutation.

- [ ] **Step 4: Release the lease in `_process_prompt` finalization**

Initialize `_project_cell_executor_handle = None` before the outer `try`. In the outer `finally`, call `await asyncio.shield(handle.release())` before clearing stream state. Log release failure and keep the DB lease intact; do not turn an otherwise completed product build into a false failure.

- [ ] **Step 5: Verify API recovery and single-flight**

```powershell
cd apps/api
uv run pytest tests/test_project_cell_executor.py tests/test_generation_runs.py tests/test_messages_project_cell.py tests/test_main_lifespan.py -q
uv run ruff check src/omnia_api/services/project_cell_executor.py src/omnia_api/services/generation_runs.py src/omnia_api/routers/messages.py src/omnia_api/main.py tests/test_project_cell_executor.py tests/test_generation_runs.py tests/test_messages_project_cell.py tests/test_main_lifespan.py
uv run mypy src/omnia_api/services/project_cell_executor.py src/omnia_api/services/generation_runs.py src/omnia_api/routers/messages.py src/omnia_api/main.py
```

Expected: duplicate browser POST, duplicate startup scan, two API processes, queue cancellation, and restart recovery all preserve one run and one generation.

---

### Task 7: Show capacity waiting as an active, recoverable MAX state

**Files:**
- Modify: `apps/api/src/omnia_api/schemas/message.py`
- Modify: `apps/api/src/omnia_api/schemas/max_studio.py`
- Modify: `apps/web/src/lib/api/types.ts`
- Modify: `apps/web/src/lib/generation-lifecycle.ts`
- Modify: `apps/web/src/components/workspace/AgentTranscript.tsx`
- Modify: `apps/web/src/hooks/usePromptStream.ts` only if status refresh does not already follow `agent.step`
- Test: `apps/web/src/lib/__tests__/generation-lifecycle.test.ts`
- Test: `apps/web/src/lib/__tests__/agent-transcript-title.test.ts`
- Test: create `apps/web/src/lib/__tests__/capacity-waiting-copy.test.tsx` if the transcript copy is component-owned

**Interfaces:**
- `GenerationRunStatus` adds `queued_for_capacity` and `ACTIVE_GENERATION_STATUSES` includes it.
- The visible Russian label is exactly `Ожидаю ресурсы сервера` with detail `Проект сохранён и запустится автоматически, как только освободится мощность.`
- Queued state keeps the input/cancel lifecycle consistent with any active generation and survives refresh from `GET /generation` plus persisted `agent_steps`.

- [ ] **Step 1: Write RED lifecycle and copy tests**

```typescript
expect(isGenerationActive({ status: "queued_for_capacity" })).toBe(true);
expect(capacityWaitingCopy.detail).toContain("запустится автоматически");
```

Also assert `isMaxBuildReady` is false while queued and Stop remains available.

- [ ] **Step 2: Confirm RED**

```powershell
cd apps/web
pnpm test -- src/lib/__tests__/generation-lifecycle.test.ts src/lib/__tests__/agent-transcript-title.test.ts src/lib/__tests__/capacity-waiting-copy.test.tsx
```

Expected before implementation: TypeScript rejects `queued_for_capacity` or treats it as inactive.

- [ ] **Step 3: Implement the additive UI state**

Do not create a second progress system. Reuse the persisted `agent.step` transcript and durable generation polling; only add the new active status and capacity-specific copy. An older API response without the status remains compatible.

- [ ] **Step 4: Verify web**

```powershell
pnpm test -- src/lib/__tests__/generation-lifecycle.test.ts src/lib/__tests__/agent-transcript-title.test.ts src/lib/__tests__/capacity-waiting-copy.test.tsx src/lib/__tests__/max-launch-single-flight.test.ts
pnpm typecheck
pnpm lint
```

Expected: tests, typecheck, and lint pass with no new warning.

---

### Task 8: Cross-service race, hibernate, restart, and data-preservation proof

**Files:**
- Create: `apps/api/tests/test_project_cell_capacity_integration.py`
- Modify: `apps/orchestrator/tests/test_project_cell_docker_integration.py`
- Modify: `apps/orchestrator/tests/test_live_docker_cell.py`
- Modify: `apps/orchestrator/tests/test_project_cell_rollout.py` if drain queries require the new active status
- Modify: production compose/config tests that assert the removed env key

**Interfaces:**
- The integration harness creates three projects/runs for one owner, forces capacity for one active bundle, and proves FIFO automatic continuation as each prior run releases and is hibernated.
- Real Docker tests remain opt-in and use unique test namespace UUIDs; they never touch production Project Cell labels or volumes.

- [ ] **Step 1: Write the failing three-project integration test**

```python
results = await submit_concurrently(project_a, project_b, project_c)
assert [result.accepted_count for result in results] == [1, 1, 1]
assert await wait_for_terminal(results, timeout=integration_timeout) == [
    "completed", "completed", "completed",
]
assert fake_provider.max_active_compute <= 1
assert fake_provider.duplicate_resource_creations == []
assert fake_provider.hibernated_projects == [project_a.id, project_b.id]
```

Persist a distinct row in each fake project database, hibernate/wake all three, and assert each project reads only its own value.

- [ ] **Step 2: Add restart and ambiguous-response cases**

Stop the API scheduler after the second run is queued, instantiate a fresh scheduler against the same database, and assert it resumes the same run/operation. Inject an ambiguous ensure response after side effect and assert reconciliation discovers the labeled bundle instead of creating a second one.

- [ ] **Step 3: Run all focused suites and full quality gates**

```powershell
cd apps/orchestrator
uv run pytest tests/test_cell_resources.py tests/test_cell_admission.py tests/test_cell_lock.py tests/test_cell_state.py tests/test_docker_cell_resources.py tests/test_workspace_provider.py tests/test_workspace_router.py tests/test_project_cell_docker_integration.py tests/test_live_docker_cell.py tests/test_project_cell_rollout.py -q
uv run ruff check src tests
uv run mypy src

cd ../api
uv run pytest tests/test_migrations_single_head.py tests/test_project_cell_models.py tests/test_generation_runs.py tests/test_project_cells.py tests/test_project_cell_lifecycle.py tests/test_project_cell_capacity.py tests/test_project_cell_capacity_integration.py tests/test_project_cell_executor.py tests/test_messages_project_cell.py tests/test_orchestrator_client.py tests/test_main_lifespan.py -q
uv run ruff check src tests migrations/versions/0055_project_cell_capacity_queue.py
uv run mypy src

cd ../web
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

Expected: all selected and full gates pass. Any pre-existing unrelated failure is recorded with exact command/output and must not mask a failure in a changed file.

- [ ] **Step 4: Run mutation-focused assertions**

Temporarily invert each critical predicate in an isolated test patch and confirm the corresponding test fails: FIFO head comparison, active-lease victim exclusion, capacity envelope identity, `waiting_capacity` active index, stale fence rejection, queued restart selection, and client active-status set. Revert each mutation immediately after observing RED and rerun GREEN.

---

### Task 9: Report, deliver, deploy, and prove production behavior

**Files:**
- Modify: `otchet/data.json`
- Commit: only the reviewed implementation, tests, migration, plan/report changes
- Production: documented full compose project `apps/llm-gateway/deploy/full`

**Interfaces:**
- H129 moves `open -> testing` when implementation begins and receives a score only after live production proof.
- V4 capacity-scheduler step becomes `true` only when three back-to-back real projects complete and data/restart/isolation checks pass.

- [ ] **Step 1: Update `/otchet` before delivery**

Raise `meta.updated` to `2026-09-02` and increment `meta.version`. Record exact test counts, migration `0055`, and then add the real implementation revision returned by Luna together with deployment health, three project IDs/workspace IDs, queue order, capacity reason, hibernation/wake evidence, and data checks. Keep `score=null` and V4 false until the live proof is complete.

- [ ] **Step 2: Hand exact reviewed files to `luna_delivery`**

Commit message:

```text
feat(project-cell): schedule cells by host capacity
```

Required trailer:

```text
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

Luna must fetch/compare again, preserve unrelated work, commit only intended files on `main`, and push `origin/main` without rewriting history.

- [ ] **Step 3: Deploy the exact pushed revision safely**

Use the repository's immutable rollout helper if it supports the changed API/orchestrator/web set; otherwise use only the documented production sequence:

```bash
ssh i48ptgvnis@170.168.72.200 'cd /opt/omnia && git fetch origin && git merge --ff-only origin/main && cd apps/llm-gateway/deploy/full && docker compose up -d --build api worker web gateway'
```

Include orchestrator deployment through its documented current immutable release/systemd path. Do not overwrite the 69 pre-existing unrelated server changes; abort if the fast-forward touches a dirty path.

- [ ] **Step 4: Verify revision, migration, services, and public health**

Require exact pushed SHA from API, worker, web, gateway where supported, and orchestrator release health. Confirm migration head `0055_project_cell_capacity_queue`, compose services healthy, systemd orchestrator active, external `/api/health` HTTP 200, external `/web-health` HTTP 200, gateway health HTTP 200, orchestrator health HTTP 200, and `/otchet/data.json` HTTP 200.

- [ ] **Step 5: Execute the real owner acceptance scenario**

Through the normal public MAX Studio authenticated as `undj00x03@gmail.com`, create and submit at least these three distinct projects back-to-back:

1. appointment application with masters, services, available time slots, bookings, and an admin schedule;
2. inventory application with products, stock movements, low-stock alerts, and an admin table;
3. task application with users, projects, task statuses, comments, and filtered boards.

For each project require a distinct Project Cell workspace and isolated PostgreSQL/Redis storage. Confirm no `active_bundle_limit`, no `indeterminate`, no duplicate prompt/run/resource, automatic progress from queued to generation where capacity is constrained, snapshot creation, private preview HTTP 200, a real frontend write reaching its backend and PostgreSQL, refresh persistence, stop/wake persistence, and cross-project denial.

- [ ] **Step 6: Exercise recovery without losing the accepted applications**

While one additional test run waits for capacity, restart the scheduler/API using the production compose service restart, then confirm the same run resumes automatically. Hibernate the first project, wake it from the normal UI, and verify its previously written database row. Never delete the three acceptance projects.

- [ ] **Step 7: Close H129 only from evidence**

If every live condition passes, set `H129.status="worked"`, assign score `8` or higher according to the report rubric, set the V4 capacity-scheduler step true, increment report version, commit/push the evidence through Luna, deploy `/otchet`, and verify HTTP 200. If any condition fails, keep `testing` or mark `partial` with the exact remaining failure and do not claim completion.

---

## Self-Review Record

- **Spec coverage:** physical capacity and no numerical gate map to Task 1; typed non-failure semantics to Tasks 2–3; durable FIFO and hibernation to Task 4; active-generation protection and idle readiness to Task 5; prompt persistence/restart/cancel to Task 6; user-visible state to Task 7; concurrency, persistence, ambiguity, and isolation to Task 8; production proof and reporting to Task 9.
- **Race ownership:** PostgreSQL advisory locking serializes scheduler choice and idle-victim reservation; orchestrator named file locking serializes physical host observation and Docker admission. No correctness claim depends on an in-memory mutex.
- **Lease lifecycle:** ensure binds an active generation; release clears it without stopping the preview; capacity pressure may then pause it through an ordinary fenced operation. A failed release leaves the lease set and therefore blocks eviction.
- **Failure classification:** only an exact 429 envelope whose operation UUID, epoch, and digest match the committed claim becomes `waiting_capacity`; malformed/unknown responses stay indeterminate and must reconcile.
- **Restart behavior:** only queued dispatches are resumed. Ordinary running work still follows the existing honest interruption path; dispatch validation prevents a forged JSONB payload from running another user's project.
- **Persistence:** pause/hibernate stops containers but retains all five existing named volumes and accepted platform snapshots. No task introduces automatic deletion.
- **Compatibility:** `queued_for_capacity` is additive to API/web lifecycle types and remains an active state; older clients continue to see an unfinished generation rather than terminal failure.
- **Completeness scan:** Every code step names its concrete behavior, error semantics, command, and neighboring interface. Task 9 records the immutable SHA returned by the delivery agent rather than guessing it in advance.
- **Type consistency:** The same status strings `queued_for_capacity` and `waiting_capacity`, exact capacity envelope keys, lifecycle kind `release`, and `GenerationDispatch` keys are used throughout all producing and consuming tasks.
- **Scope control:** The plan does not open Docker to non-owner accounts, change the model/provider, weaken completion evidence, delete old projects, or promise unlimited simultaneous compute.
