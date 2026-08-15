# Omnia Agent Kernel v2 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable Project Brain and semantic anti-loop to the existing MAX native-agent while removing the deterministic runtime/provider defects that caused the recorded 69-edit failed generation.

**Architecture:** The existing native-agent remains the only general reasoning loop. A pure `agent_brain` module normalizes observations, records one active hypothesis and experiments, and computes semantic progress; `agent_native` checkpoints and renders this state. Deterministic MAX contract, history migration and provider taxonomy fixes land first so the new brain receives truthful evidence.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, PostgreSQL JSON agent state, Redis native checkpoints, pytest, Ruff, Next.js MAX managed template.

## Global Constraints

- Native-agent remains the owner of the generation; no second general loop is introduced.
- Existing project/workspace isolation, MAX locked files, secret checks and release proof remain mandatory.
- Source code, secrets, private reasoning and tenant text must not enter shared memory.
- A primary failure is immutable; later verification failures are secondary evidence.
- `budget_exceeded`, authentication and validation errors are never retried.
- Managed root `page.tsx` must never be migrated into `ProductApp.tsx`.
- MAX rollout is controlled by `AGENT_KERNEL_V2_ENABLED` plus the owner-only
  `AGENT_KERNEL_V2_CANARY_USERS` allowlist; global `false` preserves existing
  loop behavior for every user outside the allowlist.
- Every task uses failing-test-first and produces one independently reviewable commit.

---

### Task 1: Make the MAX environment manifest describe the live managed client

**Files:**
- Modify: `apps/api/src/omnia_api/services/max_environment_manifest.py:60-100`
- Test: `apps/api/tests/test_generation_continuity.py:109-123`

**Interfaces:**
- Consumes: `render_max_managed_files(config, project_id) -> dict[str, str]`
- Produces: `build_max_environment_manifest() -> dict[str, Any]` whose `integration_client` exports come from the exact managed file written to the workspace.

- [ ] **Step 1: Write the failing parity test**

Append to `test_environment_manifest_is_source_derived_locked_and_secret_free`:

```python
from uuid import UUID

from omnia_api.services.max_project_kit import (
    default_max_project_config,
    render_max_managed_files,
)

managed = render_max_managed_files(
    default_max_project_config("Manifest"),
    UUID(int=0),
)["src/lib/omnia/integration-client.ts"]
signatures = "\n".join(manifest["managed_signatures"]["integration_client"])
assert ("trackMaxEvent" in signatures) is ("export async function trackMaxEvent" in managed)
assert "trackOmniaGoal" in signatures
```

- [ ] **Step 2: Run the test and verify the current false export fails**

Run: `cd apps/api && uv run pytest tests/test_generation_continuity.py::test_environment_manifest_is_source_derived_locked_and_secret_free -v`

Expected: FAIL because the manifest exposes `trackMaxEvent` while the rendered `integration-client.ts` exposes `trackOmniaGoal`.

- [ ] **Step 3: Build signatures from rendered managed files**

In `max_environment_manifest.py`, import `UUID`, `default_max_project_config` and `render_max_managed_files`, then replace the three independent template reads with:

```python
managed = render_max_managed_files(default_max_project_config("Manifest"), UUID(int=0))
client = managed["src/lib/omnia/integration-client.ts"]
provider = managed["src/components/MaxAppProvider.tsx"]
config = managed["src/lib/omnia/max-config.ts"]
```

Keep package and skill-index reads unchanged.

- [ ] **Step 4: Run the parity test**

Run: `cd apps/api && uv run pytest tests/test_generation_continuity.py::test_environment_manifest_is_source_derived_locked_and_secret_free -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/max_environment_manifest.py apps/api/tests/test_generation_continuity.py
git commit -m "fix(max): derive agent manifest from managed runtime"
```

---

### Task 2: Prevent managed root-page recursion during MAX history restore

**Files:**
- Modify: `apps/api/src/omnia_api/services/max_project_kit.py:707-756`
- Test: `apps/api/tests/test_max_project_kit.py:137-151`

