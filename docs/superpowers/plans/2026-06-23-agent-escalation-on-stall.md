# Agent escalation-on-stall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the agentic build loop shows the first sign of being stuck (a nudge fires), upgrade the agent from the cheap default model to a stronger reasoning model for the rest of the run — so the loop recovers instead of degenerating into the rewrite/read-only cycles, without paying the strong-model price on every build.

**Architecture:** `agent_builder.run_agent_build` gains an optional `escalate_model`. It starts on the cheap `model` and, the first time ANY anti-loop guard issues a NUDGE (consecutive-repeat, global-cycle, or no-write-streak), it switches the model used for subsequent gateway calls to `escalate_model` exactly once. The existing abort guards still bound the run, so cost stays bounded (a handful of strong-model steps, not a 120-step strong-model run). The escalation target is a new `agent_escalation` role (`deepseek-v4-pro-thinking` — already live on the gateway for `director`/`edit_escalation`, stronger than `deepseek-v4-pro`, far cheaper than Opus, so it does NOT repeat the Opus wallet-drain incident).

**Tech Stack:** Python 3.12, FastAPI, `uv` for test running, pytest. Files live under `apps/api`.

**Context for the implementer (read first):**
- This is the LIVE prod path: `USE_AGENTIC_BUILDER=true` is already set in prod, agent role currently resolves to `deepseek-v4-pro` (cheap) — that cheap model degenerating into loops is the verified root cause of the recent `fix(agent): ...loop...` commits. This plan fixes it at the source (model) instead of adding a 4th band-aid guard.
- The loop and its three guards live in `apps/api/src/omnia_api/services/agent_builder.py` `run_agent_build` (starts line 157). Threshold constants: `_NO_WRITE_NUDGE_AT=5` (L365), `_NO_WRITE_ABORT_AT=14` (L366), `_REPEAT_NUDGE_AT=2` (L371), `_REPEAT_ABORT_AT=4` (L372).
- `model` is currently used on the gateway call inside the per-step retry block (≈L205). `complete` is injectable (defaults to `llm_client.complete_chat`) — tests inject a fake.
- The only call site is `apps/api/src/omnia_api/routers/messages.py` ≈L2364-2374 (`_agent_model = model_for_role("agent", override=force_model)` then `run_agent_build(... model=_agent_model ...)`).
- Run tests from `apps/api` with `uv run pytest`.

**Out of scope (separate plan):** gateway rate-governance + GigaChat sibling deployment (apps/llm-gateway) — that is the independent Slice 2 subsystem and gets its own plan. Also note: `AGENT_BUILDER_MAX_STEPS=120` in prod is an ENV tuning, not a code change — flag for the owner separately (120 cheap-model steps is a lot of thrash budget; with escalation the model gets smart on stall, which makes a high budget less harmful, but consider lowering to ~40-60).

---

### Task 1: Add the `agent_escalation` role to the model map

**Files:**
- Modify: `apps/api/src/omnia_api/core/config.py` (the `ROLE_MODEL_MAP` dict, around L990 where `"agent"` is defined)

- [ ] **Step 1: Add the role entry**

In `ROLE_MODEL_MAP`, directly under the existing `"agent"` line, add:

```python
    "agent": "deepseek-v4-pro",
    # When the build loop trips an anti-loop guard (cycle / no-write / repeat),
    # it escalates ONCE to this stronger reasoning model for the rest of the run.
    # deepseek-v4-pro-thinking is already live on the gateway (director /
    # edit_escalation) — stronger than the cheap default, far cheaper than Opus
    # (which drained the wallet on a 1-req/sec char-billed loop, 2026-06-22).
    # Tunable without deploy via ROLE_MODELS env.
    "agent_escalation": "deepseek-v4-pro-thinking",
```

- [ ] **Step 2: Verify the role resolves**

Run: `cd apps/api && uv run python -c "from omnia_api.core.config import model_for_role; print(model_for_role('agent_escalation'))"`
Expected: `deepseek-v4-pro-thinking`

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/omnia_api/core/config.py
git commit -m "feat(agent): add agent_escalation role (deepseek-v4-pro-thinking) for on-stall upgrade"
```

---

### Task 2: Escalate the loop model on first nudge

**Files:**
- Modify: `apps/api/src/omnia_api/services/agent_builder.py` (`run_agent_build`, L157+)
- Test: `apps/api/tests/test_agent_escalation.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_agent_escalation.py`:

```python
"""Escalation-on-stall: the loop upgrades the model once a guard nudges."""
from __future__ import annotations

