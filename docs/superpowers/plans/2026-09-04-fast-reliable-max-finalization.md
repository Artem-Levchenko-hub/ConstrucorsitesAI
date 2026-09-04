# Fast and Reliable MAX Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make owner-canary MAX generation finish in one proof-carrying finalization pass, normally within 15 minutes, without sacrificing project-controlled dependencies, dedicated project PostgreSQL, fencing, rollback, or release safety.

**Architecture:** A single API-side `MaxFinalizationCoordinator` computes a digest-complete proof identity, reuses immutable per-dimension results, and owns the transition from editing through build, runtime proof, snapshot, candidate promotion, and terminalization. The portable orchestrator exposes separate bootstrap, fast-check, and full-build commands plus a durable command journal; API activity leases veto hibernation and drive recovery, while append-only generation events provide ordered WebSocket replay. All work is delivered by one implementation owner in the order below so shared persistence, API, orchestrator, and web contracts never have parallel writers.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, Redis Pub/Sub, Docker Engine API, Pydantic 2, pytest/pytest-asyncio, Next.js/React/TypeScript, React Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-fast-reliable-max-finalization-design.md`

## Global Constraints

- Run the mandatory repository preflight before implementation and again before delivery: fetch all remotes, require a clean non-detached branch with an upstream, and require `HEAD` equal to its upstream or update only by `git pull --ff-only`.
- Use one implementation owner with `superpowers:executing-plans`; do not split API, orchestrator, database, or web edits across concurrent agents because the contracts and migration head are shared.
- Preserve project-admin access to the dedicated project PostgreSQL and project-controlled dependency installation.
- Keep the machine credential-free except for its own project database identity; never add host mounts, Docker socket, privileged mode, host network, platform secrets, core database credentials, or cross-cell access.
- Keep public dependency traffic behind the existing guarded egress path and continue denying private, metadata, host, platform, cross-cell, `file:`, `link:`, git/SSH, and unauthorized direct-HTTP destinations.
- Use 15-second command heartbeats, a 25-minute overall generation deadline, and a bounded SIGTERM-before-SIGKILL timeout path.
- Allocate exactly 2 CPU cores and 2 GiB RAM to the active portable MAX machine; account for PostgreSQL, proxy, guard, gateway, and managed-core resources separately before admission.
- Permit two build workers, cap Node old-space at 1.25 GiB, retain lifecycle-script concurrency 1, and never silently oversubscribe host memory.
- Reuse a green result only when every input in its dimension key matches; a source, dependency, schema/data, manifest, base image, toolchain, resource-profile, build-config, or fence change must follow the invalidation matrix in the spec.
- Never automatically rerun an unchanged failed full-build key, never build at a provider/segment cap, and never perform more than one successful full build for the final proof identity.
- Persist ordered progress before Redis fanout; event payloads contain only bounded, redacted details and never credentials or unbounded command logs.
- Keep every new behavior behind independent settings for proof reuse/coordinator, activity watchdog, resource profile v2, and durable event replay; disabling a flag must leave existing proof, activity, event, candidate, snapshot, and project data intact.
- Each task below ends in a locally reviewable change set and recorded verification. The implementation owner does not commit or push; after all tasks and review pass, the repository-mandated `luna_delivery` owner commits every intended file, pushes `HEAD` to `origin/main`, deploys only through the documented `/opt/omnia` production path, and records service/HTTP health evidence.

---

## File map and contract boundaries

### API persistence and domain services

- Create `apps/api/migrations/versions/0056_project_cell_finalization.py`: create proof identity/result, activity lease, and generation event tables without rewriting historical generation data.
- Modify `apps/api/src/omnia_api/models/project_cell.py`: define `ProjectCellProof`, `ProjectCellProofResult`, and `ProjectCellActivityLease`.
- Create `apps/api/src/omnia_api/models/generation_event.py`: define the append-only per-run sequence model.
- Modify `apps/api/src/omnia_api/models/__init__.py`: export the four new models.
- Create `apps/api/src/omnia_api/services/project_cell_proofs.py`: canonical digest construction, dimension keys, immutable result lookup/recording, and invalidation semantics.
- Create `apps/api/src/omnia_api/services/project_cell_activity.py`: durable lease start/heartbeat/finish/reconcile APIs.
- Create `apps/api/src/omnia_api/services/generation_events.py`: allocate monotonically increasing sequence numbers, persist redacted events, replay through a high-water mark, then fan out.
- Create `apps/api/src/omnia_api/services/max_finalization.py`: the only MAX finalization state machine and acceptance owner.
- Create `apps/api/src/omnia_api/services/generation_metrics.py`: phase timing/build-count projection into `GenerationRun.agent_state` and structured terminal logs.

### API integration

- Modify `apps/api/src/omnia_api/services/project_cell_executor.py`: expose current identity, distinct command roles, observed mutation deltas, stable operation IDs, and runtime proof without hidden apply/build work.
- Modify `apps/api/src/omnia_api/services/orchestrator_client.py`: transport command-role, digest, journal-status, and heartbeat fields.
- Modify `apps/api/src/omnia_api/services/agent_native.py`: preserve proof checkpoints across segments and remove unconditional cap/provider-stop builds.
- Modify `apps/api/src/omnia_api/services/release_proof.py`: consume the coordinator's green build/runtime result instead of invoking build/runtime again.
- Modify `apps/api/src/omnia_api/services/max_runtime_probe.py`: return one reusable, content-addressed runtime verification result for the exact preview identity.
- Modify `apps/api/src/omnia_api/services/project_cell_capacity.py`: exclude all live activity and machine operations from hibernation and recheck after acquiring the lifecycle fence.
- Modify `apps/api/src/omnia_api/routers/messages.py`: construct one coordinator, route agent checks through it, remove direct duplicate proof calls, and make snapshot/candidate promotion/terminalization idempotent.
- Modify `apps/api/src/omnia_api/routers/ws.py`: accept `after_seq`, replay a captured DB high-water mark, connect live fanout, and fill the race gap.
- Modify `apps/api/src/omnia_api/core/redis.py`: keep Redis as fanout only for durable generation progress; leave legacy non-generation publishers compatible.
- Modify `apps/api/src/omnia_api/core/config.py` and `apps/api/.env.example`: add independently reversible rollout/watchdog settings.

### Portable orchestrator

- Modify `apps/orchestrator/src/omnia_orchestrator/core/project_machine.py`: add `fast_check` and `full_build` task roles while retaining legacy roles for stored manifests.
- Modify `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`: add strict command roles, digest snapshots, operation status, phase, deadline, heartbeat, and log-byte progress.
- Modify `apps/orchestrator/src/omnia_orchestrator/services/machine_defaults.py`: declare install, fast-check, build, and final-test tasks separately and seed two-worker Next configuration.
- Modify `apps/orchestrator/src/omnia_orchestrator/services/machine_adapter.py`: execute only the requested role, persist command progress, expose identity capabilities, and start the exact built artifact without replaying bootstrap.
- Modify `apps/orchestrator/src/omnia_orchestrator/services/project_machine.py`: make the detached operation journal reattachable and terminally idempotent.
- Modify `apps/orchestrator/src/omnia_orchestrator/services/docker_machine_backend.py`: compute the controller-owned environment digest, expose 2 CPU/2 GiB limits, retain sandbox controls, and bound logs/processes.
- Modify `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`: return before/after digest snapshots and expose operation-status reconciliation under the workspace lock.
- Modify `apps/orchestrator/src/omnia_orchestrator/core/cell_resources.py`: represent the active machine, managed services, and helpers as separately summed quotas.
- Modify `apps/orchestrator/src/omnia_orchestrator/core/config.py` and `apps/orchestrator/.env.example`: configure resource profile v2 and command heartbeat/grace values.

### Web reconnect projection

- Modify `apps/web/src/lib/api/types.ts`: add generation `seq`/`event_id`, replay frames, coordinator phases, and tool lifecycle event types.
- Modify `apps/web/src/lib/agent-steps.ts`: merge current and replayed steps by sequence/event identity.
- Modify `apps/web/src/hooks/usePromptStream.ts`: maintain a per-run high-water cursor, apply replay deterministically, and count tool heartbeats as liveness.
- Modify `apps/web/src/components/workspace/AgentTranscript.tsx`: render running tool phase/elapsed state from start/heartbeat/finish events.

### Verification and operations

- Create focused tests named in each task below.
- Create `apps/api/tests/test_max_finalization_integration.py`: authored no-model fixture for the complete single-pass coordinator path.
- Modify `docs/operations/project-cell-main-stack.md`: document flags, truthful capacity math, canary evidence, rollback, and watchdog diagnosis.

---

### Task 1: Add rollout observability and durable finalization records

**Files:**
- Create: `apps/api/src/omnia_api/services/generation_metrics.py`
- Modify: `apps/api/src/omnia_api/core/config.py`
- Modify: `apps/api/.env.example`
- Create: `apps/api/tests/test_generation_metrics.py`
- Create: `apps/api/tests/test_config.py`

**Interfaces:**
- Consumes: existing `GenerationRun.agent_state: dict[str, object]` and the repository's structured logger.
- Produces: `GenerationPhase`, `record_phase_started(run, phase, now)`, `record_phase_finished(run, phase, now)`, `increment_generation_counter(run, name)`, and settings `use_max_finalization_coordinator`, `use_project_cell_activity_watchdog`, `use_generation_event_replay`, `use_cell_resource_profile_v2`, `max_generation_deadline_seconds=1500`, `project_cell_heartbeat_seconds=15`, `project_cell_watchdog_grace_seconds=20`.

- [ ] **Step 1: Write failing settings and metrics tests**

```python
def test_max_finalization_defaults_are_dark_and_deadlines_are_exact() -> None:
    assert get_settings().use_max_finalization_coordinator is False
    assert get_settings().use_project_cell_activity_watchdog is False
    assert get_settings().use_generation_event_replay is False
    assert get_settings().use_cell_resource_profile_v2 is False
    assert get_settings().max_generation_deadline_seconds == 1500
    assert get_settings().project_cell_heartbeat_seconds == 15
    assert get_settings().project_cell_watchdog_grace_seconds == 20


