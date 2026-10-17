"""Agentic container-app builder — Phase 0 of the "like Claude Code" engine.

Today Yleum is a one-shot text generator: the model emits one blob, server
regex parses it into files, no feedback loop. This module is the opposite — a
real **agent loop**:

    plan -> act (a tool) -> observe the REAL result -> repeat -> verify -> done

It is a *text-protocol* ReAct loop, NOT native function-calling: the model
replies with reasoning followed by exactly ONE action in a strict
``<omnia:action name="...">{json}</omnia:action>`` block. The server parses it,
executes it against the live dev container, and feeds the real observation back
as the next user turn. This works with ANY gateway model (DeepSeek/Kimi/…) and
reuses Yleum's existing strength at parsing structured model output — no
dependency on the provider supporting OpenAI tool-calls.

Design rules that keep it safe to ship:
  * The EXECUTOR is injected (`execute` callable) so the loop is fully
    unit-testable with a fake — no container needed in tests.
  * Pure engine here; the production executor that talks to the orchestrator is
    `make_docs_media_executor(...)` at the bottom.
  * Bounded: `max_steps` hard cap, per-action output truncation. No unbounded
    grind.
  * Gated by ``Settings.use_agentic_builder`` at the call site. This loop is the
    only builder: when the gate is off, generation is refused, not rerouted.

Actions (file tools + real build/runtime observations):
    list_dir     {"path": "src/app"}
    read_file    {"path": "src/app/page.tsx", "start_line": 1, "end_line": 80}
    grep         {"pattern": "useState", "path": "src"}
    write_file   {"path": "...", "content": "...full file..."}
    edit_file    {"path": "...", "search": "...", "replace": "..."}
    build        {}                      # real typecheck/compile observation
    bash         {"cmd": "pnpm test"}    # arbitrary shell in the container
    read_logs    {}                      # live dev-server stdout/stderr (runtime errors)
    runtime_check{"path": "/"}           # hit a route, get the REAL HTTP status / crash file
    done         {"summary": "what I built"}

`build` proves it typechecks, `runtime_check`/`read_logs` inspect the running
app, and authenticated probes verify real interactions and data isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import count as segment_numbers
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, NonNegativeInt

from yleum_api.services import llm_client
from yleum_api.services.agent_output_budget import agent_output_tokens
from yleum_api.services.agent_progress import bounded_redacted_diagnostic
from yleum_api.services.generation.continuation import (
    RecoveryBindingReader,
    RecoveryObserver,
    SourceNoveltyTracker,
    shadow_recovery_executor,
)
from yleum_api.services.generation.recovery_actions import (
    ProgressReader,
    ResponseRecovery,
    verified_progress_executor,
)
from yleum_api.services.generation.recovery_contract import (
    IncompleteResponseBudget,
    RecoveryContractError,
)

if TYPE_CHECKING:
    from yleum_api.services.generation.recovery_execution import ExecutionDriver


class _LegacyLoopFrame(BaseModel):
    """Exact executable data; historical green flags never restore proof authority."""

    model_config = ConfigDict(strict=True, extra="forbid")
    contract: str
    step: NonNegativeInt
    attempt: NonNegativeInt
    retry_started_at: str
    call_deadline: str
    rate_retry_deadline: str | None
    convo: list[dict[str, Any]]
    written: dict[str, str]
    stalls: NonNegativeInt
    last_sig: str
    repeat_count: NonNegativeInt
    no_write_streak: NonNegativeInt
    infra_dead_streak: NonNegativeInt
    sig_seen: dict[str, NonNegativeInt]
    blocked_cycles: dict[str, NonNegativeInt]
    read_sigs: dict[str, list[str]]
    content_receipts_seen: bool
    last_build_ok: bool | None
    fast_check_fresh: bool
    pending_source_build: bool
    red_build_streak: NonNegativeInt
    writes_since_build: NonNegativeInt
    paths_since_build: list[str]
    rewrites_since_build: NonNegativeInt
    build_pressure_nudged: bool
    blocked_rewrites: NonNegativeInt
    last_runtime_ok: bool | None
    wrote_since_check: bool
    active_model: str
    escalated: bool
    pending_truncation: bool
    response_budget: IncompleteResponseBudget
    call_messages: list[dict[str, Any]]


class _LegacyOuterFrame(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    contract: str
    segment: NonNegativeInt
    total_steps: NonNegativeInt
    cumulative_files: dict[str, str]
    transcript: list[dict[str, Any]]
    task: str
    source_history: list[str] | None


# ── Action protocol ────────────────────────────────────────────────────────

# The model is taught to emit exactly one of these per turn. We parse the LAST
# block in the reply (the model may "think" in prose first, then act).
_ACTION_RE = re.compile(
    r"<(?:omnia:action|action|invoke)\s+name=[\"']([a-z_]+)[\"']\s*>"
    r"\s*(.*?)\s*</(?:omnia:action|action|invoke)>",
    re.DOTALL | re.IGNORECASE,
)

_KNOWN_ACTIONS = frozenset(
    {"list_dir", "read_file", "grep", "docs", "provider_docs", "write_file", "edit_file", "build",
     "bash", "read_logs", "runtime_check", "generate_media", "probe",
     "verify_isolation", "done"}
)

# Idempotent "observe the world after acting" actions. Re-running them across a
# build→fix loop is legitimate progress, NOT a cycle: a clean way to verify the
# last edit. They are therefore EXEMPT from the global non-consecutive repeat
# guard (which exists to catch repeated identical WRITES or read/grep/list
# exploration spinning). The consecutive-repeat guard (back-to-back spamming)
# and the no-write streak still bound them, so a model that does nothing but
# `build`/`runtime_check` in a row is still stopped.
_VERIFY_ACTIONS = frozenset(
    {"build", "read_logs", "runtime_check", "probe", "verify_isolation"}
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
    transcript: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""          # "done" | "max_steps" | "stalled" | "error" | "infra_error"
    # Native MAX segments carry fact-gate proof forward inside the same
    # GenerationRun. Text/legacy builders leave this empty.
    evidence: dict[str, int] = field(default_factory=dict)
    segments: int = 1
    needs_finalization: bool = False
    proof_checkpoint: object | None = None
    # Preserve the cause when finite legacy loops promote a green abort to done.
    continuation_reason: str = ""


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
            if not isinstance(parsed, dict):
                return None
            args = parsed
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
    head += "SKIPPED]" if obs.get("skipped") is True else "OK]" if ok else "FAILED]"
    body = obs.get("detail") or obs.get("content") or obs.get("error") or ""
    scope = ""
    read_range = obs.get("read_range")
    if action.name == "read_file" and ok and isinstance(read_range, dict):
        scope = (
            f"[read_file lines {read_range['start_line']}-{read_range['end_line']} "
            f"of {read_range['total_lines']}]\n"
        )
    rendered = _truncate(body, _MAX_OBS_CHARS)
    if action.name == "read_file" and ok and (
        len(str(body)) > _MAX_OBS_CHARS
        or (isinstance(read_range, dict) and read_range.get("truncated"))
    ):
        rendered += (
            "\nSource is truncated. Use grep on this exact file, then read_file "
            "with a narrow start_line/end_line range (1-based, inclusive). "
            "Do not repeat the same full-file read or guess an omitted signature."
        )
    return f"{head}\n{scope}{rendered}"


# Executor contract: an async callable Action -> {ok: bool, detail/content/error}.
# Successful file mutations may include content_change with canonical path and
# full before_sha256/after_sha256 (None before means the file did not exist).
Executor = Callable[[Action], Awaitable[dict[str, Any]]]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


def _safe_file_path(path: str) -> str | None:
    """Canonical read identity; traversal is never a progress witness."""
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() == ".":
        return None
    return candidate.as_posix()


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
    max_tokens: int | None = None,
    require_green_before_done: bool = False,
    coordinator_handoff: bool = False,
    ship_green_on_abort: bool = True,
    edit_mode: bool = False,
    bare_mode: bool = False,
    snapshot_files: Callable[[], Awaitable[dict[str, str]]] | None = None,
    max_segments: int | None = 1,
    checkpoint: Callable[[AgentResult], Awaitable[None]] | None = None,
    edit_deadline: datetime | None = None,
    recovery: RecoveryObserver | None = None,
    recovery_binding: RecoveryBindingReader | None = None,
    response_recovery: ResponseRecovery | None = None,
    take_verified_progress: ProgressReader | None = None,
    continuation: ExecutionDriver | None = None,
) -> AgentResult:
    """Continue a progressing candidate without a new run, cell or blind replay."""
    response_recovery = response_recovery or ResponseRecovery()
    execute = shadow_recovery_executor(
        execute, recovery=recovery, recovery_binding=recovery_binding,
    )
    if max_segments is None and (
        edit_deadline is None or edit_deadline.utcoffset() is None or snapshot_files is None
    ):
        raise ValueError("Progressive continuation requires an aware deadline and source snapshot")
    segment_limit = max(1, min(8, int(max_segments))) if max_segments is not None else None
    contract = hashlib.sha256(json.dumps({
        "task": user_prompt, "max_segments": segment_limit,
        "deadline": edit_deadline.isoformat() if edit_deadline else None,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() if continuation else ""
    raw = continuation.context.get("legacy_outer") if continuation is not None else None
    restored = _LegacyOuterFrame.model_validate(raw) if raw is not None else None
    if restored is not None and (
        restored.contract != contract or restored.segment < 1
        or (segment_limit is not None and restored.segment > segment_limit)
    ):
        raise RecoveryContractError("legacy segment continuation contract changed")
    novelty = None
    if segment_limit is None and restored is None:
        assert snapshot_files is not None and edit_deadline is not None
        if datetime.now(UTC) < edit_deadline:
            async with asyncio.timeout((edit_deadline - datetime.now(UTC)).total_seconds()):
                novelty = SourceNoveltyTracker(await snapshot_files())
    cumulative_files: dict[str, str] = {}
    transcript: list[dict[str, str]] = []
    total_steps = 0
    task = user_prompt
    start_segment = 1
    if restored is not None:
        start_segment = restored.segment
        cumulative_files, transcript = restored.cumulative_files, restored.transcript
        total_steps, task = restored.total_steps, restored.task
        if restored.source_history is not None:
            novelty = SourceNoveltyTracker.from_source_history(tuple(restored.source_history))

    def finish(result: AgentResult) -> AgentResult:
        if continuation is not None:
            continuation.context.pop("legacy_outer", None)
        return result

    for segment in segment_numbers(start_segment):
        offset = total_steps
        source_progress = False

        async def segment_emit(
            event: str, data: dict[str, Any], *,
            current_segment: int = segment, step_offset: int = offset,
        ) -> None:
            if emit is not None:
                payload = {**data, "segment": current_segment}
                if type(payload.get("step")) is int:
                    payload["step"] += step_offset
                await emit(event, payload)

        async def segment_progress(
            candidate: AgentResult, *,
            current_segment: int = segment, step_offset: int = offset,
        ) -> None:
            nonlocal source_progress
            source_progress = True
            if checkpoint is not None:
                candidate.steps += step_offset
                candidate.segments = current_segment
                await checkpoint(candidate)

        if (
            restored is None and segment_limit is None and segment > 1 and edit_deadline is not None
            and datetime.now(UTC) >= edit_deadline
        ):
            return AgentResult(
                done=False, summary="Source deadline reached", files=cumulative_files,
                steps=total_steps, transcript=transcript, stop_reason="source_deadline",
                segments=segment - 1,
            )
        if continuation is not None:
            outer = _LegacyOuterFrame(
                contract=contract, segment=segment, total_steps=total_steps,
                cumulative_files=cumulative_files, transcript=transcript, task=task,
                source_history=list(novelty.source_history()) if novelty is not None else None,
            )
            continuation.context = {
                **continuation.context, "legacy_outer": outer.model_dump(mode="json"),
            }
        result = await _run_agent_segment(
            system_prompt=system_prompt, user_prompt=task, model=model,
            execute=execute, escalate_model=escalate_model, max_steps=max_steps,
            emit=segment_emit if emit is not None else None, complete=complete,
            user_id=user_id, project_id=project_id, max_tokens=max_tokens,
            require_green_before_done=require_green_before_done,
            coordinator_handoff=coordinator_handoff,
            ship_green_on_abort=ship_green_on_abort,
            edit_mode=edit_mode, bare_mode=bare_mode, snapshot_files=snapshot_files,
            initial_written=cumulative_files,
            response_recovery=response_recovery, take_verified_progress=take_verified_progress,
            on_progress=segment_progress,
            edit_deadline=edit_deadline, continuation=continuation,
        )
        restored = None
        cumulative_files.update(result.files)
        total_steps += result.steps
        transcript.extend(result.transcript)
        result.files = dict(cumulative_files)
        result.steps = total_steps
        result.segments = segment
        result.transcript = list(transcript)
        if segment_limit is None and (
            result.stop_reason in {"max_steps", "max_steps_green", "max_steps_red"}
            or result.continuation_reason == "max_steps"
        ):
            # A context-window boundary does not mean the requested work is done.
            result.done = result.needs_finalization = False
            result.proof_checkpoint = None
            result.stop_reason = "max_steps"
        if result.done or result.needs_finalization:
            return finish(result)
        if result.stop_reason not in {"max_steps", "looping", "exploring", "stalled"}:
            return finish(result)
        if segment_limit == 1:
            return finish(result)
        if novelty is not None:
            assert snapshot_files is not None and edit_deadline is not None
            remaining = (edit_deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                result.stop_reason = "source_deadline"
                return finish(result)
            async with asyncio.timeout(remaining):
                source_progress = novelty.observe(await snapshot_files())
        if not source_progress:
            result.stop_reason = "no_progress"
            result.summary = "Остановлено: сегмент не изменил исходники приложения."
            return finish(result)
        if segment == segment_limit:
            result.stop_reason = "max_segments"
            result.summary = "Приложение ещё не прошло итоговые проверки; продолжение остановлено."
            return finish(result)
        diagnostics = [
            turn["content"] for turn in result.transcript[-12:]
            if turn.get("role") == "user"
        ][-4:]
        task = (
            "Continue the SAME candidate in the SAME workspace. Keep existing source; "
            "do not scaffold or duplicate the application. Fix the concrete errors, "
            "finish missing requested behavior, build after the last change, then done. "
            "A prior green check is invalid after any further write.\n\n"
            f"Original request:\n{user_prompt}\n\n"
            f"Previous segment stopped: {result.stop_reason}\n"
            + "\n".join(diagnostics)[-16000:]
        )
        if emit is not None:
            await emit("agent.continuation", {
                "step": total_steps, "segment": segment + 1,
                "human": "Продолжаю сохранённую работу и исправляю оставшиеся ошибки",
                "ok": None,
            })
        await asyncio.sleep(0)
    raise AssertionError("segment limit must be positive")

async def _run_agent_segment(
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
    max_tokens: int | None = None,
    require_green_before_done: bool = False,
    coordinator_handoff: bool = False,
    ship_green_on_abort: bool = True,
    edit_mode: bool = False,
    bare_mode: bool = False,
    snapshot_files: Callable[[], Awaitable[dict[str, str]]] | None = None,
    initial_written: dict[str, str] | None = None,
    response_recovery: ResponseRecovery | None = None,
    take_verified_progress: ProgressReader | None = None,
    on_progress: Callable[[AgentResult], Awaitable[None]] | None = None,
    edit_deadline: datetime | None = None,
    continuation: ExecutionDriver | None = None,
) -> AgentResult:
    """Drive the plan→act→observe loop until the model says done or budget hits.

    `execute` runs an action against the world (container) and returns an
    observation dict. `complete` defaults to the real gateway call but is
    injectable for tests. Returns every file the agent successfully wrote so the
    caller can commit them to git via the existing pipeline.
    """
    # The gateway can spend 240s on an accepted upstream request. The shared
    # nonstream client's 90s default discarded healthy paid responses before
    # they arrived. Give only this builder bounded response/accounting headroom;
    # injected completers and other callers retain their existing budgets.
    uses_gateway = complete is None
    complete = complete or partial(llm_client.complete_chat, timeout_seconds=300.0)
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    written: dict[str, str] = dict(initial_written or {})
    stalls = 0
    last_sig = ""
    repeat_count = 0
    no_write_streak = 0  # consecutive actions that wrote nothing (cycle breaker)
    infra_dead_streak = 0  # consecutive tool ops that died on infra (container gone)
    sig_seen: dict[str, int] = {}  # executed non-consecutive action signatures
    blocked_cycles: dict[str, int] = {}  # denied repeat attempts keep the existing bound
    read_sigs: dict[str, set[str]] = {}  # canonical file -> its read signatures
    content_receipts_seen = False  # skip generic before-read after producer proves support
    last_build_ok: bool | None = None  # result of the most recent `build` action
    fast_check_fresh = False  # candidate check, never a runtime/release proof
    pending_source_build = False  # one build owed after successful actual source progress
    red_build_streak = 0  # consecutive failed builds → escalate to the strong model
    # Build-pressure / rotation guard: counts writes since the last `build` so the
    # loop can force a typecheck instead of churning files forever (see guard below).
    writes_since_build = 0
    paths_since_build: set[str] = set()  # distinct paths written since the last build
    rewrites_since_build = 0  # writes to a path already written since the last build
    build_pressure_nudged = False  # the gentle "run build" nudge fires once per window
    blocked_rewrites = 0  # refused rewrites count toward the existing churn bound
    # `require_green_before_done` bookkeeping: a `done` is only honoured once the
    # last build was clean AND the running app was re-checked after the last write
    # (a clean typecheck is exactly what a model hallucinates completion around —
    # see SYSTEM_PROMPT). The segment budget bounds rejected claims without
    # ever converting an unverified candidate into a successful result.
    last_runtime_ok: bool | None = None  # result of the most recent `runtime_check`
    wrote_since_check = False  # a write happened with no runtime_check after it
    active_model = model
    escalated = False
    response_recovery = response_recovery or ResponseRecovery()
    execute = verified_progress_executor(execute, response_recovery, take_verified_progress)
    pending_truncation = False

    contract = hashlib.sha256(json.dumps({
        "system": system_prompt, "task": user_prompt, "model": model,
        "escalate_model": escalate_model, "max_steps": max_steps, "max_tokens": max_tokens,
        "require_green": require_green_before_done, "handoff": coordinator_handoff,
        "ship_green": ship_green_on_abort, "edit_mode": edit_mode, "bare_mode": bare_mode,
        "edit_deadline": edit_deadline.isoformat() if edit_deadline else None,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() if continuation else ""
    restored = continuation.restore_frame("legacy") if continuation is not None else None
    saved_frame = _LegacyLoopFrame.model_validate(restored) if restored is not None else None
    start_step = 0
    if saved_frame is not None:
        if (saved_frame.contract != contract or saved_frame.step >= max_steps
                or saved_frame.attempt >= 5):
            raise RecoveryContractError("legacy continuation inputs or allowance changed")
        start_step = saved_frame.step
        convo, written = saved_frame.convo, saved_frame.written
        stalls, last_sig = saved_frame.stalls, saved_frame.last_sig
        repeat_count, no_write_streak = saved_frame.repeat_count, saved_frame.no_write_streak
        infra_dead_streak = saved_frame.infra_dead_streak
        sig_seen, blocked_cycles = saved_frame.sig_seen, saved_frame.blocked_cycles
        read_sigs = {path: set(values) for path, values in saved_frame.read_sigs.items()}
        content_receipts_seen = saved_frame.content_receipts_seen
        last_build_ok, fast_check_fresh = saved_frame.last_build_ok, saved_frame.fast_check_fresh
        pending_source_build, red_build_streak = (
            saved_frame.pending_source_build, saved_frame.red_build_streak,
        )
        writes_since_build = saved_frame.writes_since_build
        paths_since_build = set(saved_frame.paths_since_build)
        rewrites_since_build = saved_frame.rewrites_since_build
        build_pressure_nudged, blocked_rewrites = (
            saved_frame.build_pressure_nudged, saved_frame.blocked_rewrites,
        )
        last_runtime_ok, wrote_since_check = saved_frame.last_runtime_ok, saved_frame.wrote_since_check
        active_model, escalated = saved_frame.active_model, saved_frame.escalated
        pending_truncation = saved_frame.pending_truncation
        response_recovery.budget = IncompleteResponseBudget(
            total=max(response_recovery.budget.total, saved_frame.response_budget.total),
            since_progress=max(
                response_recovery.budget.since_progress, saved_frame.response_budget.since_progress,
            ),
        )

    async def _step_result(
        action: Action,
        step: int,
        obs: dict[str, Any] | None = None,
        *,
        skipped: str | None = None,
    ) -> None:
        if emit:
            result = obs or {}
            await emit(
                "agent.step",
                {
                    "step": step,
                    "action": action.name,
                    "path": action.path,
                    "ok": result.get("ok") if type(result.get("ok")) is bool else None,
                    "detail": skipped
                    or bounded_redacted_diagnostic(
                        str(result.get("detail") or result.get("error") or ""),
                        max_bytes=_MAX_OBS_CHARS,
                    ),
                    **({"skipped": True} if skipped is not None else {}),
                },
            )

    async def _skip_action(action: Action, step: int, reason: str) -> None:
        nonlocal no_write_streak
        no_write_streak += 1
        await _step_result(action, step, skipped=reason)
        convo.append(
            {
                "role": "user",
                "content": _format_observation(
                    action,
                    {"ok": None, "skipped": True, "detail": reason},
                ),
            }
        )

    async def _nudge_exploration(step: int) -> None:
        green = (
            fast_check_fresh
            if coordinator_handoff
            else (last_build_ok is True and last_runtime_ok is True and not wrote_since_check)
        )
        await _escalate(step, "explore")
        if emit:
            await emit("agent.stalled", {"step": step})
        convo.append(
            {
                "role": "user",
                "content": (
                    "FAST_CHECK is current and clean. Call done to HAND OFF to the "
                    "server coordinator; do not rewrite source for deferred runtime proof."
                    if green and coordinator_handoff
                    else _DONE_WHEN_GREEN_NUDGE
                    if green
                    else _EDIT_EXPLORE_STALL_NUDGE
                    if edit_mode
                    else _EXPLORE_STALL_NUDGE
                ),
            }
        )

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

    def _ship_green_or(fallback: AgentResult, step_no: int, reason: str) -> AgentResult:
        """A loop-abort is about to discard the run — but if the LAST build was
        green the app already compiles, so SHIP it as a success instead of telling
        the user «Сборка прервана» about a working app (owner's exact complaint:
        the messenger built green at step 15, then the model fussed re-reading
        layout and the cycle-guard threw the green app away). Honest: the `done`
        gate itself only requires a clean build (see `require_green_before_done`),
        so a green abort meets the same bar. Falls through to the original abort
        when there is no green build to rescue."""
        if ship_green_on_abort and not pending_truncation and fast_check_fresh and (
            coordinator_handoff or not require_green_before_done
            or (last_runtime_ok is True and not wrote_since_check)
        ):
            print(
                f"[AGENT] step={step_no} SHIP-GREEN-ON-{reason} "
                f"(build clean → ship instead of {fallback.stop_reason})",
                flush=True,
            )
            return AgentResult(
                done=True,
                summary="Приложение собрано — билд проходит чисто.",
                files=written, steps=step_no + 1, transcript=convo,
                stop_reason="done_on_green",
                needs_finalization=coordinator_handoff,
                continuation_reason=fallback.stop_reason,
            )
        return fallback

    for step in range(start_step, max_steps):
        restoring = saved_frame is not None and step == start_step
        if not restoring and edit_deadline is not None and datetime.now(UTC) >= edit_deadline:
            return _ship_green_or(
                AgentResult(
                    done=False, summary="Завершаю работу с исходниками; сохраняю время для проверок.",
                    files=written, steps=step, transcript=convo, stop_reason="source_deadline",
                ), step, "deadline",
            )
        # Retry only positively identified preconnection failures. A missing
        # response to an accepted POST may already represent a paid model call.
        reply = None
        # COST: vsegpt bills by characters, so resending the full growing
        # transcript every step makes a long loop cost balloon. Send only a
        # sliding window (system + the seed/task + the last N turns) → per-step
        # payload stays ~constant regardless of step count → "same cost".
        call_msgs = _window_messages(convo)
        # Inject the live state (files already written + last build result) into
        # the system slot so the windowed model never forgets what it has done →
        # stops re-writing existing files (the root cycle/exploring cause).
        _note = _progress_note(
            written, last_build_ok if not coordinator_handoff or fast_check_fresh else None,
        )
        if coordinator_handoff:
            _note += (
                "\nMAX FINALIZATION HANDOFF: build runs FAST_CHECK only. After a fresh "
                "clean build and completed source changes, call done to HAND OFF the "
                "candidate; done hands off a candidate, not an accepted product. "
                "The server coordinator MUST still run full build, runtime "
                "and release checks before accepting it. Do not repeat deferred runtime "
                "checks or rewrite clean source just to obtain runtime proof here."
            )
        if _note and call_msgs:
            call_msgs = [
                {"role": call_msgs[0]["role"],
                 "content": call_msgs[0]["content"] + _note},
                *call_msgs[1:],
            ]
        # Preserve the existing five-attempt bound for proven safe failures.
        # Only confirmed refusal/preconnect evidence permits another paid POST.
        retry_started = asyncio.get_running_loop().time()
        retry_started_at = datetime.now(UTC)
        rate_retry_deadline: float | None = None
        start_attempt = 0
        if restoring:
            assert saved_frame is not None
            call_msgs = saved_frame.call_messages
            start_attempt = saved_frame.attempt
            retry_started_at = datetime.fromisoformat(saved_frame.retry_started_at)
            if retry_started_at.utcoffset() is None:
                raise RecoveryContractError("legacy retry clock is not an absolute instant")
            retry_started -= max(0.0, (datetime.now(UTC) - retry_started_at).total_seconds())
            if saved_frame.rate_retry_deadline is not None:
                absolute_rate_deadline = datetime.fromisoformat(saved_frame.rate_retry_deadline)
                if absolute_rate_deadline.utcoffset() is None:
                    raise RecoveryContractError("legacy retry deadline has no clock basis")
                rate_retry_deadline = (
                    asyncio.get_running_loop().time()
                    + (absolute_rate_deadline - datetime.now(UTC)).total_seconds()
                )
        truncated_response = False
        for attempt in range(start_attempt, 5):
            if (not restoring and rate_retry_deadline is not None
                    and asyncio.get_running_loop().time() >= rate_retry_deadline):
                return AgentResult(
                    done=False, summary="provider_refusal_rate_limit: safe retry window exhausted",
                    files=written, steps=step, transcript=convo, stop_reason="provider_error",
                )
            remaining = (
                (edit_deadline - datetime.now(UTC)).total_seconds()
                if edit_deadline is not None else None
            )
            if not restoring and remaining is not None and remaining <= 0:
                return AgentResult(
                    done=False, summary="Время работы с исходниками завершилось.",
                    files=written, steps=step, transcript=convo, stop_reason="source_deadline",
                )
            call_seconds = (
                remaining if max_tokens is None
                and agent_output_tokens(active_model, fallback=16384) is None
                else min(300.0, remaining)
            ) if uses_gateway and remaining is not None and not restoring else 300.0
            bounded_complete = (
                partial(llm_client.complete_chat, timeout_seconds=call_seconds)
                if uses_gateway and remaining is not None and not restoring else complete
            )
            try:
                rate_remaining = (
                    rate_retry_deadline - asyncio.get_running_loop().time()
                    if rate_retry_deadline is not None else None
                )
                # A retry admitted near the boundary cannot outlive its window.
                # A timed-out POST has an unknown outcome and is never replayed.
                async def invoke(
                    call: Callable[..., Awaitable[str]] = bounded_complete,
                    messages: list[dict[str, Any]] = call_msgs,
                ) -> str:
                    return await call(
                        messages, active_model, user_id=user_id, project_id=project_id,
                        max_tokens=(agent_output_tokens(active_model, fallback=16384)
                                    if max_tokens is None else max_tokens),
                        temperature=0.0,
                    )

                async with asyncio.timeout(None if restoring else rate_remaining):
                    if continuation is None:
                        reply = await invoke()
                    else:
                        frame = saved_frame if restoring else _LegacyLoopFrame(
                            contract=contract, step=step, attempt=attempt,
                            retry_started_at=retry_started_at.isoformat(),
                            call_deadline=min(
                                datetime.now(UTC) + timedelta(seconds=max(1.0, call_seconds)),
                                retry_started_at + timedelta(seconds=270)
                                if rate_retry_deadline is not None else datetime.max.replace(tzinfo=UTC),
                            ).isoformat(),
                            rate_retry_deadline=(
                                (retry_started_at + timedelta(seconds=270)).isoformat()
                                if rate_retry_deadline is not None else None
                            ),
                            convo=convo, written=written, stalls=stalls, last_sig=last_sig,
                            repeat_count=repeat_count, no_write_streak=no_write_streak,
                            infra_dead_streak=infra_dead_streak, sig_seen=sig_seen,
                            blocked_cycles=blocked_cycles,
                            read_sigs={path: sorted(values) for path, values in read_sigs.items()},
                            content_receipts_seen=content_receipts_seen,
                            last_build_ok=last_build_ok, fast_check_fresh=fast_check_fresh,
                            pending_source_build=pending_source_build,
                            red_build_streak=red_build_streak, writes_since_build=writes_since_build,
                            paths_since_build=sorted(paths_since_build),
                            rewrites_since_build=rewrites_since_build,
                            build_pressure_nudged=build_pressure_nudged,
                            blocked_rewrites=blocked_rewrites, last_runtime_ok=last_runtime_ok,
                            wrote_since_check=wrote_since_check, active_model=active_model,
                            escalated=escalated, pending_truncation=pending_truncation,
                            response_budget=response_recovery.budget, call_messages=call_msgs,
                        )
                        assert frame is not None
                        reply = await continuation.provider(
                            {"version": 1, "kind": "legacy", "value": frame.model_dump(mode="json")},
                            invoke,
                        )
                    if restoring:
                        saved_frame = None
                        restoring = False
                        last_build_ok = last_runtime_ok = None
                        fast_check_fresh = False
                        if edit_deadline is not None and datetime.now(UTC) >= edit_deadline:
                            return AgentResult(
                                done=False, summary="Saved response reconciled after source deadline",
                                files=written, steps=step, transcript=convo,
                                stop_reason="source_deadline",
                            )
                break
            except llm_client.LLMOutputTruncated as exc:
                # A settled truncated response is not a transport retry. Discard every
                # partial action and spend the next normal control step on a new plan.
                response_recovery.truncated()
                pending_truncation = truncated_response = True
                fast_check_fresh = False
                if emit:
                    await emit("agent.tool", {
                        "step": step, "tool": "provider_response", "reason": "output_limit",
                        "human": "Ответ модели достиг предела — разбиваю изменение на меньшие шаги",
                        "ok": False, "recovery_attempt": response_recovery.budget.since_progress,
                        "finish_reason": exc.finish_reason,
                        "requested_max_tokens": exc.requested_max_tokens,
                        "completion_tokens": exc.completion_tokens,
                    })
                if response_recovery.budget.since_progress >= 3:
                    return AgentResult(
                        done=False, summary="Три обрезанных ответа без подтверждённого прогресса.",
                        files=written, steps=step + 1, transcript=convo, stop_reason="output_limit",
                    )
                convo.append({"role": "user", "content": (
                    "PROVIDER OUTPUT LIMIT: the previous response was truncated and discarded. "
                    "Every action in it was not applied. Do not replay the same oversized action. "
                    "Read the current source, split files/actions into smaller complete steps, "
                    "and emit one smaller complete action. Then run build before done."
                )})
                break
            except Exception as exc:
                failure = exc if isinstance(exc, llm_client.LLMError) else None
                retry_safe = failure is not None and failure.retry_safe is True
                will_retry = retry_safe and attempt < 4
                delay = 4.0 * (attempt + 1)
                wait_budget = (
                    (edit_deadline - datetime.now(UTC)).total_seconds()
                    if edit_deadline is not None else None
                )
                if failure is not None and failure.retry_after_seconds is not None:
                    rate_retry_deadline = retry_started + 270.0
                    delay = max(delay, failure.retry_after_seconds)
                    rate_budget = rate_retry_deadline - asyncio.get_running_loop().time()
                    wait_budget = min(wait_budget, rate_budget) if wait_budget is not None else rate_budget
                if wait_budget is not None and delay >= wait_budget:
                    will_retry = False
                diagnostic = {
                    "step": step, "attempt": attempt, "will_retry": will_retry,
                    "error_code": failure.code if failure is not None else None,
                    "http_status": failure.http_status if failure is not None else None,
                    "upstream_http_status": (
                        failure.upstream_http_status if failure is not None else None
                    ),
                    "provider_charge_ambiguous": (
                        failure.provider_charge_ambiguous if failure is not None else None
                    ),
                    "provider_refusal_category": (
                        failure.provider_refusal_category if failure is not None else None
                    ),
                    "finish_reason": failure.finish_reason if failure is not None else None,
                    "requested_max_tokens": (
                        failure.requested_max_tokens if failure is not None else None
                    ),
                    "completion_tokens": failure.completion_tokens if failure is not None else None,
                }
                if emit:
                    await emit("agent.provider_error", diagnostic)
                if not will_retry:
                    cause = str(failure) if failure is not None else (
                        "Не получен подтверждённый результат LLM-запроса."
                    )
                    prefix = "Исчерпан лимит безопасных попыток. " if retry_safe else (
                        "Автоматический повтор остановлен. "
                    )
                    classification = (
                        f"provider_http_{failure.upstream_http_status}"
                        if failure is not None and failure.upstream_http_status is not None
                        else "provider_request_failed"
                    )
                    if (
                        failure is not None and failure.code == "wallet_empty"
                        and failure.http_status == 402
                    ):
                        classification = "PLATFORM_WALLET_EMPTY"
                    elif (
                        failure is not None
                        and failure.provider_refusal_category in {"access", "funds", "rate_limit"}
                        and failure.upstream_http_status is not None
                        and 400 <= failure.upstream_http_status < 500
                    ):
                        classification = f"provider_refusal_{failure.provider_refusal_category}"
                    return AgentResult(
                        done=False, summary=f"{classification}: {prefix}{cause}", files=written,
                        steps=step, transcript=convo, stop_reason="provider_error",
                    )
                if restoring:
                    # The retained refusal was consumed. The next original outer
                    # attempt must persist its own index, never the cold frame again.
                    saved_frame = None
                    restoring = False
                    last_build_ok = last_runtime_ok = None
                    fast_check_fresh = False
                if emit:
                    await emit("agent.retry", diagnostic)
                await asyncio.sleep(delay)
        if truncated_response:
            continue
        if reply is None:
            return AgentResult(
                done=False,
                summary="provider_request_failed: Не получен подтверждённый результат LLM-запроса.",
                files=written, steps=step, transcript=convo, stop_reason="provider_error",
            )

        convo.append({"role": "assistant", "content": reply})
        action = parse_action(reply)

        if action is None:
            # DIAGNOSTIC: the "stalled, 0 files" failure is opus emitting replies
            # parse_action rejects. Probes proved opus writes fine in isolation, so
            # the cause lives in the full prod context — log the VERBATIM reply +
            # why it failed to parse (these replies are short) to root-cause it.
            _tags = list(_ACTION_RE.finditer(reply or ""))
            if not (reply or "").strip():
                _why = "EMPTY reply from gateway"
            elif not _tags:
                _why = "no <omnia:action> tag"
            else:
                _nm = _tags[-1].group(1).strip().lower()
                _why = (
                    f"unknown name={_nm!r}" if _nm not in _KNOWN_ACTIONS
                    else f"name={_nm} body-not-valid-json"
                )
            print(
                f"[AGENT] step={step} NO-ACTION model={active_model} why=({_why}) "
                f"reply={_truncate(reply or '', 1400)!r}",
                flush=True,
            )
            stalls += 1
            if emit:
                await emit("agent.stalled", {"step": step})
            if stalls >= _NO_ACTION_ABORT_AT:
                return AgentResult(
                    done=False, summary="model emitted no valid action repeatedly",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="stalled",
                )
            # A model that can't emit the action protocol usually needs a DIFFERENT
            # model, not more tries on the same one. On the 2nd miss, escalate to the
            # strong model BEFORE giving up — this is the #1 reliability fix for the
            # "stalled, 0 files" first-build failure (deepseek intermittently emits
            # zero visible action). Bounded by _NO_ACTION_ABORT_AT.
            if stalls >= 2:
                await _escalate(step, "no_action")
            convo.append({"role": "user", "content": _NO_ACTION_NUDGE})
            continue
        stalls = 0

        if action.name == "done":
            if pending_truncation:
                await _step_result(action, step, skipped="Truncated response requires a fresh build")
                convo.append({"role": "user", "content": (
                    "NOT DONE: the truncated action was not applied. Finish a smaller complete "
                    "step and run build successfully before done."
                )})
                continue
            # Green-gate: refuse a premature `done`. The model loves to declare
            # victory on a clean typecheck without ever opening the route, so a
            # broken-at-runtime app ships. Require last build clean + a
            # runtime_check AFTER the last write. The segment budget bounds
            # rejected claims; insistence cannot replace real proof.
            if coordinator_handoff or require_green_before_done:
                gap = None
                if not fast_check_fresh:
                    gap = "run `build` after the latest changes until FAST_CHECK is CLEAN"
                elif last_build_ok is not True:
                    gap = (
                        "run `build` and fix errors until it is CLEAN before done"
                    )
                elif not coordinator_handoff and (
                    wrote_since_check or last_runtime_ok is not True
                ):
                    gap = (
                        "you wrote files but did not confirm they RUN — "
                        'runtime_check the main route(s) (e.g. {"path":"/"}), '
                        "fix any 5xx, THEN done"
                    )
                if gap is not None:
                    await _step_result(action, step, skipped=gap)
                    if emit:
                        await emit("agent.stalled", {"step": step})
                    convo.append({"role": "user", "content": (
                        "NOT DONE YET — " + gap + "."
                    )})
                    continue
            await _step_result(action, step, {"ok": True, "detail": action.args.get("summary", "")})
            return AgentResult(
                done=True, summary=str(action.args.get("summary", "")),
                files=written, steps=step + 1, transcript=convo,
                stop_reason="done",
                needs_finalization=coordinator_handoff,
            )

        # Failed/noop writes cannot evade the bounded no-progress limit.
        # The previous observation has already reached this model turn.
        if no_write_streak >= _NO_WRITE_ABORT_AT and not (
            action.name == "build" and pending_source_build
        ):
            await _step_result(
                action, step, skipped="No source progress; exploration budget exhausted"
            )
            return _ship_green_or(
                AgentResult(
                    done=False,
                    summary="stuck exploring without successful source changes",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="exploring",
                ),
                step,
                "explore",
            )

        # Circuit breaker: the model sometimes gets stuck re-issuing the SAME
        # action (observed live: 34x identical grep → max_steps, no progress).
        # Detect consecutive identical actions → nudge to move on → then abort.
        read_path = _safe_file_path(action.path) if action.name == "read_file" else None
        sig_args = {**action.args, "path": read_path} if read_path else action.args
        sig = (
            f"{action.name}|{read_path or action.path}|"
            f"{json.dumps(sig_args, sort_keys=True, ensure_ascii=False)}"
        )
        # GLOBAL repeat guard (non-consecutive cycle). A multi-step loop that
        # re-issues the SAME action — INCLUDING re-WRITING a file with identical
        # content (observed live: the same 5 entities, then the same 2 dashboard
        # pages + build, on a loop) — is missed by both the consecutive check below
        # (steps differ within the cycle) and the no-write streak (a write resets
        # it). Count exact signatures, resetting only reads of a file whose content
        # a successful write/edit has demonstrably changed (see witness below).
        # Only NON-consecutive occurrences count here — back-to-back repeats are the
        # job of the `repeat_count` check below; this guard is for a multi-step CYCLE
        # that returns to the same action (a,b,c,a,b,c… / re-writing the same file).
        # Idempotent verify actions (build / read_logs / runtime_check) are exempt:
        # re-observing the app after a fix is progress, not a cycle — caging them
        # here is what falsely aborted long build→fix→build loops as "looping".
        if sig != last_sig and action.name not in _VERIFY_ACTIONS:
            cycle_count = sig_seen.get(sig, 0) + blocked_cycles.get(sig, 0) + 1
            if cycle_count >= _REPEAT_ABORT_AT:
                await _step_result(action, step, skipped="Repeated unchanged action; cycle limit")
                return _ship_green_or(
                    AgentResult(
                        done=False,
                        summary=f"stuck re-issuing {action.name} {action.path}",
                        files=written, steps=step + 1, transcript=convo,
                        stop_reason="looping",
                    ),
                    step, "cycle",
                )
            if cycle_count >= _REPEAT_NUDGE_AT:
                blocked_cycles[sig] = blocked_cycles.get(sig, 0) + 1
                await _skip_action(action, step, "Repeated unchanged action; cycle guard")
                await _escalate(step, "cycle")
                print(
                    f"[AGENT] step={step} CYCLE x{cycle_count} {action.name} {action.path}",
                    flush=True,
                )
                if emit:
                    await emit("agent.stalled", {"step": step})
                convo.append({"role": "user", "content": (
                    _EDIT_REPEAT_CYCLE_NUDGE if edit_mode else _REPEAT_CYCLE_NUDGE
                )})
                continue
        next_repeat_count = repeat_count + 1 if sig == last_sig else 0
        if next_repeat_count >= 5:
            await _step_result(action, step, skipped="Repeated identical action; repeat limit")
            return _ship_green_or(
                AgentResult(
                    done=False,
                    summary=f"stuck repeating {action.name} {action.path}",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="looping",
                ),
                step, "repeat",
            )
        if next_repeat_count >= 2:
            repeat_count = next_repeat_count
            await _skip_action(action, step, "Repeated identical action; repeat guard")
            await _escalate(step, "repeat")
            print(
                f"[AGENT] step={step} REPEAT x{repeat_count} {action.name} {action.path}",
                flush=True,
            )
            convo.append({"role": "user", "content": (
                (
                    f"STOP — you ran this EXACT action {repeat_count + 1} times with "
                    "the same result. Do NOT repeat it. You have enough context: emit "
                    "the edit_file / write_file patch for the requested change NOW, "
                    "then run build, then call done."
                )
                if edit_mode
                else (
                    f"STOP — you ran this EXACT action {repeat_count + 1} times with the "
                    "same result. Do NOT repeat it. You have enough context: WRITE the "
                    "next required file using components that actually exist in this "
                    "project, or fix the named compiler error, or run build, or call done."
                )
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
            # Build-pressure / rotation guard. The model sometimes WRITES forever
            # without ever running `build` — rewriting the same files in rotation
            # with slight content variations (observed live: a messenger churned the
            # same 6 chat files for 40+ steps, never typechecked; the user's stream
            # dropped while it span and "ничего не отдало"). The sig/consecutive/
            # no-write guards all MISS this: the writes rotate across files (so the
            # consecutive + global-sig guards don't fire), content varies each time
            # (so the identical-content guard doesn't fire), and it IS writing (so
            # the no-write streak doesn't fire). Two signals close the gap:
            #   * a REWRITE of a path already written SINCE THE LAST BUILD — never
            #     legitimate without a build in between → force a build, then abort;
            #   * a high count of writes with NO build at all → one gentle reminder.
            if rewrites_since_build + blocked_rewrites >= _REWRITE_LOOP_ABORT_AT:
                await _step_result(action, step, skipped="Rewrite churn without build; limit")
                return _ship_green_or(
                    AgentResult(
                        done=False,
                        summary="rewriting the same files in a loop without ever building",
                        files=written, steps=step + 1, transcript=convo,
                        stop_reason="looping",
                    ),
                    step, "rewrite-loop",
                )
            if rewrites_since_build >= _REWRITE_BEFORE_BUILD_NUDGE:
                blocked_rewrites += 1
                await _skip_action(action, step, "Rewrite paused until build")
                await _escalate(step, "rewrite_loop")
                print(
                    f"[AGENT] step={step} REWRITE-LOOP x{rewrites_since_build} "
                    f"(no build since {writes_since_build} writes) → nudge BUILD",
                    flush=True,
                )
                if emit:
                    await emit("agent.stalled", {"step": step})
                convo.append({"role": "user", "content": _BUILD_PRESSURE_NUDGE})
                continue  # don't execute the rewrite — force a build first
            if (
                not build_pressure_nudged
                and writes_since_build >= _WRITES_BEFORE_BUILD_NUDGE
            ):
                build_pressure_nudged = True  # one gentle reminder per no-build window
                await _skip_action(action, step, "Writes paused until build")
                await _escalate(step, "no_build")
                print(
                    f"[AGENT] step={step} BUILD-PRESSURE x{writes_since_build} "
                    f"writes, no build yet → nudge BUILD",
                    flush=True,
                )
                convo.append({"role": "user", "content": _BUILD_PRESSURE_NUDGE})
                continue  # build once before piling on more files
        elif action.name not in _VERIFY_ACTIONS and no_write_streak >= (
            _EDIT_NO_WRITE_NUDGE_AT if edit_mode else _NO_WRITE_NUDGE_AT
        ):
            # The threshold-producing action ran on the previous turn. Its real
            # observation is already visible; only further discovery is paused.
            await _skip_action(action, step, "No source progress; discovery paused")
            await _nudge_exploration(step)
            continue

        # Successful writes/edits can be no-ops. Cell supplies full-content hashes;
        # generic executors get one bounded fresh BEFORE read for a tracked file.
        # Once a valid receipt proves support, skip that fallback read. No model
        # call/step is added; unavailable/truncated evidence earns no reset.
        write_path = (
            _safe_file_path(action.path)
            if action.name in {"write_file", "edit_file"} else None
        )
        before_content = None
        if snapshot_files is None and write_path in read_sigs and not content_receipts_seen:
            before_action = Action(name="read_file", args={"path": write_path})
            tool = getattr(continuation, "tool", None)
            before = (
                await tool("legacy-before", before_action, lambda current=before_action: execute(current))
                if tool is not None else await execute(before_action)
            )
            content = before.get("content")
            if before.get("ok") is True and isinstance(content, str) and len(content) <= _MAX_READ_CHARS:
                before_content = content
        # Discovery/rewrite skips above did not execute: they must not poison
        # a later first read or change the identity of the last executed action.
        if read_path:
            read_sigs.setdefault(read_path, set()).add(sig)
        if sig != last_sig and action.name not in _VERIFY_ACTIONS:
            sig_seen[sig] = sig_seen.get(sig, 0) + 1
        repeat_count, last_sig = next_repeat_count, sig
        source_before = dict(await snapshot_files()) if snapshot_files is not None else None
        tool = getattr(continuation, "tool", None)
        obs = (
            await tool("legacy-action", action, lambda current=action: execute(current))
            if tool is not None else await execute(action)
        )
        source_after = dict(await snapshot_files()) if snapshot_files is not None else None
        await _step_result(action, step, obs)
        after_content = obs.get("content")
        receipt = obs.get("content_change")
        receipt_valid = (
            write_path is not None
            and obs.get("ok") is True
            and isinstance(after_content, str)
            and isinstance(receipt, dict)
            and receipt.get("path") == write_path
            and "before_sha256" in receipt
            and (
                receipt["before_sha256"] is None
                or (
                    isinstance(receipt["before_sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", receipt["before_sha256"]) is not None
                )
            )
            and receipt.get("after_sha256") == hashlib.sha256(after_content.encode()).hexdigest()
        )
        if receipt_valid and isinstance(receipt, dict):
            content_receipts_seen = True
            content_changed = receipt["before_sha256"] != receipt["after_sha256"]
        else:
            content_changed = (
                "content_change" not in obs
                and before_content is not None
                and obs.get("ok") is True
                and isinstance(after_content, str)
                and after_content != before_content
            )
        if source_before is not None and source_after is not None:
            changed_source_paths = {
                path
                for path in source_before.keys() | source_after.keys()
                if _safe_file_path(path) == path
                and source_before.get(path) != source_after.get(path)
            }
        else:
            changed_source_paths = (
                {write_path} if write_path is not None and content_changed else set()
            )
        if changed_source_paths:
            pending_source_build = obs.get("ok") is True
        if action.name == "build":
            # Consume the allowance even on red builds; no extra model/retry budget.
            pending_source_build = False
        if changed_source_paths and obs.get("ok") is True:
            no_write_streak = 0
            if action.name != "build":
                for path in changed_source_paths:
                    writes_since_build += 1
                    if path in paths_since_build:
                        rewrites_since_build += 1
                    paths_since_build.add(path)
        else:
            no_write_streak += 1
        for path in changed_source_paths:
            for read_sig in read_sigs.get(path, ()):
                sig_seen.pop(read_sig, None)
                blocked_cycles.pop(read_sig, None)
        if source_before is None and (
            write_path is not None and write_path in read_sigs and content_changed
        ):
            for read_sig in read_sigs[write_path]:
                sig_seen.pop(read_sig, None)
                blocked_cycles.pop(read_sig, None)
        # Circuit breaker: the executor tags orchestrator/container-unreachable
        # failures as infra_dead (2026-07-08: hibernate stopped a container
        # mid-build and the loop kept feeding 500s to the model for 6 minutes).
        # A couple of consecutive infra deaths means the WORLD is gone, not the
        # app — abort honestly instead of burning LLM turns on a corpse.
        if obs.get("infra_dead"):
            infra_dead_streak += 1
            if infra_dead_streak >= _INFRA_DEAD_ABORT_AT:
                print(
                    f"[AGENT] step={step} INFRA-DEAD x{infra_dead_streak} → abort",
                    flush=True,
                )
                return AgentResult(
                    done=False,
                    summary="container/orchestrator unreachable — build aborted",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="infra_error",
                )
        else:
            infra_dead_streak = 0
        if action.name == "build":
            last_build_ok = bool(obs.get("ok"))
            if last_build_ok:
                pending_truncation = False
            fast_check_fresh = last_build_ok
            # Building clears the build-pressure / rotation window: the agent got
            # real typecheck feedback, so writes that follow are progress (a
            # targeted fix), not blind churn.
            writes_since_build = 0
            rewrites_since_build = 0
            blocked_rewrites = 0
            paths_since_build.clear()
            build_pressure_nudged = False
            # A persistent red typecheck the cheap model can't clear is exactly
            # when the stronger reasoner earns its cost. Escalate ONCE (the
            # `escalated` one-shot in `_escalate` bounds it) after two consecutive
            # red builds, so the loop grinds to green with the strong model
            # instead of declaring done on a broken app. Green resets the streak.
            if last_build_ok:
                red_build_streak = 0
            else:
                red_build_streak += 1
                if red_build_streak >= _RED_BUILD_ESCALATE_AT:
                    await _escalate(step, "red_build")
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
        if isinstance(obs.get("files"), dict) and obs["files"]:
            changed_paths: list[str] = []
            for raw_path, raw_content in obs["files"].items():
                if not isinstance(raw_path, str) or not isinstance(raw_content, str):
                    continue
                path = raw_path.replace("\\", "/")
                while path.startswith("./"):
                    path = path[2:]
                written[path] = raw_content
                changed_paths.append(path)
            if changed_paths:
                wrote_since_check = True
        # Only local edit rejection before persistence can prove no mutation.
        # Transport errors and shell operations may have changed the candidate;
        # a later rejected edit cannot restore a check they invalidated.
        rejected_edit_without_mutation = (
            action.name == "edit_file"
            and obs.get("ok") is False
            and obs.get("environment_mutated") is False
            and not any(obs.get(key) for key in (
                "files", "mutation", "invalidated_dimensions", "content_change", "infra_dead",
            ))
        )
        if changed_source_paths:
            wrote_since_check = True
        if action.name != "build" and (
            bool(changed_source_paths)
            or (
                action.name in {"write_file", "edit_file", "delete_file"}
                and not rejected_edit_without_mutation
            )
            or obs.get("files")
            or obs.get("environment_mutated") is True
            or (action.name == "bash" and obs.get("environment_mutated") is not False)
        ):
            fast_check_fresh = False
        convo.append({"role": "user", "content": _format_observation(action, obs)})
        if changed_source_paths and on_progress is not None:
            try:
                await on_progress(AgentResult(
                    done=False, summary="Исходники изменены; сохраняю черновик.",
                    files=dict(written), steps=step + 1, transcript=list(convo),
                    stop_reason="checkpoint",
                ))
            except Exception:
                # Let the pipeline restore the accepted tree. Storage failure
                # must not jump past recovery or consume another model call.
                return AgentResult(
                    done=False, summary="Не удалось сохранить черновик; требуется проверка хранилища.",
                    files=dict(written), steps=step + 1, transcript=list(convo),
                    stop_reason="checkpoint_error",
                )
        if no_write_streak == (_EDIT_NO_WRITE_NUDGE_AT if edit_mode else _NO_WRITE_NUDGE_AT):
            await _nudge_exploration(step)

    if pending_truncation:
        return AgentResult(
            done=False, summary="Ответ модели обрезан; завершение ещё не подтверждено.",
            files=written, steps=max_steps, transcript=convo, stop_reason="output_limit",
        )
    return _ship_green_or(
        AgentResult(
            done=False, summary="hit step budget without calling done",
            files=written, steps=max_steps, transcript=convo, stop_reason="max_steps",
        ),
        max_steps - 1, "budget",
    )


# Infra circuit breaker: consecutive tool ops whose failure was the CONTAINER /
# orchestrator being unreachable (executor tags them infra_dead) → abort with
# stop_reason="infra_error". 3 = tolerates a transient orchestrator restart (one bad
# op, next succeeds) while a truly dead container aborts within ~3 LLM turns
# instead of grinding the full step budget (2026-07-08 incident).
_INFRA_DEAD_ABORT_AT = 3

# Cycle breaker thresholds (no-WRITE streak): after this many consecutive actions
# that write nothing (read_file/grep/list_dir/build), nudge HARD to write; after the
# abort threshold, give up exploring rather than burn the whole step budget. A real
# build writes within a few reads (the seed context is handed up-front), so a long
# read-only streak is always a stall, never legitimate orientation.
_NO_WRITE_NUDGE_AT = 5
_NO_WRITE_ABORT_AT = 14

# Global (non-consecutive) repeat guard: the SAME exact action issued this many
# times without confirmed same-file progress is a cycle (identical writes count).
# Changed-file read counts reset; all other exact repeats nudge, then abort.
_REPEAT_NUDGE_AT = 2
_REPEAT_ABORT_AT = 4

# Build-pressure / rotation guard. A REWRITE of a file already written SINCE THE
# LAST BUILD (rotating across files with varied content, never typechecking) is the
# churn the sig/consecutive/no-write guards all miss. Force a build at the 2nd such
# rewrite; abort as looping at the 5th if it still won't build. Separately, this
# many writes with NO build at all earns one gentle "run build" reminder — high
# enough not to interrupt a legitimate from-scratch multi-file first draft.
_REWRITE_BEFORE_BUILD_NUDGE = 2
_REWRITE_LOOP_ABORT_AT = 5
_WRITES_BEFORE_BUILD_NUDGE = 10

# Escalate to the strong model after this many consecutive RED builds — a cheap
# model that can't clear a typecheck in two tries usually needs a different model,
# not more tries. Bounded by the one-shot `escalated` flag in `_escalate`.
_RED_BUILD_ESCALATE_AT = 2

# How many consecutive "no valid action emitted" turns before aborting as stalled.
# Was an implicit 2 (abort on the 2nd miss). Raised so the loop can re-prompt AND
# escalate to the strong model before giving up — the cheap model intermittently
# emits zero visible action, which used to kill the whole first build at step 2.
_NO_ACTION_ABORT_AT = 4

# Issued when the loop stalls (no-write streak) but the app is already GREEN —
# nothing left to build, the model is just thrashing after success. Push it to
# finish rather than invent more work.
_DONE_WHEN_GREEN_NUDGE = (
    "STOP — the build is CLEAN and the main route already renders (verified). The "
    "app is DONE. Do NOT run more bash / build / reads. Call "
    '<omnia:action name="done">{"summary": "what you built"}</omnia:action> NOW. '
    "Preserve the verified working result."
)

_REPEAT_CYCLE_NUDGE = (
    "STOP — you have ALREADY issued this EXACT action before (same file + same "
    "content, or the same command); repeating it changes NOTHING and the build is "
    "not advancing. The file is already written. Move to the NEXT unfinished step: "
    "write a still-MISSING page/file, fix the specific build error you were shown, "
    "or — if the build is clean and every screen exists — call done. Never re-write "
    "a file with the same content you already wrote."
)

_BUILD_PRESSURE_NUDGE = (
    "STOP writing. You have written several files — and started REWRITING files you "
    "already wrote — WITHOUT running `build` even once. That is exactly how a build "
    "churns forever and never finishes (you keep tweaking blind instead of letting "
    "the typecheck tell you what is actually wrong). Your VERY NEXT action MUST be "
    '<omnia:action name="build">{}</omnia:action>. Read the REAL errors it returns, '
    "fix the specific file/line it names, and once the build is clean call done. Do "
    "NOT rewrite a file you already wrote before you have run build."
)

_EXPLORE_STALL_NUDGE = (
    "Discovery is paused because no source changed. Use the real compiler "
    "diagnostic and the current task: fix the named file, or create the next "
    "required page and its dependencies. Do not assume a component, user field "
    "or schema column exists. Implement any needed component before importing it. "
    "Then run build after the last change; when the source is complete, call done."
)

# Edit-mode nudges. The build nudges above name ENTITY primitives (CrudResource,
# dashboard/page.tsx) — nonsense on a realtime / other-stack EDIT. Observed live:
# an "add member by name" edit on a realtime app got nudged to write a
# <CrudResource> page, so even the escalated strong model ignored the nudge and
# kept reading (14 steps, 0 writes → honest no-change, the «правка ничего не
# меняет» bug). A point edit needs a stack-agnostic "stop reading, emit the patch"
# push instead, and it needs it SOONER than a from-scratch build.
_EDIT_NO_WRITE_NUDGE_AT = 3

_EDIT_EXPLORE_STALL_NUDGE = (
    "STOP READING. You have already located the code for this change — that is "
    "ENOUGH. Do NOT read_file / grep / list_dir / bash again. Your VERY NEXT action "
    "MUST be edit_file or write_file that implements the requested change (add the "
    "UI control, wire the call, fix the handler — whatever was asked). After "
    "writing, run build; when it is clean, call done. Emitting the file patch is "
    "the ONLY way to apply the edit — another read applies nothing."
)

_EDIT_REPEAT_CYCLE_NUDGE = (
    "STOP — you have already issued this exact action; repeating it applies "
    "NOTHING and the edit is not advancing. You have the context you need. Emit the "
    "edit_file / write_file patch for the requested change NOW, then build, then "
    "done. Do not read the same file again."
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
- read_file  {"path": "src/app/page.tsx", "start_line": 1, "end_line": 80} — optional 1-based inclusive line range; narrow it if output is truncated
- grep       {"pattern": "regex", "path": "src"}
- docs       {"library": "drizzle-orm", "query": "select where"}  — pull CURRENT official docs/signatures for an EXTERNAL library (Next.js, Drizzle, NextAuth, aiogram…) when unsure of an API; use it INSTEAD of guessing
- provider_docs {"provider": "aitunnel", "query": "authentication and chat completions"} — read bounded official provider documentation; never pass credentials or arbitrary URLs
- write_file {"path": "...", "content": "FULL FILE CONTENT"}   — create/overwrite a whole file
- edit_file  {"path": "...", "search": "EXACT TEXT", "replace": "NEW TEXT"}
- build      {}                                — real typecheck; returns the actual errors
- bash       {"cmd": "npm run lint"}           — run a shell command in the container (lint/test/install)
- read_logs  {}                                — live dev-server stdout/stderr (find RUNTIME crashes build can't see)
- runtime_check {"path": "/dashboard"}         — open a real route, get the REAL HTTP status + crash file
- done       {"summary": "what you built"}     — ONLY after a clean build and the main route renders

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
what it points at. Repeat build→fix until clean. Outside MAX FINALIZATION HANDOFF, \
verify it actually RUNS: \
`runtime_check {"path":"/dashboard"}` — a typecheck-clean app can still 5xx on render. If it \
fails, `read_logs` to inspect the real runtime error, fix the named file, re-check. Call \
`done` only after the build is clean and the route renders. In MAX FINALIZATION \
HANDOFF, call done after fresh clean FAST_CHECK; the coordinator must still verify \
full build, runtime and release before acceptance.
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
- read_file  {"path": "...", "start_line": 1, "end_line": 80} — optional 1-based inclusive range; read the exact edit context and full SDK signature
- edit_file  {"path": "...", "search": "EXACT TEXT", "replace": "NEW TEXT"} — preferred: minimal patch
- write_file {"path": "...", "content": "FULL FILE"} — only when creating a new file
- list_dir   {"path": "..."}
- build      {}                                     — typecheck; fix real errors
- bash       {"cmd": "..."}                         — run a shell command if needed
- read_logs  {}                                     — live dev-server logs (runtime errors)
- runtime_check {"path": "/"}                        — open the changed route, confirm it still renders
- done       {"summary": "what changed"}            — in MAX FINALIZATION HANDOFF after fresh clean FAST_CHECK; otherwise after build and runtime_check

RULES:
- This is a SURGICAL EDIT. Change the minimum. Do NOT regenerate entities/pages \
that already work. The engine, auth, RBAC, kit and globals are fixed template \
files — never touch them. To add a data section, add `entities/<Name>.json` + a \
page that renders <CrudResource entity="Name"/>.
- grep/read to find the exact spot, prefer edit_file (search must be copied \
byte-for-byte from what you read), build, fix, done.
- Be fast and minimal — a small edit needs only a few steps. One action per reply."""