import pytest

from omnia_api.services import agent_builder


def _read_reply() -> str:
    # A valid action that writes nothing → drives the no-write streak toward
    # the explore nudge (_NO_WRITE_NUDGE_AT=5).
    return '<omnia:action name="read_file">{"path": "src/app/page.tsx"}</omnia:action>'


async def _ok_executor(action: agent_builder.Action) -> dict:
    return {"ok": True, "content": "x"}


def _recording_complete(models: list[str]):
    async def _complete(messages, model, **kwargs):
        models.append(model)
        return _read_reply()
    return _complete


@pytest.mark.asyncio
async def test_escalates_to_strong_model_after_explore_nudge():
    models: list[str] = []
    await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="u",
        model="cheap", escalate_model="strong",
        execute=_ok_executor, complete=_recording_complete(models),
        max_steps=8,
    )
    # First _NO_WRITE_NUDGE_AT(5) calls run on the cheap model; once the nudge
    # fires the rest run on the strong model.
    assert models[0] == "cheap"
    assert "strong" in models
    assert models[-1] == "strong"


@pytest.mark.asyncio
async def test_escalates_at_most_once():
    models: list[str] = []
    await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="u",
        model="cheap", escalate_model="strong",
        execute=_ok_executor, complete=_recording_complete(models),
        max_steps=12,
    )
    # Once switched, it never flips back to cheap.
    first_strong = models.index("strong")
    assert all(m == "strong" for m in models[first_strong:])


@pytest.mark.asyncio
async def test_no_escalation_when_escalate_model_none_is_byte_identical():
    models: list[str] = []
    await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="u",
        model="cheap", escalate_model=None,
        execute=_ok_executor, complete=_recording_complete(models),
        max_steps=8,
    )
    assert all(m == "cheap" for m in models)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_agent_escalation.py -v`
Expected: FAIL — `run_agent_build() got an unexpected keyword argument 'escalate_model'`

- [ ] **Step 3: Add the `escalate_model` parameter**

In `run_agent_build` signature (L157-169), add the parameter after `model`:

```python
async def run_agent_build(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    execute: Executor,
    escalate_model: str | None = None,
    max_steps: int = 12,
    emit: Emit | None = None,
    complete: Callable[..., Awaitable[str]] | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    max_tokens: int = 8192,
) -> AgentResult:
```

- [ ] **Step 4: Track the active model + add an escalation helper**

Right after the loop-state init (after `sig_seen: dict[str, int] = {}`, ≈L187) add:

```python
    active_model = model
    escalated = False

    async def _escalate(step: int, reason: str) -> None:
        """First nudge of any kind → upgrade to the stronger model, once."""
        nonlocal active_model, escalated
        if escalate_model and not escalated:
            active_model = escalate_model
            escalated = True
            print(
                f"[AGENT] step={step} ESCALATE → {escalate_model} (reason={reason})",
                flush=True,
            )
            if emit:
                await emit(
                    "agent.escalate",
                    {"step": step, "to": escalate_model, "reason": reason},
                )
