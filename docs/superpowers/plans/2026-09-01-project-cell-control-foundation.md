# Project Cell Control Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the disabled-by-default control foundation for an authenticated owner-only Project Cell: access policy, durable workspace and operation state, idempotent reservation, a replaceable orchestrator provider contract, and a read-only readiness seam, without routing any real prompt or starting Docker resources.

**Architecture:** The API owns authenticated routing policy and durable PostgreSQL records. The orchestrator owns a provider interface and exposes only a read-only capability response in this subproject; its Docker provider remains unavailable and imports no Docker lifecycle code. Existing prompt generation, agent tools, preview containers, and public API contracts remain byte-compatible until later subprojects activate the provider.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2 async, PostgreSQL 16, Alembic, httpx, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-01-docker-project-cell-owner-canary-design.md`

## Global Constraints

- All new behavior is disabled by default and must fail closed.
- No change in this plan may create a Docker container, volume, network, database sidecar, shell, browser, or model-visible executor.
- Do not modify `messages.py`, model-visible tool schemas, public prompt schemas, or existing provision/wake/stop/deploy behavior in this subproject.
- The owner allowlist is server configuration only; no personal address is committed to Git.
- Access decisions use the authenticated `User` row, require an active non-anonymous account and `email_verified_at`, and never trust prompt/request fields.
- `metadata` and operation payloads contain no credentials, DSNs, cookies, raw environment, or unbounded command output.
- Only one active cell operation may exist per workspace; duplicate idempotency keys replay only when the canonical request digest matches.
- The existing `GenerationRun` reservation remains the prompt single-flight source of truth.
- The Docker provider is not a fallback from a disabled or unsupported provider.
- Existing production behavior must remain unchanged with default configuration.
- Repository `AGENTS.md` delivery rules override per-task commit examples: one implementation owner completes and verifies the cohesive foundation, then `luna_delivery` creates the atomic commit, pushes `origin/main`, deploys the affected runtime services, and performs health checks.

---

### Task 1: Authenticated owner-canary access policy

**Files:**
- Modify: `apps/api/src/omnia_api/core/config.py:1022-1043`
- Create: `apps/api/src/omnia_api/services/project_cell_access.py`
- Create: `apps/api/tests/test_project_cell_access.py`

**Interfaces:**
- Consumes: `omnia_api.models.user.User`, `omnia_api.core.config.Settings`
- Produces: `ProjectCellAccessDecision(enabled: bool, provider: Literal["legacy", "docker_owner_canary"], reason: str)` and `decide_project_cell_access(user: User, settings: Settings | None = None) -> ProjectCellAccessDecision`

- [ ] **Step 1: Write the failing access-policy tests**

```python
from datetime import UTC, datetime
from uuid import uuid4

from omnia_api.core.config import Settings
from omnia_api.models.user import User
from omnia_api.services.project_cell_access import decide_project_cell_access


def _user(*, email: str | None, verified: bool, anon: bool = False) -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash="unused",
        is_anon=anon,
        status="active",
        email_verified_at=datetime.now(UTC) if verified else None,
    )


def test_project_cell_access_is_disabled_by_default(settings_factory):
    settings = settings_factory(project_cell_docker_canary_enabled=False)
    decision = decide_project_cell_access(
        _user(email="owner@example.com", verified=True), settings
    )
    assert decision.provider == "legacy"
    assert decision.reason == "feature_disabled"


def test_project_cell_access_requires_verified_allowlisted_account(settings_factory):
    settings = settings_factory(
        project_cell_docker_canary_enabled=True,
        project_cell_canary_emails=" Owner@Example.com ",
    )
    assert decide_project_cell_access(
        _user(email="owner@example.com", verified=True), settings
    ).provider == "docker_owner_canary"
    assert decide_project_cell_access(
        _user(email="owner@example.com", verified=False), settings
    ).provider == "legacy"
    assert decide_project_cell_access(
        _user(email="stranger@example.com", verified=True), settings
    ).provider == "legacy"
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run from `apps/api`:

```bash
uv run pytest tests/test_project_cell_access.py -q
```

Expected: collection fails because `project_cell_access` and the new settings do not exist.

- [ ] **Step 3: Add fail-closed configuration and the pure decision helper**

Add to `Settings` near the existing agent canary settings:

