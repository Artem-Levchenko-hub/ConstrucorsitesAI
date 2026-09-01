# Project Cell Fencing Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the existing Project Cell control foundation so every future orchestrator mutation is committed, canonically idempotent, fenced, and crash-safe before the Cell Resources subproject can use it.

**Architecture:** The API database is the durable authority for operation identity and fencing. A private lifecycle executor claims an operation and commits its new epoch before making one authenticated orchestrator call, then records the outcome in a separate transaction; an unknown outcome becomes `indeterminate` and is never replayed. No public prompt or messages route invokes this executor.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, PostgreSQL 16, Alembic, httpx, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-01-docker-project-cell-owner-canary-design.md`

## Global Constraints

- This hardening commit is delivered before `2026-09-01-project-cell-resources.md` and is its mandatory prerequisite.
- Public messages/prompt routing, model-visible tools, Docker mutation, runner, browser, draft, promotion, and owner routing remain unchanged and disabled.
- Every semantic field participates in the canonical digest: operation kind, workspace ID, generation-run ID, and the complete validated request object.
- The operation claim and fencing epoch are committed before any outbound call. A rolled-back/uncommitted claim produces zero outbound calls.
- Completion, confirmed rejection, and indeterminate outcome are each persisted in a new transaction after the outbound call.
- Timeout, connection loss, cancellation after dispatch, malformed success, and upstream 5xx are unknown outcomes and become `indeterminate`; they are never changed back to `pending` or automatically replayed.
- A new `reconcile` operation must claim a strictly higher epoch before observing an indeterminate mutation. It records observation only; it does not repeat the old side effect.
- No owner address, credential, DSN, raw environment, command stream, or upstream secret is persisted or committed.
- Migration roundtrip runs only against a newly created disposable database whose exact name is checked before drop.
- Repository delivery rules apply after independent review: verify, report, `luna_delivery` commit/push, dark deploy, health proof.

## File Map

- `apps/api/migrations/versions/0053_project_cell_operation_fencing.py` — operation fence, `indeterminate`, and reconcile/restore kinds.
- `apps/api/src/omnia_api/models/project_cell.py` — ORM fields and constraints.
- `apps/api/src/omnia_api/services/project_cells.py` — canonical envelope, committed claim primitives, terminal/indeterminate transitions.
- `apps/api/src/omnia_api/services/project_cell_lifecycle.py` — private commit-before-call executor and higher-fence reconcile path.
- `apps/api/src/omnia_api/services/orchestrator_client.py` — exact private ensure/control/resources methods.
- `apps/api/tests/test_project_cell_models.py`, `test_project_cells.py`, `test_project_cell_lifecycle.py`, `test_orchestrator_client.py`, `test_project_cell_migration_roundtrip.py`, `test_migrations_single_head.py` — persistence, ordering, crash, client, and disposable migration proof.
- `otchet/data.json` — truthful hardening evidence; resource milestone remains open.

---

### Task 1: Operation schema and canonical semantic envelope

**Files:**
- Create: `apps/api/migrations/versions/0053_project_cell_operation_fencing.py`
- Modify: `apps/api/src/omnia_api/models/project_cell.py`
- Modify: `apps/api/src/omnia_api/services/project_cells.py`
- Modify: `apps/api/tests/test_project_cell_models.py`
- Modify: `apps/api/tests/test_project_cells.py`
- Modify: `apps/api/tests/test_migrations_single_head.py`

**Interfaces:**
- Produces `ProjectCellOperation.fencing_epoch: int | None`, status `indeterminate`, kinds `restore` and `reconcile`, and `_canonical_operation_envelope(workspace_id: UUID, generation_run_id: UUID | None, kind: str, request: dict[str, object]) -> tuple[dict[str, object], str]`.
- `reserve_cell_operation(...)` stores the canonical envelope, not a request-only digest.

- [ ] **Step 1: Write RED schema/digest tests**

```python
async def test_same_key_different_kind_is_idempotency_conflict(db_session, workspace):
    await reserve_cell_operation(
        db_session, workspace_id=workspace.id, generation_run_id=None,
        kind="ensure", idempotency_key="semantic:key", request={"profile_version": "v1"},
    )
    with pytest.raises(ProjectCellIdempotencyConflict):
        await reserve_cell_operation(
            db_session, workspace_id=workspace.id, generation_run_id=None,
            kind="reconcile", idempotency_key="semantic:key", request={"profile_version": "v1"},
        )


