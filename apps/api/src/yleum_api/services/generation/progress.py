from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.redis import publish_event
from yleum_api.models.message import Message
from yleum_api.services.agent_progress import (
    PROVIDER_ERROR_DETAIL,
    PROVIDER_ERROR_DETAILS,
    PROVIDER_RESPONSE_DETAIL,
    provider_error_origin,
    sanitize_agent_step,
)
from yleum_api.services.generation.agent_messages import _humanize_step
from yleum_api.services.generation.recovery_trace import bind_recovery_payload, safe_recovery_trace
from yleum_api.services.generation_events import (
    PROVISIONAL_TOOL_FAILURE,
    append_generation_event,
    generation_event_envelope,
    redact_event_payload,
)

"""Durable generation progress and the bounded assistant transcript."""


_log = logging.getLogger("yleum_api.routers.messages")
_STEP_METADATA = {"eventId", "runId", "seq", "actionCount", "historyTruncated"}


def _is_action(step: Mapping[str, Any]) -> bool:
    return ((step.get("kind") is None or step.get("kind") == "step")
            and step.get("tool") != "capacity_wait"
            and step.get("action") != "Ожидаю ресурсы сервера")


def _committed_steps(value: object, run_id: UUID) -> tuple[list[dict[str, Any]], int, bool]:
    rows = [sanitize_agent_step(row) for row in value if isinstance(row, dict)] \
        if isinstance(value, list) else []
    if any(row.get("runId") not in {None, str(run_id)} for row in rows):
        raise ValueError("transcript belongs to another run")
    truncated = isinstance(value, list) and any(
        isinstance(row, dict) and row.get("historyTruncated") is True for row in value
    )
    if truncated and (not rows or type(rows[-1].get("actionCount")) is not int):
        raise ValueError("truncated transcript action count is unavailable")
    tail = 0
    for row in reversed(rows):
        if type(row.get("actionCount")) is int:
            return rows, row["actionCount"] + tail, truncated
        tail += int(_is_action(row))
    return rows, tail, truncated