def build_edit_system_prompt(stack_guide: str | None = None) -> str:
    """Compose the surgical editor with the current stack's hard invariants.

    Builds already receive ``SYSTEM_PROMPT.md`` through ``build_system_prompt``.
    Edits historically used the generic entity-engine prompt alone, which let an
    edit on a MAX Mini App remove Bridge/session/webhook primitives or pivot into
    another platform. Appending the selected stack guide keeps edits surgical
    while preserving the platform contract. Empty input is byte-identical to the
    legacy prompt.
    """
    if not stack_guide or not stack_guide.strip():
        return EDIT_SYSTEM_PROMPT
    return (
        EDIT_SYSTEM_PROMPT
        + "\n\nSTACK-SPECIFIC CONTRACT — preserve these invariants on every edit:\n"
        + stack_guide.strip()
    )


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
- read_file  {"path": "src/app/page.tsx", "start_line": 1, "end_line": 80} — optional 1-based inclusive line range; narrow it if output is truncated
- grep       {"pattern": "regex", "path": "src"}
- docs       {"library": "drizzle-orm", "query": "select where"}  — pull CURRENT official docs/signatures for an EXTERNAL library (Next.js, Drizzle, NextAuth, aiogram…) when unsure of an API; use it INSTEAD of guessing
- provider_docs {"provider": "aitunnel", "query": "authentication and chat completions"} — read bounded official provider documentation; never pass credentials or arbitrary URLs
- write_file {"path": "...", "content": "FULL FILE CONTENT"}   — create/overwrite a whole file
- edit_file  {"path": "...", "search": "EXACT TEXT", "replace": "NEW TEXT"}
- build      {}                                — real typecheck; returns the actual errors
- bash       {"cmd": "pnpm test"}              — run a shell command (lint / test / install)
- read_logs  {}                                — live dev-server stdout/stderr (RUNTIME errors build can't see)
- runtime_check {"path": "/"}                  — open a real route, get the REAL HTTP status + crash file
- probe      {"method":"POST","path":"/api/...","body":{...}}  — outside MAX FINALIZATION HANDOFF, make a REAL request AS A LOGGED-IN test user; returns the EXACT status+body. The only way to prove an interactive feature works end-to-end (catches a 4xx on a user POST that build/runtime_check miss)
- verify_isolation {"create":{"method":"POST","path":"/api/<resource>","body":{...}},"read":{"path":"/api/<resource>/{id}"}}  — outside MAX FINALIZATION HANDOFF, PROVE no data leak: logs in TWO users, user A creates the resource, then asserts user B is DENIED reading it AND it is absent from B's list. Run this for EVERY owned resource — a green build never proves isolation
- done       {"summary": "what you built"}     — in MAX FINALIZATION HANDOFF after fresh clean FAST_CHECK; otherwise after build, runtime and functional probes

WORK STYLE: explore MINIMALLY, spend most steps WRITING, never repeat an identical \
read or write, never ask the user questions — decide and act. When an EXTERNAL library's \
API bites you (a build error about a wrong signature, a renamed export, a removed option), \
call `docs` for that library BEFORE guessing — current docs beat a stale memory. When you author tests, \
run them with bash. Outside MAX FINALIZATION HANDOFF, after the build is clean, \
`runtime_check` the main route(s) — a \
typecheck-clean app can still crash on render; if it 5xx, `read_logs`, fix, re-check. \
Outside MAX FINALIZATION HANDOFF, for an INTERACTIVE feature (send a message, save, submit a form, log in), a clean build \
and a 200 page do NOT prove it works — the real failure is a 4xx on the user's POST that \
a screenshot can never show. After editing one, `read_logs` and look for a 4xx/5xx with \
its reason — the server logs the EXACT cause (e.g. a rejected/mismatched field); fix until \
the action's own request is 2xx, not just until the page loads. PROVE it with `probe`: \
perform the real action as a logged-in user (e.g. probe POST to create a resource, then \
probe POST to act on it) and require a 2xx with the expected body before `done`. \
For any OWNED data (records a user creates and others must not see), scope EVERY query \
by the authenticated user (the session user id) — never return rows the current user \
does not own, and protect every data route with auth. Outside MAX FINALIZATION HANDOFF, prove it with `verify_isolation` \
on that resource: a green build and a working create do NOT prove that another user can't \
read it. Fix until isolation passes before `done`. \
In MAX FINALIZATION HANDOFF, done hands off a candidate, not an accepted product: \
the server coordinator owns mandatory full build, signed runtime and release checks. \
Keep authenticated owner scoping and applicable isolated local tests; A/B owner isolation remains unverified \
unless an actual two-user check provides evidence. Report that limit honestly. \
Do not call deferred runtime/probe tools from this source-editing loop. \
One action per reply."""


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


def is_agentic_enabled(
    global_flag: bool, canary_csv: str | None, user_id: str | None
) -> bool:
    """Whether the agentic builder runs for THIS request.

    True when the global flag is on (everyone), OR the user is in the canary list
    (comma-separated user ids). This lets the agent loop be dogfooded on prod for
    SPECIFIC users without flipping it on for everyone — there is no per-project
    canary, so a per-user allowlist is how «flip on a canary» is done safely.
    Pure → unit-tested. Empty canary + global off → False (today's behaviour)."""
    if global_flag:
        return True
    if not canary_csv or not user_id:
        return False
    ids = {u.strip() for u in canary_csv.split(",") if u.strip()}
    return str(user_id) in ids


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


#: Export names this card PROMISES per locked module — the drift guard asserts each
#: still exists in the live template file. Update both together if the card changes.
REALTIME_CONTRACT_EXPORTS: dict[str, tuple[str, ...]] = {
    "src/lib/db/schema.ts": ("Channel", "Message", "User"),
    "src/lib/channels.ts": (
        "listUserChannels",
        "createChannel",
        "isMember",
        "addMemberByEmail",
        "getHistory",
    ),
    "src/lib/session.ts": ("APP_HOME", "getCurrentUser", "requireUser", "CurrentUser"),
    "src/lib/auth.ts": ("hashPassword", "roleForNewUser", "signIn", "signOut", "auth"),
    "src/components/realtime/use-channel.ts": ("useChannel", "UseChannelOpts"),
    "src/components/realtime/use-channel-history.ts": ("useChannelHistory",),
    "src/components/realtime/invite-member.tsx": ("InviteMember",),
    "src/lib/realtime/types.ts": ("RealtimeEvent", "PresenceState"),
}


# ── Production executor (talks to the orchestrator) ─────────────────────────

_BUILD_MOD_ERR_RE = re.compile(r"""(?:Cannot find module|Module)\s*['"]([^'"]+)['"]""")
_BUILD_NO_MEMBER_RE = re.compile(
    r"""['"]([^'"]+)['"]\s+has no exported member\s+['"]([^'"]+)['"]"""
)
_EXPORT_DECL_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function|const|let|class|type|interface|enum)\s+([A-Za-z0-9_]+)"
)
_EXPORT_LIST_RE = re.compile(r"export\s*\{([^}]+)\}")
def make_docs_media_executor(
    *,
    project_id: Any,
    emit: Any = None,
) -> Executor:
    """Действия агента, которые ничего не знают о среде проекта.

    Всё, что трогает код приложения (чтение, запись, сборка, команды,
    проверки среды), исполняет ячейка проекта. Здесь остались три действия,
    у которых среды просто нет: справка по внешней библиотеке, справка по
    провайдеру интеграции и генерация картинки или короткого видео.

    ``emit`` (тот же обратный вызов, что и у цикла) позволяет многошаговой
    генерации медиа показывать свои внутренние шаги в живой ленте; без него
    они идут молча.
    """
    from yleum_api.core.config import get_settings

    # Бюджет видео на ОДНУ сборку (исполнитель создаётся один раз за сборку).
    # Ролик стоит примерно ₽60 на балансе Yleum — это в сорок раз дороже
    # картинки и без кошелькового ограничения, поэтому число разных роликов
    # за сборку ограничено (разбор 2026-07-17). Список — чтобы замыкание
    # могло его увеличивать.
    _video_used = [0]

    async def _execute(action: Action) -> dict[str, Any]:
        try:
            if action.name == "docs":
                # Up-to-date EXTERNAL-library docs from Context7 — so the model uses
                # the real CURRENT API instead of a hallucinated/stale one (the #1
                # source of build-loop / edit-fail churn). Fail-soft: a miss returns
                # ok=False with a «continue from what you know» nudge, never raises.
                from yleum_api.services import context7_client
                _lib = str(action.args.get("library") or action.args.get("lib") or "")
                _q = str(action.args.get("query") or action.args.get("topic") or "")
                _docs = await context7_client.fetch_docs(_lib, _q)
                if not _docs:
                    return {
                        "ok": False,
                        "error": f"no up-to-date docs for {_lib!r} — continue from what you know",
                    }
                return {
                    "ok": True,
                    "content": _truncate(_docs, _MAX_READ_CHARS),
                    "detail": f"docs: {_lib} / {_q}",
                }

            if action.name == "provider_docs":
                from yleum_api.services import provider_docs

                provider_key = str(action.args.get("provider") or "").strip()
                query = str(action.args.get("query") or "").strip()
                result = await provider_docs.fetch_provider_docs(provider_key, query)
                return {
                    "ok": result.status != "error",
                    "status": result.status,
                    "summary": result.summary,
                    "content": _truncate(result.content, _MAX_READ_CHARS),
                    "next_actions": list(result.next_actions),
                    "artifacts": list(result.artifacts),
                    "detail": f"provider docs: {provider_key}",
                }

            if action.name == "generate_media":
                # Real ASSET: generate a photoreal image (flux) or a short cinematic
                # video (Kling: Flux first+last frame → interpolate) on the same
                # key, store it in MinIO, and hand the agent a public URL to embed.
                # Lazily imported (MinIO + gateway).
                from yleum_api.services import agent_media

                _kind = str(action.args.get("kind") or "image").strip().lower()
                if _kind == "video":
                    _cap = max(1, int(get_settings().video_gen_max_unique))
                    if _video_used[0] >= _cap:
                        return {
                            "ok": False,
                            "error": f"video budget reached ({_cap} clip(s) this build) — "
                                     "reuse an existing clip or use a Flux image instead.",
                        }
                    _video_used[0] += 1
                _dur = action.args.get("duration")
                return await agent_media.generate_media(
                    project_id,
                    kind=_kind,
                    prompt=str(action.args.get("prompt") or ""),
                    duration=int(_dur) if isinstance(_dur, (int, float)) else None,
                    aspect=action.args.get("aspect"),
                    first_frame=action.args.get("first_frame"),
                    last_frame=action.args.get("last_frame"),
                    first_frame_url=action.args.get("first_frame_url"),
                    last_frame_url=action.args.get("last_frame_url"),
                    image_url=action.args.get("image_url"),
                    emit=emit,
                )

            return {"ok": False, "error": f"unknown action {action.name}"}
        except Exception as exc:  # never let an executor crash kill the loop
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return _execute
