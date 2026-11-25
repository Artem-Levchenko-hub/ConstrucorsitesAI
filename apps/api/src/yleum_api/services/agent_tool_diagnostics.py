"""Allowlisted, attempt-local diagnostics for both agent tool protocols."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from re import compile as compile_pattern
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

if TYPE_CHECKING:
    from yleum_api.services.agent_builder import Action


AGENT_ACTIONS = frozenset({
    "list_dir", "read_file", "grep", "docs", "provider_docs", "write_file", "edit_file", "build",
    "bash", "read_logs", "runtime_check", "generate_media", "probe", "verify_isolation", "done",
})
_TOOL_NAMES = AGENT_ACTIONS | {"delete_file", "stage_patch"}
_STATES = frozenset({"started", "succeeded", "failed", "skipped", "cancelled", "unknown"})
_REASONS = frozenset({
    "build_required", "cached_result", "cancelled", "context_mismatch", "discovery_pause",
    "dispatch_rejected",
    "done_blocked", "empty_response", "executable_not_found", "executor_error",
    "infrastructure_unavailable", "invalid_tool", "invalid_tool_arguments", "missing_action",
    "missing_file", "nonzero_exit", "outcome_unknown", "reader_unavailable",
    "recovery_contract", "repeat_guard", "replayed_result", "source_change_required",
    "source_repair_required", "timeout", "tool_failed", "verification_deferred",
    "workspace_sync_failed",
})
_COMMAND_FAMILIES = frozenset({
    "bash", "sh", "dash", "zsh", "busybox", "rg", "grep", "find", "ls", "head", "tail", "cat",
    "sed", "awk", "jq", "node", "npx", "npm", "pnpm", "yarn", "bun", "bunx", "deno", "tsc", "tsx",
    "python", "python3", "pip", "pip3", "uv", "pytest", "make", "git", "curl", "wget", "sqlite3",
    "psql", "docker", "cp", "mkdir", "mv", "rm", "touch", "sort", "wc", "timeout", "env",
})
_COMMAND_PREFIX = compile_pattern(r"[ \t]*([a-zA-Z0-9_./+-]+)(?=$|[\s;&|<>])")
_MISSING_EXECUTABLE = compile_pattern(
    r"(?:\[shell\] )?(?:(?:/usr)?/bin/)?(?:sh: (?:[0-9]+: )?([\w.-]+): not found"
    r"|bash: (?:line [0-9]+: )?([\w.-]+): command not found)",
)
_TIMING_KEYS = ("dispatch", "workspace_sync", "total", "reader_wait")
_EFFECTFUL_TOOLS = frozenset({
    "bash", "build", "write_file", "edit_file", "delete_file", "stage_patch", "generate_media",
})
_SAFE_INTEGER = (1 << 53) - 1  # round-trips through the browser without losing precision
_LIFECYCLE_WRITE_TIMEOUT_SECONDS = 2.0
_log = logging.getLogger(__name__)

_Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
_Executor = Callable[["Action"], Awaitable[dict[str, Any]]]
_shell_metadata: ContextVar[dict[str, object] | None] = ContextVar(
    "agent_shell_metadata", default=None,
)
_tool_invocation: ContextVar[ToolInvocation | None] = ContextVar(
    "agent_tool_invocation", default=None,
)


def safe_tool_name(value: object) -> str:
    return value if isinstance(value, str) and value in _TOOL_NAMES else "tool"


def _known_command(value: object) -> str | None:
    return value if isinstance(value, str) and value in _COMMAND_FAMILIES else None


def missing_shell_executable(detail: str) -> str | None:
    match = _MISSING_EXECUTABLE.fullmatch(detail.strip())
    return _known_command(match[1] or match[2]) if match is not None else None


def _observed_missing_executable(value: Mapping[str, object]) -> str | None:
    if value.get("reason_code") == "executable_not_found":
        return _known_command(value.get("missing_executable"))
    return None


def _elapsed(value: object) -> bool:
    return type(value) is int and 0 <= value <= _SAFE_INTEGER


def _measurements(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    exit_code = value.get("exit_code")
    if type(exit_code) is int and -(1 << 31) <= exit_code < (1 << 31):
        safe["exit_code"] = exit_code
    timings = value.get("timings_ms")
    if isinstance(timings, Mapping):
        safe_timings = {key: timings[key] for key in _TIMING_KEYS if _elapsed(timings.get(key))}
        if safe_timings:
            safe["timings_ms"] = safe_timings
    return safe


def safe_tool_diagnostic(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    call_id, state = value.get("call_id"), value.get("state")
    if not isinstance(call_id, str) or len(call_id) != 36:
        return {}
    try:
        identity = UUID(call_id)
    except ValueError:
        return {}
    if identity.version not in (4, 5) or str(identity) != call_id:
        return {}
    if not isinstance(state, str) or state not in _STATES:
        return {}
    safe = {"call_id": call_id, "state": state, **_measurements(value)}
    reason = value.get("reason_code")
    if isinstance(reason, str) and reason in _REASONS:
        safe["reason_code"] = reason
    elapsed = value.get("elapsed_ms")
    if _elapsed(elapsed):
        safe["elapsed_ms"] = elapsed
    if (command := _known_command(value.get("command_family"))) is not None:
        safe["command_family"] = command
    if (missing := _observed_missing_executable(value)) is not None:
        safe["missing_executable"] = missing
    if value.get("reconciled") is True and state in {"succeeded", "failed", "skipped"}:
        safe["reconciled"] = True
    return safe


@contextmanager
def capture_shell_diagnostics() -> Iterator[dict[str, object]]:
    # Keep timestamps off the observation. The recovery driver compares the
    # exact observation with the immutable result receipt on cold resume.
    metadata: dict[str, object] = {}
    token = _shell_metadata.set(metadata)
    try:
        yield metadata
    finally:
        _shell_metadata.reset(token)


def record_shell_diagnostics(value: Mapping[str, object]) -> None:
    target = _shell_metadata.get()
    if target is None:
        return
    target.update(_measurements(value))
    for key in ("timed_out", "replayed_result"):
        if type(value.get(key)) is bool:
            target[key] = value[key]
    outcome = value.get("shell_outcome")
    if isinstance(outcome, str) and outcome in {"not_started", "unknown", "terminal"}:
        target["shell_outcome"] = outcome
    reason = value.get("reason_code")
    if isinstance(reason, str) and reason in _REASONS:
        target["reason_code"] = reason
        target.pop("missing_executable", None)
        if (missing := _observed_missing_executable(value)) is not None:
            target["missing_executable"] = missing


def _command_family(action: Action) -> str | None:
    if action.name != "bash":
        return None
    command = action.args.get("cmd")
    if isinstance(command, str) and (match := _COMMAND_PREFIX.match(command)):
        return _known_command(match[1].rsplit("/", 1)[-1])
    return None


def _returned_failure(action: Action, observation: Mapping[str, object]) -> str:
    error = observation.get("error")
    if action.name in {"read_file", "edit_file", "delete_file"} and isinstance(error, str):
        if error.startswith("not found: "):
            return "missing_file"
        if error in {
            "search text not found exactly; read the file and copy it byte-for-byte",
            "search text is not unique; add surrounding lines",
        }:
            return "context_mismatch"
    if observation.get("infra_dead") is True:
        return "infrastructure_unavailable"
    if observation.get("deferred_to") == "max_finalization":
        return "verification_deferred"
    return "tool_failed"


def skipped_tool_diagnostic(reason: str) -> dict[str, object]:
    return safe_tool_diagnostic({
        "call_id": str(uuid4()), "state": "skipped", "reason_code": reason,
    })


@dataclass
class ToolInvocation:
    emit: _Emit | None
    step: int
    action: Action
    dispatch_deadline: float | None = None
    _call_id: str = field(default_factory=lambda: str(uuid4()), init=False)
    _result: dict[str, object] = field(default_factory=dict, init=False)
    _start: float | None = field(default=None, init=False)
    _elapsed_ms: int | None = field(default=None, init=False)
    _intent_handled: bool = field(default=False, init=False)
    _reported: bool = field(default=False, init=False)
    _durable: bool = field(default=False, init=False)
    _reconciling: bool = field(default=False, init=False)
    _continuation_finalized: bool = field(default=False, init=False)
    _pending_metadata: Mapping[str, object] | None = field(default=None, init=False)

    def _diagnostic(self, result: Mapping[str, object]) -> dict[str, object]:
        diagnostic = {
            "call_id": self._call_id, "command_family": _command_family(self.action), **result,
        }
        if self._elapsed_ms is not None and result.get("state") != "started":
            diagnostic["elapsed_ms"] = self._elapsed_ms
        return safe_tool_diagnostic(diagnostic)

    async def _emit_lifecycle(self, event: str, result: Mapping[str, object]) -> None:
        if self.emit is None:
            return
        try:
            async with asyncio.timeout(_LIFECYCLE_WRITE_TIMEOUT_SECONDS):
                await self.emit(event, {
                    "step": self.step, "action": safe_tool_name(self.action.name),
                    "tool_diagnostic": self._diagnostic(result),
                })
        except Exception:
            # Additive trace writes must not turn a tool outcome into a retry.
            _log.warning("agent_tool_diagnostic_write_failed", extra={
                "event_type": event, "call_id": self._call_id,
                "tool": safe_tool_name(self.action.name),
            })

    async def start_intent(self) -> None:
        self._intent_handled = True
        if (
            self.dispatch_deadline is None
            or self.dispatch_deadline - monotonic() > _LIFECYCLE_WRITE_TIMEOUT_SECONDS
        ):
            await self._emit_lifecycle("agent.tool_started", {"state": "started"})

    async def bind_durable(self, call_id: UUID, *, reconcile_only: bool) -> None:
        self._call_id = str(call_id)
        self._durable = True
        self._reconciling = reconcile_only
        if not reconcile_only:
            await self.start_intent()

    def _set_result(
        self, state: str, reason: object = None, metadata: Mapping[str, object] | None = None,
    ) -> None:
        evidence = _measurements(metadata or {})
        if metadata is not None and metadata.get("shell_outcome") != "terminal":
            evidence.pop("exit_code", None)
        if metadata is not None and (missing := _observed_missing_executable(metadata)) is not None:
            evidence["missing_executable"] = missing
        self._result = {"state": state, "reason_code": reason, **evidence}

    def skip(self, reason: str) -> None:
        self._set_result("skipped", reason)

    def fail_context(self) -> None:
        self._result.update(state="failed", reason_code="context_mismatch")

    def finish_continuation(self, observation: Mapping[str, object]) -> None:
        self._continuation_finalized = True
        if not self._result:
            self._reconciling = self._reconciling or self._pending_metadata is None
            self._observe(observation, self._pending_metadata or {})
        if self._durable and self._reconciling and self._result["state"] in {
            "succeeded", "failed", "skipped",
        }:
            self._result["reconciled"] = True

    def note_deadline(self) -> None:
        if self._intent_handled and self._result.get("state") in (None, "cancelled"):
            if self._durable and not self._continuation_finalized:
                self.record_error(TimeoutError())
            else:
                self._set_result("unknown", "timeout", self._result)

    def _unconfirmed_continuation_error(self, error: BaseException) -> None:
        metadata = self._pending_metadata or {}
        before_dispatch = not self._reconciling and metadata.get("shell_outcome") == "not_started"
        if isinstance(error, asyncio.CancelledError):
            uncertain = self._reconciling or (self._start is not None and not before_dispatch)
            self._set_result("unknown" if uncertain else "cancelled", "cancelled")
        elif before_dispatch:
            reason = metadata.get("reason_code") or "dispatch_rejected"
            self._set_result("skipped", reason, metadata)
        elif self._reconciling or self._start is not None or self._result.get("state") != "skipped":
            reason = "timeout" if isinstance(error, TimeoutError) else "outcome_unknown"
            self._set_result("unknown", reason)

    def record_error(
        self, error: BaseException, metadata: Mapping[str, object] | None = None,
    ) -> None:
        if self._durable and not self._continuation_finalized:
            self._unconfirmed_continuation_error(error)
            return
        if self._result:
            return
        metadata = metadata or {}
        reason = metadata.get("reason_code")
        outcome = metadata.get("shell_outcome")
        if isinstance(error, asyncio.CancelledError):
            state, reason = "cancelled", "cancelled"
        elif outcome == "not_started":
            state, reason = "skipped", reason or "dispatch_rejected"
        elif outcome == "unknown" or self._reconciling:
            state = "unknown"
            reason = reason or ("timeout" if isinstance(error, TimeoutError) else "outcome_unknown")
        elif metadata.get("timed_out") is True:
            state, reason = "failed", "timeout"
        elif isinstance(error, TimeoutError):
            state, reason = "unknown", "timeout"
        elif (
            self._start is not None and self.action.name in _EFFECTFUL_TOOLS
            and outcome is None
        ):
            state, reason = "unknown", reason or "outcome_unknown"
        elif isinstance(error, Exception):
            state, reason = "failed", reason or "executor_error"
        else:
            state, reason = "unknown", "outcome_unknown"
        self._set_result(state, reason, metadata)

    def _observe(self, observation: Mapping[str, object], metadata: Mapping[str, object]) -> None:
        replay = self._reconciling or metadata.get("replayed_result") is True
        if replay and not self._durable:
            self._elapsed_ms = None
            self.skip("replayed_result")
            return
        if self._durable and not self._continuation_finalized:
            # The executor response is provisional until the driver accepts and
            # consumes its logical observation, on fresh dispatch and recovery.
            self._pending_metadata = metadata
            return
        if replay:
            # A receipt may preserve a real exit. Re-reading it does not measure
            # the original dispatch or workspace synchronization again.
            self._elapsed_ms = None
            metadata = {key: value for key, value in metadata.items() if key != "timings_ms"}
        reason = metadata.get("reason_code")
        outcome = metadata.get("shell_outcome")
        if outcome == "not_started":
            self._set_result("skipped", reason or "dispatch_rejected", metadata)
        elif outcome == "unknown" or observation.get("indeterminate") is True:
            timings_only = {key: value for key, value in metadata.items() if key == "timings_ms"}
            self._set_result("unknown", reason or "outcome_unknown", timings_only)
        elif observation.get("ok") is True:
            self._set_result("succeeded", metadata=metadata)
        elif observation.get("ok") is False:
            if not reason:
                if metadata.get("timed_out") is True:
                    reason = "timeout"
                elif metadata.get("exit_code") not in (None, 0):
                    reason = "nonzero_exit"
                else:
                    reason = _returned_failure(self.action, observation)
            self._set_result("failed", reason, metadata)
        else:
            self._set_result("unknown", "outcome_unknown", metadata)

    async def execute(self, executor: _Executor) -> dict[str, Any]:
        from yleum_api.services.generation.recovery_reconciliation import _TOOL_EFFECT

        cursor = _TOOL_EFFECT.get()
        self._reconciling = self._reconciling or (cursor is not None and cursor.reconcile_only)
        if not self._reconciling:
            if not self._intent_handled:
                await self.start_intent()
            self._start = monotonic()
        with capture_shell_diagnostics() as metadata:
            try:
                observation = await executor(self.action)
            except BaseException as error:
                if self._durable and not self._continuation_finalized:
                    self._pending_metadata = metadata
                else:
                    self.record_error(error, metadata)
                raise
            finally:
                if self._start is not None:
                    self._elapsed_ms = round((monotonic() - self._start) * 1_000)
        self._observe(observation, metadata)
        return observation

    def step_fields(self) -> dict[str, object]:
        self._reported = True
        return {"tool_diagnostic": self._diagnostic(self._result)} if self._result else {}

    async def finish(self) -> None:
        if not self._reported and self._result:
            await self._emit_lifecycle("agent.tool_finished", self._result)


async def bind_durable_tool_diagnostic(
    action: object, root_effect_id: UUID, index: int, *, reconcile_only: bool,
) -> None:
    invocation = _tool_invocation.get()
    if invocation is not None and invocation.action is action:
        call_id = uuid5(root_effect_id, f"agent-tool-diagnostic-v1:{index}")
        await invocation.bind_durable(call_id, reconcile_only=reconcile_only)


@asynccontextmanager
async def trace_tool(
    emit: _Emit | None, step: int, action: Action, *, dispatch_deadline: float | None = None,
) -> AsyncIterator[ToolInvocation]:
    invocation = ToolInvocation(emit, step, action, dispatch_deadline)
    token = _tool_invocation.set(invocation)
    try:
        try:
            yield invocation
        except BaseException as error:
            invocation.record_error(error)
            # Preserve the original exception, including cancellation/deadline.
            with suppress(asyncio.CancelledError):
                await invocation.finish()
            raise
        else:
            await invocation.finish()
    finally:
        _tool_invocation.reset(token)
