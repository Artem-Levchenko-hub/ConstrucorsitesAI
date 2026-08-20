# Production E2E Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make builder revision `a7c4fc227855bb5e4cb044194b5f31e39ab83344` reproducible, observable by exact release identity, dark-canaryable for project memory, and provable through a disposable production build/edit golden path.

**Architecture:** A normalized `OMNIA_RELEASE_SHA` flows through web, API, worker heartbeat, and orchestrator health. A single public-API canary client performs a paid, serialized MAX build/edit run with strict cleanup and release-drift assertions. Project memory gets an authenticated-user allowlist in front of both reads and writes, while repository-local release scripts and GitHub workflows make the proof repeatable.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, Redis, httpx, pytest, Next.js 15, TypeScript, Vitest, pnpm 9.15.0, Docker Compose, GitHub Actions, Bash.

**Spec:** `docs/superpowers/specs/2026-08-20-production-e2e-hardening-design.md`

## Global Constraints

- The shared deployment variable is exactly `OMNIA_RELEASE_SHA`.
- A valid release is 7-40 lowercase hexadecimal characters; every other value becomes `unknown`.
- Production requires web, API, worker, and orchestrator revisions to be equal, non-`unknown`, and equal to `EXPECTED_RELEASE_SHA`.
- Keep pnpm exactly `9.15.0`; retain `apps/web/package.json`'s existing `pnpm.overrides` block.
- Keep the code defaults for the two reference-corpus gates on, while production Compose defaults remain explicitly off.
- Project memory is enabled when `USE_PROJECT_MEMORY=true` or the authenticated user UUID is listed in `PROJECT_MEMORY_CANARY_USERS`.
- Production rollout starts with `USE_PROJECT_MEMORY=false` and only the dedicated canary UUID allowed.
- Never print passwords, cookies, signed preview URLs, full preview bodies, or secret-bearing environment files.
- Never run Alembic downgrade or `docker compose down -v` in production.
- Do not mutate production until the owner explicitly confirms the exact release and rollback revision.

---

### Task 1: Restore a Reproducible Green Baseline

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/api/tests/test_acceptance.py`

**Interfaces:**
- Consumes: current lockfile produced by pnpm `9.15.0`; production Compose policy strings.
- Produces: `packageManager: "pnpm@9.15.0"`; base acceptance tests that explicitly disable optional reference gates; a production-policy regression test.

- [ ] **Step 1: Isolate optional reference policy in the failing acceptance tests**

Change `test_evaluate_reference_dials_default_off` so its arrange phase explicitly calls:

```python
_settings_with(
    monkeypatch,
    acceptance_gauntlet_reference_gate=False,
    reference_ceiling_enforced=False,
)
```

Add a helper at the start of each base acceptance test that currently expects `_GOOD` to pass:

```python
def _disable_optional_reference_gates(monkeypatch):
    _settings_with(
        monkeypatch,
        acceptance_gauntlet_reference_gate=False,
        reference_ceiling_enforced=False,
    )
```

Use it in `test_evaluate_passes_clean`, `test_evaluate_render_failure_is_soft`, and `test_evaluate_gauntlet_clean_does_not_block`.

- [ ] **Step 2: Verify the previously red API tests turn green for the intended reason**

Run:

```bash
cd apps/api
DATABASE_URL="$DATABASE_URL" DATABASE_TEST_URL="$DATABASE_TEST_URL" JWT_SECRET=baseline-test-secret-32-bytes-minimum uv run pytest tests/test_acceptance.py -q
```

Expected: all tests pass; the code defaults remain on and production-policy assertions remain off.

- [ ] **Step 3: Pin the package manager without changing dependency resolution**

Add this top-level field after `private` in `apps/web/package.json`:

```json
"packageManager": "pnpm@9.15.0"
```

- [ ] **Step 4: Verify frozen install reproducibility**

Run:

```bash
cd apps/web
corepack pnpm --version
corepack pnpm install --frozen-lockfile
git diff --exit-code -- package.json pnpm-lock.yaml pnpm-workspace.yaml
corepack pnpm typecheck
corepack pnpm test
```

Expected: version `9.15.0`, no manifest/lockfile diff after install, typecheck and tests pass.

- [ ] **Step 5: Commit the baseline fix**

```bash
git add apps/web/package.json apps/api/tests/test_acceptance.py
git commit -m "test: make release baseline reproducible"
```

### Task 2: Add API and Worker Release Identity

**Files:**
- Create: `apps/api/src/omnia_api/core/release.py`
- Create: `apps/api/tests/test_release_identity.py`
- Modify: `apps/api/src/omnia_api/core/config.py`
- Modify: `apps/api/src/omnia_api/services/readiness.py`
- Modify: `apps/api/src/omnia_api/main.py`
- Modify: `apps/api/tests/test_readiness.py`
- Modify: `apps/api/tests/test_auth.py`
- Modify: `apps/llm-gateway/deploy/full/docker-compose.yml`

**Interfaces:**
- Produces: `normalize_release_sha(value: str | None) -> str`; `ReadinessReport(checks, dependencies)`; API JSON fields `release_sha`, `dependencies.worker_release_sha`, and `dependencies.orchestrator_release_sha`.
- Consumes: `Settings.omnia_release_sha`; Redis worker heartbeat key `omnia:health:worker`; orchestrator `GET /health` JSON.

- [ ] **Step 1: Write failing normalization and health tests**

Create `test_release_identity.py`:

```python
from omnia_api.core.release import normalize_release_sha


