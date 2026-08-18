# MAX Product Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a MAX-only contextual product advisor that shows up to three one-click improvements after successful material builds.

**Architecture:** A pure backend analyzer builds a bounded feature inventory and MAX-safe candidate set from the current material snapshot. A cheap LLM may only rank and lightly rephrase those candidates; strict server validation, deterministic fallback, and Redis caching keep the feature safe and available. The MAX chat renders the response and sends the server-owned implementation prompt through the existing prompt submission path.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async, Redis, MinIO/pygit2 repo service, pytest, Next.js 15, React 19, TanStack Query, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-18-max-product-advisor-design.md`

## Global Constraints

- MAX Mini Apps only; non-MAX projects return 404 and never render the UI.
- Return at most three suggestions after the first product snapshot and material changes.
- Cosmetic-only snapshots reuse the last material snapshot cache key.
- Default model is `claude-haiku-4-5`, output limit is 700 tokens, temperature is `0.1`.
- Model-ranked results cache for 30 days; transient deterministic fallbacks cache for 15 minutes.
- Never send raw source, environment values, credentials, or chat secrets to the ranking model.
- The model may rank known candidate ids and rewrite bounded display copy; implementation prompts remain server-owned.
- Redis/model/malformed-output failures are fail-soft and cannot block chat, preview, or generation.
- Applying advice must reuse `submitWithCredentialIntake` and the ordinary snapshot/verification pipeline.
- No new runtime dependency is required.

## File map

- Create `apps/api/src/omnia_api/services/product_advisor.py`: pure classification, inventory, candidate selection, LLM ranking, strict validation, and fallback.
- Create `apps/api/src/omnia_api/schemas/product_advice.py`: public response shapes.
- Create `apps/api/src/omnia_api/routers/product_advice.py`: ownership/MAX checks, snapshot resolution, repository loading, Redis cache, and endpoint.
- Modify `apps/api/src/omnia_api/services/design_plugin.py`: expose the existing archetype classifier through a public function.
- Modify `apps/api/src/omnia_api/services/llm_client.py`: add a per-call `free` metadata override without changing existing callers.
- Modify `apps/api/src/omnia_api/core/config.py`: add `product_advisor_model`.
- Modify `apps/api/src/omnia_api/main.py`: register the new router.
- Create `apps/api/tests/test_product_advisor.py`: pure service tests.
- Create `apps/api/tests/test_product_advice_api.py`: authentication, isolation, MAX-only, cache, and fail-soft endpoint tests.
- Create `apps/web/src/lib/api/product-advice.ts`: frontend wire types and request.
- Create `apps/web/src/components/max/MaxProductAdvisor.tsx`: compact presentational advisor card.
- Modify `apps/web/src/components/workspace/ChatPanel.tsx`: gating, query, rendering, and one-click handoff.
- Create `apps/web/src/lib/__tests__/max-product-advisor.test.tsx`: component behavior and integration source contract.

---

### Task 1: Pure product analysis and deterministic fallback

**Files:**
- Create: `apps/api/src/omnia_api/services/product_advisor.py`
- Modify: `apps/api/src/omnia_api/services/design_plugin.py`
- Test: `apps/api/tests/test_product_advisor.py`

**Interfaces:**
- Consumes: `design_plugin.classify_product_archetype(brief: str) -> str`.
- Produces: `SnapshotInput`, `AdviceContext`, `AdviceItem`, `is_material_change`, `choose_analysis_snapshot`, `extract_feature_inventory`, `build_advice_context`, `candidate_advice`, and `ADVISOR_VERSION`.

- [ ] **Step 1: Write failing classifier and inventory tests**

Add tests with exact expectations:

```python
def test_material_change_ignores_cosmetic_prompt() -> None:
    assert not is_material_change("Сделай кнопку синей и увеличь отступ")
    assert is_material_change("Добавь избранное с сохранением и отдельным экраном")


