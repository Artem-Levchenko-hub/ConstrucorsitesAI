from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.redis import publish_event
from yleum_api.models.message import Message
from yleum_api.services.agent_progress import sanitize_agent_step
from yleum_api.services.generation.agent_messages import _humanize_step
from yleum_api.services.generation_events import append_generation_event, generation_event_envelope

"""Durable generation progress and the bounded assistant transcript."""


_log = logging.getLogger("yleum_api.routers.messages")


@dataclass
class GenerationProgress:
    factory: async_sessionmaker[AsyncSession]
    run_id: UUID
    project_id: UUID
    assistant_message_id: UUID
    steps: list[dict[str, Any]] = field(default_factory=list)

    async def record_agent_step(self, step_row: dict[str, Any]) -> None:
        safe = sanitize_agent_step(step_row)
        await self._record("agent.step", safe, transcript_step=safe)

    async def record_generation_event(self, event_type: str, payload: Mapping[str, object]) -> None:
        await self._record(event_type, payload)

    async def _record(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        transcript_step: dict[str, Any] | None = None,
    ) -> None:
        async with self.factory() as progress_session:
            event = await append_generation_event(
                progress_session,
                run_id=self.run_id,
                project_id=self.project_id,
                message_id=self.assistant_message_id,
                event_type=event_type,
                payload={"message_id": str(self.assistant_message_id), **dict(payload)},
            )
            if transcript_step is not None and len(self.steps) < 200:
                self.steps.append(transcript_step)
                progress_message = await progress_session.get(Message, self.assistant_message_id)
                if progress_message is not None:
                    progress_message.agent_steps = list(self.steps)
            await progress_session.commit()
            await progress_session.refresh(event)
            progress_session.expunge(event)
        try:
            await publish_event(
                self.project_id, "generation.event", dict(generation_event_envelope(event))
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
            "ok": bool(data.get("ok", True)),
        }
        await self.record_agent_step(step_row)


@dataclass
class MessageStream:
    """Hot replay state for one assistant message, never reset between passes."""

    seq: int = 0
    content: str = ""
