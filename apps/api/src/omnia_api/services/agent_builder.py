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

Actions (file tools + real build/runtime observations):
    list_dir     {"path": "src/app"}
    read_file    {"path": "src/app/page.tsx"}
    grep         {"pattern": "useState", "path": "src"}
    write_file   {"path": "...", "content": "...full file..."}
    edit_file    {"path": "...", "search": "...", "replace": "..."}
    build        {}                      # real typecheck/compile observation
    bash         {"cmd": "pnpm test"}    # arbitrary shell in the container
    read_logs    {}                      # live dev-server stdout/stderr (runtime errors)
    runtime_check{"path": "/"}           # hit a route, get the REAL HTTP status / crash file
    see          {"path": "/"}           # screenshot the live page → vision-model design critique
    done         {"summary": "what I built"}

`read_logs` + `runtime_check` + `see` give the loop EYES on the running app:
`build` proves it typechecks, `runtime_check`/`read_logs` prove it actually
renders, and `see` shows what it LOOKS like (vision judge → concrete design
fixes) — closing the gap between "compiles", "works" and "good-looking" (the
prototype-vs-real-product line).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
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
    {"list_dir", "read_file", "grep", "write_file", "edit_file", "build", "bash",
     "read_logs", "runtime_check", "see", "done"}
)

# Idempotent "observe the world after acting" actions. Re-running them across a
# build→fix loop is legitimate progress, NOT a cycle: a clean way to verify the
# last edit. They are therefore EXEMPT from the global non-consecutive repeat
# guard (which exists to catch repeated identical WRITES or read/grep/list
# exploration spinning). The consecutive-repeat guard (back-to-back spamming)
# and the no-write streak still bound them, so a model that does nothing but
# `build`/`runtime_check` in a row is still stopped.
_VERIFY_ACTIONS = frozenset({"build", "read_logs", "runtime_check", "see"})

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
    convo: list[dict[str, Any]], keep_last: int = 12
) -> list[dict[str, Any]]:
    head = 2  # system + first user (orientation + seeded layout)
    if len(convo) <= head + keep_last:
        return convo
    return convo[:head] + convo[-keep_last:]