def test_normalize_release_sha_accepts_lower_hex() -> None:
    assert normalize_release_sha("a7c4fc22") == "a7c4fc22"


def test_normalize_release_sha_rejects_unsafe_or_unknown_values() -> None:
    assert normalize_release_sha(None) == "unknown"
    assert normalize_release_sha("A7C4FC22") == "unknown"
    assert normalize_release_sha("a7c4fc22\nSECRET=x") == "unknown"
    assert normalize_release_sha("abc123") == "unknown"
```

Update API endpoint tests to monkeypatch `get_settings().omnia_release_sha` to `a7c4fc22` and expect:

```python
{"status": "ok", "release_sha": "a7c4fc22"}
```

for `/health`, and:

```python
{
    "status": "ok",
    "service": "api",
    "release_sha": "a7c4fc22",
    "checks": {"database": "ok", "redis": "ok", "worker": "ok"},
    "dependencies": {
        "worker_release_sha": "a7c4fc22",
        "orchestrator_release_sha": "a7c4fc22",
    },
}
```

for `/api/health`.

- [ ] **Step 2: Run tests to verify the contract is absent**

Run:

```bash
cd apps/api
DATABASE_URL="$DATABASE_URL" DATABASE_TEST_URL="$DATABASE_TEST_URL" JWT_SECRET=baseline-test-secret-32-bytes-minimum uv run pytest tests/test_release_identity.py tests/test_readiness.py tests/test_auth.py -q
```

Expected: fail because the helper, settings field, heartbeat metadata, and health fields do not exist.

- [ ] **Step 3: Implement release normalization and settings**

Create `core/release.py`:

```python
from __future__ import annotations

import re

_RELEASE_SHA = re.compile(r"[0-9a-f]{7,40}")


def normalize_release_sha(value: str | None) -> str:
    return value if value is not None and _RELEASE_SHA.fullmatch(value) else "unknown"
```

Add to API `Settings`:

```python
omnia_release_sha: str = Field(default="unknown")
```

- [ ] **Step 4: Make heartbeat and readiness metadata structured**

In `readiness.py`, introduce:

```python
from typing import NamedTuple


class ReadinessReport(NamedTuple):
    checks: dict[str, str]
    dependencies: dict[str, str]
```

Write worker heartbeat JSON as:

```python
payload = json.dumps(
    {
        "at": datetime.now(UTC).isoformat(),
        "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
    },
    separators=(",", ":"),
)
```

Change `_redis_and_worker()` to return `(redis_ok, worker_ok, worker_release_sha)`. Parse new JSON; treat an existing legacy timestamp as a healthy worker with release `unknown`.

Change the orchestrator probe to return `(healthy, release_sha)` from its JSON and make `probe_readiness()` return:

```python
ReadinessReport(
    checks={
        "database": "ok" if database_ok else "failed",
        "redis": "ok" if redis_ok else "failed",
        "worker": "ok" if worker_ok else "failed",
        "deploy_control_plane": "ok" if deploy_ok else "failed",
        "preview_storage": "ok" if preview_ok else "failed",
    },
    dependencies={
        "worker_release_sha": worker_release_sha,
        "orchestrator_release_sha": orchestrator_release_sha,
    },
)
```

- [ ] **Step 5: Return release metadata from API health**

Use `normalize_release_sha(get_settings().omnia_release_sha)` in both health handlers. In readiness, calculate health from `report.checks` and return `report.dependencies` without exposing response bodies or URLs.

- [ ] **Step 6: Inject the revision into API and worker containers**

Add this exact environment entry to both service blocks:

```yaml
OMNIA_RELEASE_SHA: ${OMNIA_RELEASE_SHA:-unknown}
```

- [ ] **Step 7: Run focused tests and static checks**

```bash
cd apps/api
uv run ruff check src/omnia_api/core/release.py src/omnia_api/services/readiness.py src/omnia_api/main.py tests/test_release_identity.py tests/test_readiness.py tests/test_auth.py
uv run mypy src/omnia_api/core/release.py src/omnia_api/services/readiness.py src/omnia_api/main.py
DATABASE_URL="$DATABASE_URL" DATABASE_TEST_URL="$DATABASE_TEST_URL" JWT_SECRET=baseline-test-secret-32-bytes-minimum uv run pytest tests/test_release_identity.py tests/test_readiness.py tests/test_auth.py -q
```

Expected: all commands pass.

- [ ] **Step 8: Commit API/worker identity**

```bash
git add apps/api apps/llm-gateway/deploy/full/docker-compose.yml
git commit -m "feat: expose api and worker release identity"
```

### Task 3: Add Orchestrator/Web Identity and Enforce Static Smoke Consistency

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/core/release.py`
- Create: `apps/orchestrator/tests/test_health.py`
- Create: `apps/web/src/lib/release.ts`
- Create: `apps/web/src/lib/__tests__/release.test.ts`
- Modify: `apps/orchestrator/src/omnia_orchestrator/core/config.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/routers/health.py`
- Modify: `apps/web/src/app/web-health/route.ts`
- Modify: `apps/llm-gateway/deploy/full/docker-compose.yml`
- Modify: `apps/orchestrator/.env.example`
- Modify: `apps/llm-gateway/deploy/full/.env.example`
- Modify: `.github/workflows/production-smoke.yml`