**Interfaces:**
- Produces: `is_managed_max_root_page(source: str) -> bool`
- Preserves: byte-for-byte migration of real historical model-owned root pages.

- [ ] **Step 1: Write the failing regression test**

```python
def test_current_managed_root_page_is_not_migrated_into_product_entry() -> None:
    managed_page = max_project_kit_svc._template_file(MAX_PRODUCT_PAGE_PATH)

    files = render_max_history_files(
        {MAX_PRODUCT_PAGE_PATH: managed_page},
        _config(),
        uuid4(),
    )

    assert files[MAX_PRODUCT_PAGE_PATH] == managed_page
    assert files[MAX_PRODUCT_ENTRY_PATH] != managed_page
    assert 'data-max-product-canvas="empty"' in files[MAX_PRODUCT_ENTRY_PATH]
```

- [ ] **Step 2: Run it and verify recursion is reproduced**

Run: `cd apps/api && uv run pytest tests/test_max_project_kit.py::test_current_managed_root_page_is_not_migrated_into_product_entry -v`

Expected: FAIL because `ProductApp.tsx` equals the managed root page.

- [ ] **Step 3: Add the exact managed-page classifier**

Immediately before `max_history_product_files`:

```python
def is_managed_max_root_page(source: str) -> bool:
    return source.strip() == _template_file(MAX_PRODUCT_PAGE_PATH).strip()
```

Change the legacy migration condition to require:

```python
and not is_managed_max_root_page(legacy_page)
```

Do not use a broad `"OmniaProductRuntime" in source` check: only the exact current managed file is platform-owned; real historical source remains recoverable.

- [ ] **Step 4: Run focused migration tests**

Run: `cd apps/api && uv run pytest tests/test_max_project_kit.py -k "history or migration or managed_root" -v`

Expected: all selected tests PASS, including preservation of the historical product page fixture.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/max_project_kit.py apps/api/tests/test_max_project_kit.py
git commit -m "fix(max): keep managed root page out of ProductApp"
```

---

### Task 3: Make provider budget exhaustion terminal and non-retryable

**Files:**
- Modify: `apps/api/src/omnia_api/services/agent_native.py:1530-1605`
- Test: `apps/api/tests/test_agent_native.py`

**Interfaces:**
- Consumes: Anthropic-shaped gateway error `{error: {type|code}}`
- Produces: `PermanentProviderError` for permanent provider rejection and `AmbiguousPaidCallError` only for unknown settlement.

- [ ] **Step 1: Add a real HTTPX response-classification test**

Follow the existing `_call_messages` test pattern in `test_agent_native.py`:

```python
@pytest.mark.asyncio
async def test_budget_exceeded_is_permanent_and_not_retried(monkeypatch):
    calls = 0

    async def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            request=httpx.Request("POST", "https://gateway.test/v1/messages"),
            json={"error": {"type": "budget_exceeded"}},
        )

    client = httpx.AsyncClient()
    monkeypatch.setattr(client, "post", fake_post)
    try:
        with pytest.raises(agent_native.PermanentProviderError):
            await agent_native._call_messages(
                client,
                "https://gateway.test/v1/messages",
                [{"role": "user", "content": "x"}],
                "system",
            )
    finally:
        await client.aclose()
    assert calls == 1
```

- [ ] **Step 2: Run the test and verify current ambiguity/retry fails it**

Run: `cd apps/api && uv run pytest tests/test_agent_native.py::test_budget_exceeded_is_permanent_and_not_retried -v`

Expected: FAIL because the current branch raises `AmbiguousPaidCallError` and may retry.

- [ ] **Step 3: Add permanent provider error types before generic 429 handling**

Immediately after extracting `error_type`:

```python
permanent_provider_error = error_type in {
    "auth_error",
    "authentication_error",
    "budget_exceeded",
    "insufficient_balance",
    "invalid_request",
    "validation_error",
}
if permanent_provider_error:
    raise PermanentProviderError(r.status_code)
