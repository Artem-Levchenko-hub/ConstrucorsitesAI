"""Explicit durable login setup for tests that seed accounts directly."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.security import create_access_token
from yleum_api.models.account import AuthSession
from yleum_api.models.user import User


async def authenticated_session_token(session: AsyncSession, user: User) -> str:
    """Persist a real session before protected requests or separate WS readers."""
    await session.flush()
    login = AuthSession(
        id=uuid4(),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().jwt_ttl_days),
    )
    session.add(login)
    await session.commit()
    return create_access_token(
        user.id,
        session_id=login.id,
        session_version=user.session_version,
    )