def test_canonical_envelope_includes_every_semantic_field(workspace_id, generation_run_id):
    envelope, digest = _canonical_operation_envelope(
        workspace_id, generation_run_id, "restore", {"checkpoint_ref": "accepted-1"}
    )
    assert envelope == {
        "workspace_id": str(workspace_id),
        "generation_run_id": str(generation_run_id),
        "kind": "restore",
        "request": {"checkpoint_ref": "accepted-1"},
    }
    canonical = json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 2: Confirm RED**

```bash
cd apps/api
uv run pytest tests/test_project_cell_models.py tests/test_project_cells.py -q
```

Expected: failures because the column/status/kinds do not exist and the current digest excludes kind and identity.

- [ ] **Step 3: Implement revision and envelope**

Revision 0053 adds nullable `fencing_epoch`, changes kind constraint to `ensure,wake,pause,stop,destroy,status,restore,reconcile`, and status constraint to `pending,running,completed,failed,cancelled,indeterminate`. `_canonical_operation_envelope` validates JSON-native data, rejects unsafe keys, serializes with sorted keys and compact separators, enforces the existing 64 KiB bound on the whole envelope, and computes SHA-256 from those exact bytes.

The downgrade first aborts if restore/reconcile rows exist, converts `indeterminate` rows to `failed`, then restores the 0052 constraints and drops the column.

- [ ] **Step 4: Verify focused persistence**

```bash
uv run pytest tests/test_project_cell_models.py tests/test_project_cells.py tests/test_migrations_single_head.py -q
uv run ruff check src/omnia_api/models/project_cell.py src/omnia_api/services/project_cells.py tests/test_project_cell_models.py tests/test_project_cells.py migrations/versions/0053_project_cell_operation_fencing.py
uv run mypy src/omnia_api/models/project_cell.py src/omnia_api/services/project_cells.py
```

Expected: PASS, including same-key/different-kind, different generation run, reordered equivalent request, oversized envelope, and unsafe nested keys.

**Review gate:** Reject a request-only digest, nullable semantic omission, or downgrade that silently drops restore/reconcile meaning.

---

### Task 2: Private orchestrator client contract

**Files:**
- Modify: `apps/api/src/omnia_api/services/orchestrator_client.py`
- Modify: `apps/api/tests/test_orchestrator_client.py`

**Interfaces:**
- Produces one `ProjectCellOrchestratorClient(Protocol)` with `ensure(request: EnsureProjectCellResourcesRequest) -> ProjectCellResourceResponse`, `control(request: ControlProjectCellResourcesRequest) -> ProjectCellResourceResponse`, and `observe_resources(request: ObserveProjectCellResourcesRequest) -> ProjectCellResourceResponse`.
- Each frozen request DTO contains `operation_id: UUID`, positive `fencing_epoch: int`, and `request_digest: str` matching `^[0-9a-f]{64}$`, plus `to_wire_json() -> dict[str, object]` which flattens every field without recomputing identity. Ensure additionally contains workspace/project/owner UUID and exact profile; control additionally contains workspace UUID, kind, and optional checkpoint; observe contains workspace UUID. The response is typed and contains workspace UUID, state, observed fence, optional checkpoint, and boolean resource facts.
- Exact paths are `POST /internal/workspaces/ensure`, `POST /internal/workspaces/{workspace_id}/control`, and `POST /internal/workspaces/{workspace_id}/resources/observe`. Each body is the DTO's complete flattened JSON; no semantic field is dropped or recomputed by the client.

```python
class ProjectCellOrchestratorClient(Protocol):
    async def ensure(
        self, request: EnsureProjectCellResourcesRequest
    ) -> ProjectCellResourceResponse: ...
    async def control(
        self, request: ControlProjectCellResourcesRequest
    ) -> ProjectCellResourceResponse: ...
    async def observe_resources(
        self, request: ObserveProjectCellResourcesRequest
    ) -> ProjectCellResourceResponse: ...
```