```

Leave trusted concurrency/rate-limit retry unchanged.

- [ ] **Step 4: Run provider classification tests**

Run: `cd apps/api && uv run pytest tests/test_agent_native.py -k "budget_exceeded or rate_limit or provider" -v`

Expected: permanent error uses one request; trusted concurrency retry tests remain PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/agent_native.py apps/api/tests/test_agent_native.py
git commit -m "fix(agent): stop retrying exhausted provider budget"
```

---

### Task 4: Add the pure Project Brain and semantic progress model

**Files:**
- Create: `apps/api/src/omnia_api/services/agent_brain.py`
- Create: `apps/api/tests/test_agent_brain.py`

**Interfaces:**
- Produces: `new_brain`, `normalize_error_signature`, `record_hypothesis`, `record_observation`, `record_mutation`, `brain_prompt_view`, `semantic_loop_count`.
- State is a JSON-serializable `dict[str, object]`; no ORM dependency.

- [ ] **Step 1: Write failing tests for normalization and bounded memory**

```python
from omnia_api.services.agent_brain import (
    brain_prompt_view,
    new_brain,
    normalize_error_signature,
    record_hypothesis,
    record_observation,
    semantic_loop_count,
)


def test_error_signature_ignores_locations_ids_and_whitespace():
    first = "src/a.tsx:437:9 TS2305: Module has no exported member 'trackMaxEvent'"
    second = "src/b.tsx:99:2  TS2305: Module has no exported member 'trackMaxEvent'"
    assert normalize_error_signature(first) == normalize_error_signature(second)


def test_repeated_error_with_distinct_edits_is_semantic_loop():
    brain = new_brain("Build fitness app", ["typecheck clean"])
    signature = normalize_error_signature("TS2305 missing export trackMaxEvent")
    for index in range(3):
        brain = record_hypothesis(
            brain,
            root_cause=f"attempt {index}",
            evidence=[signature],
            experiment=f"edit {index}",
            expected_result="typecheck clean",
        )
        brain = record_observation(
            brain,
            kind="build",
            status="error",
            summary="compile failed",
            error_signature=signature,
            evidence=["typecheck red"],
        )
    assert semantic_loop_count(brain) == 3


def test_prompt_view_is_bounded_and_contains_no_source():
    brain = new_brain("Goal", ["criterion"])
    view = brain_prompt_view(brain, max_chars=1200)
    assert len(view) <= 1200
    assert "NEXT REQUIRED ACTION" in view
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `cd apps/api && uv run pytest tests/test_agent_brain.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the JSON-only brain**

Implement these exact public signatures:

```python
def new_brain(objective: str, acceptance: Sequence[str]) -> dict[str, object]: ...
def normalize_error_signature(text: str) -> str: ...
def record_hypothesis(
    brain: Mapping[str, object], *, root_cause: str,
    evidence: Sequence[str], experiment: str, expected_result: str,
) -> dict[str, object]: ...
def record_observation(
    brain: Mapping[str, object], *, kind: str, status: str, summary: str,
    error_signature: str = "", evidence: Sequence[str] = (),
    artifacts: Sequence[str] = (),
) -> dict[str, object]: ...
def record_mutation(
    brain: Mapping[str, object], *, paths: Sequence[str], revision: int,
) -> dict[str, object]: ...
def semantic_loop_count(brain: Mapping[str, object]) -> int: ...
def brain_prompt_view(brain: Mapping[str, object], *, max_chars: int = 6000) -> str: ...
```

Normalization must lowercase, replace project-relative paths and `line:column`
locations, collapse UUID/hex/digits, preserve compiler code and quoted symbol,
then SHA-256 the canonical text. Retain at most 20 observations, 12 experiments,
12 failed approaches and 20 artifact revisions. `brain_prompt_view` exposes only
objective, open acceptance, active hypothesis, latest observation, failed
approaches, loop count and one next action.

- [ ] **Step 4: Run Project Brain tests**

