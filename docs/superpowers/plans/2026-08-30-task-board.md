# Team Task Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a shared four-person Kanban board at `/doska` with persisted CRUD tasks and status movement.

**Architecture:** Add one PostgreSQL model and migration plus a focused FastAPI CRUD router. Add a typed frontend adapter and an isolated client component that owns loading, forms, identity selection, periodic refresh, and native drag-and-drop.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, PostgreSQL, Next.js 15, React 19, TypeScript, Tailwind CSS, Vitest, Pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-task-board-design.md`

## Global Constraints

- The route is exactly `/doska` and remains directly reachable without the Omnia account flow.
- Members are exactly `alexey`, `alexey_jr`, `artem`, and `roman`.
- Statuses are exactly `backlog`, `in_progress`, `review`, and `done`.
- Priorities are exactly `low`, `medium`, and `high`.
- Task titles are 1–160 trimmed characters and descriptions are at most 2,000 characters.
- All intended changes must be committed, pushed to `origin/main`, deployed with the documented `full` production Compose project, and health-checked.

---

### Task 1: Persisted task-board API

**Files:**
- Create: `apps/api/src/omnia_api/models/task_board.py`
- Create: `apps/api/src/omnia_api/schemas/task_board.py`
- Create: `apps/api/src/omnia_api/routers/task_board.py`
- Create: `apps/api/migrations/versions/0049_task_board.py`
- Create: `apps/api/tests/test_task_board_api.py`
- Modify: `apps/api/src/omnia_api/models/__init__.py`
- Modify: `apps/api/src/omnia_api/main.py`

**Interfaces:**
- Produces: `GET|POST /api/task-board/tasks` and `PATCH|DELETE /api/task-board/tasks/{task_id}`.
- Produces: `TaskBoardTaskPublic` with `id`, `title`, `description`, `status`, `assignee`, `priority`, `position`, `created_at`, and `updated_at`.

- [x] **Step 1: Write failing API tests**

```python
async def test_task_board_crud(client):
    empty = await client.get("/api/task-board/tasks")
    assert empty.json() == []
    created = await client.post("/api/task-board/tasks", json={
        "title": "Проверить релиз", "assignee": "roman", "priority": "high"
    })
    assert created.status_code == 201
    task_id = created.json()["id"]
    moved = await client.patch(
        f"/api/task-board/tasks/{task_id}", json={"status": "review"}
    )
    assert moved.json()["status"] == "review"
    assert (await client.delete(f"/api/task-board/tasks/{task_id}")).status_code == 204
```

- [x] **Step 2: Run the test and confirm the route is missing**

Run: `cd apps/api && uv run pytest tests/test_task_board_api.py -q`
Expected: FAIL because `/api/task-board/tasks` is not registered.

- [x] **Step 3: Add the model, schema, migration, and router**

```python
class TaskBoardTask(Base):
    __tablename__ = "task_board_tasks"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="backlog")
    assignee: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

The router computes `max(position) + 1` for new cards and status moves under a transaction-scoped advisory lock, caps the public board at 500 cards, rate-limits public mutations, commits each mutation, refreshes the row, and raises `ApiError("not_found", ..., 404)` for missing ids.

- [x] **Step 4: Run API and migration tests**

Run: `cd apps/api && uv run pytest tests/test_task_board_api.py tests/test_migrations_single_head.py -q`
Expected: all tests pass.

### Task 2: Responsive board UI

**Files:**
- Create: `apps/web/src/lib/api/task-board.ts`
- Create: `apps/web/src/components/task-board/TaskBoard.tsx`
- Create: `apps/web/src/app/doska/page.tsx`
- Create: `apps/web/src/lib/__tests__/task-board.test.tsx`

**Interfaces:**
- Consumes: API endpoints from Task 1 through `taskBoardApi`.
- Produces: `<TaskBoard api={taskBoardApi} />` and the `/doska` page.

- [x] **Step 1: Write failing component tests**

```tsx
it("shows four members and four workflow columns", async () => {
  renderBoard(fakeApi);
  expect(container.textContent).toContain("Алексей jr.");
  expect(container.querySelectorAll("[data-board-column]")).toHaveLength(4);
});

it("moves a dropped task to the destination status", async () => {
  dropCard("task-1", "review");
  expect(fakeApi.updateTask).toHaveBeenCalledWith("task-1", { status: "review" });
});
```

- [x] **Step 2: Run the test and confirm the component is missing**

Run: `cd apps/web && pnpm test src/lib/__tests__/task-board.test.tsx`
Expected: FAIL because the task-board module does not exist.

- [x] **Step 3: Add typed API adapter and board component**

```ts
export const taskBoardApi = {
  listTasks: () => apiFetch<TaskBoardTask[]>("/api/task-board/tasks"),
  createTask: (payload: CreateTaskPayload) =>
    apiFetch<TaskBoardTask>("/api/task-board/tasks", { method: "POST", json: payload }),
  updateTask: (id: string, payload: UpdateTaskPayload) =>
    apiFetch<TaskBoardTask>(`/api/task-board/tasks/${id}`, { method: "PATCH", json: payload }),
  deleteTask: (id: string) =>
    apiFetch<void>(`/api/task-board/tasks/${id}`, { method: "DELETE" }),
};
```

The component renders the fixed member switcher, summary, four drop targets, cards, create/edit modal, retryable error banner, and status fallback control for touch screens. It stores only the selected member id in `localStorage` and never treats it as authorization.

- [x] **Step 4: Run component tests and frontend checks**

Run: `cd apps/web && pnpm test src/lib/__tests__/task-board.test.tsx && pnpm typecheck && pnpm lint && pnpm build`
Expected: tests and all checks pass.

### Task 3: Report, delivery, and live verification

**Files:**
- Modify: `otchet/data.json`

**Interfaces:**
- Consumes: verified API and UI from Tasks 1–2.
- Produces: pushed production revision and live `/doska` board.

- [ ] **Step 1: Update the live report**

Add an owner action describing the four-person shared board, add or update the relevant hypothesis with its actual verification status, and advance the matching vector only after live verification.

- [x] **Step 2: Run the complete verification suite**

Run targeted API and web tests, API Ruff, web typecheck/lint/build, `python -m json.tool otchet/data.json`, `git diff --check`, and inspect `git diff --stat` plus `git status --short`.
Expected: every command exits 0 and only intended files are changed.

- [ ] **Step 3: Commit and push**

```bash
git add apps/api apps/web docs/superpowers otchet/data.json
git commit -m "feat: add shared team task board"
git fetch origin
git rebase origin/main
git push origin HEAD:main
```

- [ ] **Step 4: Deploy the pushed revision**

On the VPS, run `git fetch origin && git merge --ff-only origin/main`, then from `apps/llm-gateway/deploy/full` run `docker compose -p full up -d --build api web`.

- [ ] **Step 5: Verify production**

Check Compose service health, API and web health endpoints, `GET /api/task-board/tasks`, `GET /doska`, and release SHA. Open `/doska` in the browser, create and move a disposable verification task, delete it, and confirm the final board state remains clean.
