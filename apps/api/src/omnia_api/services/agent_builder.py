"""Agentic container-app builder — Phase 0 of the "like Claude Code" engine.

Today Omnia is a one-shot text generator: the model emits one blob, server
regex parses it into files, no feedback loop. This module is the opposite — a
real **agent loop**:

    plan -> act (a tool) -> observe the REAL result -> repeat -> verify -> done

It is a *text-protocol* ReAct loop, NOT native function-calling: the model
replies with reasoning followed by exactly ONE action in a strict
``<omnia:action name="...">{json}</omnia:action>`` block. The server parses it,
executes it against the live dev container, and feeds the real observation back
as the next user turn. This works with ANY gateway model (DeepSeek/Kimi/…) and
reuses Omnia's existing strength at parsing structured model output — no
dependency on the provider supporting OpenAI tool-calls.

Design rules that keep it safe to ship:
  * The EXECUTOR is injected (`execute` callable) so the loop is fully
    unit-testable with a fake — no container needed in tests.
  * Pure engine here; the production executor that talks to the orchestrator is
    `make_container_executor(...)` at the bottom.
  * Bounded: `max_steps` hard cap, per-action output truncation. No unbounded
    grind.
  * Gated by ``Settings.use_agentic_builder`` (default False) at the call site —
    when off, this module is never entered and current generation is untouched.

Actions in Phase 0 (file tools + a real build observation):
    list_dir   {"path": "src/app"}
    read_file  {"path": "src/app/page.tsx"}
    grep       {"pattern": "useState", "path": "src"}
    write_file {"path": "...", "content": "...full file..."}
    edit_file  {"path": "...", "search": "...", "replace": "..."}
    build      {}                      # real typecheck/compile observation
    done       {"summary": "what I built"}

Phase 1 adds {"name": "bash"} on the same loop; Phase 3 adds {"name": "test"}.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from omnia_api.services import llm_client

# ── Action protocol ────────────────────────────────────────────────────────

# The model is taught to emit exactly one of these per turn. We parse the LAST
# block in the reply (the model may "think" in prose first, then act).
_ACTION_RE = re.compile(
    r"<omnia:action\s+name=[\"']([a-z_]+)[\"']\s*>\s*(.*?)\s*</omnia:action>",
    re.DOTALL | re.IGNORECASE,
)

_KNOWN_ACTIONS = frozenset(
    {"list_dir", "read_file", "grep", "write_file", "edit_file", "build", "bash", "done"}
)

# Caps so one fat observation can't blow the context window.
_MAX_OBS_CHARS = 6_000
_MAX_READ_CHARS = 16_000


@dataclass
class Action:
    name: str
    args: dict[str, Any]
    raw: str = ""

    @property
    def path(self) -> str:
        p = self.args.get("path")
        return p if isinstance(p, str) else ""


@dataclass
class AgentResult:
    done: bool
    summary: str
    files: dict[str, str]          # path -> final content the agent wrote
    steps: int
    transcript: list[dict[str, str]] = field(default_factory=list)
    stop_reason: str = ""          # "done" | "max_steps" | "stalled" | "error"


def parse_action(reply: str) -> Action | None:
    """Pull the LAST well-formed <omnia:action> out of a model reply.

    Tolerant: the body may be fenced in ``` or be bare JSON; an unknown action
    name or unparseable JSON returns None so the caller can nudge and retry
    rather than crash.
    """
    matches = list(_ACTION_RE.finditer(reply or ""))
    if not matches:
        return None
    m = matches[-1]
    name = m.group(1).strip().lower()
    if name not in _KNOWN_ACTIONS:
        return None
    body = m.group(2).strip()
    # strip a ```json fence if the model wrapped the body
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    if not body:
        args: dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(body)
            args = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            # `done` and `build` legitimately carry no/loose body — accept them.
            if name in ("done", "build"):
                args = {"summary": body[:500]} if name == "done" else {}
            else:
                return None
    return Action(name=name, args=args, raw=m.group(0))


def _truncate(s: str, n: int) -> str:
    if not isinstance(s, str):
        s = str(s)
    return s if len(s) <= n else s[:n] + f"\n…[truncated {len(s) - n} chars]"