def test_phase_accounting_is_monotonic_and_counts_expensive_work() -> None:
    run = GenerationRun(agent_state={})
    record_phase_started(run, GenerationPhase.FINAL_BUILD, now=datetime(2026, 9, 4, tzinfo=UTC))
    increment_generation_counter(run, "full_build")
    record_phase_finished(
        run,
        GenerationPhase.FINAL_BUILD,
        now=datetime(2026, 9, 4, 0, 3, tzinfo=UTC),
    )
    assert run.agent_state["max_finalization"]["counters"]["full_build"] == 1
    assert run.agent_state["max_finalization"]["phase_ms"]["final_build"] == 180_000
```

- [ ] **Step 2: Run the focused tests and confirm the missing interfaces fail**

Run: `cd apps/api && uv run pytest tests/test_generation_metrics.py tests/test_config.py -q`

Expected: FAIL during import or field lookup because `generation_metrics.py` and the seven settings do not exist.

- [ ] **Step 3: Implement bounded phase/counter persistence**

```python
class GenerationPhase(StrEnum):
    PREPARE = "prepare"
    EDIT = "edit"
    FAST_CHECK = "fast_check"
    FINAL_BUILD = "final_build"
    RUNTIME_PROBE = "runtime_probe"
    SNAPSHOT = "snapshot"
    PROMOTE = "promote"
    COMPLETE = "complete"


def increment_generation_counter(run: GenerationRun, name: str) -> None:
    if name not in {"bootstrap", "fast_check", "full_build", "runtime_probe", "proof_hit"}:
        raise ValueError(f"unsupported generation counter: {name}")
    state = _copy_finalization_state(run.agent_state)
    counters = cast(dict[str, int], state.setdefault("counters", {}))
    counters[name] = counters.get(name, 0) + 1
    run.agent_state = {**run.agent_state, "max_finalization": state}
```

Store only integer timestamps, elapsed milliseconds, counters, the current phase, and terminal reason. Emit one structured terminal log containing total duration, phase durations, counts, proof key, operation ID, and outcome; do not add a new metrics backend.

- [ ] **Step 4: Run tests, lint, and type checking**

Run: `cd apps/api && uv run pytest tests/test_generation_metrics.py tests/test_config.py -q && uv run ruff check src/omnia_api/services/generation_metrics.py src/omnia_api/core/config.py tests/test_generation_metrics.py tests/test_config.py && uv run mypy src/omnia_api/services/generation_metrics.py src/omnia_api/core/config.py`

Expected: PASS; malformed counter names and deadline values outside Pydantic bounds are rejected.

- [ ] **Step 5: Run the observability cycle and keep it in the Task 1 change set**

Do not commit yet; the schema and its metrics projection form one reviewable persistence contract.

#### Persistence red/green cycle

**Files:**
- Create: `apps/api/migrations/versions/0056_project_cell_finalization.py`
- Modify: `apps/api/src/omnia_api/models/project_cell.py`
- Create: `apps/api/src/omnia_api/models/generation_event.py`
- Modify: `apps/api/src/omnia_api/models/__init__.py`
- Create: `apps/api/src/omnia_api/services/project_cell_proofs.py`
- Create: `apps/api/src/omnia_api/services/project_cell_activity.py`
- Create: `apps/api/src/omnia_api/services/generation_events.py`
- Modify: `apps/api/tests/test_migrations_single_head.py`
- Modify: `apps/api/tests/test_project_cell_migration_roundtrip.py`
- Create: `apps/api/tests/test_project_cell_proofs.py`
- Create: `apps/api/tests/test_project_cell_activity.py`
- Create: `apps/api/tests/test_generation_events.py`

**Interfaces:**
- Consumes: `ProjectCellWorkspace`, `GenerationRun`, PostgreSQL advisory/row locks, `sanitize_agent_step`, and `publish_event` for post-commit fanout.
- Produces: immutable `ProjectCellProof`, immutable `ProjectCellProofResult`, `ProjectCellActivityLease`, `GenerationEvent`, `ProofIdentity`, `ProofDimension`, `find_proof_result`, `record_proof_result`, `start_activity`, `heartbeat_activity`, `finish_activity`, `activity_blocks_hibernation`, `append_generation_event`, and `replay_generation_events`.

- [ ] **Step 1: Write migration/model tests for exact constraints**

```python
async def test_one_terminal_result_exists_per_dimension_key(db_session: AsyncSession) -> None:
    proof = await create_proof_identity(db_session, identity=identity())
    await record_proof_result(
        db_session,
        proof=proof,
        dimension=ProofDimension.FULL_BUILD,
        outcome=ProofOutcome.GREEN,
        operation_id=UUID(int=7),
        artifact_ref="build/sha256/" + "b" * 64,
        detail="green",
    )
    with pytest.raises(ProjectCellProofConflict, match="already terminal"):
        await record_proof_result(
            db_session,
            proof=proof,
            dimension=ProofDimension.FULL_BUILD,
            outcome=ProofOutcome.RED,
            operation_id=UUID(int=8),
            artifact_ref=None,
            detail="must not overwrite green",
        )


async def test_generation_event_sequence_is_gap_free_per_run(db_session: AsyncSession) -> None:
    first = await append_generation_event(db_session, run_id=run.id, message_id=None,
                                          event_type="generation.phase", payload={"phase": "edit"})
    second = await append_generation_event(db_session, run_id=run.id, message_id=None,
                                           event_type="generation.phase", payload={"phase": "fast_check"})
    assert (first.seq, second.seq) == (1, 2)
```

Also assert one active activity row per workspace, allowed states/kinds/dimensions, 64-character lowercase digest checks, cascade/restrict behavior, and an Alembic chain of `0055 -> 0056 -> head` through upgrade/downgrade/upgrade.

- [ ] **Step 2: Run persistence tests and verify the absent migration fails**

Run: `cd apps/api && uv run pytest tests/test_migrations_single_head.py tests/test_project_cell_migration_roundtrip.py tests/test_project_cell_proofs.py tests/test_project_cell_activity.py tests/test_generation_events.py -q`

Expected: FAIL because revision `0056_project_cell_finalization`, models, and services are missing.

- [ ] **Step 3: Create the exact schema**

Use these tables and uniqueness rules:

```text
project_cell_proofs:
  id uuid PK; workspace_id uuid FK CASCADE; generation_run_id uuid FK RESTRICT;
  fencing_epoch int > 0; proof_key char(64); workspace_revision char(64);
  dependency_digest char(64); schema_data_digest char(64);
  cell_manifest_digest char(64); base_image_digest char(64);
  toolchain_digest char(64); resource_profile_version text;
  build_config_digest char(64); created_at timestamptz;
  UNIQUE(workspace_id, fencing_epoch, proof_key)

project_cell_proof_results:
  id uuid PK; proof_id uuid FK CASCADE; workspace_id uuid FK CASCADE;
  dimension {bootstrap,fast_check,full_build,runtime,release};
  dimension_key char(64); outcome {green,red}; operation_id uuid;
  artifact_ref text NULL; detail_digest char(64); redacted_detail text;
  created_at timestamptz;
  UNIQUE(workspace_id, dimension, dimension_key)

project_cell_activity_leases:
  operation_id uuid PK; workspace_id uuid FK CASCADE;
  generation_run_id uuid FK SET NULL; kind {command,tool,finalization,snapshot,promotion};
  state {active,completed,failed,timed_out,cancelled}; fencing_epoch int > 0;
  proof_key char(64) NULL; phase text; started_at/deadline_at/heartbeat_at/finished_at;
  log_bytes bigint >= 0; redacted_diagnostic text NULL;
  partial UNIQUE(workspace_id) WHERE state='active'

generation_events:
  id uuid PK; generation_run_id uuid FK CASCADE; project_id uuid FK CASCADE;
  message_id uuid FK SET NULL; seq bigint > 0; event_type text;
  payload jsonb; created_at timestamptz;
  UNIQUE(generation_run_id, seq)
  INDEX(project_id, generation_run_id, seq)