`HttpProjectCellOrchestratorClient` is the sole production implementation; the lifecycle executor depends only on the protocol.

- [ ] **Step 1: Write RED request-shape tests**

```python
async def test_control_client_sends_only_fenced_envelope(monkeypatch, workspace_id):
    request = AsyncMock(return_value={"state": "resources_ready"})
    monkeypatch.setattr(orchestrator_client, "_request", request)
    client = HttpProjectCellOrchestratorClient()
    request_dto = ControlProjectCellResourcesRequest(
        workspace_id=workspace_id, kind="wake", checkpoint_ref=None,
        operation_id=uuid4(), fencing_epoch=4, request_digest="a" * 64,
    )
    result = await client.control(request_dto)
    assert result.state == "resources_ready"
    request.assert_awaited_once_with(
        "POST", f"/internal/workspaces/{workspace_id}/control",
        json={
            "workspace_id": str(workspace_id), "kind": "wake", "checkpoint_ref": None,
            "operation_id": str(request_dto.operation_id), "fencing_epoch": 4,
            "request_digest": "a" * 64,
        },
    )


@pytest.mark.parametrize("method", ["ensure", "observe_resources"])
async def test_every_client_body_preserves_mutation_identity(method, client_harness):
    dto = client_harness.request_for(method, operation_id=uuid4(), fence=9, digest="b" * 64)
    await getattr(client_harness.client, method)(dto)
    sent_json = client_harness.raw_request.await_args.kwargs["json"]
    assert sent_json["operation_id"] == str(dto.operation_id)
    assert sent_json["fencing_epoch"] == 9
    assert sent_json["request_digest"] == "b" * 64
    assert sent_json == dto.to_wire_json()
```

- [ ] **Step 2: Confirm RED, implement thin methods, verify**

```bash
uv run pytest tests/test_orchestrator_client.py -q
uv run ruff check src/omnia_api/services/orchestrator_client.py tests/test_orchestrator_client.py
uv run mypy src/omnia_api/services/orchestrator_client.py
```

Expected RED: imports fail. Expected GREEN: PASS for all three exact bodies and typed responses; no method accepts owner email, arbitrary environment, Docker kwargs, or public URL.

**Review gate:** Reject a public endpoint, dynamic path supplied by the caller, or client-side retry of a mutation.

---

### Task 3: Commit-before-call lifecycle executor

**Files:**
- Create: `apps/api/src/omnia_api/services/project_cell_lifecycle.py`
- Modify: `apps/api/src/omnia_api/services/project_cells.py`
- Create: `apps/api/tests/test_project_cell_lifecycle.py`
- Modify: `apps/api/tests/test_project_cells.py`

**Interfaces:**
- Produces `claim_cell_operation_committed(session_factory: async_sessionmaker[AsyncSession], operation_id: UUID) -> ClaimedCellOperation`, where the frozen result contains operation/workspace/project/owner IDs, kind, canonical request, unchanged `request_digest: str`, and committed positive fence.
- Produces `execute_cell_operation(session_factory, operation_id, client: ProjectCellOrchestratorClient) -> ProjectCellOperationOutcome` and `reconcile_indeterminate_cell_operation(session_factory, indeterminate_operation_id, reconcile_operation_id, client) -> ProjectCellOperationOutcome`.

- [ ] **Step 1: Write transaction-order RED tests**