```python
project_cell_docker_canary_enabled: bool = Field(default=False)
project_cell_canary_emails: str = Field(default="")
project_cell_operation_timeout_seconds: int = Field(default=180, ge=1, le=3600)

@property
def project_cell_canary_email_set(self) -> frozenset[str]:
    return frozenset(
        item.strip().casefold()
        for item in self.project_cell_canary_emails.split(",")
        if item.strip()
    )
```

Create the service with an immutable return type:

```python
from dataclasses import dataclass
from typing import Literal

from omnia_api.core.config import Settings, get_settings
from omnia_api.models.user import User


@dataclass(frozen=True, slots=True)
class ProjectCellAccessDecision:
    enabled: bool
    provider: Literal["legacy", "docker_owner_canary"]
    reason: str


def decide_project_cell_access(
    user: User,
    settings: Settings | None = None,
) -> ProjectCellAccessDecision:
    config = settings or get_settings()
    if not config.project_cell_docker_canary_enabled:
        return ProjectCellAccessDecision(False, "legacy", "feature_disabled")
    if user.status != "active" or user.is_anon:
        return ProjectCellAccessDecision(False, "legacy", "account_ineligible")
    if user.email is None or user.email_verified_at is None:
        return ProjectCellAccessDecision(False, "legacy", "email_unverified")
    if user.email.strip().casefold() not in config.project_cell_canary_email_set:
        return ProjectCellAccessDecision(False, "legacy", "account_not_allowlisted")
    return ProjectCellAccessDecision(True, "docker_owner_canary", "owner_canary")
```

- [ ] **Step 4: Run the access-policy tests and static checks**

```bash
uv run pytest tests/test_project_cell_access.py -q
uv run ruff check src/omnia_api/core/config.py src/omnia_api/services/project_cell_access.py tests/test_project_cell_access.py
uv run mypy src/omnia_api/services/project_cell_access.py
```

Expected: all commands pass; no runtime code imports this helper yet.

---

### Task 2: Durable workspace and operation records

**Files:**
- Create: `apps/api/src/omnia_api/models/project_cell.py`
- Modify: `apps/api/src/omnia_api/models/__init__.py`
- Create: `apps/api/migrations/versions/0052_project_cell_control_foundation.py`
- Create: `apps/api/tests/test_project_cell_models.py`
- Modify: `apps/api/tests/test_migrations_single_head.py`

**Interfaces:**
- Consumes: existing `Base`, `Project`, `User`, and `GenerationRun` tables
- Produces: `ProjectCellWorkspace` and `ProjectCellOperation` SQLAlchemy models; Alembic revision `0052_project_cell_control_foundation`

- [ ] **Step 1: Write failing model and migration tests**

```python
async def test_workspace_is_unique_per_project(db_session, project, user):
    first = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state="provisioning",
    )
    db_session.add(first)
    await db_session.flush()
    db_session.add(
        ProjectCellWorkspace(
            project_id=project.id,
            owner_id=user.id,
            provider="docker_owner_canary",
            state="provisioning",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_operation_payload_defaults_are_not_shared(
    db_session, project_cell_workspace
):
    one = ProjectCellOperation(
        workspace_id=project_cell_workspace.id,
        kind="ensure",
        idempotency_key="ensure:run-one",
        request_digest="0" * 64,
    )
    two = ProjectCellOperation(
        workspace_id=project_cell_workspace.id,
        kind="status",
        idempotency_key="status:run-two",
        request_digest="1" * 64,
    )
    assert one.request_payload == {}
    assert two.request_payload == {}
    assert one.request_payload is not two.request_payload
```

Add a migration assertion that `0052_project_cell_control_foundation` is the only Alembic head.

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_project_cell_models.py tests/test_migrations_single_head.py -q
```

Expected: import/revision failures because the models and migration are absent.

- [ ] **Step 3: Add the two focused models**

Implement these exact public attributes:

```python
class ProjectCellWorkspace(Base):
    __tablename__ = "project_cell_workspaces"

    id: Mapped[UUID]
    project_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    provider: Mapped[str]
    provider_ref: Mapped[str | None]
    state: Mapped[str]
    generation_run_id: Mapped[UUID | None]
    provider_metadata: Mapped[dict[str, object]]
    fencing_epoch: Mapped[int]
    version: Mapped[int]
    last_error: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    ready_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class ProjectCellOperation(Base):
    __tablename__ = "project_cell_operations"

    id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    generation_run_id: Mapped[UUID | None]
    idempotency_key: Mapped[str]
    request_digest: Mapped[str]
    kind: Mapped[str]
    status: Mapped[str]
    request_payload: Mapped[dict[str, object]]
    result_payload: Mapped[dict[str, object] | None]
    error: Mapped[str | None]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
