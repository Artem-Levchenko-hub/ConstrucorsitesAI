from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.minio import preview_public_url
from omnia_api.models.account import BusinessEntitlement
from omnia_api.models.message import Message
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User

ORCHESTRATION_LABEL = "Оркестратор Sonnet+DeepSeek"


def _snapshot_payload(s: Snapshot) -> dict[str, object]:
    return {
        "id": str(s.id),
        "project_id": str(s.project_id),
        "commit_sha": s.commit_sha,
        "prompt_text": s.prompt_text,
        "model_id": s.model_id,
        "parent_id": str(s.parent_id) if s.parent_id else None,
        "preview_url": preview_public_url(s.preview_key),
        "is_rollback_target": s.is_rollback_target,
        "created_at": s.created_at.isoformat(),
    }


async def _finalize_message(
    factory: async_sessionmaker[AsyncSession],
    message_id: UUID,
    content: str,
    usage_data: dict[str, float | int] | None,
    snapshot_id: UUID | None,
) -> None:
    async with factory() as session:
        msg = await session.get(Message, message_id)
        if msg is None:
            return
        msg.content = content
        if snapshot_id is not None:
            msg.snapshot_id = snapshot_id
        if usage_data:
            msg.tokens_in = int(usage_data.get("tokens_in") or 0)
            msg.tokens_out = int(usage_data.get("tokens_out") or 0)
        await session.commit()


async def consume_free_generation(
    session: AsyncSession, *, is_free: bool, free_business_id: UUID | None, user_id: UUID
) -> None:
    if not is_free:
        return
    if free_business_id is not None:
        entitlement = await session.get(
            BusinessEntitlement,
            free_business_id,
            with_for_update=True,
        )
        if entitlement is not None:
            entitlement.free_generations_used += 1
            entitlement.updated_at = datetime.now(UTC)
            return
    user_row = await session.get(User, user_id)
    if user_row is not None:
        user_row.free_generations_used = (user_row.free_generations_used or 0) + 1