```python
async def test_zero_outbound_call_before_claim_commit(harness):
    harness.fail_next_commit = True
    with pytest.raises(CommitFailed):
        await execute_cell_operation(harness.session_factory, harness.operation_id, harness.client)
    assert harness.client.calls == []


async def test_completion_rollback_does_not_reuse_committed_epoch(harness):
    first = await harness.claim_and_commit("ensure")
    harness.fail_completion_commit = True
    await harness.execute_expect_indeterminate(first.operation_id)
    reconcile = await harness.reserve_claim_and_commit("reconcile")
    assert reconcile.fencing_epoch > first.fencing_epoch


async def test_timeout_is_indeterminate_and_never_replayed(harness):
    harness.client.ensure.side_effect = httpx.ReadTimeout("unknown")
    outcome = await execute_cell_operation(
        harness.session_factory, harness.operation_id, harness.client
    )
    assert outcome.status == "indeterminate"
    await execute_cell_operation(harness.session_factory, harness.operation_id, harness.client)
    assert harness.client.ensure.await_count == 1


async def test_database_digest_reaches_exact_outbound_body_unchanged(harness):
    claimed = await harness.claim_and_commit("ensure")
    await execute_cell_operation(harness.session_factory, claimed.operation_id, harness.client)
    sent = harness.client.ensure.await_args.args[0]
    assert sent.operation_id == claimed.operation_id
    assert sent.fencing_epoch == claimed.fencing_epoch
    assert sent.request_digest == claimed.request_digest
    assert sent.request_digest == harness.database_operation.request_digest
```

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_project_cell_lifecycle.py tests/test_project_cells.py -q
```

Expected: collection fails because the private executor and committed claim result do not exist.

- [ ] **Step 3: Implement exact transaction sequence**

`claim_cell_operation_committed` opens its own transaction, takes the workspace advisory lock, locks operation and workspace rows, requires `pending`, increments `workspace.fencing_epoch` and `workspace.version`, copies the epoch to the operation, marks it `running`, commits, then returns the immutable claimed snapshot. The client cannot be injected into or called from this function.

`execute_cell_operation` constructs exactly one typed request from `ClaimedCellOperation`, copying operation UUID, committed fence, and stored digest without transformation, then performs exactly one `ProjectCellOrchestratorClient` call. It opens a new transaction to mark `completed` for a validated 2xx response, `failed` only for a confirmed pre-side-effect 4xx rejection, and `indeterminate` for timeout, cancellation after dispatch, transport loss, 5xx, invalid JSON, or failure to commit the terminal result. If terminal commit itself fails, a final independent transaction marks the still-running operation indeterminate.

No exception handler changes an indeterminate operation to pending. Calling the executor again returns the stored terminal/indeterminate outcome without a client call.

- [ ] **Step 4: Implement higher-fence reconcile**

`reconcile_indeterminate_cell_operation` requires the old operation to be `indeterminate` and the new operation kind to be `reconcile`. It claims/commits the new operation, proves its fence is greater, calls only `ProjectCellOrchestratorClient.observe_resources` with the new operation UUID/fence/digest, and stores observed state plus `reconciles_operation_id`; it never calls the old ensure/control mutation.

- [ ] **Step 5: Verify ordering, concurrency, and failure matrix**

```bash
uv run pytest tests/test_project_cell_lifecycle.py tests/test_project_cells.py -q
uv run ruff check src/omnia_api/services/project_cell_lifecycle.py src/omnia_api/services/project_cells.py tests/test_project_cell_lifecycle.py
uv run mypy src/omnia_api/services/project_cell_lifecycle.py src/omnia_api/services/project_cells.py
```

Expected: PASS for commit failure, completion rollback, timeout, cancellation, 5xx, malformed response, duplicate delivery, concurrent claim, same key/different kind, and higher-fence reconcile.

**Review gate:** Reject any outbound call before claim commit, mutation retry, terminal update in the claim transaction, or reconcile that repeats the indeterminate side effect.

---

### Task 4: Disposable migration roundtrip and compatibility verification

**Files:**
- Create: `apps/api/tests/test_project_cell_migration_roundtrip.py`
- Modify only tests from Tasks 1–3 when evidence requires correction.

**Interfaces:**
- Produces a verified single Alembic head at 0053 and no public lifecycle caller.

- [ ] **Step 1: Implement a Windows-safe unique disposable database fixture**

```python
@pytest_asyncio.fixture
async def disposable_migration_database(admin_database_url: str):
    name = f"omnia_cell_fence_{uuid4().hex}"
    assert re.fullmatch(r"omnia_cell_fence_[0-9a-f]{32}", name)
    admin = await asyncpg.connect(admin_database_url)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield replace_database_name(admin_database_url, name)
    finally:
        assert re.fullmatch(r"omnia_cell_fence_[0-9a-f]{32}", name)
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=$1 AND pid <> pg_backend_pid()", name,
        )
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