```

Use named checks for workspace states
`provisioning|ready|stopped|failed|deleting|deleted`, operation kinds
`ensure|wake|pause|stop|destroy|status`, and operation states
`pending|running|completed|failed|cancelled`. Add:

```python
UniqueConstraint("project_id", name="uq_project_cell_workspaces_project_id")
UniqueConstraint(
    "workspace_id",
    "idempotency_key",
    name="uq_project_cell_operations_workspace_id_idempotency_key",
)
Index(
    "uq_project_cell_operations_one_active_per_workspace",
    "workspace_id",
    unique=True,
    postgresql_where=text("status IN ('pending', 'running')"),
)
```

Foreign-key behavior is exact: project/owner/workspace cascade on delete;
generation run references set null. JSONB fields use `default=dict` and
`server_default="{}"` with no credentials.

- [ ] **Step 4: Add reversible migration 0052**

Create both tables, named checks, unique constraints, and indexes in dependency
order. `downgrade()` drops operations before workspaces. Import both models from
`models/__init__.py` and include them in `__all__`.

- [ ] **Step 5: Run model, migration, and schema checks**

```bash
uv run pytest tests/test_project_cell_models.py tests/test_migrations_single_head.py -q
uv run alembic upgrade head
uv run alembic downgrade 0051_task_board_cleanup_retry
uv run alembic upgrade head
```

Expected: tests pass and the upgrade/downgrade/upgrade cycle succeeds against the configured test database.

---

### Task 3: Idempotent workspace and operation reservation service

**Files:**
- Create: `apps/api/src/omnia_api/services/project_cells.py`
- Create: `apps/api/tests/test_project_cells.py`

**Interfaces:**
- Consumes: `Project`, `User`, `GenerationRun`, `ProjectCellWorkspace`, `ProjectCellOperation`, `AsyncSession`
- Produces:
  - `get_or_create_workspace(session, *, project, user, run) -> tuple[ProjectCellWorkspace, bool]`
  - `reserve_cell_operation(session, *, workspace_id, generation_run_id, kind, idempotency_key, request) -> tuple[ProjectCellOperation, bool]`
  - `claim_cell_operation(session, operation_id) -> ProjectCellOperation`
  - `complete_cell_operation(session, operation_id, result) -> None`
  - `fail_cell_operation(session, operation_id, error) -> None`
  - `recover_interrupted_cell_operations(session) -> int`

- [ ] **Step 1: Write RED tests for replay and single-flight**

```python
async def test_same_operation_key_replays_same_request(
    db_session, project_cell_workspace
):
    first, replayed = await reserve_cell_operation(
        db_session,
        workspace_id=project_cell_workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:one",
        request={"profile_version": "v1"},
    )
    second, replayed_again = await reserve_cell_operation(
        db_session,
        workspace_id=project_cell_workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:one",
        request={"profile_version": "v1"},
    )
    assert replayed is False
    assert replayed_again is True
    assert second.id == first.id


async def test_same_key_with_different_request_is_rejected(...):
    with pytest.raises(ProjectCellIdempotencyConflict):
        await reserve_cell_operation(..., request={"profile_version": "v2"})


async def test_different_key_is_busy_while_operation_active(...):
    with pytest.raises(ProjectCellBusy):
        await reserve_cell_operation(..., idempotency_key="ensure:two")
```

Also cover completed replay, failed operation with a new key, invalid kind,
bounded redacted result, state transitions, and restart recovery of `running`
to `pending` without changing its idempotency key.

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_project_cells.py -q
```

Expected: import failures for the new service and exceptions.

- [ ] **Step 3: Implement canonical requests and transactional locks**

```python
def _canonical_payload(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_digest(value: dict[str, object]) -> str:
    return sha256(_canonical_payload(value).encode("utf-8")).hexdigest()
```

Before each workspace create or operation reservation, execute:

```python
await session.execute(
    text("SELECT pg_advisory_xact_lock(hashtext(:workspace_key))"),
    {"workspace_key": str(project.id or workspace_id)},
)
```

Then apply this order: exact-key lookup and digest comparison; active-operation
lookup; insert. Do not catch `IntegrityError` as normal flow while the advisory
lock is held. Keep result payload at or below a serialized 64 KiB and reject
keys shorter than 8 or longer than 128 characters.