```

Do not backfill proofs. Existing `Message.agent_steps` stays as a compatibility projection until Task 6.

- [ ] **Step 4: Implement canonical proof and dimension keys**

```python
class ProofDimension(StrEnum):
    BOOTSTRAP = "bootstrap"
    FAST_CHECK = "fast_check"
    FULL_BUILD = "full_build"
    RUNTIME = "runtime"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class ProofIdentity:
    workspace_id: UUID
    generation_run_id: UUID
    fencing_epoch: int
    workspace_revision: str
    dependency_digest: str
    schema_data_digest: str
    cell_manifest_digest: str
    base_image_digest: str
    toolchain_digest: str
    resource_profile_version: str
    build_config_digest: str

    @property
    def proof_key(self) -> str:
        return sha256(_canonical_json(asdict(self)).encode()).hexdigest()

    def dimension_key(self, dimension: ProofDimension) -> str:
        fields = _DIMENSION_FIELDS[dimension]
        return sha256(_canonical_json({name: getattr(self, name) for name in fields}).encode()).hexdigest()
```

`BOOTSTRAP` excludes `workspace_revision` and includes dependency/manifest/base/toolchain/resource/fence. `FAST_CHECK` adds workspace/schema/build config. `FULL_BUILD` uses every identity component. `RUNTIME` and `RELEASE` use every component and the referenced green build/runtime artifact digest respectively. This is how a source edit reuses bootstrap but never reuses build/runtime.

- [ ] **Step 5: Implement lease and event transactions**

```python
async def append_generation_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    message_id: UUID | None,
    event_type: str,
    payload: Mapping[str, object],
) -> GenerationEvent:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:run_id))"),
                          {"run_id": str(run_id)})
    next_seq = 1 + int(await session.scalar(
        select(func.coalesce(func.max(GenerationEvent.seq), 0)).where(
            GenerationEvent.generation_run_id == run_id
        )
    ) or 0)
    event = GenerationEvent(
        generation_run_id=run_id,
        project_id=project_id,
        message_id=message_id,
        seq=next_seq,
        event_type=event_type,
        payload=redact_event_payload(event_type, payload),
    )
    session.add(event)
    await session.flush()
    return event
```

Fanout is a separate post-commit call `publish_generation_event(event)` so Redis failure cannot erase DB truth. `heartbeat_activity` must update only an exact active `(operation_id, workspace_id, fencing_epoch)` row. `finish_activity` is idempotent only when the requested terminal state matches the stored terminal state.

- [ ] **Step 6: Run persistence, migration, lint, and type tests**

Run: `cd apps/api && uv run pytest tests/test_migrations_single_head.py tests/test_project_cell_migration_roundtrip.py tests/test_project_cell_proofs.py tests/test_project_cell_activity.py tests/test_generation_events.py -q && uv run ruff check migrations/versions/0056_project_cell_finalization.py src/omnia_api/models src/omnia_api/services/project_cell_proofs.py src/omnia_api/services/project_cell_activity.py src/omnia_api/services/generation_events.py tests/test_project_cell_proofs.py tests/test_project_cell_activity.py tests/test_generation_events.py && uv run mypy src/omnia_api/models/project_cell.py src/omnia_api/models/generation_event.py src/omnia_api/services/project_cell_proofs.py src/omnia_api/services/project_cell_activity.py src/omnia_api/services/generation_events.py`

Expected: PASS, including concurrent sequence allocation and duplicate terminal proof rejection.

- [ ] **Step 7: Checkpoint the observability and persistence contract for review**

Run: `git diff --check -- apps/api/migrations apps/api/src/omnia_api apps/api/tests apps/api/.env.example`

Expected: no whitespace errors. Record the exact passing commands and changed files; leave the change uncommitted for the final `luna_delivery` handoff.

### Task 2: Split portable commands and classify execution by digest

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/project_machine.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/machine_defaults.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/machine_adapter.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_machine_backend.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/project_machine.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`
- Modify: `apps/api/src/omnia_api/services/orchestrator_client.py`
- Test: `apps/orchestrator/tests/test_project_machine_manifest.py`
- Test: `apps/orchestrator/tests/test_machine_defaults.py`
- Test: `apps/orchestrator/tests/test_machine_adapter.py`
- Test: `apps/orchestrator/tests/test_project_machine.py`
- Test: `apps/orchestrator/tests/test_docker_machine_backend.py`
- Test: `apps/api/tests/test_orchestrator_client.py`

**Interfaces:**
- Consumes: current manifest v1, workspace revision, fencing epoch, pinned base image, stored operation ID, and project workspace volume.
- Produces: `WorkspaceIdentityDigest`, `WorkspaceAgentOperationStatusResponse`, roles `bootstrap|fast_check|full_build`, `DockerMachineBackend.environment_digest()`, and an idempotent GET status route for a known operation.

- [ ] **Step 1: Write role-separation and identity-delta tests**

```python
@pytest.mark.asyncio
async def test_full_build_never_executes_bootstrap_or_fast_check(tmp_path: Path) -> None:
    adapter, machine = adapter_fixture(tmp_path)
    result = await adapter.execute(state(), manifest(), request(task_role="full_build"))
    assert machine.started_task_names == ["build", "final-test"]
    assert "install" not in machine.started_task_names


@pytest.mark.asyncio
async def test_clean_command_returns_equal_before_after_digests() -> None:
    response = await client.project_cell_agent_exec(
        workspace_id=workspace_id,
        cmd="pwd",
        generation_run_id=run_id,
        fencing_epoch=7,
        expected_revision="a" * 64,
        operation_id=UUID(int=9),
    )
    assert response.before_identity == response.after_identity
    assert response.environment_mutated is False
```

Add cases proving bootstrap executes only install, fast-check executes only typecheck/lint/targeted tests, an `apt-get install` fixture changes `environment_digest`, an unchanged operation ID reattaches without starting a second Docker exec, and an operation ID with a different digest is rejected.

- [ ] **Step 2: Run focused suites and confirm the old coupled build fails**

Run: `cd apps/orchestrator && uv run pytest tests/test_project_machine_manifest.py tests/test_machine_defaults.py tests/test_machine_adapter.py tests/test_project_machine.py tests/test_docker_machine_backend.py -q`

Expected: FAIL because `build` currently expands to `bootstrap, build, test`, the new task roles are rejected, and the operation journal lacks digest/heartbeat fields.

Run: `cd apps/api && uv run pytest tests/test_orchestrator_client.py -q`

Expected: FAIL because the client rejects `fast_check`/`full_build` and has no identity/status response models.

- [ ] **Step 3: Define strict transport types**

```python
class WorkspaceIdentityDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_data_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cell_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceAgentExecRequest(BaseModel):
    # existing strict fields remain
    task_role: Literal["bootstrap", "fast_check", "full_build"] | None = None


class WorkspaceAgentExecResponse(BaseModel):
    ok: bool
    exit_code: int
    detail: str
    timed_out: bool = False
    operation_id: UUID
    before_identity: WorkspaceIdentityDigest
    after_identity: WorkspaceIdentityDigest
    environment_mutated: bool
```

Add `GET /internal/workspaces/{workspace_id}/agent/operations/{operation_id}` returning `state`, `phase`, `started_at`, `deadline_at`, `heartbeat_at`, `log_bytes`, and the same terminal response when completed.

- [ ] **Step 4: Split manifest tasks without restricting project packages**

```python
"tasks": [
    {"name": "install", "role": "bootstrap",
     "argv": ["pnpm", "install", "--frozen-lockfile"], "timeout_seconds": 900},
    {"name": "typecheck", "role": "fast_check",
     "argv": ["pnpm", "typecheck"], "timeout_seconds": 180},
    {"name": "lint", "role": "fast_check",
     "argv": ["pnpm", "lint"], "timeout_seconds": 180},
    {"name": "build", "role": "full_build",
     "argv": ["pnpm", "build"], "timeout_seconds": 600},
    {"name": "final-test", "role": "full_build",
     "argv": ["pnpm", "test"], "timeout_seconds": 300},
]
```

For a new dependency request, the agent updates `package.json` and resolves `pnpm-lock.yaml` once using an explicit shell/install path; finalization then uses frozen bootstrap. Stored old manifests with `build`/`test` remain parseable but the adapter maps them to the compatibility path only when the coordinator flag is off.

- [ ] **Step 5: Implement controller-owned digest capture and journal heartbeat**

`workspace_revision` hashes tracked source. `dependency_digest` hashes canonical `package.json`, `pnpm-lock.yaml`, Python/system package manifests when present, and their absence markers. `schema_data_digest` hashes migration source plus the durable project-database snapshot/schema identity. `environment_digest()` hashes the pinned base image, normalized `dpkg-query`, Python package inventory, Node/Corepack/pnpm versions, and controller-owned environment revision; no secret values are included.

