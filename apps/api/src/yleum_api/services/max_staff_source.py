"""Published business snapshots for the platform's staff and checkout services."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.schemas.max_management import (
    OwnerRecordItem,
    OwnerRecordPublic,
    OwnerRecordsPagePublic,
    OwnerRecordsQuery,
    ResourceId,
)
from yleum_api.services import max_management, repo
from yleum_api.services.max_management_contract import parse_management_contract
from yleum_api.services.max_management_generation import linked_checkout_connected


@dataclass(frozen=True)
class StaffCheckoutOrder:
    id: UUID
    total_kopecks: int
    currency: Literal["RUB"]
    created_at: datetime
    actor_id: int
    owner_id: UUID | None = None
    items: tuple[OwnerRecordItem, ...] = ()


def _needs_publication() -> ApiError:
    return ApiError(
        "unsupported",
        "Обновите и опубликуйте приложение с поддержкой рабочих очередей, затем включите их.",
        409,
    )


async def confirm_staff_source(
    session: AsyncSession,
    project: Project,
    resources: set[ResourceId],
    *,
    linked_checkout: bool,
) -> None:
    try:
        context = await max_management._prepare(
            session, project, project.owner_id, require_idle=True
        )
        if not resources.issubset({resource.id for resource in context.contract.resources}):
            raise _needs_publication()
        await max_management._describe(session, context, project.owner_id, require_staff_read=True)
        if linked_checkout:
            await _require_linked_checkout(project, context.target.commit_sha)
    except max_management._State:
        raise _needs_publication() from None
    except ApiError as exc:
        if exc.code == "unsupported":
            raise _needs_publication() from None
        raise
    except Exception:
        raise max_management._error() from None


async def require_compatible_staff_release(
    project: Project,
    commit_sha: str,
    resources: set[ResourceId],
    *,
    linked_checkout: bool,
) -> None:
    """Prevent publishing a restored snapshot that predates an active queue."""
    route, raw = await asyncio.gather(
        asyncio.to_thread(
            repo.read_file, project.id, commit_sha, "src/app/api/omnia/owner/route.ts"
        ),
        asyncio.to_thread(repo.read_file, project.id, commit_sha, ".omnia/management.json"),
    )
    if not route or b"staff_read" not in route or not raw:
        raise _needs_publication()
    try:
        contract = parse_management_contract(json.loads(raw))
    except (ValueError, TypeError):
        raise _needs_publication() from None
    if not resources.issubset({resource.id for resource in contract.resources}):
        raise _needs_publication()
    if linked_checkout:
        await _require_linked_checkout(project, commit_sha)


async def _require_linked_checkout(project: Project, commit_sha: str) -> None:
    # SDK presence alone is insufficient: an older screen may still create
    # generic payments which have no immutable order link.
    files = await asyncio.to_thread(repo.read_files, project.id, commit_sha)
    if not linked_checkout_connected(files):
        raise ApiError(
            "unsupported",
            "Добавьте в приложение оплату по сохранённому заказу через ЮKassa "
            "и опубликуйте обновление.",
            409,
        )


async def list_staff_source_records(
    session: AsyncSession,
    project: Project,
    query: OwnerRecordsQuery,
) -> OwnerRecordsPagePublic:
    # Owner search also matches contacts that a staff projection may conceal.
    if query.query:
        raise ApiError("invalid_request", "Поиск по обращениям сотрудников недоступен", 400)
    return await max_management.list_records(
        session,
        project.id,
        project.owner_id,
        query,
        require_staff_read=True,
    )


async def get_staff_source_record(
    session: AsyncSession,
    project: Project,
    resource: ResourceId,
    record_id: UUID,
) -> OwnerRecordPublic:
    return await max_management.get_record(
        session,
        project.id,
        project.owner_id,
        record_id,
        resource,
    )


async def read_staff_checkout_order(
    session: AsyncSession,
    project_id: UUID,
    order_id: UUID,
    max_user_id: int,
) -> StaffCheckoutOrder:
    if type(max_user_id) is not int or not 0 < max_user_id <= 2**63 - 1:
        raise ApiError("invalid_request", "Некорректные данные заказа", 400)
    project = await session.get(Project, project_id, populate_existing=True)
    if project is None or project.id != project_id:
        raise ApiError("not_found", "Проект не найден", 404)
    owner_id = project.owner_id
    record = await max_management.get_record(
        session,
        project.id,
        owner_id,
        order_id,
        ResourceId.orders,
        actor=str(max_user_id),
    )
    if record.total_kopecks is None or record.total_kopecks <= 0 or record.currency != "RUB":
        raise ApiError("invalid_request", "У заказа нет суммы к оплате в рублях", 400)
    return StaffCheckoutOrder(
        id=record.id,
        total_kopecks=record.total_kopecks,
        currency=record.currency,
        created_at=record.created_at,
        actor_id=max_user_id,
        owner_id=owner_id,
        # The managed snapshot trigger protects the stored line payload.
        items=tuple(record.items),
    )