- [ ] **Step 4: Implement explicit state transitions**

`claim` accepts only `pending`; `complete`/`fail` accept only `running`;
terminal operations replay but cannot be claimed again. Recovery changes only
`running` to `pending` and clears `started_at`; it never invents provider
success. Use named domain exceptions rather than `HTTPException` in the service.

- [ ] **Step 5: Run service and regression tests**

```bash
uv run pytest tests/test_project_cells.py tests/test_generation_runs.py -q
uv run ruff check src/omnia_api/services/project_cells.py tests/test_project_cells.py
uv run mypy src/omnia_api/services/project_cells.py
```

Expected: all tests pass and existing generation single-flight behavior is unchanged.

---

### Task 4: Replaceable orchestrator provider with a dark capability endpoint

**Files:**
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/config.py:16-216`
- Create: `apps/orchestrator/src/omnia_orchestrator/core/workspace_provider.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/disabled_workspace_provider.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/docker_owner_canary_provider.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/workspace_provider_factory.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/schemas/workspace.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/routers/workspace.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/main.py:65-69`
- Modify: `apps/orchestrator/.env.example`
- Create: `apps/orchestrator/tests/test_workspace_provider.py`
- Create: `apps/orchestrator/tests/test_workspace_router.py`

**Interfaces:**
- Consumes: orchestrator `Settings`, internal header authentication
- Produces: `WorkspaceProvider` protocol and `GET /internal/projects/{project_id}/workspace/capabilities`

- [ ] **Step 1: Write provider and route tests first**

```python
async def test_default_provider_is_disabled(settings_factory):
    provider = build_workspace_provider(
        settings_factory(
            workspace_provider="disabled",
            docker_owner_canary_enabled=False,
        )
    )
    status = await provider.status(uuid4())
    assert status.provider == "disabled"
    assert status.ready is False
    assert status.state == "disabled"


async def test_docker_owner_provider_is_still_unsupported_in_foundation(
    settings_factory,
):
    provider = build_workspace_provider(
        settings_factory(
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
        )
    )
    status = await provider.status(uuid4())
    assert status.provider == "docker_owner_canary"
    assert status.ready is False
    assert status.state == "unsupported"


async def test_workspace_capabilities_requires_internal_header(client):
    response = await client.get(f"/internal/projects/{uuid4()}/workspace/capabilities")
    assert response.status_code == 401
```

The authenticated route test asserts a stable object and monkeypatches the
factory; it also asserts no Docker client, provisioner, subprocess, or shell
function was called.

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_workspace_provider.py tests/test_workspace_router.py -q
```

Expected: modules and route are absent.

- [ ] **Step 3: Define immutable provider DTOs and protocol**

```python
@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    profile_version: str


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    workspace_id: UUID
    provider: str
    provider_ref: str


@dataclass(frozen=True, slots=True)
class WorkspaceStatus:
    project_id: UUID
    provider: Literal["disabled", "docker_owner_canary"]
    enabled: bool
    ready: bool
    state: Literal["disabled", "unsupported"]
    detail: str


class WorkspaceProvider(Protocol):
    async def ensure(self, spec: WorkspaceSpec) -> WorkspaceHandle: ...
    async def wake(self, workspace_id: UUID) -> WorkspaceHandle: ...
    async def pause(self, workspace_id: UUID, checkpoint_ref: str) -> None: ...
    async def destroy(self, workspace_id: UUID) -> None: ...
    async def status(self, project_id: UUID) -> WorkspaceStatus: ...
    async def execute_control(
        self, workspace_id: UUID, action: ControlAction
    ) -> ControlResult: ...
```

All mutating methods in both foundation providers raise
`WorkspaceProviderUnavailable`. Neither provider imports `docker_client`,
`provisioner`, `subprocess`, or shell helpers.

- [ ] **Step 4: Add fail-closed settings, factory, schema, and route**

```python
workspace_provider: Literal["disabled", "docker_owner_canary"] = Field(
    default="disabled"
)
docker_owner_canary_enabled: bool = Field(default=False)
```

The factory returns `DisabledWorkspaceProvider` unless both values explicitly
select and enable the Docker owner canary. Even then the foundation provider
returns `ready=False`, `state="unsupported"`. The new router verifies
`X-Internal-Token` before calling `status()` and has no mutation endpoint.

