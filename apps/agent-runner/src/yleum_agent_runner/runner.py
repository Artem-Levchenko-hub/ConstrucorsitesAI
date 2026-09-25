"""Trusted Project Cell runner core.

This package stays intentionally narrow:
- operation identity is explicit and immutable;
- messages auth is injectable and replaceable;
- tool completion emits safe durable evidence in sequence order;
- no generated workspace code is imported or executed here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from .messages_auth import MessagesAuthFactory

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
)
_OMITTED_VALUE_KEYS = {
    "body",
    "content",
    "env",
    "environment",
    "headers",
    "output",
    "stderr",
    "stdout",
    "traceback",
}


class ExecutorClient(Protocol):
    async def execute(self, action: Any, identity: RunnerIdentity) -> dict[str, Any]: ...


class ControlClient(Protocol):
    async def cancel_requested(self, identity: RunnerIdentity) -> bool: ...


class EventSink(Protocol):
    async def emit(self, event: RunnerEvent) -> None: ...


class LoopResult(Protocol):
    done: bool
    summary: str
    files: Mapping[str, str]
    steps: int
    stop_reason: str


class MessagesAuthProvider(Protocol):
    def headers(self, identity: RunnerIdentity) -> Mapping[str, str] | None: ...

    def auth_factory(self, identity: RunnerIdentity) -> MessagesAuthFactory | None: ...


NativeLoop = Callable[..., Awaitable[LoopResult]]


class RunnerCancelled(RuntimeError):
    """Cancellation fence observed before loop completion."""


@dataclass(frozen=True, slots=True)
class RunnerIdentity:
    project_id: UUID
    run_id: UUID
    session_id: UUID
    workspace_id: UUID
    fencing_epoch: int
    cancel_epoch: int

    def __post_init__(self) -> None:
        if self.fencing_epoch <= 0:
            raise ValueError("fencing_epoch must be positive")
        if self.cancel_epoch < 0:
            raise ValueError("cancel_epoch must be non-negative")


@dataclass(frozen=True, slots=True)
class StaticBearerMessagesAuth:
    bearer_token: str
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.bearer_token.strip():
            raise ValueError("bearer_token is required")

    def headers(self, identity: RunnerIdentity) -> Mapping[str, str]:
        _ = identity
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            **dict(self.extra_headers),
        }

    def auth_factory(self, identity: RunnerIdentity) -> MessagesAuthFactory | None:
        _ = identity
        return None


@dataclass(frozen=True, slots=True)
class RunnerEvent:
    seq: int
    event_type: str
    session_id: UUID
    run_id: UUID
    workspace_id: UUID
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    action: str
    ok: bool
    path: str | None = None
    changed_paths: tuple[str, ...] = ()
    bytes_written: int | None = None
    content_sha256: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class RunnerOutcome:
    done: bool
    summary: str
    files: dict[str, str]
    steps: int
    stop_reason: str
    events_emitted: int
    emitted_events: tuple[RunnerEvent, ...] = ()
    tool_evidence: tuple[ToolEvidence, ...] = ()


@dataclass(slots=True)
class TrustedRunner:
    identity: RunnerIdentity
    messages_url: str
    messages_auth: MessagesAuthProvider
    control_client: ControlClient
    executor_client: ExecutorClient
    event_sink: EventSink
    native_loop: NativeLoop

    async def run(self, *, system: str, task: str, max_steps: int = 24) -> RunnerOutcome:
        seq = 0
        cancel_emitted = False
        emitted_events: list[RunnerEvent] = []
        tool_evidence: list[ToolEvidence] = []

        def cancelled_outcome() -> RunnerOutcome:
            return RunnerOutcome(
                done=False,
                summary="Run cancelled before completion.",
                files={},
                steps=len(tool_evidence),
                stop_reason="cancelled",
                events_emitted=seq,
                emitted_events=tuple(emitted_events),
                tool_evidence=tuple(tool_evidence),
            )

        async def record_event(event_type: str, payload: Mapping[str, Any]) -> None:
            nonlocal seq
            seq += 1
            event = RunnerEvent(
                seq=seq,
                event_type=event_type,
                session_id=self.identity.session_id,
                run_id=self.identity.run_id,
                workspace_id=self.identity.workspace_id,
                payload=_sanitize_payload(dict(payload)),
            )
            await self.event_sink.emit(event)
            emitted_events.append(event)

        async def emit_cancel() -> None:
            nonlocal cancel_emitted
            if cancel_emitted:
                return
            cancel_emitted = True
            changed_paths = tuple(
                path
                for evidence in tool_evidence
                for path in evidence.changed_paths
            )
            await record_event(
                "runner.cancelled",
                {
                    "cancel_epoch": self.identity.cancel_epoch,
                    "completed_tools": len(tool_evidence),
                    "changed_paths": list(changed_paths),
                },
            )

        async def fail_if_cancelled() -> None:
            if await self.control_client.cancel_requested(self.identity):
                await emit_cancel()
                raise RunnerCancelled("cancel requested")

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            await fail_if_cancelled()
            await record_event(event_type, payload)

        async def execute(action: Any) -> dict[str, Any]:
            await fail_if_cancelled()

            result = await self.executor_client.execute(action, self.identity)
            evidence = _tool_evidence(action, result)
            tool_evidence.append(evidence)
            await record_event("agent.tool_result", _tool_payload(evidence))
            return result

        try:
            messages_headers = self.messages_auth.headers(self.identity)
            result = await self.native_loop(
                system=system,
                task=task,
                execute=execute,
                emit=emit,
                max_steps=max_steps,
                messages_url=self.messages_url,
                messages_headers=(
                    dict(messages_headers) if messages_headers is not None else None
                ),
                messages_auth_factory=self.messages_auth.auth_factory(self.identity),
            )
            await fail_if_cancelled()
        except RunnerCancelled:
            return cancelled_outcome()

        return RunnerOutcome(
            done=result.done,
            summary=result.summary,
            files=dict(result.files),
            steps=max(result.steps, len(tool_evidence)),
            stop_reason=result.stop_reason,
            events_emitted=seq,
            emitted_events=tuple(emitted_events),
            tool_evidence=tuple(tool_evidence),
        )


def _tool_evidence(action: Any, result: Mapping[str, Any]) -> ToolEvidence:
    action_name = _action_name(action)
    action_args = _action_args(action)
    path = _action_path(action_args)
    changed_paths = tuple(_coerce_string_list(result.get("changed_paths")))
    exit_code = result.get("exit_code")
    bytes_written: int | None = None
    content_sha256: str | None = None

    if action_name == "write_file" and isinstance(action_args.get("content"), str) and path is not None:
        content = action_args["content"]
        encoded = content.encode("utf-8")
        bytes_written = len(encoded)
        content_sha256 = hashlib.sha256(encoded).hexdigest()
        changed_paths = (path,)

    return ToolEvidence(
        action=action_name,
        ok=bool(result.get("ok")),
        path=path,
        changed_paths=changed_paths,
        bytes_written=bytes_written,
        content_sha256=content_sha256,
        exit_code=exit_code if isinstance(exit_code, int) else None,
    )


def _tool_payload(evidence: ToolEvidence) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": evidence.action,
        "ok": evidence.ok,
    }
    if evidence.path is not None:
        payload["path"] = evidence.path
    if evidence.changed_paths:
        payload["changed_paths"] = list(evidence.changed_paths)
    if evidence.bytes_written is not None:
        payload["bytes_written"] = evidence.bytes_written
    if evidence.content_sha256 is not None:
        payload["content_sha256"] = evidence.content_sha256
    if evidence.exit_code is not None:
        payload["exit_code"] = evidence.exit_code
    return payload


def _action_name(action: Any) -> str:
    name = getattr(action, "name", None)
    return name if isinstance(name, str) and name else type(action).__name__


def _action_args(action: Any) -> dict[str, Any]:
    args = getattr(action, "args", None)
    if not isinstance(args, Mapping):
        return {}
    return {str(key): value for key, value in args.items()}


def _action_path(action_args: Mapping[str, Any]) -> str | None:
    path = action_args.get("path")
    return path if isinstance(path, str) and path else None


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            items.append(item)
    return items


def _sanitize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _sanitize_value(key, value)
        for key, value in payload.items()
        if isinstance(key, str)
    }


def _sanitize_value(key: str | None, value: Any) -> Any:
    key_name = key.lower() if key is not None else ""
    if key_name and _is_sensitive_key(key_name):
        return "[REDACTED]"
    if key_name in _OMITTED_VALUE_KEYS:
        return "[OMITTED]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [_sanitize_value(None, item) for item in value]
    if isinstance(value, str):
        return value if len(value) <= 400 else f"{value[:397]}..."
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    return any(part in key for part in _SENSITIVE_KEY_PARTS)