@dataclass
class GenerationProgress:
    factory: async_sessionmaker[AsyncSession]
    run_id: UUID
    project_id: UUID
    assistant_message_id: UUID
    steps: list[dict[str, Any]] = field(default_factory=list)
    defer_verification_errors: bool = False
    _record_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def record_agent_step(self, step_row: dict[str, Any]) -> None:
        safe = sanitize_agent_step(bind_recovery_payload(
            {key: value for key, value in step_row.items() if key not in _STEP_METADATA},
            run_id=str(self.run_id), event_type="agent.step",
        ))
        await self._record("agent.step", safe, transcript_step=safe)

    async def record_generation_event(self, event_type: str, payload: Mapping[str, object]) -> None:
        payload = bind_recovery_payload(payload, run_id=str(self.run_id), event_type=event_type)
        if (
            self.defer_verification_errors
            and event_type == "tool.finished"
            and payload.get("state") in ("failed", "timed_out")
        ):
            payload = {**payload, PROVISIONAL_TOOL_FAILURE: True}
        await self._record(event_type, payload)

    async def _record(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        transcript_step: dict[str, Any] | None = None,
    ) -> None:
        # Keep publication/final-publication cache order aligned with each
        # recorder's committed SQL order, including concurrent callbacks.
        async with self._record_lock:
            await self._record_locked(event_type, payload, transcript_step=transcript_step)

    async def _record_locked(
        self, event_type: str, payload: Mapping[str, object], *,
        transcript_step: dict[str, Any] | None,
    ) -> None:
        wire_payload = {**dict(payload), "message_id": str(self.assistant_message_id)}
        next_steps = None
        async with self.factory() as progress_session:
            event = await append_generation_event(
                progress_session,
                run_id=self.run_id,
                project_id=self.project_id,
                message_id=self.assistant_message_id,
                event_type=event_type,
                payload=wire_payload,
            )
            if transcript_step is not None:
                # Durable append already holds Run. Lock/reload Message next;
                # a restarted recorder must continue the committed counter.
                progress_message = await progress_session.get(
                    Message, self.assistant_message_id,
                    with_for_update=True, populate_existing=True,
                )
                if progress_message is None or progress_message.project_id != self.project_id:
                    raise ValueError("transcript message project mismatch")
                previous, count, truncated = _committed_steps(
                    progress_message.agent_steps, self.run_id,
                )
                count += int(_is_action(transcript_step))
                if count > 2**31 - 1:
                    raise ValueError("transcript action count exceeds limit")
                # Exact identity survives different detail limits on the event
                # and history surfaces. Legacy publication has no durable seq.
                identity: dict[str, Any] = {
                    "eventId": str(event.id if event is not None else uuid4()),
                    "runId": str(self.run_id),
                    "actionCount": count,
                    "historyTruncated": truncated or len(previous) >= 200,
                }
                if event is not None:
                    identity["seq"] = event.seq
                step = {**transcript_step, **identity}
                wire_payload.update(identity)
                next_steps = [*previous[:199], step] if len(previous) >= 200 else [*previous, step]
                progress_message.agent_steps = next_steps
                if event is not None:
                    # Replace JSON, not an in-place mutation. Trusted metadata
                    # goes first so payload bounds cannot drop the counter.
                    event.payload = redact_event_payload({
                        **identity,
                        **{key: value for key, value in event.payload.items()
                           if key not in _STEP_METADATA},
                    })
            await progress_session.commit()
            if next_steps is not None:
                self.steps = next_steps
            if event is not None:
                await progress_session.refresh(event)
                progress_session.expunge(event)
        try:
            await publish_event(
                self.project_id,
                "generation.event" if event is not None else event_type,
                dict(generation_event_envelope(event)) if event is not None else wire_payload,
            )
        except Exception:
            _log.warning(
                "generation_event_publish_failed",
                extra={"project_id": str(self.project_id), "run_id": str(self.run_id)},
                exc_info=True,
            )

    async def emit_agent_event(self, event: str, data: dict[str, Any]) -> None:
        # Surface each agent step as a STRUCTURED transcript event so the
        # frontend renders a live Claude-Code-style step list instead of
        # gray "[агент] …" text. One WS event type `agent.step` carries
        # every loop signal via `kind` (step/escalate/stalled/retry).
        #
        # `action` is now a HUMAN-READABLE phrase («Пишу главную страницу»)
        # — deep-research (16.07): raw tool names read as "непонятно что
        # делает агент". The raw tool goes in `tool` so the UI can still
        # pick an icon. Frontend shows `action` as-is → instant win, no
        # frontend change needed.
        kind = (event.rsplit(".", 1)[-1] or "step").lower()
        raw_tool = str(data.get("action", "") or "")
        path = str(data.get("path", "") or "")
        # A tool can hand over a ready human phrase for a SUB-step it emits
        # itself (generate_media: «Рисую первый кадр», «Kling соединяет
        # кадры») — honour it verbatim instead of re-deriving from (tool,
        # path), which a multi-stage tool can't express.
        human = str(data.get("human", "") or "")
        if kind == "escalate":
            action = f"усиливаю модель → {data.get('to', '')}"
        elif kind == "stalled":
            action = "думаю, как лучше — меняю подход"
        elif kind == "retry":
            action = f"повторяю запрос (#{data.get('attempt', 0)})"
        elif kind == "provider_error":
            action = "Запрос к модели не завершён"
            raw_tool = "provider_request"
        elif human:
            action = human
            raw_tool = str(data.get("tool", "") or raw_tool)
        else:
            action = _humanize_step(raw_tool, path)
        step_row = {
            "step": data.get("step"),
            "kind": kind,
            "action": action,
            "tool": raw_tool,
            "path": path,
            # `detail` = what the step did inside (content/output) so the
            # UI can drill into a step; `ok` colours success/failure.
            "detail": str(data.get("detail", "") or ""),
            "ok": data.get("ok") if type(data.get("ok")) is bool else None,
        }
        if data.get("skipped") is True:
            step_row["ok"] = None
            step_row["skipped"] = True
        if (
            self.defer_verification_errors
            and raw_tool in {"build", "done"}
            and (data.get("ok") is False or data.get("skipped") is True)
        ):
            step_row.update(action="Проверяю приложение", ok=None, detail="")
            step_row.pop("skipped", None)
        if kind == "provider_error":
            step_row["ok"] = False
            step_row["detail"] = PROVIDER_ERROR_DETAIL
            category = data.get("provider_refusal_category")
            if category in ("access", "funds", "rate_limit"):
                step_row["provider_refusal_category"] = category
                step_row["detail"] = PROVIDER_ERROR_DETAILS[category]
        if raw_tool == "provider_response":
            reason = data.get("reason")
            if isinstance(reason, str) and reason in {
                "output_limit",
                "invalid_tool_arguments",
                "missing_tool_arguments",
            }:
                step_row["reason"] = reason
            recovery_attempt = data.get("recovery_attempt")
            if type(recovery_attempt) is int and 1 <= recovery_attempt <= 3:
                step_row["recovery_attempt"] = recovery_attempt
        if kind == "provider_error" or raw_tool == "provider_response":
            trace = safe_recovery_trace({
                **data, "run_id": str(self.run_id), "error_origin": provider_error_origin(data),
            })
            step_row["recovery_trace"] = trace
            # Preserve the existing flat diagnostic contract using the same
            # normalizer as durable traces. Never copy provider content.
            for key in ("finish_reason", "requested_max_tokens", "completion_tokens"):
                if key in trace:
                    step_row[key] = trace[key]
            if kind == "provider_error":
                for key in ("http_status", "upstream_http_status", "error_code",
                            "provider_charge_ambiguous", "will_retry", "attempt"):
                    value = trace.get(key)
                    if key in trace and (
                        key != "http_status" or (type(value) is int and value >= 400)
                    ):
                        step_row[key] = trace[key]
            else:
                step_row["action"] = "Проверяю ответ модели"
                step_row["detail"] = PROVIDER_RESPONSE_DETAIL
        elif isinstance(data.get("recovery_trace"), Mapping):
            step_row["recovery_trace"] = safe_recovery_trace({
                **data["recovery_trace"], "run_id": str(self.run_id),
            })
        await self.record_agent_step(step_row)


@dataclass
class MessageStream:
    """Hot replay state for one assistant message, never reset between passes."""

    seq: int = 0
    content: str = ""
