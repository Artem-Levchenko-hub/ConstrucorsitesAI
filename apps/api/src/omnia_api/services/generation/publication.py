from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.account import BusinessEntitlement
from omnia_api.models.message import Message
from omnia_api.models.user import User
from omnia_api.schemas.snapshot import snapshot_event_dict

ORCHESTRATION_LABEL = "Оркестратор Sonnet+DeepSeek"


_snapshot_payload = snapshot_event_dict


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