```python
async def _execute(self, state: Any, manifest: MachineManifest, request: Any) -> DockerCommandResult:
    before = await self.identity_digest(state, manifest)
    commands = self._commands_for_role(manifest, request.task_role)
    result = await self._run_commands_with_journal(
        state=state,
        manifest=manifest,
        request=request,
        commands=commands,
    )
    after = await self.identity_digest(state, manifest)
    return replace(result, operation_id=request.operation_id,
                   before_identity=before, after_identity=after,
                   environment_mutated=before != after)
```

Persist journal fields before Docker start, refresh `heartbeat_at`, `phase`, and bounded `log_bytes` every 15 seconds during polling, and persist the terminal result before returning. Do not infer mutation from `task_role` or command name.

- [ ] **Step 6: Run all contract tests, lint, and type checks**

Run: `cd apps/orchestrator && uv run pytest tests/test_project_machine_manifest.py tests/test_machine_defaults.py tests/test_machine_adapter.py tests/test_project_machine.py tests/test_docker_machine_backend.py -q && uv run ruff check src tests/test_project_machine_manifest.py tests/test_machine_defaults.py tests/test_machine_adapter.py tests/test_project_machine.py tests/test_docker_machine_backend.py && uv run mypy src/omnia_orchestrator`

Run: `cd apps/api && uv run pytest tests/test_orchestrator_client.py -q && uv run ruff check src/omnia_api/services/orchestrator_client.py tests/test_orchestrator_client.py && uv run mypy src/omnia_api/services/orchestrator_client.py`

Expected: PASS; the full-build trace contains no install task and clean commands report no mutation.

- [ ] **Step 7: Keep the transport cycle in the Task 2 change set**

Do not commit yet; the API executor must consume this transport in the same review gate.

#### API executor red/green cycle

**Files:**
- Modify: `apps/api/src/omnia_api/services/project_cell_executor.py`
- Modify: `apps/api/src/omnia_api/services/max_runtime_probe.py`
- Test: `apps/api/tests/test_project_cell_executor.py`
- Test: `apps/api/tests/test_max_runtime_probe.py`

**Interfaces:**
- Consumes: `WorkspaceIdentityDigest` and role/status client methods from the first Task 2 cycle; `ProofIdentity` from Task 1.
- Produces: `ProjectCellCommandRole`, `ProjectCellCommandObservation`, `ProjectCellExecutorHandle.current_identity()`, `run_role()`, `runtime_probe()`, `operation_status()`, and exact invalidation deltas.

- [ ] **Step 1: Replace the old mutation tests with digest-driven regression tests**

```python
async def test_clean_portable_bash_does_not_invalidate_preview_or_proof(handle) -> None:
    result = await handle.execute(Action("bash", {"cmd": "pwd"}, ""))
    assert result["ok"] is True
    assert result["environment_mutated"] is False
    assert await handle.current_identity() == initial_identity


async def test_dependency_change_invalidates_bootstrap_and_every_later_dimension(handle) -> None:
    result = await handle.execute(Action("bash", {"cmd": "pnpm add zod@4"}, ""))
    assert result["mutation"]["dependency_changed"] is True
    assert set(result["invalidated_dimensions"]) == {
        "bootstrap", "fast_check", "full_build", "runtime", "release"
    }
```

Add tests for source-only, schema-only, manifest, environment/toolchain/resource/fence changes, repeated bootstrap reuse, and one runtime probe returning a verification content address tied to the exact proof key.

- [ ] **Step 2: Run focused executor/probe tests and verify current broad invalidation fails**

Run: `cd apps/api && uv run pytest tests/test_project_cell_executor.py tests/test_max_runtime_probe.py -q`

Expected: FAIL because portable `bash` and `build` always return `environment_mutated=True`, the handle has no identity/role APIs, and preview synchronization invokes coupled apply/build behavior.

- [ ] **Step 3: Add exact executor result types**

```python
class ProjectCellCommandRole(StrEnum):
    BOOTSTRAP = "bootstrap"
    FAST_CHECK = "fast_check"
    FULL_BUILD = "full_build"


@dataclass(frozen=True, slots=True)
class ProjectCellCommandObservation:
    operation_id: UUID
    role: ProjectCellCommandRole | None
    ok: bool
    timed_out: bool
    redacted_detail: str
    before: ProofIdentity
    after: ProofIdentity
    invalidated_dimensions: frozenset[ProofDimension]


@dataclass(frozen=True, slots=True)
class ProjectCellExecutorHandle:
    # retain existing execute/stage/snapshot/release fields
    current_identity: Callable[[], Awaitable[ProofIdentity]]
    run_role: Callable[[ProjectCellCommandRole, UUID], Awaitable[ProjectCellCommandObservation]]
    runtime_probe: Callable[[str], Awaitable[RuntimeProofResult]]
    operation_status: Callable[[UUID], Awaitable[ProjectCellOperationStatus]]
```

Map the old agent `build` tool to `FAST_CHECK` while the coordinator flag is enabled; the coordinator alone can call `FULL_BUILD`. `sync_preview()` may start/reconcile preview for an already green build but may not call install/build/test.

- [ ] **Step 4: Apply the invalidation matrix from before/after identity fields**

```python
def invalidated_dimensions(before: ProofIdentity, after: ProofIdentity) -> frozenset[ProofDimension]:
    if before.fencing_epoch != after.fencing_epoch or any(
        getattr(before, field) != getattr(after, field)
        for field in ("cell_manifest_digest", "base_image_digest", "toolchain_digest",
                      "resource_profile_version")
    ):
        return frozenset(ProofDimension)
    invalid: set[ProofDimension] = set()
    if before.dependency_digest != after.dependency_digest:
        invalid.update(ProofDimension)
    if before.workspace_revision != after.workspace_revision:
        invalid.update({ProofDimension.FAST_CHECK, ProofDimension.FULL_BUILD,
                        ProofDimension.RUNTIME, ProofDimension.RELEASE})
    if before.schema_data_digest != after.schema_data_digest:
        invalid.update({ProofDimension.RUNTIME, ProofDimension.RELEASE})
        if before.build_config_digest != after.build_config_digest:
            invalid.add(ProofDimension.FULL_BUILD)
    return frozenset(invalid)
```

Build/test/probe/read/log actions with equal identities do not invalidate anything. A partial failure still returns the observed post-command identity and invalidates only what changed.

- [ ] **Step 5: Run focused and routing regressions**

Run: `cd apps/api && uv run pytest tests/test_project_cell_executor.py tests/test_max_runtime_probe.py tests/test_messages_project_cell.py -q && uv run ruff check src/omnia_api/services/project_cell_executor.py src/omnia_api/services/max_runtime_probe.py tests/test_project_cell_executor.py tests/test_max_runtime_probe.py && uv run mypy src/omnia_api/services/project_cell_executor.py src/omnia_api/services/max_runtime_probe.py`

Expected: PASS; the selected Project Cell never falls back to legacy execution and clean commands retain their proof identity.

- [ ] **Step 6: Checkpoint role separation and identity-aware execution for review**

Run: `git diff --check -- apps/orchestrator apps/api/src/omnia_api/services/orchestrator_client.py apps/api/src/omnia_api/services/project_cell_executor.py apps/api/src/omnia_api/services/max_runtime_probe.py apps/api/tests`

Expected: no whitespace errors. Record the exact passing commands and changed files; leave the change uncommitted for the final `luna_delivery` handoff.

### Task 3: Implement and integrate the single finalization coordinator

**Files:**
- Create: `apps/api/src/omnia_api/services/max_finalization.py`
- Modify: `apps/api/src/omnia_api/services/release_proof.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_candidates.py`
- Create: `apps/api/tests/test_max_finalization.py`
- Test: `apps/api/tests/test_release_proof.py`
- Test: `apps/api/tests/test_project_cells.py`

**Interfaces:**
- Consumes: proof/activity/event services, `ProjectCellExecutorHandle`, `max_source_completion_gap`, runtime probe, candidate CAS service, and generation metrics.
- Produces: `MaxFinalizationCheckpoint`, `MaxFinalizationOutcome`, `MaxFinalizationCoordinator.fast_check()`, `finalize()`, `resume()`, and `run_release_proof(project_id, project_slug, proof=proof_bundle, require_max_data=True, project_cell_handle=handle)` that performs no command when dimensions are green.

- [ ] **Step 1: Write coordinator state-machine tests before implementation**

```python
async def test_finalize_runs_one_full_build_and_reuses_it_for_release(coordinator, executor) -> None:
    outcome = await coordinator.finalize(files=complete_files(), prompt="Build tracker")
    assert outcome.status == "complete"
    assert executor.roles == ["bootstrap", "full_build"]
    assert executor.runtime_probe_calls == 1
    assert outcome.proof.full_build.outcome == "green"
    assert outcome.proof.release.outcome == "green"


async def test_unchanged_red_build_is_terminal_without_retry(coordinator, executor) -> None:
    executor.full_build_result = failed("TS2322")
    first = await coordinator.finalize(files=complete_files(), prompt="Build tracker")
    second = await coordinator.resume(first.checkpoint)
    assert first.status == second.status == "failed"
    assert executor.roles.count("full_build") == 1
```