Run: `cd apps/api && uv run pytest tests/test_agent_brain.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/agent_brain.py apps/api/tests/test_agent_brain.py
git commit -m "feat(agent): add durable semantic Project Brain"
```

---

### Task 5: Persist Project Brain in the native checkpoint and inject its view

**Files:**
- Modify: `apps/api/src/omnia_api/core/config.py`
- Modify: `apps/api/src/omnia_api/services/agent_native.py:1436-1518,1680-1905`
- Modify: `apps/llm-gateway/deploy/full/docker-compose.yml`
- Test: `apps/api/tests/test_generation_continuity.py`
- Test: `apps/api/tests/test_agent_native.py`
- Test: `apps/llm-gateway/tests/test_full_compose_safety.py`

**Interfaces:**
- Adds settings `agent_kernel_v2_enabled: bool = Field(default=False)` and
  `agent_kernel_v2_canary_users: str = Field(default="")`.
- Adds checkpoint field: `brain_v2`.
- `_stable_max_working_memory(..., brain: Mapping[str, object] | None = None)` appends `brain_prompt_view` when enabled.

- [ ] **Step 1: Add a failing canary checkpoint-resume test**

Add a dedicated MAX test. Set the cached settings object's global flag false and
canary list to `owner-fixture`, run `stable_max_loop=True` with
`user_id="owner-fixture"`, cancel the first provider call after it receives the
working memory, then assert:

```python
assert checkpoints[-1]["brain_v2"]["version"] == 1
assert checkpoints[-1]["brain_v2"]["objective"]
```

Resume from `checkpoints[-1]`, capture the next provider payload and assert its
working note contains both `PROJECT BRAIN v1` and the same objective.

Run the same one-turn cancellation with `user_id="other-fixture"`; assert there
is no `brain_v2` and its checkpoint stays version 2. Restore both mutated setting
attributes in a `monkeypatch` fixture.

- [ ] **Step 2: Run the focused continuity test**

Run: `cd apps/api && uv run pytest tests/test_generation_continuity.py -k "checkpoint" -v`

Expected: FAIL because `brain_v2` is absent.

- [ ] **Step 3: Add the feature flag and checkpoint field**

In `Settings`, next to native-agent settings:

```python
agent_kernel_v2_enabled: bool = Field(default=False)
agent_kernel_v2_canary_users: str = Field(default="")
```

In `run_native_build`, resolve the existing per-user canary primitive:

```python
brain_enabled = stable_max_loop and is_agentic_enabled(
    settings.agent_kernel_v2_enabled,
    settings.agent_kernel_v2_canary_users,
    str(user_id) if user_id is not None else None,
)
```

Initialize `brain_v2 = new_brain(task, [])` only when `brain_enabled`; otherwise
keep `None`.
Restore a mapping from `resume_checkpoint["brain_v2"]`. Persist it under the same
key and write checkpoint version 3 only when the brain is enabled; the disabled
path remains byte-compatible version 2. Readers accept version-2 checkpoints with
no brain and version-3 checkpoints with `brain_v2`.

- [ ] **Step 4: Inject the bounded brain view**

Add optional `brain` to `_stable_max_working_memory`. Append
`brain_prompt_view(brain)` after the factual rows and before the authoritative
checkpoint footer. Do not append the full brain JSON.

- [ ] **Step 5: Run continuity/native tests**

Run: `cd apps/api && uv run pytest tests/test_generation_continuity.py tests/test_agent_native.py -k "checkpoint or working_memory" -v`

Expected: PASS for version-2 compatibility and version-3 brain continuity.

- [ ] **Step 6: Pass both flags through production Compose**

Add these exact entries to both the `api` and `worker` environment maps:

```yaml
AGENT_KERNEL_V2_ENABLED: ${AGENT_KERNEL_V2_ENABLED:-false}
AGENT_KERNEL_V2_CANARY_USERS: ${AGENT_KERNEL_V2_CANARY_USERS:-}
```