- [ ] **Step 5: Verify orchestrator provider and compatibility**

```bash
uv run pytest tests/test_workspace_provider.py tests/test_workspace_router.py -q
uv run pytest tests/test_health.py tests/test_provisioner.py tests/test_agent_exec_security.py -q
uv run ruff check src tests/test_workspace_provider.py tests/test_workspace_router.py
uv run mypy src
```

Expected: all tests pass; existing runtime routes remain unchanged.

---

### Task 5: API readiness client and control coordinator

**Files:**
- Modify: `apps/api/src/omnia_api/services/orchestrator_client.py`
- Create: `apps/api/src/omnia_api/services/project_cell_control.py`
- Modify: `apps/api/tests/test_orchestrator_client.py`
- Create: `apps/api/tests/test_project_cell_control.py`

**Interfaces:**
- Consumes: Task 1 access decision, Task 3 reservation service, orchestrator capability route
- Produces:
  - `get_project_cell_capabilities(project_id: UUID) -> dict[str, Any]`
  - `ProjectCellControlReadiness(selected: bool, ready: bool, provider: str, reason: str)`
  - `inspect_project_cell_control(user: User, project_id: UUID) -> ProjectCellControlReadiness`

- [ ] **Step 1: Write failing client and coordinator tests**

```python
async def test_capability_client_calls_exact_internal_path(httpx_mock):
    project_id = uuid4()
    httpx_mock.add_response(
        url=f"http://orchestrator:8003/internal/projects/{project_id}/workspace/capabilities",
        json={
            "project_id": str(project_id),
            "provider": "disabled",
            "enabled": False,
            "ready": False,
            "state": "disabled",
            "detail": "workspace provider is disabled",
        },
    )
    result = await get_project_cell_capabilities(project_id)
    assert result["ready"] is False


async def test_control_inspection_skips_orchestrator_for_legacy_user(...):
    readiness = await inspect_project_cell_control(user, project_id)
    assert readiness.selected is False
    assert readiness.provider == "legacy"
    orchestrator_call.assert_not_awaited()


async def test_selected_owner_fails_closed_while_provider_is_not_ready(...):
    readiness = await inspect_project_cell_control(owner, project_id)
    assert readiness.selected is True
    assert readiness.ready is False
    assert readiness.reason == "provider_unsupported"
```

- [ ] **Step 2: Confirm RED**

```bash
uv run pytest tests/test_orchestrator_client.py tests/test_project_cell_control.py -q
```

Expected: missing client/coordinator symbols.

- [ ] **Step 3: Add the thin client and pure readiness coordinator**

```python
async def get_project_cell_capabilities(project_id: UUID) -> dict[str, Any]:
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/workspace/capabilities",
    )
```

The coordinator first calls `decide_project_cell_access`. Legacy decisions
return without network I/O. Selected owner decisions call the capability route,
validate exact `project_id`, provider, enabled, ready, and state fields, and
return fail-closed readiness. It does not create a workspace, reserve an
operation, or modify a generation run in this subproject.

- [ ] **Step 4: Verify coordinator behavior and current client regressions**

```bash
uv run pytest tests/test_orchestrator_client.py tests/test_project_cell_control.py -q
uv run ruff check src/omnia_api/services/orchestrator_client.py src/omnia_api/services/project_cell_control.py tests/test_project_cell_control.py
uv run mypy src/omnia_api/services/project_cell_control.py
```

Expected: all tests pass; no public route imports the coordinator.

---

### Task 6: Foundation integration verification and live report

**Files:**
- Modify: `otchet/data.json`
- Verify only: `apps/api/src/omnia_api/routers/messages.py`
- Verify only: `apps/orchestrator/src/omnia_orchestrator/routers/runtime.py`
- Verify only: `apps/llm-gateway/deploy/full/docker-compose.yml`

**Interfaces:**
- Consumes: Tasks 1–5
- Produces: verified dark foundation and an updated H128/V4 report entry

- [ ] **Step 1: Add a static dark-launch regression**

Add to `test_project_cell_control.py`:

```python
def test_control_foundation_is_not_imported_by_public_prompt_router():
    source = Path("src/omnia_api/routers/messages.py").read_text(encoding="utf-8")
    assert "project_cell_control" not in source
    assert "get_project_cell_capabilities" not in source
```

