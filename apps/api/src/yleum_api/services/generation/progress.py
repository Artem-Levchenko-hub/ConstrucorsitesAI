from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.redis import publish_event
from yleum_api.models.generation_event import GenerationEvent
from yleum_api.models.message import Message
from yleum_api.services.agent_progress import (
    PROVIDER_ERROR_DETAIL,
    PROVIDER_ERROR_DETAILS,
    PROVIDER_RESPONSE_DETAIL,
    STEP_METADATA,
    TOOL_LIFECYCLE_EVENTS,
    provider_error_origin,
    sanitize_agent_step,
    tool_lifecycle_step,
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
_CONFIRMED_TOOL_STATES = ("succeeded", "failed", "skipped")


def _is_action(step: Mapping[str, Any]) -> bool:
    return ((step.get("kind") is None or step.get("kind") == "step")
            and step.get("tool") != "capacity_wait"
            and step.get("action") != "Ожидаю ресурсы сервера")


def _bounded_transcript(
    previous: list[dict[str, Any]], latest: dict[str, Any],
) -> list[dict[str, Any]]:
    if len(previous) < 200:
        return [*previous, latest]
    # A pending outer call can emit progress of its own. Give its temporary
    # history slot to the earliest completed step before dropping that step.
    discard = 199
    if previous[199].get("tool_diagnostic", {}).get("state") != "started":
        discard = next((
            i for i in range(198, -1, -1)
            if previous[i].get("tool_diagnostic", {}).get("state") == "started"
        ), discard)
    return [*previous[:discard], *previous[discard + 1:200], latest]


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


async def _prior_tool_result(
    session: AsyncSession, event: GenerationEvent, call_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    # append holds the run lock. Look beyond the bounded transcript on recovery.
    facts = GenerationEvent.payload["tool_diagnostic"]
    prior = select(
        facts.label("diagnostic"), GenerationEvent.payload["kind"].astext.label("kind"),
        GenerationEvent.seq,
    ).where(
        GenerationEvent.generation_run_id == event.generation_run_id,
        GenerationEvent.message_id == event.message_id,
        GenerationEvent.seq < event.seq,
        facts["call_id"].astext == call_id,
    ).cte()
    confirmed = select(prior.c.diagnostic).where(
        prior.c.diagnostic["state"].astext.in_(_CONFIRMED_TOOL_STATES),
    ).order_by(prior.c.seq).limit(1).scalar_subquery()
    counted = select(func.bool_or(prior.c.kind == "step")).scalar_subquery()
    evidence, was_counted = (await session.execute(select(confirmed, counted))).one()
    return evidence, bool(was_counted)


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
            {key: value for key, value in step_row.items() if key not in STEP_METADATA},
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
                diagnostic = transcript_step.get("tool_diagnostic")
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
                already_counted = False
                if isinstance(diagnostic, dict):
                    call_id = diagnostic["call_id"]
                    matching = [
                        row for row in previous
                        if row.get("tool_diagnostic", {}).get("call_id") == call_id
                    ]
                    confirmed = next((
                        row["tool_diagnostic"] for row in matching
                        if row["tool_diagnostic"]["state"] in _CONFIRMED_TOOL_STATES
                    ), None)
                    already_counted = any(_is_action(row) for row in matching)
                    unresolved = any(
                        row["tool_diagnostic"]["state"] in {"unknown", "cancelled"}
                        for row in matching
                    )
                    # On recovery a previous terminal or counted step may survive
                    # only in the ledger. The usual start/result pair needs no query.
                    if event is not None and (
                        diagnostic.get("reconciled") or unresolved or (truncated and not matching)
                    ):
                        confirmed, already_counted = await _prior_tool_result(
                            progress_session, event, call_id,
                        )
                    if confirmed is not None:
                        if (already_counted or not _is_action(transcript_step)
                                or diagnostic["state"] not in _CONFIRMED_TOOL_STATES):
                            await progress_session.rollback()
                            self.steps = previous
                            return
                        # Recovery supplied the canonical step after only a terminal
                        # heartbeat was saved. Keep the original measured result.
                        if confirmed["state"] == diagnostic["state"]:
                            diagnostic = {**diagnostic, **confirmed}
                            transcript_step = {**transcript_step, "tool_diagnostic": diagnostic}
                            wire_payload["tool_diagnostic"] = diagnostic
                            if event is not None:
                                event.payload = {**event.payload, "tool_diagnostic": diagnostic}
                    elif matching and diagnostic["state"] == "started":
                        await progress_session.rollback()
                        self.steps = previous
                        return
                    if matching:
                        # Each invocation occupies one history slot, even after
                        # repeated interruptions. The ledger retains its observations.
                        previous = [
                            row for row in previous
                            if row.get("tool_diagnostic", {}).get("call_id") != call_id
                        ]
                count += int(_is_action(transcript_step) and not already_counted)
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
                    if diagnostic and event.created_at is not None:
                        identity["recordedAt"] = event.created_at.astimezone(UTC).isoformat(
                            timespec="milliseconds"
                        )
                step = {**transcript_step, **identity}
                wire_payload.update(identity)
                next_steps = _bounded_transcript(previous, step)
                progress_message.agent_steps = next_steps
                if event is not None:
                    # Replace JSON, not an in-place mutation. Trusted metadata
                    # goes first so payload bounds cannot drop the counter.
                    event.payload = redact_event_payload({
                        **identity,
                        **{key: value for key, value in event.payload.items()
                           if key not in STEP_METADATA},
                    })
            await progress_session.commit()
            if next_steps is not None:
                self.steps = next_steps
            if event is not None:
                await progress_session.refresh(event)
                progress_session.expunge(event)
        try:
            legacy_event = "agent.step" if event_type in TOOL_LIFECYCLE_EVENTS else event_type
            await publish_event(
                self.project_id,
                "generation.event" if event is not None else legacy_event,
                dict(generation_event_envelope(event)) if event is not None else wire_payload,
            )
        except Exception:
            _log.warning(
                "generation_event_publish_failed",
                extra={"project_id": str(self.project_id), "run_id": str(self.run_id)},
                exc_info=True,
            )

    async def emit_agent_event(self, event: str, data: dict[str, Any]) -> None:
        if event in TOOL_LIFECYCLE_EVENTS:
            observation = tool_lifecycle_step(event, data)
            if observation:
                await self._record(event, observation, transcript_step=observation)
            return
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
        if "tool_diagnostic" in data:
            step_row["tool_diagnostic"] = data["tool_diagnostic"]
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