**Interfaces:**
- Consumes: normalization contract from Task 2 and API readiness dependency names.
- Produces: orchestrator/web `release_sha`; `PRODUCTION_EXPECTED_RELEASE_SHA` enforcement in scheduled static smoke.

- [ ] **Step 1: Write failing orchestrator and web normalization tests**

The orchestrator tests cover valid and unsafe values plus the router result:

```python
def test_health_exposes_normalized_release(monkeypatch):
    monkeypatch.setattr(health, "get_settings", lambda: SimpleNamespace(omnia_release_sha="a7c4fc22"))
    assert asyncio.run(health.health()) == {"status": "ok", "release_sha": "a7c4fc22"}
```

The web Vitest file imports `normalizeReleaseSha` and asserts valid lowercase hex passes while uppercase, whitespace, and fewer than seven characters return `unknown`.

- [ ] **Step 2: Run tests to verify both helpers are absent**

```bash
cd apps/orchestrator && uv run pytest tests/test_health.py -q
cd ../web && corepack pnpm test -- src/lib/__tests__/release.test.ts
```

Expected: both fail on missing files/contracts.

- [ ] **Step 3: Implement orchestrator health identity**

Create the independently deployed orchestrator helper with this exact contract:

```python
from __future__ import annotations

import re

_RELEASE_SHA = re.compile(r"[0-9a-f]{7,40}")


def normalize_release_sha(value: str | None) -> str:
    return value if value is not None and _RELEASE_SHA.fullmatch(value) else "unknown"
```

Add `omnia_release_sha: str = Field(default="unknown")` to orchestrator settings and return:

```python
{
    "status": "ok",
    "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
}
```

- [ ] **Step 4: Implement runtime web health identity**

Create `release.ts`:

```typescript
const RELEASE_SHA = /^[0-9a-f]{7,40}$/;

export function normalizeReleaseSha(value: string | undefined): string {
  return value && RELEASE_SHA.test(value) ? value : "unknown";
}
```

Change the route to `export const dynamic = "force-dynamic"` and return:

```typescript
return Response.json({
  status: "ok",
  service: "web",
  release_sha: normalizeReleaseSha(process.env.OMNIA_RELEASE_SHA),
});
```

- [ ] **Step 5: Inject and document runtime revision values**

Add `OMNIA_RELEASE_SHA: ${OMNIA_RELEASE_SHA:-unknown}` to the web service. Add `OMNIA_RELEASE_SHA=unknown` to the production and orchestrator example environment files with comments that the deploy procedure replaces it with `git rev-parse HEAD`.

- [ ] **Step 6: Enforce all four revisions in static production smoke**

Add workflow env:

```yaml
PRODUCTION_EXPECTED_RELEASE_SHA: ${{ vars.PRODUCTION_EXPECTED_RELEASE_SHA }}
```

After reading `web` and `api`, extract:

```bash
expected="$PRODUCTION_EXPECTED_RELEASE_SHA"
test "$expected" != ""
test "$expected" != "unknown"
for actual in \
  "$(jq -r '.release_sha' <<<"$web")" \
  "$(jq -r '.release_sha' <<<"$api")" \
  "$(jq -r '.dependencies.worker_release_sha' <<<"$api")" \
  "$(jq -r '.dependencies.orchestrator_release_sha' <<<"$api")"; do
  test "$actual" = "$expected"
done
```

Update the incident body to state that release identity is checked.

- [ ] **Step 7: Verify focused tests and Compose rendering**