Add `test_agent_kernel_v2_flags_reach_api_and_worker` to
`apps/llm-gateway/tests/test_full_compose_safety.py`; split the Compose source at
`\n  worker:` and `\n  web:` and assert both keys occur in each service section.
Run:

```bash
cd apps/llm-gateway
uv run pytest tests/test_full_compose_safety.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/omnia_api/core/config.py apps/api/src/omnia_api/services/agent_native.py apps/api/tests/test_generation_continuity.py apps/api/tests/test_agent_native.py apps/llm-gateway/deploy/full/docker-compose.yml apps/llm-gateway/tests/test_full_compose_safety.py
git commit -m "feat(agent): persist Project Brain across native continuations"
```

---

### Task 6: Enforce diagnosis-first repair and semantic loop termination

**Files:**
- Modify: `apps/api/src/omnia_api/services/agent_native.py`
- Modify: `apps/api/src/omnia_api/services/generation_continuity.py:159-222`
- Test: `apps/api/tests/test_agent_native.py`
- Test: `apps/api/tests/test_generation_continuity.py`

**Interfaces:**
- Adds native tool `diagnose(root_cause, evidence, experiment, expected_result)`.
- Adds stop reason `semantic_loop_red` classified as `internal_no_progress`.

- [ ] **Step 1: Write failing diagnosis/loop tests**

Create a native-loop fixture whose model responses are:

1. `build` returning the same normalized TS2305 error;
2. an immediate `edit_file` without diagnosis;
3. `diagnose` then `edit_file` then the same red `build`, repeated three times.

Assert:

```python
assert "diagnose" in first_rejected_edit_result["content"]
assert result.done is False
assert result.stop_reason == "semantic_loop_red"
```

Add continuity assertion:

```python
decision = classify_stop(
    "semantic_loop_red", attempt=0, started_at=datetime.now(UTC)
)
assert decision.continue_run is False
assert decision.classification == "internal_no_progress"
```

- [ ] **Step 2: Run tests and verify missing tool/stop classification**

Run: `cd apps/api && uv run pytest tests/test_agent_native.py -k "diagnosis or semantic_loop" tests/test_generation_continuity.py -k "semantic_loop" -v`

Expected: FAIL.

- [ ] **Step 3: Add the typed diagnose tool**

Add to `_TOOLS` before `done`:

```python
_tool(
    "diagnose",
    "Record one evidence-based root-cause hypothesis before repairing a repeated red build/runtime observation.",
    {
        "root_cause": _STR,
        "evidence": _STR_ARRAY,
        "experiment": _STR,
        "expected_result": _STR,
    },
    ["root_cause", "evidence", "experiment", "expected_result"],
)
```

Add `diagnose` to `_STABLE_MAX_TOOL_NAMES` and to the stable repair, repair-verify
and progress tool subsets, so it remains callable while a red build narrows the
available schema. Handle it inside `run_native_build` without calling the
container executor. Call `record_hypothesis`, return a normalized successful tool
result and checkpoint.

- [ ] **Step 4: Record build observations and mutations**

After every successful write/edit, call `record_mutation`. After every build,
normalize the compiler output and call `record_observation`. Clear the active
hypothesis on green build; move it to failed approaches when the same signature
returns after the experiment. Persist `diagnosis_required_signature` inside
`brain_v2`; a red build sets it, a valid `diagnose` clears it for one mutation,
and another red build restores it.

When a red build signature already has an unresolved experiment, reject another
write/edit until `diagnose` records a different experiment. When
`semantic_loop_count(brain_v2) >= 3`, return `AgentResult(done=False,
stop_reason="semantic_loop_red", ...)` after one final local build check and no
new provider call.

- [ ] **Step 5: Classify the stop as terminal internal no-progress**

Add to `_TERMINAL_INTERNAL_REASONS`:

```python
"semantic_loop_red": (
    "Три проверенных исправления не изменили одну и ту же ошибку. "
    "Рабочая версия восстановлена; сохранены гипотезы и evidence для архитектурного ремонта."
),
```