```

- [ ] **Step 5: Use the active model on the gateway call**

In the per-step retry block (≈L205), change `model` to `active_model`:

```python
                reply = await complete(
                    call_msgs,
                    active_model,
                    user_id=user_id,
                    project_id=project_id,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
```

- [ ] **Step 6: Call `_escalate` at each of the three nudge sites**

(a) In the global-cycle nudge branch — inside `if sig_seen[sig] >= _REPEAT_NUDGE_AT:` (≈L281), as the first statement of the branch:

```python
            if sig_seen[sig] >= _REPEAT_NUDGE_AT:
                await _escalate(step, "cycle")
                print(
```

(b) In the consecutive-repeat nudge branch — inside `if repeat_count >= 2:` (≈L301), as the first statement:

```python
        if repeat_count >= 2:
            await _escalate(step, "repeat")
            print(
```

(c) In the no-write nudge branch — inside `if no_write_streak >= _NO_WRITE_NUDGE_AT:` (≈L332), as the first statement:

```python
            if no_write_streak >= _NO_WRITE_NUDGE_AT:
                await _escalate(step, "explore")
                print(
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_agent_escalation.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Run the full agent test suite (no regression)**

Run: `cd apps/api && uv run pytest tests/test_agent_builder.py tests/test_agent_loop_cycle.py tests/test_agent_message.py tests/test_agent_escalation.py -v`
Expected: all PASS (existing 25 + new 3)

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/omnia_api/services/agent_builder.py apps/api/tests/test_agent_escalation.py
git commit -m "feat(agent): escalate to stronger model on first stall-nudge (kills cheap-model loop degeneration)"
```

---

### Task 3: Wire the escalation model at the call site

**Files:**
- Modify: `apps/api/src/omnia_api/routers/messages.py` (≈L2364-2374, the single `run_agent_build` call)

- [ ] **Step 1: Resolve and pass the escalation model**

Where `_agent_model` is resolved (≈L2366), add the escalation model right after it, and pass it into the call (≈L2367-2374):

```python
            _agent_model = model_for_role("agent", override=force_model)
            # Cheap default; the loop upgrades to this stronger model the first
            # time a stall-guard nudges (cycle / repeat / no-write) — smart only
            # when stuck, so cost stays bounded.
            _escalate_model = model_for_role("agent_escalation", override=force_model)
            _agent_res = await agent_builder.run_agent_build(
                system_prompt=_agent_system,
                user_prompt=_agent_user,
                model=_agent_model,
                escalate_model=_escalate_model,
                execute=_agent_executor,
                max_steps=_agent_steps,
                emit=_agent_emit,
                user_id=str(user_id),
```

(Leave the remaining args of the call — `project_id`, etc. — unchanged.)

- [ ] **Step 2: Typecheck / import-check the module**

Run: `cd apps/api && uv run python -c "import omnia_api.routers.messages"`
Expected: no error (imports cleanly; `model_for_role` is already imported in this module).

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/omnia_api/routers/messages.py
git commit -m "feat(agent): pass agent_escalation model into the build loop"
```

---

### Task 4: Full verification before delivery

- [ ] **Step 1: Run the whole api test suite**

Run: `cd apps/api && uv run pytest -q`
Expected: all green (no regressions).

- [ ] **Step 2: Lint the changed files (if the project lints)**

Run: `cd apps/api && uv run ruff check src/omnia_api/services/agent_builder.py src/omnia_api/core/config.py src/omnia_api/routers/messages.py`
Expected: no errors (fix any reported).

---

### Task 5: Deliver to prod (per project CLAUDE.md delivery rule)

> This is a runtime change to `apps/api` → full delivery cycle. **Work on `origin/main`, not the stale local tree** (local was 318 commits behind). Implement in the `C:/omnia-research` worktree (detached at origin/main) on a short-lived branch, or fast-forward a fresh worktree.

- [ ] **Step 1:** Ensure the three commits sit on top of `origin/main`; push (`git push` — PAT may be required, see memory `omnia-ssh-deploy-alias`). If push is rejected for permissions, STOP and tell the owner (do not silently skip).
- [ ] **Step 2:** Deploy api+worker: `ssh lh-server 'cd /opt/omnia && git fetch && git merge --ff-only origin/main && cd apps/llm-gateway/deploy/full && docker compose -p full up -d --build api worker'`
- [ ] **Step 3:** Health-check: confirm api container Up + public 200; confirm `model_for_role('agent_escalation')` resolves in the running container.
- [ ] **Step 4:** Live smoke: drive one container-app build that historically looped; confirm an `agent.escalate` log/event fires on the first nudge and the build reaches `done` (or at least no longer aborts `looping`/`exploring`).

---

## Self-Review

- **Spec coverage:** P0(a) "escalate agent off cheap model on stall" → Tasks 1-3. Backward-compat (escalate_model=None) → Task 2 Step 1 third test. Delivery to live prod → Task 5. P0(b) gateway/GigaChat → explicitly out of scope (separate plan). ✓
- **Placeholders:** none — every code step shows the actual code/command + expected output. ✓
- **Type consistency:** `escalate_model: str | None`, role key `"agent_escalation"`, helper `_escalate(step, reason)`, event `"agent.escalate"` — used identically across Tasks 1-3 and the tests. ✓