Add an orchestrator test asserting the foundation provider files contain no
`docker_client`, `provisioner`, `subprocess`, `exec_cmd`, or `run_sandbox_command`
imports.

- [ ] **Step 2: Run focused suites**

From `apps/api`:

```bash
uv run pytest \
  tests/test_project_cell_access.py \
  tests/test_project_cell_models.py \
  tests/test_project_cells.py \
  tests/test_project_cell_control.py \
  tests/test_orchestrator_client.py \
  tests/test_generation_runs.py \
  tests/test_migrations_single_head.py -q
uv run ruff check src tests
uv run mypy src
```

From `apps/orchestrator`:

```bash
uv run pytest \
  tests/test_workspace_provider.py \
  tests/test_workspace_router.py \
  tests/test_health.py \
  tests/test_provisioner.py \
  tests/test_agent_exec_security.py -q
uv run ruff check src tests
uv run mypy src
```

- [ ] **Step 3: Run complete affected-service suites**

```bash
cd apps/api && uv run pytest -q
cd apps/orchestrator && uv run pytest -q
```

Expected: all existing and new tests pass with default flags.

- [ ] **Step 4: Update the live report**

In `otchet/data.json`, increment `meta.version`, update H128 evidence and impact,
keep `status="testing"` and `score=null`, and keep the V4 Docker owner-canary
step false because no model-visible cell has run. Record exact test results and
the fact that production routing remains disabled.

- [ ] **Step 5: Verify report and diff integrity**

```bash
node -e "JSON.parse(require('fs').readFileSync('otchet/data.json','utf8'))"
git diff --check
git status --short
```

Expected: valid JSON, clean diff check, and only intended foundation files.

---

### Task 7: Atomic delivery and production dark-deploy proof

**Files:**
- Commit: all intended files from Tasks 1–6 only
- Deploy: `api`, `worker`, and the host orchestrator service or documented orchestrator release path

**Interfaces:**
- Consumes: complete verified foundation
- Produces: exact revision on `origin/main` and production with disabled settings

- [ ] **Step 1: Hand verified files to `luna_delivery`**

Give Luna the exact file list, commit intent
`feat(project-cell): add dark control foundation`, current branch/upstream,
verification evidence, and required co-author trailer. Luna must stop on a
non-fast-forward remote or unexpected file.

- [ ] **Step 2: Push exact commit to `origin/main`**

Expected: remote `main` resolves to the new commit with no force push.

- [ ] **Step 3: Deploy only documented production services**

Fast-forward `/opt/omnia` with the documented `git fetch && git merge --ff-only
origin/main`. Apply migration 0052 through the normal API deployment, rebuild
`api worker`, and restart/release the host orchestrator using its documented
path. Do not deploy the development `infra/` compose stack.

- [ ] **Step 4: Verify dark settings and health**

Confirm on production:

```text
PROJECT_CELL_DOCKER_CANARY_ENABLED is absent or false
WORKSPACE_PROVIDER is absent or disabled
DOCKER_OWNER_CANARY_ENABLED is absent or false
```

Then require HTTP 200 from API health, gateway health, web root, and
orchestrator health; require migration head 0052; require the internal workspace
capability endpoint to return `ready=false` without creating Docker resources.

- [ ] **Step 5: Prove no runtime side effect**

Compare production Docker container, network, and volume inventories before and
after the read-only capability call. No Project Cell resource may appear. Run
one legacy generation smoke only if the existing production canary fixture is
available without creating user-visible test data; otherwise preserve the
existing focused route regression as the compatibility evidence and report that
the live generation proof remains for the later real owner canary.

## Plan Self-Review

- Spec coverage for Subproject 1: owner policy, durable records, provider
  abstraction, idempotency, fail-closed readiness, dark rollout, verification,
  delivery, and rollback are each mapped to a task.
- Explicitly out of scope here: Docker resources, PostgreSQL/Redis sidecars,
  resident runner, model-visible tools, draft preview, promotion, and owner
  enablement. These are Subprojects 2–4 in the approved design.
- No placeholder steps remain; every new function and route has an exact name,
  input, output, test, and verification command.
- Type names are consistent across tasks: `ProjectCellWorkspace`,
  `ProjectCellOperation`, `WorkspaceProvider`, `WorkspaceStatus`,
  `ProjectCellAccessDecision`, and `ProjectCellControlReadiness`.
- Execution method is already selected by the owner: subagent-driven execution
  proceeds immediately after this plan is delivered, without another approval.
