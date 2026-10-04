"""Count durable provider creations without changing their outcome or transaction."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.services import max_analytics

logger = logging.getLogger(__name__)
CREATION_KINDS = {("amocrm", "lead"), ("bitrix24", "lead"), ("moysklad", "customer_order")}


async def record_confirmed_action(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: int,
    provider: str,
    kind: str,
    client_key: str,
) -> None:
    if (provider, kind) not in CREATION_KINDS or type(user_id) is not int or user_id <= 0:
        return
    try:
        bind = getattr(session, "bind", None)
        if not isinstance(bind, (AsyncEngine, AsyncConnection)):
            return
        # A separate checkout sees only committed receipts and cannot roll back or
        # commit the provider request's transaction, even when it bound a connection.
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as analytics_session:
            try:
                event_id = await analytics_session.scalar(
                    select(IntegrationOperation.id).where(
                        IntegrationOperation.project_id == project_id,
                        IntegrationOperation.max_user_id == str(user_id),
                        IntegrationOperation.provider == provider,
                        IntegrationOperation.kind == kind,
                        IntegrationOperation.client_key == client_key,
                        IntegrationOperation.status == "succeeded",
                    )
                )
                if event_id is not None:
                    await max_analytics.record_event(
                        analytics_session, project_id, user_id, event_id, "action"
                    )
            except Exception:
                await analytics_session.rollback()
                raise
    except Exception:
        # No payload, actor, receipt, credential or database exception in logs.
        # A later normal replay can repair telemetry using the same durable UUID.
        logger.warning("Confirmed integration action analytics unavailable")