def test_choose_analysis_snapshot_reuses_material_parent() -> None:
    snapshots = [
        SnapshotInput("cosmetic", "c" * 40, "Поменяй цвет заголовка"),
        SnapshotInput("feature", "b" * 40, "Добавь историю заказов"),
        SnapshotInput("initial", "a" * 40, "Приложение кофейни"),
    ]
    assert choose_analysis_snapshot(snapshots).id == "feature"


def test_inventory_filters_secrets_and_detects_features() -> None:
    inventory = extract_feature_inventory({
        "src/app/page.tsx": "function Search(){ return <input placeholder='Поиск'/> }",
        ".env": "PAYMENT_TOKEN=secret",
        "pnpm-lock.yaml": "search favorites analytics",
    })
    assert "search" in inventory
    assert "payment" not in inventory
    assert "secret" not in repr(inventory).lower()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd apps/api && uv run pytest tests/test_product_advisor.py -q`

Expected: collection fails because `omnia_api.services.product_advisor` and `classify_product_archetype` do not exist.

- [ ] **Step 3: Expose the shared archetype helper**

Add to `design_plugin.py`:

```python
def classify_product_archetype(brief: str) -> str:
    return _pick_pattern(brief).id
```

Export it from `__all__`, and keep `_pick_pattern` as the single implementation used by `build_design_contract`.

- [ ] **Step 4: Implement pure analysis types and rules**

In `product_advisor.py`, define:

```python
ADVISOR_VERSION = "1.0.0"
MAX_ADVICE_ITEMS = 3

@dataclass(frozen=True)
class SnapshotInput:
    id: str
    commit_sha: str
    prompt_text: str | None

@dataclass(frozen=True)
class AdviceItem:
    id: str
    kind: Literal["feature", "improvement"]
    title: str
    benefit: str
    prompt: str

@dataclass(frozen=True)
class AdviceContext:
    project_name: str
    material_prompt: str
    archetype: str
    inventory: tuple[str, ...]

@dataclass(frozen=True)
class AdviceCandidate(AdviceItem):
    archetypes: tuple[str, ...]
    presence_signals: tuple[str, ...]
    priority: int