- [ ] **Step 6: Run diagnosis and adjacent loop tests**

Run: `cd apps/api && uv run pytest tests/test_agent_native.py tests/test_generation_continuity.py tests/test_agent_loop_cycle.py -v`

Expected: PASS; byte-identical legacy loop tests remain green when the feature flag is false.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/omnia_api/services/agent_native.py apps/api/src/omnia_api/services/generation_continuity.py apps/api/tests/test_agent_native.py apps/api/tests/test_generation_continuity.py
git commit -m "feat(agent): stop semantic repair loops with diagnosis evidence"
```

---

### Task 7: Preserve the primary generation failure

**Files:**
- Modify: `apps/api/src/omnia_api/services/generation_runs.py:320-336`
- Modify: `apps/api/src/omnia_api/routers/messages.py:6460-6476`
- Test: `apps/api/tests/test_generation_runs.py`
- Test: `apps/api/tests/test_max_generation_contract.py` or the existing MAX terminal helper test file.

**Interfaces:**
- Changes: `set_generation_run_error(run_id, error_code, *, preserve_existing=True) -> str | None`.
- Final verification may add secondary detail but cannot replace a stored primary code.

- [ ] **Step 1: Write failing persistence test**

```python
await set_generation_run_error(run.id, "agent_stopped:provider_rejected_red")
primary = await set_generation_run_error(run.id, "final_verification_failed")
await db_session.refresh(run)
assert run.error == "agent_stopped:provider_rejected_red"
assert primary == "agent_stopped:provider_rejected_red"
```

Add a second assertion that `preserve_existing=False` intentionally replaces the value for administrative repair paths.

- [ ] **Step 2: Run the focused test**

Run: `cd apps/api && uv run pytest tests/test_generation_runs.py -k "primary_generation_failure" -v`

Expected: FAIL because the second call overwrites the first.

- [ ] **Step 3: Implement immutable-primary semantics**

Change the function signature and assignment:

```python
async def set_generation_run_error(
    run_id: UUID,
    error_code: str,
    *,
    preserve_existing: bool = True,
) -> str | None:
    ...
    if not preserve_existing or not run.error:
        run.error = error_code[:2000]
    primary = run.error
    await session.commit()
    return primary
```

Keep both existing call sites on the default. In the terminal MAX branch, assign
the returned value to `primary_code`. Emit `code=primary_code or error_code` and,
only when it differs, `secondary_code=error_code`; do not expose provider bodies.

- [ ] **Step 4: Run generation-run and MAX terminal tests**

Run: `cd apps/api && uv run pytest tests/test_generation_runs.py tests/test_max_generation_contract.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/generation_runs.py apps/api/src/omnia_api/routers/messages.py apps/api/tests/test_generation_runs.py apps/api/tests/test_max_generation_contract.py
git commit -m "fix(agent): preserve primary generation failure"
```

---

### Task 8: Offline replay gate, full verification and MAX canary configuration

**Files:**
- Create: `apps/api/tests/fixtures/max_agent_kernel_v2/semantic-loop.json`
- Create: `apps/api/tests/test_agent_kernel_v2_replay.py`
- Modify: `apps/api/.env.example`
- Modify: `apps/llm-gateway/deploy/full/.env.example`
- Modify: `docs/MAX_APP_ENGINEER.md`

**Interfaces:**
- Replay fixture contains sanitized tool/action/error signatures only; no user prompt, code, credentials, project ID or production URL.
- `replay_trace(events) -> ReplayVerdict` is a pure test helper in the test module.

- [ ] **Step 1: Add the sanitized replay fixture**

Store this exact sanitized shape. It preserves the observed mutation count while
keeping only the minimal prefix needed to prove termination:

```json
{
  "recorded_mutation_count": 69,
  "events": [
    {"kind": "diagnose", "root_cause": "state branch", "experiment": "narrow state", "expected_result": "green"},
    {"kind": "mutation", "paths": ["src/product.tsx"], "revision": 1},
    {"kind": "build", "status": "error", "summary": "TS2367 incompatible state comparison"},
    {"kind": "diagnose", "root_cause": "stale narrowing", "experiment": "replace branch", "expected_result": "green"},
    {"kind": "mutation", "paths": ["src/product.tsx"], "revision": 2},
    {"kind": "build", "status": "error", "summary": "TS2367 incompatible state comparison"},
    {"kind": "diagnose", "root_cause": "wrong state model", "experiment": "remove impossible state", "expected_result": "green"},
    {"kind": "mutation", "paths": ["src/product.tsx"], "revision": 3},
    {"kind": "build", "status": "error", "summary": "TS2367 incompatible state comparison"},
    {"kind": "build", "status": "error", "summary": "TS2305 missing managed export FixtureEvent"},
    {"kind": "build", "status": "error", "summary": "TS2304 missing provider export FixtureUser"}
  ]
}
```

- [ ] **Step 2: Add replay assertions**

```python
def test_recorded_churn_stops_before_fourth_same_error_experiment():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    verdict = replay_trace(fixture["events"])
    assert verdict.stop_reason == "semantic_loop_red"
    assert verdict.processed_mutations < fixture["recorded_mutation_count"]
    assert verdict.processed_builds == 3
    assert verdict.provider_calls == 0