Also cover bootstrap result reuse after a source edit, deterministic source-gap return to `EDIT`, schema migration exactly once, proof reuse after coordinator reconstruction, one candidate prepare/promote, idempotent completed acceptance, and mismatched proof key/fence rejection.

- [ ] **Step 2: Run tests and confirm the coordinator is absent**

Run: `cd apps/api && uv run pytest tests/test_max_finalization.py tests/test_release_proof.py tests/test_project_cells.py -q`

Expected: FAIL importing `MaxFinalizationCoordinator`; current release proof directly invokes build and runtime.

- [ ] **Step 3: Define the durable checkpoint and deterministic decision types**

```python
class MaxFinalizationStatus(StrEnum):
    NEEDS_EDIT = "needs_edit"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class MaxFinalizationCheckpoint:
    generation_run_id: UUID
    workspace_id: UUID
    proof_key: str
    phase: GenerationPhase
    operation_id: UUID | None
    candidate_id: UUID | None
    acceptance_id: str


@dataclass(frozen=True, slots=True)
class MaxFinalizationOutcome:
    status: MaxFinalizationStatus
    checkpoint: MaxFinalizationCheckpoint
    proof: ProofBundle
    redacted_detail: str
```

Serialize the checkpoint under `GenerationRun.agent_state["max_finalization"]["checkpoint"]`; never put transcript text, credentials, or full logs there.

- [ ] **Step 4: Implement `finalize()` as the only acceptance owner**

```python
async def finalize(self, *, files: Mapping[str, str], prompt: str) -> MaxFinalizationOutcome:
    source_gap = max_source_completion_gap(prompt, files, portable=True)
    if source_gap is not None:
        return await self._needs_edit(source_gap)
    identity = await self.executor.current_identity()
    proof = await self.proofs.get_or_create(identity)
    await self._satisfy_bootstrap(proof)
    build = await self._satisfy_full_build_once(proof)
    if build.outcome is ProofOutcome.RED:
        return await self._failed("final_build", build.redacted_detail)
    runtime = await self._satisfy_runtime_once(proof, build)
    if runtime.outcome is ProofOutcome.RED:
        return await self._failed("runtime_probe", runtime.redacted_detail)
    release = await run_release_proof(
        self.project_id,
        self.project_slug,
        proof=ProofBundle(identity=proof, full_build=build, runtime=runtime),
        require_max_data=True,
        project_cell_handle=self.executor,
    )
    candidate = await self._snapshot_and_prepare_candidate_once(proof, release)
    await self._promote_candidate_once(candidate)
    return await self._complete_once(proof, candidate)
```

Every `_satisfy_*` checks the relevant `dimension_key` before starting activity. A terminal red or green result is returned directly. Only absence starts a stable `operation_id=uuid5(run_id, f"{dimension}:{dimension_key}")`. Runtime/release refs include the green build artifact digest. Snapshot/candidate/promotion use stable idempotency keys derived from `acceptance_id=sha256(run_id + proof_key)`.

- [ ] **Step 5: Convert release proof to evidence consumption**

```python
async def run_release_proof(
    project_id: UUID,
    project_slug: str,
    *,
    proof: ProofBundle | None = None,
    require_max_data: bool = False,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
) -> FunctionalVerdict:
    if proof is not None:
        return summarize(proof.release_checks(require_max_data=require_max_data))
    return await _run_legacy_release_proof(
        project_id,
        project_slug,
        require_max_data=require_max_data,
        project_cell_handle=project_cell_handle,
    )
```

The flagged MAX Project Cell path must never call `project_cell_handle.execute(Action("build"))`, `runtime_check`, or create a second preview session from this function. Legacy/non-cell behavior remains unchanged while the flag is off.

- [ ] **Step 6: Run coordinator, release, candidate, lint, and type tests**

Run: `cd apps/api && uv run pytest tests/test_max_finalization.py tests/test_release_proof.py tests/test_project_cells.py tests/test_max_generation_contract.py -q && uv run ruff check src/omnia_api/services/max_finalization.py src/omnia_api/services/release_proof.py src/omnia_api/services/project_cell_candidates.py tests/test_max_finalization.py tests/test_release_proof.py tests/test_project_cells.py && uv run mypy src/omnia_api/services/max_finalization.py src/omnia_api/services/release_proof.py src/omnia_api/services/project_cell_candidates.py`

Expected: PASS; command traces show one full build, one runtime probe, and no command during release evidence consumption.

- [ ] **Step 7: Keep coordinator core in the Task 3 change set**

Do not commit yet; the coordinator is not independently useful until all MAX callers route through it.

#### MAX call-site integration red/green cycle

**Files:**
- Modify: `apps/api/src/omnia_api/services/agent_native.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Test: `apps/api/tests/test_agent_native.py`
- Test: `apps/api/tests/test_messages_project_cell.py`
- Test: `apps/api/tests/test_generation_runs.py`
- Create: `apps/api/tests/test_max_finalization_integration.py`

**Interfaces:**
- Consumes: coordinator/checkpoint from the first Task 3 cycle, executor fast-check mapping from Task 2, existing `AgentResult`, `max_source_completion_gap`, repository commit/snapshot services, and generation outcome finalizer.
- Produces: `NativeProofCheckpoint`, a segment runner that returns `needs_finalization` without building, one coordinator instance per run, and an authored no-model complete path.

- [ ] **Step 1: Add failing cap/continuation and call-count regressions**

```python
async def test_segment_cap_never_runs_full_build() -> None:
    result = await run_one_step_max_segment(execute=recording_execute)
    assert result.stop_reason == "max_steps"
    assert "full_build" not in recorded_roles
    assert result.needs_finalization is True


async def test_first_portable_seed_and_release_use_one_final_build(no_model_max_fixture) -> None:
    result = await no_model_max_fixture.run()
    assert result.terminal_status == "completed"
    assert result.counts == {"bootstrap": 1, "full_build": 1, "runtime_probe": 1,
                             "snapshot": 1, "promotion": 1}
    assert result.model_calls == 0
```

Add explicit tests for setup apply, provider stop, 40-step boundary, continuation, rollback, release proof, and final snapshot. Each must assert no direct full-build call outside the coordinator. A completed coordinator checkpoint replay must not create another snapshot, candidate, promotion, or terminal event.

- [ ] **Step 2: Run integration tests and verify duplicate callers are exposed**

Run: `cd apps/api && uv run pytest tests/test_agent_native.py tests/test_messages_project_cell.py tests/test_generation_runs.py tests/test_max_finalization_integration.py -q`

Expected: FAIL because `_finish_without_provider()` builds at caps, each segment starts with local proof state, starter apply/probe double-builds, and `messages.py` calls post-agent/release/final snapshot proof paths directly.

- [ ] **Step 3: Replace segment-local build evidence with a checkpoint**

```python
@dataclass(frozen=True, slots=True)
class NativeProofCheckpoint:
    proof_key: str | None = None
    fast_check_green: bool = False
    source_complete: bool = False
    acceptance_started: bool = False


async def _finish_segment_at_cap(
    *,
    effective_max_steps: int,
    written: dict[str, str],
    convo: list[dict[str, object]],
    evidence: dict[str, int],
    source_gap: str | None,
    current_checkpoint: NativeProofCheckpoint,
) -> AgentResult:
    return AgentResult(
        done=False,
        summary=source_gap or "Source is complete; deterministic finalization is reserved.",
        files=written,
        steps=effective_max_steps,
        transcript=convo,
        stop_reason="max_steps",
        evidence=evidence,
        needs_finalization=source_gap is None,
        proof_checkpoint=current_checkpoint,
    )
```

`_run_native_segments` passes the returned checkpoint into the next segment. It continues only for a deterministic source gap or observable content progress. A proof-only gap transfers control to the coordinator; the continuation prompt no longer says “rerun required proof.”

- [ ] **Step 4: Integrate exactly one coordinator in `messages.py`**

At `_prepare_max_runtime_context`, create the handle and coordinator once after admission. Replace:

- starter `_apply_project_cell_preview_files()` plus `_probe_build_status()` with file staging only;
- `_probe_build_status()` in the MAX cell agent loop with `coordinator.fast_check()`;
- post-agent double runtime checks with `coordinator.finalize()`;
- rollback proof calls with restoration followed by a new identity lookup only;
- universal release proof direct calls with the coordinator's release verdict;
- final snapshot preview apply with coordinator snapshot/promotion result reuse.

The final branch is deterministic:

```python
if agent_result.needs_finalization or agent_result.done:
    final = await coordinator.finalize(files=await handle.snapshot_files(), prompt=prompt_text)
    if final.status is MaxFinalizationStatus.NEEDS_EDIT:
        agent_result = await continue_for_source_gap(final.redacted_detail)
    elif final.status is MaxFinalizationStatus.COMPLETE:
        return await persist_completed_generation(final)
    else:
        return await persist_failed_generation(final)