```bash
cd apps/orchestrator
uv run ruff check src/omnia_orchestrator/core/release.py src/omnia_orchestrator/routers/health.py tests/test_health.py
uv run mypy src/omnia_orchestrator/core/release.py src/omnia_orchestrator/routers/health.py
uv run pytest tests/test_health.py -q
cd ../web
corepack pnpm test -- src/lib/__tests__/release.test.ts
corepack pnpm typecheck
cd ../llm-gateway/deploy/full
JWT_SECRET=test-jwt-secret SECRETS_ENCRYPTION_KEY=test-encryption-key ORCHESTRATOR_INTERNAL_TOKEN=test-orchestrator-token NEXTAUTH_SECRET=test-nextauth OMNIA_RELEASE_SHA=a7c4fc22 docker compose config --quiet
```

Expected: all pass.

- [ ] **Step 8: Commit cross-service release proof**

```bash
git add apps/orchestrator apps/web apps/llm-gateway/deploy/full .github/workflows/production-smoke.yml
git commit -m "feat: prove deployed release across services"
```

### Task 4: Add Authenticated Project-Memory Canary Policy

**Files:**
- Create: `apps/api/src/omnia_api/services/project_memory_policy.py`
- Create: `apps/api/tests/test_project_memory_policy.py`
- Modify: `apps/api/src/omnia_api/core/config.py`
- Modify: `apps/api/src/omnia_api/services/generation_runs.py`
- Modify: `apps/api/src/omnia_api/routers/messages.py`
- Modify: `apps/api/tests/test_generation_runs.py`
- Modify: `apps/api/tests/test_project_memory.py`
- Modify: `apps/api/tests/test_deploy_gate_defaults.py`
- Modify: `apps/llm-gateway/deploy/full/docker-compose.yml`
- Modify: `apps/llm-gateway/deploy/full/.env.example`

**Interfaces:**
- Produces: `project_memory_enabled(*, global_enabled: bool, canary_users: str, user_id: UUID) -> bool` and `load_project_memory_context(session: AsyncSession, *, project_id: UUID, user_id: UUID) -> str`.
- Consumes: authenticated `current_user.id` on reads and durable `GenerationRun.user_id` on terminal compilation.

- [ ] **Step 1: Write the failing truth-table and invalid-entry tests**

Create `test_project_memory_policy.py`:

```python
from uuid import UUID

import pytest

from omnia_api.services.project_memory_policy import project_memory_enabled

USER = UUID("00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize(
    ("global_enabled", "canary_users", "expected"),
    [
        (False, "", False),
        (False, str(USER), True),
        (True, "", True),
        (True, str(USER), True),
        (False, "bad-entry,00000000-0000-4000-8000-000000000002", False),
    ],
)
def test_project_memory_policy(global_enabled, canary_users, expected) -> None:
    assert project_memory_enabled(
        global_enabled=global_enabled,
        canary_users=canary_users,
        user_id=USER,
    ) is expected
```

- [ ] **Step 2: Run the policy test to verify it fails**

```bash
cd apps/api && uv run pytest tests/test_project_memory_policy.py -q
```

Expected: fail because the policy module does not exist.

- [ ] **Step 3: Implement the minimal authenticated policy**

Implement:

```python
from __future__ import annotations

from uuid import UUID


def project_memory_enabled(*, global_enabled: bool, canary_users: str, user_id: UUID) -> bool:
    if global_enabled:
        return True
    for raw in canary_users.split(","):
        try:
            if UUID(raw.strip()) == user_id:
                return True
        except ValueError:
            continue
    return False
```

Add `project_memory_canary_users: str = Field(default="")` to settings. Add this loader in the same module so the large prompt handler has one directly testable seam:

```python
async def load_project_memory_context(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> str:
    settings = get_settings()
    if not project_memory_enabled(
        global_enabled=settings.use_project_memory,
        canary_users=settings.project_memory_canary_users,
        user_id=user_id,
    ):
        return ""
    return await render_project_memory_context(session, project_id)
```

- [ ] **Step 4: Gate both memory reads and writes through the helper**

In `compile_terminal_run_memory`, pass `run.user_id` to the helper with both settings values before opening the savepoint.

In `_process_prompt`, replace the global-only read condition and call `load_project_memory_context(session, project_id=project_id, user_id=user_id)`. Do not use `project_id`, request JSON, or a query parameter as the canary identity.

- [ ] **Step 5: Add service-level regression tests**

Extend `test_project_memory_policy.py` with a fake renderer test:

```python
async def test_context_loader_uses_authenticated_canary(monkeypatch):
    calls: list[object] = []

    async def render(session, project_id):
        calls.append((session, project_id))
        return "<project_memory>v1</project_memory>"

    settings = SimpleNamespace(
        use_project_memory=False,
        project_memory_canary_users=str(USER),
    )
    monkeypatch.setattr(policy, "get_settings", lambda: settings)
    monkeypatch.setattr(policy, "render_project_memory_context", render)
    session = object()
    project_id = uuid4()

    assert await policy.load_project_memory_context(
        session, project_id=project_id, user_id=USER
    ) == "<project_memory>v1</project_memory>"
    assert calls == [(session, project_id)]
```

Add the inverse with an empty allowlist and assert the return is `""` and `calls == []`.

