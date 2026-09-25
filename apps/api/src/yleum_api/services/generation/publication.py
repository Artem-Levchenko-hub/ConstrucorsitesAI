from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.message import Message
from yleum_api.models.user import User
from yleum_api.schemas.snapshot import snapshot_event_dict

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
    session: AsyncSession, *, is_free: bool, user_id: UUID
) -> None:
    """Every free build is spent from the owner's personal allowance."""
    if not is_free:
        return
    user_row = await session.get(User, user_id)
    if user_row is not None:
        user_row.free_generations_used = (user_row.free_generations_used or 0) + 1