```

No model call is allowed after all deterministic source checks are green. Reserve coordinator time outside `max_steps`; use the 25-minute run deadline, not provider steps, as its outer bound.

- [ ] **Step 5: Run single-pass, lifecycle, and unchanged legacy regressions**

Run: `cd apps/api && uv run pytest tests/test_agent_native.py tests/test_messages_project_cell.py tests/test_generation_runs.py tests/test_max_finalization.py tests/test_max_finalization_integration.py tests/test_release_proof.py -q`

Expected: PASS. The authored fixture has zero provider calls and exactly one bootstrap/full-build/runtime/snapshot/promotion; legacy and non-MAX paths retain existing behavior.

- [ ] **Step 6: Run static checks on the high-risk orchestration files**

Run: `cd apps/api && uv run ruff check src/omnia_api/services/agent_native.py src/omnia_api/services/max_finalization.py src/omnia_api/routers/messages.py tests/test_agent_native.py tests/test_messages_project_cell.py tests/test_max_finalization_integration.py && uv run mypy src/omnia_api/services/agent_native.py src/omnia_api/services/max_finalization.py src/omnia_api/routers/messages.py`

Expected: PASS without unused compatibility branches or untyped checkpoint fields.

- [ ] **Step 7: Checkpoint the end-to-end single-pass coordinator flow for review**

Run: `git diff --check -- apps/api/src/omnia_api apps/api/tests`

Expected: no whitespace errors. Record the exact passing commands and changed files; leave the change uncommitted for the final `luna_delivery` handoff.

### Task 4: Add durable heartbeat, restart reattachment, hibernation veto, and terminal watchdog

**Files:**
- Modify: `apps/api/src/omnia_api/services/project_cell_activity.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_executor.py`
- Modify: `apps/api/src/omnia_api/services/project_cell_capacity.py`
- Modify: `apps/api/src/omnia_api/services/max_finalization.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/project_machine.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/machine_adapter.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`
- Test: `apps/api/tests/test_project_cell_activity.py`
- Test: `apps/api/tests/test_project_cell_capacity.py`
- Test: `apps/api/tests/test_project_cell_capacity_integration.py`
- Test: `apps/api/tests/test_max_finalization.py`
- Test: `apps/orchestrator/tests/test_project_machine.py`
- Test: `apps/orchestrator/tests/test_machine_adapter.py`

**Interfaces:**
- Consumes: Task 1 activity rows, Task 2 operation journal/status endpoint, existing capacity scheduler, workspace operation lock, generation cancellation, and Task 3 checkpoint.
- Produces: `run_with_activity_lease`, `reconcile_activity`, `watch_generation_deadline`, and a two-layer hibernation veto checked in API selection and again under the orchestrator workspace lock.

- [ ] **Step 1: Write race, restart, and timeout tests**

```python
async def test_hibernation_rechecks_activity_after_selecting_victim(db_session, client) -> None:
    victim = await claim_idle_hibernation_victim(db_session, requester_workspace_id=requester.id)
    await start_test_activity(
        db_session,
        workspace_id=victim.id,
        generation_run_id=run.id,
        operation_id=UUID(int=44),
        kind="command",
    )
    assert await hibernate_selected_test_workspace(victim.id) is False
    assert client.pause_calls == []


async def test_api_restart_reattaches_running_operation_without_second_exec(activity_fixture) -> None:
    first = await activity_fixture.start_and_drop_client(operation_id=UUID(int=51))
    resumed = await reconcile_activity(first.operation_id)
    assert resumed.terminal_result.ok is True
    assert activity_fixture.docker_exec_start_count == 1
```

Add tests that a stale API heartbeat plus a running machine operation blocks hibernation, a completed machine operation closes a stale lease, the 25-minute watchdog creates one failed terminal state, SIGTERM precedes SIGKILL after 20 seconds, cancellation wins over completion, and no orphan active lease remains.

- [ ] **Step 2: Run the focused lifecycle suites and confirm current lease gaps fail**

Run: `cd apps/api && uv run pytest tests/test_project_cell_activity.py tests/test_project_cell_capacity.py tests/test_project_cell_capacity_integration.py tests/test_max_finalization.py -q`

Run: `cd apps/orchestrator && uv run pytest tests/test_project_machine.py tests/test_machine_adapter.py -q`

Expected: FAIL because capacity selection sees only generation lease/operation state, API has no 15-second activity updater, and a restarted owner cannot query/rejoin a detached operation.

- [ ] **Step 3: Wrap every long operation in the same lease helper**

```python
async def run_with_activity_lease[T](
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease: ActivityStart,
    work: Callable[[], Awaitable[T]],
    poll_status: Callable[[UUID], Awaitable[ProjectCellOperationStatus]],
    emit: Callable[[str, Mapping[str, object]], Awaitable[None]],
) -> T:
    await start_activity(session_factory, lease)
    heartbeat = asyncio.create_task(_heartbeat_loop(lease, poll_status, emit, seconds=15))
    try:
        result = await work()
    except asyncio.CancelledError:
        await reconcile_activity(
            session_factory=session_factory,
            workspace_id=lease.workspace_id,
            operation_id=lease.operation_id,
            poll_status=poll_status,
            cancellation_requested=True,
        )
        raise
    except Exception as exc:
        await finish_activity(
            session_factory,
            operation_id=lease.operation_id,
            state="failed",
            diagnostic=redact_exception(exc),
        )
        raise
    else:
        await finish_activity(
            session_factory,
            operation_id=lease.operation_id,
            state="completed",
        )
        return result
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
```

Use it for bootstrap, fast check, full build, runtime probe, finalization, snapshot, and promotion. Heartbeat events include operation ID, phase, elapsed seconds, deadline, and bounded log bytes only.

- [ ] **Step 4: Make hibernation fail closed against both durable authorities**

`claim_idle_hibernation_victim` excludes live activity rows. `hibernate_one_idle_workspace` then acquires the lifecycle fence, reloads workspace/run/activity rows, queries orchestrator journal state, and pauses only when every source says idle. A stale activity row is reconciled only after the journal says `completed|failed|cancelled|missing`; `running` always vetoes hibernation.

```python
if await activity_blocks_hibernation(session, workspace.id):
    return False
status = await client.agent_operation_status(workspace.id, latest_operation_id)
if status.state in {"starting", "running"}:
    return False
return await client.pause(exact_fenced_request)
```

- [ ] **Step 5: Implement one recovery and one terminal watchdog outcome**

At process recovery or deadline, load the checkpoint operation ID. Reattach once by the same ID. If it completes, resume the next coordinator phase; if its deadline expires, the command watchdog sends SIGTERM, waits 20 seconds, verifies the same fence/operation is alive, then SIGKILLs it. Record exactly one terminal generation failure containing phase, proof key, operation ID, and redacted diagnostic. Never restart a completed phase or unchanged failed build.

- [ ] **Step 6: Run lifecycle, cancellation, and capacity suites**

Run: `cd apps/api && uv run pytest tests/test_project_cell_activity.py tests/test_project_cell_capacity.py tests/test_project_cell_capacity_integration.py tests/test_max_finalization.py tests/test_generation_runs.py -q`

Run: `cd apps/orchestrator && uv run pytest tests/test_project_machine.py tests/test_machine_adapter.py tests/test_cell_reservations.py -q`

Expected: PASS; all simulated API/worker restart and hibernation races retain one Docker exec and one terminal run state.

- [ ] **Step 7: Checkpoint lifecycle protection for review**

Run: `git diff --check -- apps/api apps/orchestrator`

Expected: no whitespace errors. Record the exact passing commands and changed files; leave the change uncommitted for the final `luna_delivery` handoff.

### Task 5: Introduce the truthful 2 CPU/2 GiB active profile and safe project caches

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/config.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/cell_resources.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/machine_adapter.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/docker_machine_backend.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/services/machine_defaults.py`
- Modify: `apps/orchestrator/.env.example`
- Test: `apps/orchestrator/tests/test_cell_resources.py`
- Test: `apps/orchestrator/tests/test_cell_reservations.py`
- Test: `apps/orchestrator/tests/test_docker_cell_resources.py`
- Test: `apps/orchestrator/tests/test_docker_machine_backend.py`
- Test: `apps/orchestrator/tests/test_machine_defaults.py`

**Interfaces:**
- Consumes: existing reservation ledger, resource names, Docker cgroup options, pinned image settings, and manifest mounts.
- Produces: `CellResourceProfile.active_machine_quota`, separately named helper/core/database quotas, exact `full_quota`, profile version `docker-owner-cell-resources-v2`, and project-scoped pnpm/Corepack/Next caches keyed by identity.

- [ ] **Step 1: Write exact envelope and runtime-option tests**

