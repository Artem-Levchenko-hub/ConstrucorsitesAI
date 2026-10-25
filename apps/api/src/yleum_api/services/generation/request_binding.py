"""Admission identity is evidence; raw request text alone selects semantics."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from yleum_api.models.message import Message
from yleum_api.services.reference_images import request_identity


@dataclass(frozen=True)
class BoundRequest:
    text: str
    identity: str


def bound_message_request(
    run: Any, message: Any, *, project_id: UUID, run_id: UUID,
) -> BoundRequest:
    if message is None or message.project_id != project_id or message.role != "user":
        raise ValueError("admission message binding conflicts")
    text = message.content
    references = getattr(message, "reference_images", None) or []
    identity = request_identity(text, references)
    if hashlib.sha256(identity.encode()).hexdigest() != run.prompt_hash:
        from yleum_api.schemas.message import RestorationAdaptationReference
        from yleum_api.services.restoration_adaptation import adaptation_reservation_prompt

        raw = (run.agent_state or {}).get("restoration_adaptation")
        if not (
            isinstance(raw, dict) and raw.get("version") in {1, 2}
            and raw.get("project_id") == str(project_id)
            and raw.get("owner_id") == str(run.user_id)
            and raw.get("adaptation_run_id") == str(run_id)
        ):
            raise ValueError("admission request identity conflicts")
        try:
            reference = RestorationAdaptationReference(
                operation_id=UUID(raw["operation_id"]),
                expected_draft_snapshot_id=UUID(raw["base_draft_snapshot_id"]),
            )
            text = adaptation_reservation_prompt(text, reference)
            identity = request_identity(text, references)
        except (KeyError, TypeError, ValueError):
            raise ValueError("admission adaptation binding conflicts") from None
    if hashlib.sha256(identity.encode()).hexdigest() != run.prompt_hash:
        raise ValueError("admission request identity conflicts")
    return BoundRequest(text, identity)


async def original_request_binding(
    session: Any, run: Any, prompt: str, *, project_id: UUID, run_id: UUID,
) -> BoundRequest:
    if not run.user_message_id and hashlib.sha256(prompt.encode()).hexdigest() == run.prompt_hash:
        return BoundRequest(prompt, prompt)
    message = (
        await session.get(Message, run.user_message_id, populate_existing=True)
        if run.user_message_id else None
    )
    return bound_message_request(run, message, project_id=project_id, run_id=run_id)