def test_0053_roundtrip(disposable_migration_database: str):
    env = {**os.environ, "DATABASE_URL": disposable_migration_database}
    for args in (
        ["uv", "run", "alembic", "upgrade", "head"],
        ["uv", "run", "alembic", "downgrade", "0052_project_cell_control_foundation"],
        ["uv", "run", "alembic", "upgrade", "head"],
    ):
        subprocess.run(args, cwd=API_ROOT, env=env, check=True, shell=False)
```

The fixture reuses the existing test admin database URL configuration, creates one UUID-named database, and owns cleanup in `try/finally`. No shell `createdb/dropdb`, POSIX variable, or application database URL is used.

- [ ] **Step 2: Run the roundtrip**

```powershell
uv run pytest tests/test_project_cell_migration_roundtrip.py -q
```

Expected: 0052→0053→0052→0053 succeeds; exact-name guarded cleanup runs on pass/failure/cancellation and no application database is mutated.

- [ ] **Step 3: Run complete focused verification and caller scan**

```bash
uv run pytest tests/test_project_cell_models.py tests/test_project_cells.py tests/test_project_cell_lifecycle.py tests/test_project_cell_control.py tests/test_orchestrator_client.py tests/test_project_cell_migration_roundtrip.py tests/test_migrations_single_head.py -q
uv run ruff check src/omnia_api/models/project_cell.py src/omnia_api/services/project_cells.py src/omnia_api/services/project_cell_lifecycle.py src/omnia_api/services/orchestrator_client.py tests/test_project_cell_models.py tests/test_project_cells.py tests/test_project_cell_lifecycle.py tests/test_orchestrator_client.py migrations/versions/0053_project_cell_operation_fencing.py
uv run mypy src/omnia_api/models/project_cell.py src/omnia_api/services/project_cells.py src/omnia_api/services/project_cell_lifecycle.py src/omnia_api/services/orchestrator_client.py
rg -n "execute_cell_operation|reconcile_indeterminate_cell_operation" src/omnia_api/routers/messages.py src/omnia_api/services/agent_native.py src/omnia_api/services/agent_builder.py
```

Expected: tests/static checks pass; caller scan has no matches, proving no public prompt route is enabled.

**Review gate:** Independently review schema, transaction boundaries, outbound call count, and all unknown-outcome paths.

---

### Task 5: Report and prerequisite delivery

**Files:**
- Modify: `otchet/data.json`
- Include only the intended Task 1–5 files.

- [ ] **Step 1: Record truthful hardening evidence**

Keep H128 `testing`, score `null`, and V4 incomplete. State that durable fencing is safe for the next subproject but no Docker bundle or model-visible execution exists.

- [ ] **Step 2: Final sanity and secret scan**

```bash
python -m json.tool otchet/data.json > /dev/null
git diff --check
git status --short
rg -n "@gmail\.com|PROJECT_CELL_CANARY_EMAILS=.*@|PGPASSWORD=|postgres_password" apps/api otchet/data.json
```

Expected: only intended files; no personal address or secret value.

- [ ] **Step 3: Deliver before Cell Resources**

Hand the exact reviewed files to `luna_delivery` with commit message `fix(project-cell): harden fenced lifecycle dispatch` and required repository co-author trailer. Push `origin/main`, deploy API/worker, confirm migration head 0053 and exact release health, and verify Project Cell capability remains disabled/unsupported.

**Review gate:** The Cell Resources implementation must not start until this exact commit is pushed, deployed, and healthy.

---

## Self-Review Record

- **Coverage:** canonical idempotency, committed fence, private client methods, separate terminal transactions, indeterminate recovery, and higher-fence reconcile are each owned by a task and tested.
- **Transaction boundary:** no outbound client object is available inside claim; terminal persistence cannot roll back the already committed fence.
- **Typed boundary:** one `ProjectCellOrchestratorClient` protocol and three frozen DTOs carry the database operation UUID, committed positive fence, and stored 64-hex digest unchanged in exact outbound JSON.
- **Compatibility:** there is no router/public caller change; this plan only creates a private executor consumed later.
- **Migration safety:** roundtrip targets only a guarded unique disposable database.
- **Rollback:** disabling remains unchanged; downgrade refuses semantically lossy rows; unknown mutations remain observable instead of replayed.