In `test_generation_runs.py`, create a terminal `GenerationRun` with global off, monkeypatch `omnia_api.core.config.get_settings` to return a matching canary allowlist, call `compile_terminal_run_memory(db_session, run)`, and assert a `ProjectMemoryRevision` exists for `run.id`. Repeat with a different UUID and assert none exists. Retain the existing idempotent compilation and secret-redaction tests.

- [ ] **Step 6: Ship production dark by default**

Change both API and worker production environment blocks to:

```yaml
USE_PROJECT_MEMORY: ${USE_PROJECT_MEMORY:-false}
PROJECT_MEMORY_CANARY_USERS: ${PROJECT_MEMORY_CANARY_USERS:-}
```

Add the same keys to `.env.example` with global false and an empty allowlist. Task 7's Compose-rendering test proves the effective API and worker defaults; do not add a source-text assertion.

- [ ] **Step 7: Verify memory tests and type checks**

```bash
cd apps/api
uv run ruff check src/omnia_api/services/project_memory_policy.py src/omnia_api/services/generation_runs.py src/omnia_api/routers/messages.py tests/test_project_memory_policy.py tests/test_generation_runs.py tests/test_project_memory.py tests/test_deploy_gate_defaults.py
uv run mypy src/omnia_api/services/project_memory_policy.py src/omnia_api/services/generation_runs.py
DATABASE_URL="$DATABASE_URL" DATABASE_TEST_URL="$DATABASE_TEST_URL" JWT_SECRET=baseline-test-secret-32-bytes-minimum uv run pytest tests/test_project_memory_policy.py tests/test_generation_runs.py tests/test_project_memory.py tests/test_deploy_gate_defaults.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit memory canary controls**

```bash
git add apps/api apps/llm-gateway/deploy/full
git commit -m "feat: add project memory canary allowlist"
```

### Task 5: Build the Disposable Public-API Generation Canary

**Files:**
- Create: `apps/api/src/omnia_api/ops/__init__.py`
- Create: `apps/api/src/omnia_api/ops/production_canary.py`
- Create: `apps/api/scripts/production_generation_canary.py`
- Create: `apps/api/tests/test_production_canary.py`

**Interfaces:**
- Produces: `CanaryConfig.from_env()`, `ProductionCanary.run() -> CanaryResult`, CLI exit `0` on complete cleanup and nonzero on any failed assertion or cleanup.
- Consumes: only documented public API endpoints and environment variables prefixed `PRODUCTION_CANARY_` plus `PRODUCTION_EXPECTED_RELEASE_SHA`.

- [ ] **Step 1: Write failing configuration and URL-safety tests**

Test exact requirements:

```python
def test_config_rejects_missing_credentials(monkeypatch):
    monkeypatch.delenv("PRODUCTION_CANARY_PASSWORD", raising=False)
    with pytest.raises(CanaryConfigurationError, match="PRODUCTION_CANARY_PASSWORD"):
        CanaryConfig.from_env()


@pytest.mark.parametrize(
    "url",
    [
        "http://demo.preview.lead-generator.ru/api/omnia/preview-session?expires=1&signature=x",
        "https://attacker.example/api/omnia/preview-session?expires=1&signature=x",
        "https://user@demo.preview.lead-generator.ru/api/omnia/preview-session?expires=1&signature=x",
    ],
)
def test_preview_url_rejects_unsafe_origins(url):
    with pytest.raises(CanaryFailure):
        validate_preview_url(url, ".preview.lead-generator.ru")
```

- [ ] **Step 2: Write a fake-transport success test with mandatory cleanup**

Use `httpx.MockTransport` with an ordered state machine that returns health, login, create, build prompt, running/completed polling, project/snapshot files, runtime start, preview bootstrap `307`, final preview `200`, edit prompt, second completion, and delete `204`. Assert the request log ends with project delete and logout, and no `CanaryResult` field contains the signed URL or cookie.

- [ ] **Step 3: Write failure-path tests**

Add independent fake-transport tests for:

- generation deadline exceeded;
- terminal `failed` status;
- release changes between initial and final health;
- final preview response not `200`;
- project delete returning `503`.

Every test that reaches project creation must assert delete was attempted. Cleanup failure must override an otherwise successful result.

- [ ] **Step 4: Run tests to verify the client is absent**

```bash
cd apps/api && uv run pytest tests/test_production_canary.py -q
```

Expected: fail on missing module and symbols.

- [ ] **Step 5: Implement strict configuration and redacted events**

`CanaryConfig` contains:

```python
base_url: str
email: str
password: str
expected_release_sha: str
preview_host_suffix: str
overall_timeout_seconds: int
poll_seconds: float
```

Defaults are `https://constructor.lead-generator.ru`, `.preview.lead-generator.ru`, `2700`, and `5`. Reject non-HTTPS base URLs, invalid expected revisions, missing credentials, a timeout outside `300..3600`, or poll outside `1..30`.