```

- [ ] **Step 3: Run replay and full focused suite**

Run:

```bash
cd apps/api
uv run pytest tests/test_agent_kernel_v2_replay.py tests/test_agent_brain.py tests/test_agent_native.py tests/test_generation_continuity.py tests/test_max_project_kit.py tests/test_generation_runs.py -v
uv run ruff check src/omnia_api/services/agent_brain.py src/omnia_api/services/agent_native.py src/omnia_api/services/generation_continuity.py src/omnia_api/services/max_environment_manifest.py src/omnia_api/services/max_project_kit.py src/omnia_api/services/generation_runs.py src/omnia_api/routers/messages.py
```

Expected: all tests PASS; Ruff reports no errors.

- [ ] **Step 4: Document the canary flag**

Add to `apps/api/.env.example`:

```dotenv
# Project Brain + semantic diagnosis gate for the native MAX agent.
AGENT_KERNEL_V2_ENABLED=false
# Comma-separated owner UUIDs; works while the global flag stays false.
AGENT_KERNEL_V2_CANARY_USERS=
```

Add the same two entries to
`apps/llm-gateway/deploy/full/.env.example`; production Compose consumes this
canonical file, while the API example documents local execution.

Update `docs/MAX_APP_ENGINEER.md` with Brain v2 checkpoint fields, semantic
progress definition, diagnosis tool and exact rollback flag.

- [ ] **Step 5: Run the whole API suite**

Run: `cd apps/api && uv run pytest -q`

Expected: all tests PASS. If unrelated environment-only tests cannot start,
record exact test names and error; do not claim the full gate passed.

- [ ] **Step 6: Inspect final diff and secret scan**

Run:

```bash
git diff --check
git diff --stat 7eaefe06..HEAD
git grep -n -E "(sk-[A-Za-z0-9_-]{20,}|api[_-]?key[[:space:]]*=[[:space:]]*['\"][^'\"]+)" -- apps/api docs/MAX_APP_ENGINEER.md
```

Expected: diff check clean; secret grep returns no introduced credential.

- [ ] **Step 7: Commit**

```bash
git add apps/api/tests/fixtures/max_agent_kernel_v2/semantic-loop.json apps/api/tests/test_agent_kernel_v2_replay.py apps/api/.env.example apps/llm-gateway/deploy/full/.env.example docs/MAX_APP_ENGINEER.md
git commit -m "test(agent): add MAX Agent Kernel v2 replay gate"
```

---

### Task 9: Delivery and production health

**Files:** no source changes.

**Interfaces:** exact pushed commit is the only deploy candidate.

- [ ] **Step 1: Push current `main`**

Run: `git push origin main`

Expected: origin advances to the verified local HEAD without force.

- [ ] **Step 2: Deploy through the documented production compose**

Run the runbook preflight and encrypted backup. It must abort when an active
generation exists:

```powershell
$preflight = @'
set -euo pipefail
PROD_ENV=/opt/omnia/apps/llm-gateway/deploy/full/.env
test -r "$PROD_ENV"
active="$(docker exec omnia-prod-postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select status,count(*) from generation_runs where status in ('"'"'pending'"'"','"'"'running'"'"','"'"'cancel_requested'"'"') group by status;"')"
test -z "$active"
cd /opt/omnia
./infra/backup/backup-omnia.sh
cd apps/llm-gateway/deploy/full
docker compose --env-file "$PROD_ENV" -p full config --quiet
'@
ssh lh-server $preflight
```

Then deploy exact pushed revision, API first, worker second:

```powershell
$expected = (git rev-parse HEAD).Trim()
$deploy = @"
set -euo pipefail
cd /opt/omnia
git fetch origin main
git merge --ff-only origin/main
test \"`$(git rev-parse HEAD)\" = \"$expected\"
cd apps/llm-gateway/deploy/full
PROD_ENV=/opt/omnia/apps/llm-gateway/deploy/full/.env
docker compose --env-file \"`$PROD_ENV\" -p full build api worker
docker compose --env-file \"`$PROD_ENV\" -p full up -d api
curl -fsS http://127.0.0.1:8200/health
docker compose --env-file \"`$PROD_ENV\" -p full up -d worker
"@
ssh lh-server $deploy
```

Do not deploy the development `infra/` stack.

- [ ] **Step 3: Confirm exact revision and service health**

Verify production checkout revision equals pushed HEAD, API health is 200,
worker is running, Redis/PostgreSQL are healthy and the existing MAX project
page returns HTTP 200. Do not start a generation.

- [ ] **Step 4: Enable shadow mode before enforcement**

Keep `AGENT_KERNEL_V2_ENABLED=false`. Run the replay in the deployed API image:

```bash
docker exec omnia-prod-api /app/.venv/bin/pytest -q tests/test_agent_kernel_v2_replay.py
```

Then reuse the existing single owner allowlist from
`AGENTIC_BUILDER_CANARY_USERS` without printing it. A remote Python step must:

1. read the canonical `.env` as key/value lines without sourcing it;
2. require exactly one UUID in `AGENTIC_BUILDER_CANARY_USERS`;
3. replace or append `AGENT_KERNEL_V2_ENABLED=false` and
   `AGENT_KERNEL_V2_CANARY_USERS=<that UUID>` through a mode-preserving temporary
   file plus `os.replace`;
4. recreate API then worker with explicit `--env-file`;
5. use `docker inspect --format` predicates to confirm global false and one
   non-empty canary value, while redirecting values away from task output.

Abort canary enablement if the existing owner allowlist is empty or contains more
than one UUID. Never print the UUID or `.env` content.

- [ ] **Step 5: Rollback condition**

Disable `AGENT_KERNEL_V2_ENABLED` immediately if native checkpoint resume, build completion or preview release regresses. Code rollback is unnecessary unless the disabled path differs from pre-deploy behavior.

---

## Self-Review

- **Spec coverage:** deterministic manifest, history recursion, provider taxonomy and primary failure defects are Tasks 1–3 and 7; Project Brain and semantic loop are Tasks 4–6; replay, rollout and verification are Tasks 8–9. Shared lessons, Goose specialist and all-generator rollout remain separate Phase 2–4 plans as required by subsystem boundaries.
- **Placeholder scan:** no `TBD`, `TODO`, unspecified test or unnamed interface remains in this Phase 1 plan.
- **Type consistency:** `brain_v2`, `new_brain`, `record_hypothesis`, `record_observation`, `record_mutation`, `semantic_loop_count`, `brain_prompt_view`, `diagnose`, `semantic_loop_red` and `AGENT_KERNEL_V2_ENABLED` are used consistently across tasks.