```

Implement `is_material_change` with explicit product-flow signals and an explicit cosmetic-only vocabulary. Implement `choose_analysis_snapshot` over newest-first inputs, selecting the newest material prompt and falling back to the newest snapshot.

`build_advice_context(project_name, material_prompt, discovery_spec, files)` combines only the project name, sanitized material request, string values from the discovery spec, and normalized inventory. It returns `AdviceContext`; raw files are discarded before any ranking call.

Allowlist `.ts`, `.tsx`, `.js`, `.jsx`, `.json`, `.css`, `.html`, and `.md`; exclude dot-env files, secrets, lockfiles, dependency/build directories, and files over 200,000 characters. Cap the joined scan at 500,000 characters. Map source evidence to normalized signals including `search`, `filters`, `favorites`, `history`, `notifications`, `analytics`, `onboarding`, `empty_states`, `error_states`, `payments`, `booking`, `progress`, `sharing`, `roles`, and `offline_state`.

- [ ] **Step 5: Add the bounded candidate catalog and fallback**

Define server-owned candidates for all archetypes. The first catalog must contain these ids so behavior is explicit and testable:

```text
commerce: smart-search, saved-favorites, transparent-order-status, repeat-order
booking-service: quick-rebooking, booking-reminders, waitlist, reschedule-flow
fitness-health: progress-insights, adaptive-plan, habit-streaks, workout-reminders
communication: unread-priorities, conversation-search, pinned-context, smart-notifications
learning-content: continue-learning, knowledge-checks, saved-content, progress-streaks
operations: priority-inbox, bulk-actions, audit-history, role-aware-workspace
analytics: anomaly-highlights, date-comparison, saved-filters, export-share
productivity: guided-onboarding, fast-search, saved-state, actionable-empty-states
universal improvement: resilient-states, faster-repeat-action, useful-notifications
```

Every prompt must require one vertical slice, persisted state where relevant, working loading/empty/error/success states, preservation of the current design, and no fake provider connection. `candidate_advice` filters candidates whose presence signals already exist, orders by archetype relevance then priority, deduplicates, and returns at most three `AdviceItem` values with at least one improvement when an applicable improvement exists.

- [ ] **Step 6: Run service tests and commit**

Run:

```bash
cd apps/api
uv run pytest tests/test_product_advisor.py -q
uv run ruff check src/omnia_api/services/product_advisor.py src/omnia_api/services/design_plugin.py tests/test_product_advisor.py
```

Expected: all targeted tests pass and Ruff reports no errors.

Commit:

```bash
git add apps/api/src/omnia_api/services/product_advisor.py apps/api/src/omnia_api/services/design_plugin.py apps/api/tests/test_product_advisor.py
git commit -m "feat(max): add bounded product advice analysis"
```

---

### Task 2: Bounded model ranking and response validation

**Files:**
- Modify: `apps/api/src/omnia_api/services/product_advisor.py`
- Modify: `apps/api/src/omnia_api/services/llm_client.py`
- Modify: `apps/api/src/omnia_api/core/config.py`
- Test: `apps/api/tests/test_product_advisor.py`

**Interfaces:**
- Consumes: `candidate_advice(...) -> tuple[AdviceItem, ...]` from Task 1.
- Produces: `generate_product_advice(...) -> ProductAdviceResult` and `llm_client.complete_chat(..., free: bool | None = None)`.

- [ ] **Step 1: Add failing ranking tests**

Test that the model receives inventory and candidates but no source content, that returned ids are validated, display rewrites are length-bounded, the server-owned prompt is preserved, and malformed/empty output returns the deterministic fallback:

```python
@pytest.mark.asyncio
async def test_model_can_rank_but_cannot_replace_prompt() -> None:
    async def complete(messages, model, **kwargs):
        assert kwargs["stage"] == "product_advisor"
        assert kwargs["free"] is True
        assert "PAYMENT_TOKEN" not in repr(messages)
        return '{"items":[{"id":"saved-favorites","title":"Сохраняйте любимое","benefit":"Возвращаться к выбору быстрее"}]}'

    result = await generate_product_advice(context, complete=complete)
    assert result.source == "model"
    assert result.items[0].id == "saved-favorites"
    assert "сохран" in result.items[0].prompt.lower()
```

- [ ] **Step 2: Verify RED**

Run: `cd apps/api && uv run pytest tests/test_product_advisor.py -q`

Expected: ranking tests fail because `generate_product_advice` and `free` are absent.

- [ ] **Step 3: Add per-call free metadata and model setting**

Add `product_advisor_model: str = Field(default="claude-haiku-4-5")` to `Settings`.

Extend `complete_chat` with `free: bool | None = None` and set metadata with:

```python
"free": _free_generation.get() if free is None else free,
```

Existing callers omit the keyword and remain byte-for-byte equivalent.

- [ ] **Step 4: Implement strict ranking**

Define:

```python
@dataclass(frozen=True)
class ProductAdviceResult:
    archetype: str
    items: tuple[AdviceItem, ...]
    source: Literal["model", "fallback"]
```

`generate_product_advice` sends only project name, archetype, normalized inventory, sanitized material request, and the candidate JSON. Call `complete_chat` with the configured model, `stage="product_advisor"`, `free=True`, `max_tokens=700`, and `temperature=0.1`.

Parse one JSON object with an `items` list. Accept only candidate ids already offered, at most once. Strip control characters/markup from optional title and benefit, clamp them to 80 and 180 characters, and always take `kind` and `prompt` from the server candidate. Fill fewer-than-three valid model choices from deterministic candidates. Catch provider, JSON, and validation errors and return the fallback.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
cd apps/api
uv run pytest tests/test_product_advisor.py -q
uv run ruff check src/omnia_api/services/product_advisor.py src/omnia_api/services/llm_client.py src/omnia_api/core/config.py tests/test_product_advisor.py
```

