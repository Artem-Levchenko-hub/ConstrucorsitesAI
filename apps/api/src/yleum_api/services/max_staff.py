"""Staff workflow and payment admission, independent of generated application code."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.max_staff import MaxStaffAudit, MaxStaffSettings, MaxStaffWork
from yleum_api.models.user import User
from yleum_api.schemas.max_management import OwnerRecordPublic, OwnerRecordsQuery, ResourceId
from yleum_api.schemas.max_staff import (
    StaffAction,
    StaffHistoryEntry,
    StaffLead,
    StaffOrder,
    StaffOrderItem,
    StaffRecord,
    StaffRecordQuery,
    StaffRecords,
    StaffRecordUpdate,
    StaffResource,
    StaffStatus,
)
from yleum_api.services import max_staff_source as source
from yleum_api.services.max_staff_team import (
    Access,
    assignee_name,
    cutover,
    get_access,
    order_checkout_settings,
)


async def get_order_checkout_settings(session: AsyncSession, project_id: UUID) -> MaxStaffSettings:
    return await order_checkout_settings(session, project_id)


def order_times(
    scheduled: datetime,
    delay_minutes: int,
    admitted_at: datetime,
    prep_minutes: int,
) -> tuple[datetime, datetime]:
    prep = timedelta(minutes=prep_minutes)
    if delay_minutes == 0:
        return admitted_at + prep, admitted_at
    return scheduled, max(scheduled - prep, admitted_at)


def _conflict() -> ApiError:
    return ApiError(
        "staff_record_changed",
        "Карточка уже изменилась. Обновите очередь и попробуйте снова.",
        409,
    )


def _not_found() -> ApiError:
    return ApiError("not_found", "Карточка не найдена в этой очереди", 404)


def transition(
    status: StaffStatus,
    assignee: UUID | None,
    actor: UUID,
    owner: bool,
    update: StaffRecordUpdate,
) -> tuple[StaffStatus, UUID | None]:
    if status in (StaffStatus.cancelled, StaffStatus.completed):
        raise _conflict()
    action = update.action
    if action == StaffAction.reassign:
        if not owner:
            raise ApiError("staff_access_denied", "Переназначать может владелец", 403)
        return status, update.assignee_id
    if action == StaffAction.claim:
        if assignee is not None and (assignee != actor or status != StaffStatus.new):
            raise _conflict()
        return StaffStatus.ready if status == StaffStatus.ready else StaffStatus.in_progress, actor
    if assignee is None or (assignee != actor and not owner):
        raise _conflict()
    if update.resource == StaffResource.orders:
        if action == StaffAction.ready and status == StaffStatus.in_progress:
            return StaffStatus.ready, assignee
        if action == StaffAction.hand_over and status == StaffStatus.ready:
            return StaffStatus.completed, assignee
    if (
        update.resource == StaffResource.leads
        and action == StaffAction.close
        and status == StaffStatus.in_progress
    ):
        return StaffStatus.completed, assignee
    raise _conflict()


@dataclass(frozen=True)
class _OrderClock:
    scheduled_for: datetime
    prepare_at: datetime
    payment: Literal["pickup", "paid", "test"]


class _StaffRecordData(TypedDict):
    id: UUID
    created_at: datetime
    revision: str
    status: StaffStatus
    comment: str | None
    assignee_id: UUID | None
    assignee_name: str | None
    outcome: str | None


async def _order_clocks(
    session: AsyncSession,
    access: Access,
    records: list[OwnerRecordPublic],
) -> dict[UUID, _OrderClock]:
    from yleum_api.services.max_staff_checkout import read_staff_payment_proofs

    settings = access.settings
    assert settings is not None
    live: dict[UUID, datetime] = {}
    test: dict[UUID, datetime] = {}
    if settings.orders_mode == "yookassa":
        amounts = {
            record.id: record.total_kopecks
            for record in records
            if record.total_kopecks is not None and record.total_kopecks > 0
        }
        live = await read_staff_payment_proofs(
            session,
            project_id=access.project.id,
            owner_id=access.project.owner_id,
            order_amounts=amounts,
        )
        test = await read_staff_payment_proofs(
            session,
            project_id=access.project.id,
            owner_id=access.project.owner_id,
            order_amounts=amounts,
            test=True,
        )
    clocks = {}
    payment: Literal["pickup", "paid", "test"]
    for record in records:
        if not record.items:
            continue
        if settings.orders_mode == "pickup":
            admitted, payment = record.created_at, "pickup"
        elif record.id in live:
            admitted, payment = live[record.id], "paid"
        elif record.id in test:
            admitted, payment = test[record.id], "test"
        else:
            continue
        fulfillment = record.fulfillment
        pickup, prep = order_times(
            fulfillment.scheduled_for if fulfillment else record.created_at,
            fulfillment.delay_minutes if fulfillment else 0,
            admitted,
            settings.prep_minutes,
        )
        clocks[record.id] = _OrderClock(pickup, prep, payment)
    return clocks


def _revision(record: OwnerRecordPublic, work: MaxStaffWork | MaxStaffAudit | None) -> str:
    if work is None:
        return record.revision
    return sha256(f"{record.revision}:{work.version}".encode()).hexdigest()


def _project_record(
    record: OwnerRecordPublic,
    work: MaxStaffWork | MaxStaffAudit | None,
    clock: _OrderClock | None,
) -> StaffRecord:
    values: _StaffRecordData = {
        "id": record.id,
        "created_at": record.created_at,
        "revision": _revision(record, work),
        "status": StaffStatus(work.status if work else record.status),
        "comment": record.comment,
        "assignee_id": work.assignee_id if work else None,
        "assignee_name": work.assignee_name if work else None,
        "outcome": work.outcome if work else None,
    }
    if record.resource == ResourceId.leads:
        return StaffLead(
            **values,
            customer_name=record.customer_name,
            contact_phone=record.contact_phone,
            contact_email=record.contact_email,
        )
    assert clock is not None
    return StaffOrder(
        **values,
        scheduled_for=clock.scheduled_for,
        prepare_at=clock.prepare_at,
        payment=clock.payment,
        items=[
            StaffOrderItem(title=item.title, quantity=item.quantity, option=item.option)
            for item in record.items
        ],
    )


async def _work_rows(
    session: AsyncSession,
    access: Access,
    resource: StaffResource,
    ids: list[UUID],
) -> dict[UUID, MaxStaffWork]:
    if not ids:
        return {}
    rows = await session.scalars(
        select(MaxStaffWork)
        .where(
            MaxStaffWork.project_id == access.project.id,
            MaxStaffWork.owner_id == access.project.owner_id,
            MaxStaffWork.resource == resource,
            MaxStaffWork.record_id.in_(ids),
        )
        .execution_options(populate_existing=True)
    )
    return {row.record_id: row for row in rows}


async def list_records(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    query: StaffRecordQuery,
) -> StaffRecords:
    access = await get_access(session, project_id, user, query.resource)
    owner_id = access.project.owner_id
    since = cutover(access.settings, query.resource)
    result = await source.list_staff_source_records(
        session,
        access.project,
        OwnerRecordsQuery(
            resource=ResourceId(query.resource),
            created_from=since,
            cursor=query.cursor,
            limit=40,
        ),
    )
    access = await get_access(session, project_id, user, query.resource)
    if access.project.owner_id != owner_id or cutover(access.settings, query.resource) != since:
        raise _conflict()
    records = [
        record
        for record in result.records
        if record.resource == ResourceId(query.resource)
        and since is not None
        and record.created_at >= since
    ]
    clocks = (
        await _order_clocks(session, access, records)
        if query.resource == StaffResource.orders
        else {}
    )
    visible = [
        record for record in records if query.resource == StaffResource.leads or record.id in clocks
    ]
    works = await _work_rows(session, access, query.resource, [record.id for record in visible])
    return StaffRecords(
        records=[
            _project_record(record, works.get(record.id), clocks.get(record.id))
            for record in visible
        ],
        next_cursor=result.next_cursor,
        server_time=datetime.now(UTC),
    )


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def update_record(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    record_id: UUID,
    update: StaffRecordUpdate,
) -> StaffRecord:
    access = await get_access(session, project_id, user, update.resource)
    owner_id = access.project.owner_id
    record = await source.get_staff_source_record(
        session, access.project, ResourceId(update.resource), record_id
    )
    # All team changes lock the same project exclusively. Hold the shared lock
    # only for the local transaction, never across a call into the miniapp.
    access = await get_access(session, project_id, user, update.resource, lock=True)
    since = cutover(access.settings, update.resource)
    if (
        access.project.owner_id != owner_id
        or since is None
        or record.created_at < since
        or record.id != record_id
        or record.resource != ResourceId(update.resource)
    ):
        raise _not_found()
    clock = None
    if update.resource == StaffResource.orders:
        clock = (await _order_clocks(session, access, [record])).get(record_id)
        if clock is None:
            raise _not_found()
        if clock.payment == "test":
            raise ApiError("staff_test_order", "Это заказ для проверки оплаты", 409)
    await _lock(session, f"staff-operation:{project_id}:{user.id}:{update.operation_key}")
    digest = sha256(f"{record_id}:{update.model_dump_json()}".encode()).hexdigest()
    receipt = await session.scalar(
        select(MaxStaffAudit).where(
            MaxStaffAudit.project_id == project_id,
            MaxStaffAudit.owner_id == owner_id,
            MaxStaffAudit.actor_id == user.id,
            MaxStaffAudit.operation_key == update.operation_key,
        )
    )
    if receipt is not None:
        if receipt.digest != digest:
            raise _conflict()
        return _project_record(record, receipt, clock)
    await _lock(session, f"staff-record:{project_id}:{update.resource}:{record_id}")
    row = await session.get(
        MaxStaffWork, (project_id, update.resource, record_id), populate_existing=True
    )
    if (row is not None and row.owner_id != owner_id) or _revision(
        record, row
    ) != update.expected_revision:
        raise _conflict()
    now = datetime.now(UTC)
    if clock is not None and clock.prepare_at > now and update.action == StaffAction.claim:
        raise ApiError("staff_order_scheduled", "Время приготовления ещё не наступило", 409)
    current = StaffStatus(row.status if row else record.status)
    current_assignee = row.assignee_id if row else None
    status, assignee = transition(
        current, current_assignee, user.id, access.public.is_owner, update
    )
    name = row.assignee_name if row else None
    if update.action == StaffAction.claim:
        name = access.actor_name
    if update.action == StaffAction.reassign:
        name = await assignee_name(session, access, assignee, update.resource)
    if row is None:
        row = MaxStaffWork(
            project_id=project_id, resource=update.resource, record_id=record_id, owner_id=owner_id
        )
        session.add(row)
    row.status = status
    row.assignee_id = assignee
    row.assignee_name = name
    row.version = uuid4()
    if update.action == StaffAction.close:
        row.outcome = update.outcome
    session.add(
        MaxStaffAudit(
            project_id=project_id,
            resource=update.resource,
            record_id=record_id,
            owner_id=owner_id,
            actor_id=user.id,
            actor_name=access.actor_name,
            operation_key=update.operation_key,
            digest=digest,
            action=update.action,
            status=status,
            version=row.version,
            assignee_id=assignee,
            assignee_name=name,
            outcome=row.outcome,
            created_at=now,
        )
    )
    result = _project_record(record, row, clock)
    await session.commit()
    return result


async def history(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    resource: StaffResource,
    record_id: UUID,
) -> list[StaffHistoryEntry]:
    access = await get_access(session, project_id, user, resource)
    if not access.public.is_owner:
        raise ApiError("staff_access_denied", "История доступна владельцу", 403)
    rows = await session.scalars(
        select(MaxStaffAudit)
        .where(
            MaxStaffAudit.project_id == project_id,
            MaxStaffAudit.owner_id == access.project.owner_id,
            MaxStaffAudit.resource == resource,
            MaxStaffAudit.record_id == record_id,
        )
        .order_by(MaxStaffAudit.created_at.desc(), MaxStaffAudit.id.desc())
        .limit(100)
    )
    return [
        StaffHistoryEntry(
            actor_name=row.actor_name,
            action=StaffAction(row.action),
            created_at=row.created_at,
            assignee_name=row.assignee_name,
            outcome=row.outcome,
        )
        for row in rows
    ]