The event emitter prints one JSON line containing only `step`, `status`, `elapsed_seconds`, `project_id`, `run_id`, `snapshot_id`, and a fixed error code. Never serialize request headers, bodies, cookies, URLs with queries, or response bodies.

- [ ] **Step 6: Implement the bounded build/edit flow**

Use one `httpx.Client(timeout=30, follow_redirects=False)` with an in-memory cookie jar. Compute one monotonic overall deadline. Implement helpers:

```python
def _request_json(self, method: str, path: str, *, json: object | None = None) -> dict[str, object]
def _poll_generation(self, project_id: str, run_id: str) -> dict[str, object]
def _assert_release_health(self) -> str
def _assert_new_snapshot(self, project_id: str, previous_snapshot_id: str) -> str
def _verify_preview(self, bootstrap_url: str) -> None
```

Use fixed repository prompts and UUID-based idempotency keys. Require `response_mode == "build"` for the first terminal run and `response_mode == "edit"` for the second. Validate generated snapshot files are a non-empty mapping. Validate bootstrap scheme, no userinfo, exact path `/api/omnia/preview-session`, host suffix, expected `307` to relative `/`, then `200` from the same origin using the cookie jar.

Wrap every post-create step in `try/finally`; delete the exact project UUID and log out. Raise `CanaryCleanupFailure(project_id)` if delete does not return `204`.

- [ ] **Step 7: Add a no-secret CLI wrapper**

The script imports the ops module, constructs config from environment, runs the client, and returns `1` after printing only the exception's fixed public message. It must not dump a traceback because httpx request representations may contain signed URLs.

- [ ] **Step 8: Verify canary unit tests and static checks**

```bash
cd apps/api
uv run ruff check src/omnia_api/ops scripts/production_generation_canary.py tests/test_production_canary.py
uv run mypy src/omnia_api/ops scripts/production_generation_canary.py
uv run pytest tests/test_production_canary.py -q
```

Expected: all pass without network access.

- [ ] **Step 9: Commit the canary client**

```bash
git add apps/api/src/omnia_api/ops apps/api/scripts/production_generation_canary.py apps/api/tests/test_production_canary.py
git commit -m "feat: add disposable production generation canary"
```

### Task 6: Activate CI and the Serialized Generation Workflow

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/production-generation-canary.yml`
- Modify: `infra/ci/README.md`
- Delete: `infra/ci/github-actions-ci.yml`

**Interfaces:**
- Consumes: exact pnpm version, Python lockfiles, canary CLI, GitHub repository variable `PRODUCTION_GENERATION_CANARY_ENABLED`, protected variable `PRODUCTION_EXPECTED_RELEASE_SHA`, and two canary secrets.
- Produces: push/PR baseline CI and a manual/daily serialized paid canary with a distinct incident issue.

- [ ] **Step 1: Activate and strengthen baseline CI**

Move the reference workflow to `.github/workflows/ci.yml` and update it so web runs:

```yaml
- run: corepack enable && corepack prepare pnpm@9.15.0 --activate
- run: test "$(pnpm --version)" = "9.15.0"
- run: cd apps/web && pnpm install --frozen-lockfile
- run: git diff --exit-code -- apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml
- run: cd apps/web && pnpm typecheck && pnpm test && pnpm build
```

Retain gateway tests and Python compileall. Update the README to say CI is active and link the workflow path.

- [ ] **Step 2: Create the production generation workflow**

Use triggers:

```yaml
on:
  schedule:
    - cron: "17 2 * * *"
  workflow_dispatch:
```

Use one job gated by:

```yaml
if: github.event_name == 'workflow_dispatch' || vars.PRODUCTION_GENERATION_CANARY_ENABLED == 'true'
```

Set:

```yaml
concurrency:
  group: production-generation-canary
  cancel-in-progress: false