Commit:

```bash
git add apps/api/src/omnia_api/services/product_advisor.py apps/api/src/omnia_api/services/llm_client.py apps/api/src/omnia_api/core/config.py apps/api/tests/test_product_advisor.py
git commit -m "feat(max): rank product advice with bounded model"
```

---

### Task 3: Authenticated cached product-advice endpoint

**Files:**
- Create: `apps/api/src/omnia_api/schemas/product_advice.py`
- Create: `apps/api/src/omnia_api/routers/product_advice.py`
- Modify: `apps/api/src/omnia_api/main.py`
- Test: `apps/api/tests/test_product_advice_api.py`

**Interfaces:**
- Consumes: Task 2 `generate_product_advice`, `choose_analysis_snapshot`, `ADVISOR_VERSION`; existing `repo.read_files`, `get_redis`, `SessionDep`, and `CurrentUserDep`.
- Produces: `POST /api/projects/{project_id}/product-advice -> ProductAdviceResponse`.

- [ ] **Step 1: Write failing API tests**

Use the established register/create helpers and insert real `Snapshot` rows into the test database. Monkeypatch `repo.read_files`, `generate_product_advice`, and `get_redis` with controlled fakes. Assert:

```python
assert (await anonymous.post(url)).status_code == 401
assert (await owner.post(non_max_url)).status_code == 404
assert (await other_user.post(owner_max_url)).status_code == 404
assert response.json()["items"][:1] == [{
    "id": "saved-favorites",
    "kind": "feature",
    "title": "Избранное",
    "benefit": "Быстрее вернуться к выбору",
    "prompt": "Добавь сохранение избранного ...",
}]
assert len(response.json()["items"]) <= 3
```

Add a cache test where a cosmetic current snapshot resolves to the previous material commit and two endpoint calls invoke the model fake once. Add Redis-get/set failure tests that still return deterministic advice.

- [ ] **Step 2: Verify RED**

Run: `cd apps/api && uv run pytest tests/test_product_advice_api.py -q`

Expected: 404 because the router is not registered.

- [ ] **Step 3: Add public schemas**

Define bounded Pydantic models:

```python
class ProductAdviceItem(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=64)
    kind: Literal["feature", "improvement"]
    title: str = Field(min_length=1, max_length=80)
    benefit: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1, max_length=3000)

class ProductAdviceResponse(BaseModel):
    version: str
    project_id: UUID
    current_snapshot_id: UUID
    analysis_snapshot_id: UUID
    archetype: str
    source: Literal["model", "fallback", "cache"]
    items: list[ProductAdviceItem] = Field(max_length=3)
```

- [ ] **Step 4: Implement endpoint and cache**

Verify project ownership and `max_miniapp` before reading snapshots or files. Load newest-first snapshots for the project, resolve the analysis snapshot, and use:

```python
cache_key = (
    f"omnia:product-advice:{ADVISOR_VERSION}:"
    f"{project.id}:{analysis_snapshot.commit_sha}"
)
```

Validate cached JSON through `ProductAdviceResponse`. On a miss, load files via `await asyncio.to_thread(repo.read_files, project.id, analysis_snapshot.commit_sha)`, generate advice, construct the response, then `setex` for `2_592_000` seconds for model output or `900` seconds for fallback. Wrap Redis get/set separately so cache outages do not skip analysis or replace a good response.

- [ ] **Step 5: Register router, run tests, and commit**

Run:

```bash
cd apps/api
uv run pytest tests/test_product_advice_api.py tests/test_product_advisor.py -q
uv run ruff check src/omnia_api/routers/product_advice.py src/omnia_api/schemas/product_advice.py src/omnia_api/main.py tests/test_product_advice_api.py
```

Commit:

```bash
git add apps/api/src/omnia_api/routers/product_advice.py apps/api/src/omnia_api/schemas/product_advice.py apps/api/src/omnia_api/main.py apps/api/tests/test_product_advice_api.py
git commit -m "feat(max): expose cached product advice endpoint"
```

---

### Task 4: Compact MAX advisor card

**Files:**
- Create: `apps/web/src/lib/api/product-advice.ts`
- Create: `apps/web/src/components/max/MaxProductAdvisor.tsx`
- Create: `apps/web/src/lib/__tests__/max-product-advisor.test.tsx`

**Interfaces:**
- Consumes: authenticated `apiFetch`; endpoint response from Task 3.
- Produces: `requestProductAdvice(projectId: string): Promise<ProductAdviceResponse>` and `<MaxProductAdvisor advice items applyingId onApply />`.

- [ ] **Step 1: Write failing DOM behavior test**

Mount with `createRoot` in jsdom and assert only three rows render, Russian copy is visible, and a click calls `onApply` with the server prompt while disabling that row:

```tsx
const items = [first, second, third, fourth];
root.render(
  <MaxProductAdvisor
    items={items}
    applyingId={null}
    onApply={onApply}
  />,
);
expect(container.querySelectorAll("[data-advice-id]")).toHaveLength(3);
container.querySelector<HTMLButtonElement>("[data-advice-id='saved-favorites'] button")!.click();
expect(onApply).toHaveBeenCalledWith(first);
```

- [ ] **Step 2: Verify RED**

Run: `cd apps/web && pnpm test -- max-product-advisor.test.tsx`

Expected: module resolution fails because the component and API file do not exist.

- [ ] **Step 3: Implement API contract**

Define `ProductAdviceItem` and `ProductAdviceResponse` matching Task 3, and call:

```typescript
return apiFetch<ProductAdviceResponse>(
  `/api/projects/${projectId}/product-advice`,
  { method: "POST", timeoutMs: 20_000 },
);
```

- [ ] **Step 4: Implement compact card**

Render a MAX-styled bordered panel headed `Что улучшить дальше` with a `Lightbulb` icon. Slice to three items defensively. Each stacked row renders `Добавить` or `Улучшить`, title, benefit, and a button labelled `Добавить`. Use a minimum 44px touch target, `aria-busy` for the applying row, and `data-testid="max-product-advisor"` plus `data-advice-id` for tests. No raw HTML rendering.

- [ ] **Step 5: Run component tests and commit**

Run:

```bash
cd apps/web
pnpm test -- max-product-advisor.test.tsx
pnpm typecheck
```

Commit:

```bash
git add apps/web/src/lib/api/product-advice.ts apps/web/src/components/max/MaxProductAdvisor.tsx apps/web/src/lib/__tests__/max-product-advisor.test.tsx
git commit -m "feat(max): add one-click product advisor card"
```

---

### Task 5: Integrate advice into the successful-build chat lifecycle

**Files:**
- Modify: `apps/web/src/components/workspace/ChatPanel.tsx`
- Modify: `apps/web/src/lib/__tests__/max-product-advisor.test.tsx`

**Interfaces:**
- Consumes: Task 4 `requestProductAdvice` and `MaxProductAdvisor`; existing `submitWithCredentialIntake`.
- Produces: MAX-only post-build advice query and one-click submission behavior.

- [ ] **Step 1: Add failing integration contract tests**

Assert the source contains all gates and the normal handoff:

```typescript
expect(chatPanel).toContain('mode === "max"');
expect(chatPanel).toContain("latestCompletedSnapshotId");
expect(chatPanel).toContain("!isStreaming");
expect(chatPanel).toContain("!showSurvey");
expect(chatPanel).toContain("requestProductAdvice(projectId)");
expect(chatPanel).toContain("submitWithCredentialIntake(item.prompt, [])");
expect(chatPanel).toContain("<MaxProductAdvisor");
```

- [ ] **Step 2: Verify RED**