def _progress_note(written: dict[str, str], last_build_ok: bool | None) -> str:
    """A compact live-state reminder injected into the system slot of EVERY model
    call. The sliding window (keep_last) drops the middle of a long build, so the
    model forgets which files it already wrote and re-writes them on a loop (the
    #1 cause of the cycle/exploring aborts). Telling it the current state every
    turn — what exists, whether the last build was clean — kills that amnesia.
    """
    parts: list[str] = []
    if written:
        listing = "\n".join(f"  - {p}" for p in sorted(written))
        parts.append(
            "FILES YOU HAVE ALREADY WRITTEN this run — they EXIST. Do NOT write "
            "them again with the same content. Only touch one with edit_file if "
            "you must FIX a specific build error in it:\n" + listing
        )
    if last_build_ok is True:
        parts.append(
            "LAST build: CLEAN. If every file the task needs now exists, call "
            "done — do not keep re-writing existing files."
        )
    elif last_build_ok is False:
        parts.append(
            "LAST build: FAILED — read the reported error, fix the named file "
            "with edit_file, then build again. Do not blindly re-write."
        )
    if not parts:
        return ""
    return "\n\n[PROGRESS — current container state]\n" + "\n".join(parts)


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
    escalate_model: str | None = None,
    max_steps: int = 12,
    emit: Emit | None = None,
    complete: Callable[..., Awaitable[str]] | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    max_tokens: int = 8192,
    require_green_before_done: bool = False,
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
    no_write_streak = 0  # consecutive actions that wrote nothing (cycle breaker)
    sig_seen: dict[str, int] = {}  # global repeat count per action (cycle breaker)
    last_build_ok: bool | None = None  # result of the most recent `build` action
    # `require_green_before_done` bookkeeping: a `done` is only honoured once the
    # last build was clean AND the running app was re-checked after the last write
    # (a clean typecheck is exactly what a model hallucinates completion around —
    # see SYSTEM_PROMPT). Bounded by `_DONE_REJECT_CAP` so it nudges, never hangs.
    last_runtime_ok: bool | None = None  # result of the most recent `runtime_check`
    wrote_since_check = False  # a write happened with no runtime_check after it
    done_rejections = 0
    active_model = model
    escalated = False

    async def _escalate(step: int, reason: str) -> None:
        """First stall-nudge of any kind → upgrade to the stronger model, once.

        Cheap model by default; the moment a guard signals the loop is stuck
        (cycle / repeat / no-write) we switch to a stronger reasoning model for
        the rest of the run. The existing abort guards still bound the run, so
        this stays a handful of strong-model steps, not a full strong-model
        build (which on a char-billed 1-req/sec gateway blew cost + reliability).
        """
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
        # Inject the live state (files already written + last build result) into
        # the system slot so the windowed model never forgets what it has done →
        # stops re-writing existing files (the root cycle/exploring cause).
        _note = _progress_note(written, last_build_ok)
        if _note and call_msgs:
            call_msgs = [
                {"role": call_msgs[0]["role"],
                 "content": call_msgs[0]["content"] + _note},
                *call_msgs[1:],
            ]
        # 429-resilience: vsegpt enforces ~1 req/sec globally; under concurrent
        # prod traffic a step can 429 for several seconds. 5 attempts w/ growing
        # backoff (4/8/12/16/20s) ride it out instead of aborting the build.
        for attempt in range(5):
            try:
                reply = await complete(
                    call_msgs,
                    active_model,
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
            # Green-gate: refuse a premature `done`. The model loves to declare
            # victory on a clean typecheck without ever opening the route, so a
            # broken-at-runtime app ships. Require last build clean + a
            # runtime_check AFTER the last write. Bounded by _DONE_REJECT_CAP so
            # a genuinely-unverifiable build (e.g. no reachable route) still
            # finishes instead of looping (R-10 fail-soft). Default OFF.
            if require_green_before_done and done_rejections < _DONE_REJECT_CAP:
                gap = None
                if last_build_ok is not True:
                    gap = (
                        "run `build` and fix errors until it is CLEAN before done"
                    )
                elif wrote_since_check or last_runtime_ok is not True:
                    gap = (
                        "you wrote files but did not confirm they RUN — "
                        'runtime_check the main route(s) (e.g. {"path":"/"}), '
                        "fix any 5xx, THEN done"
                    )
                if gap is not None:
                    done_rejections += 1
                    if emit:
                        await emit("agent.stalled", {"step": step})
                    convo.append({"role": "user", "content": (
                        "NOT DONE YET — " + gap + "."
                    )})
                    continue
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
        # GLOBAL repeat guard (non-consecutive cycle). A multi-step loop that
        # re-issues the SAME action — INCLUDING re-WRITING a file with identical
        # content (observed live: the same 5 entities, then the same 2 dashboard
        # pages + build, on a loop) — is missed by both the consecutive check below
        # (steps differ within the cycle) and the no-write streak (a write resets
        # it). Count every exact signature across the whole run: an exact repeat is
        # never progress, so nudge to MOVE ON, then abort as looping.
        # Only NON-consecutive occurrences count here — back-to-back repeats are the
        # job of the `repeat_count` check below; this guard is for a multi-step CYCLE
        # that returns to the same action (a,b,c,a,b,c… / re-writing the same file).
        # Idempotent verify actions (build / read_logs / runtime_check) are exempt:
        # re-observing the app after a fix is progress, not a cycle — caging them
        # here is what falsely aborted long build→fix→build loops as "looping".
        if sig != last_sig and action.name not in _VERIFY_ACTIONS:
            sig_seen[sig] = sig_seen.get(sig, 0) + 1
            if sig_seen[sig] >= _REPEAT_ABORT_AT:
                return AgentResult(
                    done=False,
                    summary=f"stuck re-issuing {action.name} {action.path}",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="looping",
                )
            if sig_seen[sig] >= _REPEAT_NUDGE_AT:
                await _escalate(step, "cycle")
                print(
                    f"[AGENT] step={step} CYCLE x{sig_seen[sig]} {action.name} {action.path}",
                    flush=True,
                )
                if emit:
                    await emit("agent.stalled", {"step": step})
                convo.append({"role": "user", "content": _REPEAT_CYCLE_NUDGE})
                continue
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
            await _escalate(step, "repeat")
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

        # Cycle breaker: the consecutive-identical check above MISSES a multi-step
        # read loop (observed live: read→grep→list→read→read repeating for the whole
        # 80-step budget, 0 files written) because each step differs from the last,
        # so `repeat_count` keeps resetting. Track a no-WRITE streak instead: too
        # many actions in a row that produce no file means the model is exploring,
        # not building. Nudge HARD to write; if it still won't, abort early (with a
        # distinct stop_reason) rather than burning the whole budget reading.
        if action.name in ("write_file", "edit_file"):
            no_write_streak = 0
        else:
            no_write_streak += 1
            if no_write_streak >= _NO_WRITE_ABORT_AT:
                return AgentResult(
                    done=False,
                    summary="stuck exploring (reading) without writing any file",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="exploring",
                )
            if no_write_streak >= _NO_WRITE_NUDGE_AT:
                await _escalate(step, "explore")
                print(
                    f"[AGENT] step={step} EXPLORE-STALL x{no_write_streak} "
                    f"({action.name}) → nudge WRITE",
                    flush=True,
                )
                if emit:
                    await emit("agent.stalled", {"step": step})
                convo.append({"role": "user", "content": _EXPLORE_STALL_NUDGE})
                continue  # don't execute another read — force a write next

        obs = await execute(action)
        if action.name == "build":
            last_build_ok = bool(obs.get("ok"))
        if action.name == "runtime_check":
            # A runtime_check observed the CURRENT app state → clears the
            # "wrote but never verified" debt; its ok/fail feeds the green-gate.
            last_runtime_ok = bool(obs.get("ok"))
            wrote_since_check = False
        print(
            f"[AGENT] step={step} {action.name} {action.path} ok={obs.get('ok')}",
            flush=True,
        )
        # Track files the agent actually committed to the container.
        if action.name in ("write_file", "edit_file") and obs.get("ok"):
            if "content" in obs and isinstance(obs["content"], str):
                written[action.path] = obs["content"]
            wrote_since_check = True  # a new write is unverified until re-checked
        convo.append({"role": "user", "content": _format_observation(action, obs)})

    return AgentResult(
        done=False, summary="hit step budget without calling done",
        files=written, steps=max_steps, transcript=convo, stop_reason="max_steps",
    )


# Cycle breaker thresholds (no-WRITE streak): after this many consecutive actions
# that write nothing (read_file/grep/list_dir/build), nudge HARD to write; after the
# abort threshold, give up exploring rather than burn the whole step budget. A real
# build writes within a few reads (the seed context is handed up-front), so a long
# read-only streak is always a stall, never legitimate orientation.
_NO_WRITE_NUDGE_AT = 5
_NO_WRITE_ABORT_AT = 14

# Global (non-consecutive) repeat guard: the SAME exact action issued this many
# times in one run is a cycle, not progress (re-writing identical content counts).
# An exact repeat never advances the build, so nudge to move on, then abort.
_REPEAT_NUDGE_AT = 2
_REPEAT_ABORT_AT = 4

# Green-gate: how many premature `done`s to reject (nudging the model to build +
# runtime_check) before honouring it anyway. Bounded so an app with no checkable
# route can still finish — the server-side acceptance gate is the hard backstop.
_DONE_REJECT_CAP = 2
_REPEAT_CYCLE_NUDGE = (
    "STOP — you have ALREADY issued this EXACT action before (same file + same "
    "content, or the same command); repeating it changes NOTHING and the build is "
    "not advancing. The file is already written. Move to the NEXT unfinished step: "
    "write a still-MISSING page/file, fix the specific build error you were shown, "
    "or — if the build is clean and every screen exists — call done. Never re-write "
    "a file with the same content you already wrote."
)

_EXPLORE_STALL_NUDGE = (
    "STOP READING. You have already read the entity JSON, the CrudResource "
    "component and use-entity — that is ENOUGH context. Do NOT read_file / grep / "
    "list_dir again. Your VERY NEXT action MUST be write_file: create the next "
    "missing page now — an entity page is \"use client\" and renders "
    "<CrudResource entity=\"Name\" .../>; the dashboard index is "
    "src/app/(app)/dashboard/page.tsx. When every page exists, run build; when the "
    "build is clean, call done. Writing a file is the ONLY way to make progress."
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
- read_logs  {}                                — live dev-server stdout/stderr (find RUNTIME crashes build can't see)
- runtime_check {"path": "/dashboard"}         — open a real route, get the REAL HTTP status + crash file
- see        {"path": "/dashboard"}            — LOOK at the rendered page (screenshot → design critique); fix the issues it returns
- done       {"summary": "what you built"}     — ONLY after a clean build, the main route renders, AND `see` is happy

THIS TEMPLATE (nextjs-entities) — already built for you, DO NOT rebuild or read its internals:
- A fixed ENTITY ENGINE turns JSON schemas into full CRUD+REST+auth+RBAC. You do NOT write \
backend/API/db code for PLAIN data — declare entities instead. But you MAY author \
CUSTOM server logic (a server action, or a route under src/app/api/custom/**) for \
real workflows BEYOND crud — reaching data ONLY through the SDK (@/lib/sdk) or the \
engine (@/lib/entities/engine), which enforce auth+ownership+membership. NEVER import \
@/lib/db, drizzle-orm or pg in your own files (that bypasses the access model and is \
rejected before ship). To add data, write `entities/<Name>.json`:
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
- Write ONE page per entity YOU declared in entities/*.json (at \
`src/app/(app)/dashboard/<entity>/page.tsx`). The template's example Task/Product are \
NOT the user's data — build pages for the entities the USER asked for, then remove or \
ignore the examples; do not loop building a `tasks` page for an app that has none.
- After your files are in, run `build` ONCE. ON A FAILED BUILD: the observation shows \
the EXACT file + error — make a TARGETED fix to THAT file/line (common causes: a custom \
column/prop passed to <CrudResource> — pass ONLY `entity` + optional title; a wrong \
import path; or an entity field type the engine rejects). NEVER re-issue write_file with \
the SAME content — an identical re-write fixes NOTHING; read the error and change exactly \
what it points at. Repeat build→fix until clean. THEN verify it actually RUNS: \
`runtime_check {"path":"/dashboard"}` — a typecheck-clean app can still 5xx on render. If it \
fails, `read_logs` to see the real runtime error, fix the named file, re-check. THEN `see` the \
main route: the vision judge returns CONCRETE design fixes (hero too small, 3 identical cards, \
weak contrast) — apply them so the page is not just working but genuinely good-looking. Call \
`done` ONLY after the build is clean, the route renders, AND `see` has no blocking issues.
- Never repeat an identical read OR an identical write. Never ask the user questions — \
decide and act. One action per reply."""


EDIT_SYSTEM_PROMPT = """You are editing an EXISTING, working Next.js app inside a \
live container. Make ONLY the change the user asks — do NOT rebuild the app or \
touch unrelated files. Work like a developer: find the right file, read it, make \
the minimal edit, run the build, fix any error, then done.

PROTOCOL — every reply: ONE short sentence of reasoning, then EXACTLY ONE action block:
<omnia:action name="ACTION">{json args}</omnia:action>

ACTIONS:
- grep       {"pattern": "text", "path": "src"}   — locate where to change
- read_file  {"path": "..."}                       — read before editing (mandatory)
- edit_file  {"path": "...", "search": "EXACT TEXT", "replace": "NEW TEXT"} — preferred: minimal patch
- write_file {"path": "...", "content": "FULL FILE"} — only when creating a new file
- list_dir   {"path": "..."}
- build      {}                                     — typecheck; fix real errors
- bash       {"cmd": "..."}                         — run a shell command if needed
- read_logs  {}                                     — live dev-server logs (runtime errors)
- runtime_check {"path": "/"}                        — open the changed route, confirm it still renders
- see        {"path": "/"}                           — LOOK at the changed page; fix any visual regression it reports
- done       {"summary": "what changed"}            — after a clean build (runtime_check + see the touched route first)

RULES:
- This is a SURGICAL EDIT. Change the minimum. Do NOT regenerate entities/pages \
that already work. The engine, auth, RBAC, kit and globals are fixed template \
files — never touch them. To add a data section, add `entities/<Name>.json` + a \
page that renders <CrudResource entity="Name"/>.
- grep/read to find the exact spot, prefer edit_file (search must be copied \
byte-for-byte from what you read), build, fix, done.
- Be fast and minimal — a small edit needs only a few steps. One action per reply."""


# ── Per-stack prompts (the loop builds on ANY stack, not just entities) ──────
# The hardcoded SYSTEM_PROMPT above is the entity-engine guide. To build a realtime
# app, a Vue app, an API, the agent needs the SAME ReAct protocol but the RIGHT
# stack knowledge (and the right safe PRIMITIVES — e.g. the realtime hub + members
# ACL). LOOP_PROTOCOL is the stack-agnostic protocol; build_system_prompt composes
# it with a per-stack guide loaded from that template's SYSTEM_PROMPT.md. This is
# what lets the model "use its full power" on any stack instead of being boxed into
# CRUD-over-entities.

_TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "orchestrator" / "templates"

LOOP_PROTOCOL = """You are an autonomous full-stack engineer building a REAL app \
inside a live container, working like a developer: make changes, run the build, \
read the REAL errors, fix them — until the build is clean and the app actually \
works. You take ONE action at a time and observe its result before the next.

PROTOCOL — every reply: ONE short sentence of reasoning, then EXACTLY ONE action block:
<omnia:action name="ACTION">{json args}</omnia:action>

ACTIONS:
- list_dir   {"path": "src/app"}
- read_file  {"path": "src/app/page.tsx"}
- grep       {"pattern": "regex", "path": "src"}
- write_file {"path": "...", "content": "FULL FILE CONTENT"}   — create/overwrite a whole file
- edit_file  {"path": "...", "search": "EXACT TEXT", "replace": "NEW TEXT"}
- build      {}                                — real typecheck; returns the actual errors
- bash       {"cmd": "pnpm test"}              — run a shell command (lint / test / install)
- read_logs  {}                                — live dev-server stdout/stderr (RUNTIME errors build can't see)
- runtime_check {"path": "/"}                  — open a real route, get the REAL HTTP status + crash file
- see        {"path": "/"}                     — LOOK at the rendered page (screenshot → design critique); fix what it reports
- done       {"summary": "what you built"}     — ONLY after a clean build, the app renders, AND `see` is happy

WORK STYLE: explore MINIMALLY, spend most steps WRITING, never repeat an identical \
read or write, never ask the user questions — decide and act. When you author tests, \
run them with bash. After the build is clean, `runtime_check` the main route(s) — a \
typecheck-clean app can still crash on render; if it 5xx, `read_logs`, fix, re-check. \
Then `see` the main route — the vision judge returns concrete design fixes; apply them \
so the result is good-looking, not just working. One action per reply."""


def build_system_prompt(stack_guide: str, skills: str | None = None) -> str:
    """Compose the agent system prompt for ANY stack: the shared loop protocol +
    the stack-specific guide (typically a template's SYSTEM_PROMPT.md). Same loop,
    right primitives — so the model can build a realtime app, a CRUD app or an API
    with equal fluency instead of being boxed into one shape.

    Optional ``skills`` (a stack's ``.omnia/skills`` content) is appended so the
    first draft already carries the security/a11y/perf canons the gates enforce —
    knowledge ALIGNED with enforcement. None/empty → unchanged (current behaviour).
    """
    parts = [LOOP_PROTOCOL, stack_guide.strip()]
    if skills and skills.strip():
        parts.append(skills.strip())
    return "\n\n".join(parts)


def load_stack_system_prompt(orch_template: str | None) -> str | None:
    """Read a stack's SYSTEM_PROMPT.md (the per-stack guide that documents its
    primitives + conventions), or None when absent. `orch_template` is the
    orchestrator directory name, e.g. 'nextjs-realtime'. Fail-soft."""
    if not orch_template:
        return None
    path = _TEMPLATES_DIR / orch_template / "SYSTEM_PROMPT.md"
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except Exception:
        return None


def load_stack_skills(orch_template: str | None) -> str | None:
    """Read a stack's ``.omnia/skills`` (INDEX first, then each ``*.md``) into one
    block, or None when absent. Mirrors :func:`load_stack_system_prompt`.

    These are the security/a11y/perf canons the deterministic gates enforce —
    injected so the FIRST draft already follows them. For these CRITICAL canons we
    deliberately do NOT rely on the model probabilistically pulling a skill
    (research caveat: auto-trigger is unreliable) — we inject them; selective
    per-task disclosure is a later optimization once there are many domain skills.
    Fail-soft."""
    if not orch_template:
        return None
    skills_dir = _TEMPLATES_DIR / orch_template / ".omnia" / "skills"
    if not skills_dir.is_dir():
        return None
    try:
        bodies: list[str] = []
        index = skills_dir / "INDEX.md"
        if index.is_file():
            bodies.append(index.read_text(encoding="utf-8"))
        for p in sorted(skills_dir.glob("*.md")):
            if p.name == "INDEX.md":
                continue
            bodies.append(p.read_text(encoding="utf-8"))
        block = "\n\n".join(b.strip() for b in bodies if b.strip())
        return block or None
    except Exception:
        return None


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
                if not ok:
                    # The agent gets the full `detail` in its observation; log a
                    # tail here too so operators can SEE the real compiler error
                    # behind a stuck loop (grep "build FAILED" in the api logs).
                    print(
                        f"[AGENT] build FAILED slug={slug}: {str(detail)[:600]}",
                        flush=True,
                    )
                return {"ok": ok, "detail": detail}

            if action.name == "bash":
                cmd = action.args.get("cmd")
                if not isinstance(cmd, str) or not cmd.strip():
                    return {"ok": False, "error": "bash needs a non-empty cmd string"}
                res = await orchestrator_client.agent_exec(project_id, slug, cmd)
                return {"ok": bool(res.get("ok")),
                        "detail": res.get("detail") or "(no output)"}

            if action.name == "read_logs":
                # Live dev-server stdout/stderr — the RUNTIME errors `build`
                # (typecheck) can't see (an unhandled exception, a failed import
                # at request time, a crashed route). Tail is bounded; the loop
                # truncates the observation to _MAX_OBS_CHARS on top.
                try:
                    _tail = int(action.args.get("tail", 120))
                except (TypeError, ValueError):
                    _tail = 120
                res = await orchestrator_client.get_logs(
                    project_id, tail=max(20, min(_tail, 400)))
                logs = res.get("logs") if isinstance(res, dict) else ""
                return {"ok": True, "detail": (logs or "").strip() or "(no logs yet)"}

            if action.name == "runtime_check":
                # Actually HIT a route in the running app and report the REAL HTTP
                # status. ok=False ONLY on a 5xx (a compile-clean app that still
                # crashes on render) — that's a real failure observation, not an
                # executor error, so the loop reads it and fixes the named file.
                path = action.args.get("path") or "/"
                res = await orchestrator_client.runtime_status(
                    project_id, slug=slug, path=str(path))
                ok = bool(res.get("ok", True))
                code = res.get("status_code")
                if ok:
                    detail = f"route {path} renders OK (HTTP {code or 200})"
                else:
                    err = res.get("error") or "5xx"
                    where = res.get("file")
                    detail = (
                        f"route {path} FAILED (HTTP {code or 500}): {err}"
                        + (f" — in {where}" if where else "")
                    )
                return {"ok": ok, "detail": detail}

            if action.name == "see":
                # Real EYES: screenshot the live page → vision judge → concrete
                # fix-deltas. Lazily imported so the pure engine + its tests carry
                # no Playwright/vision dependency. Fail-soft inside see_page.
                from omnia_api.services import agent_vision

                return await agent_vision.see_page(
                    project_id, path=action.path or "/")

            return {"ok": False, "error": f"unknown action {action.name}"}
        except Exception as exc:  # never let an executor crash kill the loop
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return _execute