```python
def test_v2_full_quota_sums_every_component_once() -> None:
    profile = CellResourceProfile.from_settings(v2_settings())
    assert profile.active_machine_quota.cpu_cores == 2.0
    assert profile.active_machine_quota.memory_bytes == 2 * 1024**3
    expected_cpu = sum(q.cpu_cores for q in profile.component_quotas())
    expected_memory = sum(q.memory_bytes for q in profile.component_quotas())
    assert profile.full_quota.cpu_cores == expected_cpu
    assert profile.full_quota.memory_bytes == expected_memory


def test_active_machine_uses_two_workers_and_1250mb_heap(tmp_path) -> None:
    options = v2_backend(tmp_path).container_options(manifest(), "guard", 7)
    assert options["nano_cpus"] == 2_000_000_000
    assert options["mem_limit"] == 2 * 1024**3
    assert options["environment"]["NODE_OPTIONS"] == "--max-old-space-size=1280"
    assert options["environment"]["NEXT_PRIVATE_BUILD_WORKER"] == "2"
    assert options["environment"]["npm_config_child_concurrency"] == "1"
```

Also assert an insufficient host queues rather than admitting, reserved quota equals the actual Docker limits, cache mounts are project-scoped and not host paths, profile v1 remains available with its flag off, and sandbox/egress/DB isolation options are byte-for-byte retained.

- [ ] **Step 2: Run resource tests and confirm current hidden subtraction fails**

Run: `cd apps/orchestrator && uv run pytest tests/test_cell_resources.py tests/test_cell_reservations.py tests/test_docker_cell_resources.py tests/test_docker_machine_backend.py tests/test_machine_defaults.py -q`

Expected: FAIL because the machine currently receives `executor - helpers` (about 0.3 CPU/896 MiB), `full_quota` double-shares slices, Node heap is 512 MiB, and Next is forced to one CPU.

- [ ] **Step 3: Add explicit component settings and profile validation**

```python
cell_profile_version: str = "docker-owner-cell-resources-v2"
cell_active_machine_cpu_cores: float = Field(default=2.0, gt=0)
cell_active_machine_memory_bytes: int = Field(default=2 * 1024**3, gt=0)
cell_project_postgres_cpu_cores: float = Field(default=0.15, gt=0)
cell_project_postgres_memory_bytes: int = Field(default=256 * 1024**2, gt=0)
cell_helper_cpu_cores: float = Field(default=0.2, ge=0)
cell_helper_memory_bytes: int = Field(default=128 * 1024**2, ge=0)
cell_managed_core_cpu_cores: float = Field(default=0.35, gt=0)
cell_managed_core_memory_bytes: int = Field(default=768 * 1024**2, gt=0)
```

`component_quotas()` returns active machine, project PostgreSQL, guard/proxy/gateway helpers, managed core, and any still-live legacy bundle component exactly once. Admission uses the sum, not nominal `cell_bundle_*` fields. Reject v2 if any Docker cgroup value differs from the admitted component.

- [ ] **Step 4: Mount caches within project-owned Docker volumes**

Use deterministic workspace-prefixed named volumes for pnpm store and Corepack. Reuse `.next/cache` only when dependency, Next/toolchain, and resource-profile digests match; otherwise clear only that project cache. Do not share writable caches across projects and do not use host bind mounts.

Remove `experimental: { cpus: 1 }` from the seed and set supported two-worker environment/config. Preserve `cap_drop`, `no-new-privileges`, PID limits, guarded network namespace, memswap equality, and project-only database environment.

- [ ] **Step 5: Run resource, security-boundary, lint, and type tests**

Run: `cd apps/orchestrator && uv run pytest tests/test_cell_resources.py tests/test_cell_reservations.py tests/test_docker_cell_resources.py tests/test_docker_machine_backend.py tests/test_machine_defaults.py -q && uv run ruff check src/omnia_orchestrator/core/config.py src/omnia_orchestrator/core/cell_resources.py src/omnia_orchestrator/services/machine_adapter.py src/omnia_orchestrator/services/docker_machine_backend.py src/omnia_orchestrator/services/machine_defaults.py tests/test_cell_resources.py tests/test_cell_reservations.py tests/test_docker_machine_backend.py tests/test_machine_defaults.py && uv run mypy src/omnia_orchestrator/core/config.py src/omnia_orchestrator/core/cell_resources.py src/omnia_orchestrator/services/machine_adapter.py src/omnia_orchestrator/services/docker_machine_backend.py`

Expected: PASS with exact 2 CPU/2 GiB limits and unchanged host/network/credential denial assertions.

- [ ] **Step 6: Checkpoint resource profile v2 for review**

Run: `git diff --check -- apps/orchestrator`

Expected: no whitespace errors. Record the exact passing commands and changed files; leave the change uncommitted for the final `luna_delivery` handoff.

### Task 6: Make progress replayable, verify the full contract, and deliver safely

**Files:**
- Modify: `apps/api/src/omnia_api/services/generation_events.py`
- Modify: `apps/api/src/omnia_api/core/redis.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Modify: `apps/api/src/omnia_api/routers/ws.py`
- Modify: `apps/web/src/lib/api/types.ts`
- Modify: `apps/web/src/lib/agent-steps.ts`
- Modify: `apps/web/src/hooks/usePromptStream.ts`
- Modify: `apps/web/src/components/workspace/AgentTranscript.tsx`
- Test: `apps/api/tests/test_generation_events.py`
- Create: `apps/api/tests/test_ws_generation_replay.py`
- Modify: `apps/web/src/lib/__tests__/agent-steps.test.ts`
- Create: `apps/web/src/lib/__tests__/generation-event-replay.test.ts`

**Interfaces:**
- Consumes: Task 1 `GenerationEvent`, Task 4 tool/activity heartbeats, existing Redis project channel, React Query caches, and authenticated project WebSocket.
- Produces: `GenerationEventEnvelope`, `GET/WS after_seq` semantics, `mergeAgentStepsBySequence`, and UI liveness driven by durable tool events.

- [ ] **Step 1: Write server replay race tests**

```python
async def test_replay_subscribe_gap_fill_delivers_every_sequence_once(ws_client, event_store) -> None:
    await event_store.append_many(run.id, seqs=range(1, 131))
    socket = await ws_client.connect(project.id, after_seq=51)
    await event_store.append(run.id, seq=131, during_subscribe=True)
    received = await socket.read_sequences_until(131)
    assert received == list(range(52, 132))


async def test_event_is_committed_before_redis_publish_failure(db_session, broken_redis) -> None:
    event = await persist_and_publish_generation_event(
        session_factory=session_factory,
        run_id=run.id,
        project_id=project.id,
        message_id=message.id,
        event_type="tool.started",
        payload={"operation_id": str(operation_id), "phase": "full_build"},
    )
    assert await replay_generation_events(db_session, run_id=run.id, after_seq=0) == [event]
```

Also test owner authorization, wrong-run/project rejection, redaction/log-size bounds, high-water capture, duplicate Redis delivery, and heartbeat sequences.

- [ ] **Step 2: Write client merge/high-water tests**

```typescript
const steps = (start: number, end: number): AgentStep[] =>
  Array.from({ length: end - start + 1 }, (_, offset) => {
    const seq = start + offset;
    return {
      eventId: `event-${seq}`,
      runId: "00000000-0000-0000-0000-000000000001",
      seq,
      step: seq,
      kind: "step",
      action: "build",
      path: "",
    };
  });

it("merges DB replay 1..130 over local 1..51 without gaps", () => {
  const merged = mergeAgentStepsBySequence(steps(1, 51), steps(1, 130));
  expect(merged.map((step) => step.seq)).toEqual(
    Array.from({ length: 130 }, (_, index) => index + 1),
  );
});

it("deduplicates a live event already included in replay", () => {
  const state = mergeAgentStepsBySequence([], steps(52, 131));
  expect(mergeAgentStepsBySequence(state, steps(131, 131))).toEqual(state);
});
```

- [ ] **Step 3: Run server/client tests and verify the current 51-vs-130 behavior fails**

Run: `cd apps/api && uv run pytest tests/test_generation_events.py tests/test_ws_generation_replay.py -q`

Run: `cd apps/web && pnpm test src/lib/__tests__/agent-steps.test.ts src/lib/__tests__/generation-event-replay.test.ts`

Expected: FAIL because WS only replays a Redis text buffer and `restorePersistedAgentSteps` returns local steps whenever any exist.

- [ ] **Step 4: Implement the high-water protocol**

Client connects with `?run_id=<uuid>&after_seq=<last-seq>`. Server:

1. authorizes the project/run;
2. captures `high_water=max(seq)`;
3. sends DB events `(after_seq, high_water]` in bounded frames;
4. connects Redis live fanout;
5. queries and sends `(high_water, current_max]` to close the race;
6. sends `generation.replay.complete {run_id, high_water}`;
7. forwards live envelopes thereafter.

```python
class GenerationEventEnvelope(TypedDict):
    event_id: str
    run_id: str
    seq: int
    type: str
    data: dict[str, object]
```

Keep `stream.sync` for legacy/non-flagged text streams. Flagged MAX uses the durable sequence for coordinator phase and tool events; `llm.chunk` may retain its message-local text sequence until a separate migration.

- [ ] **Step 5: Persist agent/coordinator events instead of rewriting JSON as authority**

`_record_agent_step` calls `append_generation_event`, commits, then fans out. It may continue updating `Message.agent_steps` as a bounded compatibility projection, but reconnect reads `generation_events`. Task 4 heartbeat events use `tool.started`, `tool.heartbeat`, and `tool.finished`; the coordinator emits `generation.phase` at every transition.

- [ ] **Step 6: Merge and render by sequence on the client**

```typescript
export type AgentStep = {
  eventId: string;
  runId: string;
  seq: number;
  step: number | null;
  kind: "step" | "escalate" | "stalled" | "retry" | "heartbeat";
  action: string;
  path: string;
  detail?: string;
  ok?: boolean;
};