```

The job checks out the reviewed repository, installs `uv`, runs `uv sync --frozen` in `apps/api`, verifies all required variables/secrets are non-empty without printing them, then executes the CLI with a 50-minute job timeout.

- [ ] **Step 3: Add separate incident lifecycle**

On failure, open at most one issue named `[monitor] Omnia production generation canary failed` with the Actions run link and no command output. On success, comment with the recovery run and close only that exact issue. The static smoke continues to own its separate issue title.

- [ ] **Step 4: Validate workflow behavior with actionlint**

Add a `workflow-lint` job to the already-active `ci.yml`. After checkout and `actions/setup-go@v5` with Go `1.24`, run:

```bash
go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
actionlint
```

This exercises GitHub expression, event, shell, and workflow structure rather than grepping source text. A workstation without Go reports this check as not executed; the GitHub runner is the required consumer and must pass it before merge.

- [ ] **Step 5: Commit workflows**

```bash
git add .github/workflows/ci.yml .github/workflows/production-generation-canary.yml infra/ci/README.md infra/ci/github-actions-ci.yml
git commit -m "ci: gate builds and schedule production generation canary"
```

### Task 7: Add the Local Release Gate and Production Rollout Runbook

**Files:**
- Create: `infra/release/local-release-gate.sh`
- Create: `infra/release/update-env-value.sh`
- Create: `infra/release/test-release-tools.sh`
- Create: `infra/release/test-compose-policy.sh`
- Create: `infra/release/README.md`
- Modify: `.gitignore`
- Modify: `apps/llm-gateway/deploy/full/README.md`

**Interfaces:**
- Produces: a no-production-mutation local gate; an atomic permission-preserving single-key environment updater; an exact owner-confirmed production sequence.
- Consumes: `EXPECTED_RELEASE_SHA`, API test database URLs, Docker, Corepack, uv, the full Compose file, and the canary CLI from Task 5.

- [ ] **Step 1: Write shell contract tests before implementation**

Create `test-release-tools.sh` with temporary Git repositories and environment files. It invokes the future scripts and proves:

- a dirty tree exits before any build command;
- a mismatched `EXPECTED_RELEASE_SHA` exits;
- `unknown` is rejected;
- evidence paths never include environment values;
- the updater changes only the named key, preserves file mode, and rejects newline-containing values.

For the updater case, create a mode-`600` file containing `A=1`, `TARGET=old`, and `B=2`; call `update-env-value.sh "$file" TARGET new`; assert the final file is exactly `A=1`, `B=2`, `TARGET=new` and still mode `600`. Call it with a newline-containing value and require nonzero exit.

For the gate preflight, initialize and commit a minimal temporary Git repository, copy `local-release-gate.sh` into it, set a mismatched `EXPECTED_RELEASE_SHA`, and require nonzero exit before a sentinel command can create a file. Dirty the repository and repeat with the exact SHA. Grep captured stderr to ensure an injected secret value is absent.

Run:

```bash
bash infra/release/test-release-tools.sh
```

Expected before implementation: file not found.

- [ ] **Step 2: Implement the atomic environment updater**

`update-env-value.sh FILE KEY VALUE` validates `KEY` with `^[A-Z][A-Z0-9_]*$`, rejects CR/LF in `VALUE`, resolves an existing regular file, creates a temp file in the same directory, replaces all existing `KEY=` lines with one final line, preserves mode/owner where permitted, then atomically renames. It prints only `updated KEY in FILE`, never the value or file contents.

- [ ] **Step 3: Implement the local release gate**

The gate requires `EXPECTED_RELEASE_SHA`, a clean tree, exact `git rev-parse HEAD`, `uv`, Docker, Corepack, and `DATABASE_URL`/`DATABASE_TEST_URL`. It writes under ignored `.release-evidence/<sha>-<UTC timestamp>/`:

```text
manifest.json
api.log
orchestrator.log
web.log
compose.log
images.log
```

A `run_step NAME LOG COMMAND...` function records UTC start/end, exit code, and command name in `manifest.json` while redirecting output to the named log. It never records environment values.

Commands are, in order:

```bash
cd apps/api && uv sync --frozen
cd apps/api && uv run ruff check .
cd apps/api && uv run mypy src
cd apps/api && uv run alembic upgrade head
cd apps/api && uv run pytest -q
cd apps/orchestrator && uv sync --frozen
cd apps/orchestrator && uv run ruff check .
cd apps/orchestrator && uv run mypy src
cd apps/orchestrator && uv run pytest -q
cd apps/web && corepack pnpm install --frozen-lockfile
cd apps/web && corepack pnpm typecheck
cd apps/web && corepack pnpm test
cd apps/web && corepack pnpm build
docker compose -f apps/llm-gateway/deploy/full/docker-compose.yml config --quiet
docker compose -f apps/llm-gateway/deploy/full/docker-compose.yml build api web
```

After the install command, fail if Git changes `package.json`, `pnpm-lock.yaml`, or `pnpm-workspace.yaml`. Set `OMNIA_RELEASE_SHA=$EXPECTED_RELEASE_SHA` only for Compose rendering/building.

- [ ] **Step 4: Test rendered production policy through Docker Compose**

`test-compose-policy.sh` supplies non-secret values for required variables and runs:

```bash
docker compose -f apps/llm-gateway/deploy/full/docker-compose.yml config --format json
```

Pass that JSON to Python's standard `json` module and assert the rendered `api.environment` and `worker.environment` values are:

```python
assert api["USE_PROJECT_MEMORY"] == "false"
assert worker["USE_PROJECT_MEMORY"] == "false"
assert api["PROJECT_MEMORY_CANARY_USERS"] == ""
assert worker["PROJECT_MEMORY_CANARY_USERS"] == ""
assert api["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert worker["ACCEPTANCE_GAUNTLET_REFERENCE_GATE"] == "false"
assert api["REFERENCE_CEILING_ENFORCED"] == "false"
assert worker["REFERENCE_CEILING_ENFORCED"] == "false"
```

Run this test in the Docker-enabled CI image-build job and from the production host preflight. A workstation without Docker reports this check as not executed; it never records a false pass.

- [ ] **Step 5: Document exact production preflight, rollout, and rollback**

The runbook requires:

1. owner confirmation containing release and rollback SHAs;
2. exact live SHA/image/service capture and backups;
3. zero active generations query;
4. `USE_PROJECT_MEMORY=false` and one `PROJECT_MEMORY_CANARY_USERS` UUID;
5. temp revision-tagged image build;
6. API migration/restart before worker, then web and orchestrator;
7. localhost and off-host health with exact SHA equality;
8. manual generation workflow success;
9. previous image/orchestrator rollback with memory disabled and migration retained.

Include the exact existing active-run SQL and the warning against `docker compose down -v`.

- [ ] **Step 6: Link the full-stack deployment README to the release gate**

Add a top-level note that production updates must follow `infra/release/README.md`, and replace the unstructured “run a disposable canary” line with the exact manual workflow/CLI command from Tasks 5-6.

- [ ] **Step 7: Verify shell syntax and self-tests**

```bash
bash -n infra/release/local-release-gate.sh infra/release/update-env-value.sh infra/release/test-release-tools.sh infra/release/test-compose-policy.sh
bash infra/release/test-release-tools.sh
git diff --check
```

Expected: all pass.

- [ ] **Step 8: Commit release tooling and runbook**

```bash
git add .gitignore infra/release apps/llm-gateway/deploy/full/README.md
git commit -m "ops: add production release gate and rollback runbook"
```

### Task 8: Full Verification, Review, Push, and Owner-Gated Deployment

**Files:**
- Modify only files required by verified failures or review findings.
- Evidence: ignored `.release-evidence/<release>-<timestamp>/`.

**Interfaces:**
- Consumes: every task deliverable and the approved design.
- Produces: green local evidence, reviewed/pushed branch, green CI, and a deployment-ready exact revision. Production mutation still requires explicit owner confirmation.

- [ ] **Step 1: Run focused regression suites**

```bash
cd apps/api
DATABASE_URL="$DATABASE_URL" DATABASE_TEST_URL="$DATABASE_TEST_URL" JWT_SECRET=baseline-test-secret-32-bytes-minimum uv run pytest tests/test_acceptance.py tests/test_deploy_gate_defaults.py tests/test_release_identity.py tests/test_readiness.py tests/test_auth.py tests/test_project_memory_policy.py tests/test_generation_runs.py tests/test_project_memory.py tests/test_production_canary.py -q
cd ../orchestrator
uv run pytest tests/test_health.py tests/test_runtime_probe.py tests/test_deploy_state.py tests/test_provisioner.py tests/test_max_preview_session.py tests/test_docker_client.py -q
cd ../web
corepack pnpm typecheck
corepack pnpm test
```

Expected: all pass.

- [ ] **Step 2: Commit any test-only corrections, then require a clean tree**

```bash
git diff --check
git status --short
```

Expected: no output from either command after any correction commit.

- [ ] **Step 3: Run the full local release gate**

```bash
EXPECTED_RELEASE_SHA="$(git rev-parse HEAD)" \
DATABASE_URL="$DATABASE_URL" \
DATABASE_TEST_URL="$DATABASE_TEST_URL" \
JWT_SECRET=release-gate-test-secret-32-bytes \
bash infra/release/local-release-gate.sh
```

Expected: exit `0`; manifest records every step successful; no secret appears in evidence logs.

- [ ] **Step 4: Perform independent review**

Use `superpowers:requesting-code-review` against the approved spec. Fix only evidence-backed issues, rerun affected tests, and commit each accepted correction.

- [ ] **Step 5: Verify completion evidence again**

Use `superpowers:verification-before-completion`, rerun the affected focused suites plus `git diff --check`, and record the exact final SHA.

- [ ] **Step 6: Push the authorized feature branch and observe CI**

```bash
git push -u origin codex/production-e2e-hardening
```

Wait for all branch checks. If workflow-file push is rejected for missing workflow permission, report that exact external blocker without rewriting history or bypassing checks.

- [ ] **Step 7: Stop at the production confirmation gate**

Tell the owner:

- exact release SHA and rollback SHA;
- CI and local evidence status;
- required canary account UUID/secrets/repository variables;
- expected production impact and rollback command set.

Ask for explicit confirmation before any SSH, production environment-file mutation, container restart, migration, GitHub production-variable update, or paid production canary.

- [ ] **Step 8: After confirmation, execute the runbook and prove the golden path**

Capture backups and active-run state, deploy dark memory, require exact health identity, manually run the disposable canary, and keep memory canary-only until ten consecutive green build/edit cycles complete. If any gate fails, use the documented previous images/orchestrator revision and leave migration `0046` applied.