# Keep the per-call payload bounded: always the system prompt + the first user
# turn (task + seeded project context) + the most recent `keep_last` turns. The
# loop still holds the full transcript for its own logic; only the MODEL CALL is
# windowed. This is what keeps a 30-step loop at roughly one-step cost.
def _window_messages(
    convo: list[dict[str, Any]], keep_last: int = 8
) -> list[dict[str, Any]]:
    head = 2  # system + first user (orientation + seeded layout)
    if len(convo) <= head + keep_last:
        return convo
    return convo[:head] + convo[-keep_last:]


def _format_observation(action: Action, obs: dict[str, Any]) -> str:
    """Render an executor result as the next user turn the model reads."""
    ok = obs.get("ok", True)
    head = f"[observation: {action.name}{(' ' + action.path) if action.path else ''} "
    head += "OK]" if ok else "FAILED]"
    body = obs.get("detail") or obs.get("content") or obs.get("error") or ""
    return f"{head}\n{_truncate(body, _MAX_OBS_CHARS)}"


# Executor contract: an async callable Action -> {ok: bool, detail/content/error}
Executor = Callable[[Action], Awaitable[dict[str, Any]]]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


async def run_agent_build(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    execute: Executor,
    max_steps: int = 12,
    emit: Emit | None = None,
    complete: Callable[..., Awaitable[str]] | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    max_tokens: int = 8192,
) -> AgentResult:
    """Drive the plan→act→observe loop until the model says done or budget hits.

    `execute` runs an action against the world (container) and returns an
    observation dict. `complete` defaults to the real gateway call but is
    injectable for tests. Returns every file the agent successfully wrote so the
    caller can commit them to git via the existing pipeline.
    """
    complete = complete or llm_client.complete_chat
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    written: dict[str, str] = {}
    stalls = 0
    last_sig = ""
    repeat_count = 0

    for step in range(max_steps):
        # Retry the model call on transient gateway errors (ReadTimeout / 5xx /
        # rate-limit). A single hiccup over a 30-step loop must NOT throw away all
        # the work done so far — without this, one opus timeout aborts the build.
        reply = None
        last_exc: Exception | None = None
        # COST: vsegpt bills by characters, so resending the full growing
        # transcript every step makes a long loop cost balloon. Send only a
        # sliding window (system + the seed/task + the last N turns) → per-step
        # payload stays ~constant regardless of step count → "same cost".
        call_msgs = _window_messages(convo)
        # 429-resilience: vsegpt enforces ~1 req/sec globally; under concurrent
        # prod traffic a step can 429 for several seconds. 5 attempts w/ growing
        # backoff (4/8/12/16/20s) ride it out instead of aborting the build.
        for attempt in range(5):
            try:
                reply = await complete(
                    call_msgs,
                    model,
                    user_id=user_id,
                    project_id=project_id,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                break
            except Exception as exc:  # noqa: BLE001 — fail-soft + retry
                last_exc = exc
                if emit:
                    await emit("agent.retry", {"step": step, "attempt": attempt})
                await asyncio.sleep(4.0 * (attempt + 1))
        if reply is None:
            return AgentResult(
                done=False,
                summary=f"gateway error after retries: {last_exc}",
                files=written, steps=step, transcript=convo, stop_reason="error",
            )

        convo.append({"role": "assistant", "content": reply})
        action = parse_action(reply)

        if action is None:
            stalls += 1
            if emit:
                await emit("agent.stalled", {"step": step})
            if stalls >= 2:
                return AgentResult(
                    done=False, summary="model emitted no valid action twice",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="stalled",
                )
            convo.append({"role": "user", "content": _NO_ACTION_NUDGE})
            continue
        stalls = 0

        if emit:
            await emit("agent.step", {
                "step": step, "action": action.name, "path": action.path,
            })

        if action.name == "done":
            return AgentResult(
                done=True, summary=str(action.args.get("summary", "")),
                files=written, steps=step + 1, transcript=convo,
                stop_reason="done",
            )

        # Circuit breaker: the model sometimes gets stuck re-issuing the SAME
        # action (observed live: 34x identical grep → max_steps, no progress).
        # Detect consecutive identical actions → nudge to move on → then abort.
        sig = (
            f"{action.name}|{action.path}|"
            f"{json.dumps(action.args, sort_keys=True, ensure_ascii=False)}"
        )
        if sig == last_sig:
            repeat_count += 1
        else:
            repeat_count, last_sig = 0, sig
        if repeat_count >= 5:
            return AgentResult(
                done=False,
                summary=f"stuck repeating {action.name} {action.path}",
                files=written, steps=step + 1, transcript=convo,
                stop_reason="looping",
            )
        if repeat_count >= 2:
            print(
                f"[AGENT] step={step} REPEAT x{repeat_count} {action.name} {action.path}",
                flush=True,
            )
            convo.append({"role": "user", "content": (
                f"STOP — you ran this EXACT action {repeat_count + 1} times with the "
                "same result. Do NOT repeat it. You have enough context: WRITE the "
                "next file now (a dashboard page renders <CrudResource entity=\"...\"/>; "
                "also write dashboard/page.tsx), or run build, or call done."
            )})
            continue

        obs = await execute(action)
        print(
            f"[AGENT] step={step} {action.name} {action.path} ok={obs.get('ok')}",
            flush=True,
        )
        # Track files the agent actually committed to the container.
        if action.name in ("write_file", "edit_file") and obs.get("ok"):
            if "content" in obs and isinstance(obs["content"], str):
                written[action.path] = obs["content"]
        convo.append({"role": "user", "content": _format_observation(action, obs)})

    return AgentResult(
        done=False, summary="hit step budget without calling done",
        files=written, steps=max_steps, transcript=convo, stop_reason="max_steps",
    )


_NO_ACTION_NUDGE = (
    "Your reply contained no valid <omnia:action> block. Respond with brief "
    "reasoning, then EXACTLY ONE action block, e.g.:\n"
    '<omnia:action name="read_file">{"path": "src/app/page.tsx"}</omnia:action>\n'
    "When the app is complete and the last build was clean, call "
    '<omnia:action name="done">{"summary": "..."}</omnia:action>.'
)


SYSTEM_PROMPT = """You are an autonomous full-stack engineer building a real \
Next.js app inside a live container, working like a developer: make changes, \
run the build, read the REAL errors, fix them — until the build is clean. You \
take ONE action at a time and observe its result before the next.

PROTOCOL — every reply: ONE short sentence of reasoning, then EXACTLY ONE action block:
<omnia:action name="ACTION">{json args}</omnia:action>

ACTIONS:
- list_dir   {"path": "src/app"}
- read_file  {"path": "src/app/page.tsx"}
- grep       {"pattern": "regex", "path": "src"}
- write_file {"path": "...", "content": "FULL FILE CONTENT"}   — create/overwrite a whole file
- edit_file  {"path": "...", "search": "EXACT TEXT", "replace": "NEW TEXT"}
- build      {}                                — real typecheck; returns the actual errors
- bash       {"cmd": "npm run lint"}           — run a shell command in the container (lint/test/install)
- done       {"summary": "what you built"}     — ONLY after a clean build

THIS TEMPLATE (nextjs-entities) — already built for you, DO NOT rebuild or read its internals:
- A fixed ENTITY ENGINE turns JSON schemas into full CRUD+REST+auth+RBAC. You do NOT write \
backend/API/db code. To add data, write `entities/<Name>.json`:
    {"name":"Client","label":"Клиент","labelPlural":"Клиенты",
     "fields":[{"name":"name","label":"Имя","type":"string","required":true},
               {"name":"phone","label":"Телефон","type":"string"},
               {"name":"car","label":"Авто","type":"string"}],
     "access":"admin"}
  field type ∈ {string,text,number,boolean,date,datetime,time,enum,reference}; \
for enum add "options":[...]; for reference add "ref":"<OtherEntity>". \
access ∈ {owner (per-user private), public (open read), admin (back-office)}. \
Back-office CRM data → "admin".
- SCREENS: write pages under `src/app/(app)/dashboard/`. For each entity, a page that renders \
`<CrudResource entity="Name" />` (from `@/components/omnia`) gives a full list+create+edit screen \
out of the box — read `src/components/omnia/crud-resource.tsx` ONCE to confirm its exact props, \
then write ALL the pages quickly. Pass ONLY `entity` (and an optional title) to \
<CrudResource> — it DERIVES the table columns + create/edit form from the entity schema, so \
values render automatically. Do NOT hand-build a table or pass custom column configs (that is \
what makes cells show "—"). Data SDK is `@/lib/sdk`, UI is `@/components/ui`, icons \
`lucide-react`. Auth/login, the dashboard shell, global CSS and the kit already exist — don't recreate them.
- ALWAYS write `src/app/(app)/dashboard/page.tsx` — the dashboard HOME (a short index with a \
card/link to each section). Without it, `/dashboard` is a 404 right after login. This is mandatory.

WORK STYLE (you have a LIMITED step budget — be decisive):
- Explore MINIMALLY: at most read ONE existing dashboard page + ONE existing entities/*.json as \
examples if present (use list_dir on `src/app/(app)/dashboard` and `entities`). Do NOT read the \
engine, registry, sdk or every ui component — they are fixed and correct.
- Then WRITE: declare every entity the user asked for, then write the screens. Spend most steps WRITING, not reading.
- After your files are in, run `build` ONCE, fix any real errors, then `done`.
- Never repeat an identical read. Never ask the user questions — decide and act. One action per reply."""


# ── Production executor (talks to the orchestrator) ─────────────────────────

def make_container_executor(
    *,
    project_id: Any,
    slug: str,
) -> Executor:
    """Bind the abstract actions to the live dev container via orchestrator_client.

    Imported lazily so the pure engine + its tests carry no orchestrator/httpx
    dependency. Each branch returns the observation dict the loop feeds back.
    """
    from omnia_api.services import orchestrator_client

    async def _execute(action: Action) -> dict[str, Any]:
        try:
            if action.name == "list_dir":
                detail = await orchestrator_client.agent_list_dir(
                    project_id, slug, action.path or ".")
                return {"ok": True, "detail": detail}

            if action.name == "read_file":
                content = await orchestrator_client.agent_read_file(
                    project_id, slug, action.path)
                if content is None:
                    return {"ok": False, "error": f"not found: {action.path}"}
                return {"ok": True, "content": _truncate(content, _MAX_READ_CHARS)}

            if action.name == "grep":
                detail = await orchestrator_client.agent_grep(
                    project_id, slug,
                    pattern=str(action.args.get("pattern", "")),
                    path=action.path or "src")
                return {"ok": True, "detail": detail}

            if action.name == "write_file":
                content = action.args.get("content")
                if not isinstance(content, str) or not action.path:
                    return {"ok": False, "error": "write_file needs path + content"}
                await orchestrator_client.hot_reload(
                    project_id=project_id, slug=slug, files={action.path: content})
                return {"ok": True, "content": content,
                        "detail": f"wrote {action.path} ({len(content)} bytes)"}

            if action.name == "edit_file":
                search = action.args.get("search")
                replace = action.args.get("replace")
                if not action.path or not isinstance(search, str) or replace is None:
                    return {"ok": False, "error": "edit_file needs path, search, replace"}
                current = await orchestrator_client.agent_read_file(
                    project_id, slug, action.path)
                if current is None:
                    return {"ok": False, "error": f"not found: {action.path}"}
                if search not in current:
                    return {"ok": False,
                            "error": "search text not found exactly; read the file and copy it byte-for-byte"}
                if current.count(search) > 1:
                    return {"ok": False,
                            "error": "search text is not unique; add surrounding lines"}
                new_content = current.replace(search, str(replace), 1)
                await orchestrator_client.hot_reload(
                    project_id=project_id, slug=slug, files={action.path: new_content})
                return {"ok": True, "content": new_content,
                        "detail": f"patched {action.path}"}

            if action.name == "build":
                res = await orchestrator_client.agent_build(project_id, slug)
                ok = bool(res.get("ok"))
                detail = res.get("detail") or res.get("error") or "build clean"
                return {"ok": ok, "detail": detail}

            if action.name == "bash":
                cmd = action.args.get("cmd")
                if not isinstance(cmd, str) or not cmd.strip():
                    return {"ok": False, "error": "bash needs a non-empty cmd string"}
                res = await orchestrator_client.agent_exec(project_id, slug, cmd)
                return {"ok": bool(res.get("ok")),
                        "detail": res.get("detail") or "(no output)"}

            return {"ok": False, "error": f"unknown action {action.name}"}
        except Exception as exc:  # never let an executor crash kill the loop
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return _execute