export function mergeAgentStepsBySequence(current: AgentStep[] = [], incoming: AgentStep[] = []) {
  const byId = new Map([...current, ...incoming].map((step) => [step.eventId, step]));
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}
```

Store `lastGenerationSeq[runId]`; request replay after reconnect or any gap. Heartbeats reset the silence watchdog and update the last running transcript row rather than appending an unbounded row every 15 seconds.

- [ ] **Step 7: Run replay, web, lint, and type checks**

Run: `cd apps/api && uv run pytest tests/test_generation_events.py tests/test_ws_generation_replay.py tests/test_agent_progress.py -q && uv run ruff check src/omnia_api/services/generation_events.py src/omnia_api/core/redis.py src/omnia_api/routers/ws.py src/omnia_api/routers/messages.py tests/test_generation_events.py tests/test_ws_generation_replay.py`

Run: `cd apps/web && pnpm test src/lib/__tests__/agent-steps.test.ts src/lib/__tests__/generation-event-replay.test.ts && pnpm typecheck && pnpm lint`

Expected: PASS; reconnect from sequence 51 reaches DB high-water 130/131 without gaps or duplicates, and an eight-minute tool remains visibly alive through heartbeats.

- [ ] **Step 8: Keep durable replay in the Task 6 delivery change set**

Do not commit yet; run the complete cross-service verification and include the runbook in the same review gate.

#### Full verification and delivery cycle

**Files:**
- Modify: `apps/api/tests/test_max_finalization_integration.py`
- Modify: `docs/operations/project-cell-main-stack.md`
- Modify: `apps/api/.env.example`
- Modify: `apps/orchestrator/.env.example`

**Interfaces:**
- Consumes: all earlier tasks, production compose/runbook, owner-canary routing, and the authored no-model fixture.
- Produces: a deterministic acceptance report, exact rollout/rollback instructions, pushed revision, `/opt/omnia` deployment, service health, and owner-canary evidence.

- [ ] **Step 1: Extend the authored fixture to cover the production acceptance invariants**

```python
async def test_authored_max_finalization_is_single_pass_and_terminal(authored_fixture) -> None:
    report = await authored_fixture.run(api_restart_during="full_build", reconnect_after_seq=51)
    assert report.generation_status == "completed"
    assert report.provider_calls == 0
    assert report.dimension_attempts == {
        "bootstrap": 1, "fast_check": 1, "full_build": 1,
        "runtime": 1, "release": 1,
    }
    assert report.snapshot_count == report.candidate_count == report.promotion_count == 1
    assert report.docker_exec_starts["full_build"] == 1
    assert report.hibernation_attempts_during_build == 0
    assert report.ui_high_water == report.database_high_water
    assert report.active_leases == []
```

The fixture uses authored files and fake deterministic command/runtime responses. It does not call any LLM or spend a generation.

- [ ] **Step 2: Run the complete targeted suites**

Run: `cd apps/api && uv run pytest tests/test_max_finalization_integration.py tests/test_max_finalization.py tests/test_project_cell_proofs.py tests/test_project_cell_activity.py tests/test_project_cell_executor.py tests/test_project_cell_capacity.py tests/test_project_cell_capacity_integration.py tests/test_agent_native.py tests/test_messages_project_cell.py tests/test_release_proof.py tests/test_generation_runs.py tests/test_generation_events.py tests/test_ws_generation_replay.py tests/test_max_runtime_probe.py tests/test_project_cells.py tests/test_migrations_single_head.py tests/test_project_cell_migration_roundtrip.py -q`

Run: `cd apps/orchestrator && uv run pytest tests/test_project_machine_manifest.py tests/test_machine_defaults.py tests/test_machine_adapter.py tests/test_project_machine.py tests/test_docker_machine_backend.py tests/test_cell_resources.py tests/test_cell_reservations.py tests/test_docker_cell_resources.py -q`

Run: `cd apps/web && pnpm test src/lib/__tests__/agent-steps.test.ts src/lib/__tests__/generation-event-replay.test.ts && pnpm typecheck && pnpm lint && pnpm build`

Expected: PASS. Record any unrelated pre-existing baseline failure verbatim and do not relabel it as a regression or silently ignore it.

- [ ] **Step 3: Run repository-wide static and unit verification**

Run: `cd apps/api && uv run ruff check src tests migrations && uv run mypy src/omnia_api && uv run pytest -q`

Run: `cd apps/orchestrator && uv run ruff check src tests && uv run mypy src/omnia_orchestrator && uv run pytest -q`

Run: `cd apps/web && npm test && npm run typecheck && npm run lint && npm run build`

Expected: PASS, or an explicitly documented unchanged baseline failure with the focused new suites still green.

- [ ] **Step 4: Document exact flags, capacity math, diagnostics, and rollback**

Add the following rollout order to `docs/operations/project-cell-main-stack.md`:

```dotenv
USE_MAX_FINALIZATION_COORDINATOR=false
USE_PROJECT_CELL_ACTIVITY_WATCHDOG=false
USE_GENERATION_EVENT_REPLAY=false
USE_CELL_RESOURCE_PROFILE_V2=false
MAX_GENERATION_DEADLINE_SECONDS=1500
PROJECT_CELL_HEARTBEAT_SECONDS=15
PROJECT_CELL_WATCHDOG_GRACE_SECONDS=20
CELL_PROFILE_VERSION=docker-owner-cell-resources-v2
CELL_ACTIVE_MACHINE_CPU_CORES=2
CELL_ACTIVE_MACHINE_MEMORY_BYTES=2147483648
```

Document the full admitted component sum, queue behavior, proof/event/activity queries, command-journal inspection, heartbeat-gap alert, terminal watchdog diagnosis, and independent flag rollback. State explicitly that rollback never deletes records and never disables package installation or dedicated project PostgreSQL.

- [ ] **Step 5: Review the complete diff and prepare the delivery handoff**

Run: `git diff --check && git status --short && git log --oneline --decorate -10`

Expected: no whitespace errors; only intended source, tests, migration, env examples, and runbook files are present.

Prepare an exact intended-file list and a concise commit intent for `luna_delivery`. The implementation owner must not stage, commit, push, or deploy.

- [ ] **Step 6: Repeat mandatory pre-delivery freshness checks and push**

The implementation owner records `git status --short` and the exact intended-file list. `luna_delivery` verifies that every dirty path is listed, then runs `git fetch --all --prune` and `git rev-list --left-right --count HEAD...origin/main` before committing.

Expected before the delivery commit: only the reviewed intended paths are dirty and the divergence is `0 0`. Any unlisted path, behind state, or divergence stops delivery without stash/reset/rebase/overwrite.

After committing exactly the reviewed list, `luna_delivery` runs: `git push origin HEAD:main`.

Expected: `origin/main` advances to the exact local `HEAD`; fetch again and require `HEAD == origin/main`.

- [ ] **Step 7: Perform the documented production preflight and deploy the pushed revision**

Using the repository's documented read-only connection and `/opt/omnia`, first record server branch/revision/status and compare it with local `HEAD` and the remote-tracking revision. Report pre-existing server dirtiness and stop only if it overlaps an intended file or makes revision identity ambiguous. Then run only the documented production compose/deploy procedure; never deploy the development `infra/` stack.

Expected: `/opt/omnia` reaches the pushed revision, migration `0056_project_cell_finalization` is at Alembic head, and API, worker, orchestrator, web, PostgreSQL, Redis, gateway, and proxy services report healthy.

- [ ] **Step 8: Enable flags in canary order and run authored no-model acceptance**

Enable and verify one at a time: observability (already safe), duplicate-build/coordinator path, activity watchdog, resource profile v2, durable event replay, then coordinator ownership for the existing owner MAX canary. After each flag, check service health and rollback that flag alone on failure.

Run the authored deterministic fixture in the deployed API test environment, not a real generation. Expected evidence:

```text
terminal_status=completed
provider_calls=0
bootstrap_count=1
full_build_count=1
runtime_probe_count=1
snapshot_count=1
promotion_count=1
active_hibernation_kills=0
max_heartbeat_gap_seconds<=20
ui_high_water=db_high_water
active_activity_leases=0
```

- [ ] **Step 9: Record deployment and success-metric evidence**

Report the exact commit SHA, upstream push equality, server SHA, Alembic head, compose service status, API/web/preview health responses, authored fixture counts, resource cgroup values, and enabled flags. Do not claim the 15-minute production latency target from the authored fixture; collect real owner-canary median/p95, full-build counts, OOM/timeouts, heartbeat gaps, reconnect high-water, hibernation kills, false proof reuse, and completion rate before broadening rollout.