Run: `cd apps/web && pnpm test -- max-product-advisor.test.tsx`

Expected: integration assertions fail because `ChatPanel` has no advisor query.

- [ ] **Step 3: Add the lifecycle query**

Find the newest assistant message whose `snapshot_id` is non-null and whose `tokens_out` is non-null. Enable the TanStack query only when mode is MAX, that id exists, streaming is false, and the onboarding survey is not open:

```typescript
const advice = useQuery({
  queryKey: ["product-advice", projectId, latestCompletedSnapshotId],
  queryFn: () => requestProductAdvice(projectId),
  enabled:
    mode === "max" &&
    !!latestCompletedSnapshotId &&
    !isStreaming &&
    !showSurvey,
  staleTime: Infinity,
  retry: 1,
});
```

Do not show an error toast; advice is optional and fail-soft.

- [ ] **Step 4: Add one-click application**

Track `applyingAdviceId`. Await `submitWithCredentialIntake(item.prompt, [])`; retain the card if submission returns false, and clear the pending id in `finally`. Render `MaxProductAdvisor` below the messages only when the query returned at least one item and the gates still hold. Include advice length in the auto-scroll effect so the new card becomes visible.

- [ ] **Step 5: Run frontend checks and commit**

Run:

```bash
cd apps/web
pnpm test -- max-product-advisor.test.tsx
pnpm typecheck
pnpm lint
```

Commit:

```bash
git add apps/web/src/components/workspace/ChatPanel.tsx apps/web/src/lib/__tests__/max-product-advisor.test.tsx
git commit -m "feat(max): surface advice after successful builds"
```

---

### Task 6: Full verification and delivery evidence

**Files:**
- Modify only files required by failures found in this task.

**Interfaces:**
- Consumes: complete vertical slice from Tasks 1–5.
- Produces: verified revision ready for the repository delivery loop.

- [ ] **Step 1: Run focused backend verification**

```bash
cd apps/api
uv run pytest tests/test_product_advisor.py tests/test_product_advice_api.py -q
uv run ruff check src/omnia_api/services/product_advisor.py src/omnia_api/services/design_plugin.py src/omnia_api/services/llm_client.py src/omnia_api/routers/product_advice.py src/omnia_api/schemas/product_advice.py src/omnia_api/main.py tests/test_product_advisor.py tests/test_product_advice_api.py
uv run mypy src/omnia_api/services/product_advisor.py src/omnia_api/routers/product_advice.py src/omnia_api/schemas/product_advice.py
```

Expected: all commands exit 0.

- [ ] **Step 2: Run full frontend verification**

```bash
cd apps/web
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

Expected: all commands exit 0 and Next.js produces a successful production build.

- [ ] **Step 3: Review diff and behavior contracts**

Run from repository root:

```bash
git diff --check origin/main...HEAD
git status --short
git log --oneline --decorate origin/main..HEAD
```

Confirm no secret/config files, raw generated project data, unrelated restoration changes, or more than three advice items entered the diff.

- [ ] **Step 4: Commit verification fixes if any**

If verification required code corrections, list them with `git status --short`, stage each listed path explicitly, and commit:

```bash
git commit -m "fix(max): harden product advisor verification"
```

If no correction was needed, do not create an empty commit.

- [ ] **Step 5: Complete repository delivery loop**

Run:

```bash
git push origin main
ssh lh-server 'cd /opt/omnia && git fetch origin && git merge --ff-only origin/main && cd apps/llm-gateway/deploy/full && docker compose -p full up -d --build api worker web && docker compose -p full ps api worker web'
curl -fsS https://constructor.lead-generator.ru/api/health
curl -fsS -o /dev/null -w '%{http_code}\n' https://constructor.lead-generator.ru/max
```

Record the pushed revision plus service/HTTP health evidence. If credentials, SSH, push, deploy, or health checks fail, report the exact blocker and do not describe the feature as deployed.
