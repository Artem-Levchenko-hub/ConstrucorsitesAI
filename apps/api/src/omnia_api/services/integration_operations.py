"""Record external dispatch before effects; never replay an ambiguous write."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.integration_operation import IntegrationOperation


def unknown_operation() -> ApiError:
    return ApiError(
        "integration_operation_unknown",
        "Результат отправки пока не подтверждён. Проверьте заявку в CRM перед новой отправкой.",
        409,
    )


async def execute_once(
    session: AsyncSession,
    *,
    project_id: UUID,
    integration_id: UUID,
    provider: str,
    max_user_id: int,
    kind: str,
    client_key: str,
    payload: dict[str, Any],
    send: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    values = dict(
        project_id=project_id,
        provider=provider,
        max_user_id=str(max_user_id),
        kind=kind,
        client_key=client_key,
    )
    identifier = await session.scalar(
        insert(IntegrationOperation)
        .values(
            id=uuid4(),
            integration_id=integration_id,
            **values,
            request_digest=digest,
            status="dispatching",
            result={},
        )
        .on_conflict_do_nothing(constraint="uq_integration_operation_key")
        .returning(IntegrationOperation.id)
    )
    # Receipt survives a process crash or lost provider response.
    await session.commit()
    if identifier is None:
        row = (await session.scalars(select(IntegrationOperation).filter_by(**values))).one()
        if row.request_digest != digest:
            raise ApiError(
                "integration_operation_conflict",
                "Этот ключ отправки уже использован для другой заявки.",
                409,
            )
        if row.status == "succeeded":
            return dict(row.result)
        if row.status == "rejected":
            raise ApiError(
                "integration_request_rejected",
                "Сервис отклонил эту заявку. Исправьте данные перед новой отправкой.",
                422,
            )
        raise unknown_operation()
    claimed = await session.get(IntegrationOperation, identifier)
    assert claimed is not None
    row = claimed
    try:
        result = await send()
    except ApiError as exc:
        row.status = "rejected" if exc.code == "integration_request_rejected" else "unknown"
        row.finished_at = datetime.now(UTC)
        await session.commit()
        if row.status == "rejected":
            raise
        raise unknown_operation() from exc
    # Unexpected failures/cancellation leave dispatching; retries fail closed.
    row.status = "succeeded"
    row.result = result
    row.finished_at = datetime.now(UTC)
    await session.commit()
    return result
