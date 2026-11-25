"""Monitoring reads terminal publication evidence from its owning controllers."""

import asyncio
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from json import dumps
from statistics import median, quantiles
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.project import Project
from yleum_api.models.restoration import Restoration
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client, orchestrator_hosts
from yleum_api.services.monitoring.prometheus import Sample

PUBLICATION_METRICS = {
    53: "Доля по завершённым публикациям. Отмена и проверки текущей версии исключены.",
    54: "Время от принятия публикации до подтверждённой доступности новой версии.",
    55: "Подтверждённые публикации после восстановления версии, по клиенту и типу восстановления.",
    56: "Успешные доработки до первой подтверждённой публикации проекта.",
}
PUBLISH_SOURCE = "Журнал публикаций MAX"
_BATCH = 750


class PublicationFact(BaseModel):
    project_id: UUID
    run_id: str
    phase: Literal["done", "failed"]
    started_at: AwareDatetime | None
    finished_at: AwareDatetime
    restoration_id: UUID | None = None


class PublicationHistory(BaseModel):
    facts: list[PublicationFact]
    first_success: dict[UUID, AwareDatetime]


def _distribution(series: str, values: list[float]) -> list[Sample]:
    if not values:
        return []
    p95 = quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0]
    return [Sample(series=series, value=median(values), p95=p95, samples=len(values))]


async def publication_samples(
    session: AsyncSession,
    metric_id: int,
    *,
    start: datetime,
    end: datetime,
    client: UUID | None,
    project: UUID | None,
    vps: UUID | None,
) -> list[Sample]:
    scope = (
        select(Project.id, User.email, Project.owner_id)
        .join(User, User.id == Project.owner_id)
        .where(Project.template == "max_miniapp")
        .order_by(Project.id)
    )
    if client is not None:
        scope = scope.where(Project.owner_id == client)
    if project is not None:
        scope = scope.where(Project.id == project)
    if vps is not None:
        scope = scope.where(Project.deploy_target_id == vps)
    projects = {id_: email or str(owner) for id_, email, owner in (await session.execute(scope))}
    if not projects:
        return []

    histories = await _histories(list(projects), start, include_restorations=metric_id == 55)
    facts: dict[tuple[UUID, str], PublicationFact] = {}
    first: dict[UUID, datetime] = {}
    for history in histories:
        for fact in history.facts:
            if fact.project_id in projects and start <= fact.finished_at <= end:
                facts[(fact.project_id, fact.run_id)] = fact
        for id_, timestamp in history.first_success.items():
            if id_ in projects:
                first[id_] = min(first.get(id_, timestamp), timestamp)

    if metric_id == 53:
        if not facts:
            return []
        failures = sum(fact.phase == "failed" for fact in facts.values())
        return [Sample(series="Публикации", value=100 * failures / len(facts), samples=len(facts))]
    if metric_id == 54:
        return _distribution(
            "Публикации",
            [
                (fact.finished_at - fact.started_at).total_seconds()
                for fact in facts.values()
                if fact.phase == "done" and fact.started_at is not None
                and fact.started_at <= fact.finished_at
            ],
        )
    if metric_id == 55:
        return await _restorations(session, facts.values(), projects)
    return await _edits_before_first(session, first, start, end)


async def _histories(
    project_ids: list[UUID], start: datetime, *, include_restorations: bool,
) -> list[PublicationHistory]:
    hosts = orchestrator_hosts.registry().hosts
    semaphore = asyncio.Semaphore(6)

    async def collect(ids: list[UUID], host: str) -> PublicationHistory:
        async with semaphore:
            payload = await orchestrator_client.get_monitoring_publication_history(
                ids, start.isoformat(), host=host, include_restorations=include_restorations,
            )
        return PublicationHistory.model_validate(payload)

    return await asyncio.gather(*(
        collect(project_ids[index:index + _BATCH], host.name)
        for host in hosts for index in range(0, len(project_ids), _BATCH)
    ))


async def _restorations(
    session: AsyncSession, facts: Iterable[PublicationFact], projects: dict[UUID, str],
) -> list[Sample]:
    restored = [
        fact for fact in facts if fact.phase == "done" and fact.restoration_id is not None
    ]
    if not restored:
        return []
    modes = {
        (id_, project_id): mode for id_, project_id, mode in (
            await session.execute(
                select(Restoration.id, Restoration.project_id, Restoration.selected_branch)
                .where(Restoration.id.in_(fact.restoration_id for fact in restored))
            )
        )
    }
    counts: Counter[str] = Counter()
    reasons = {"exact": "Точное восстановление", "adaptive": "Восстановление с адаптацией"}
    for fact in restored:
        mode = modes.get((fact.restoration_id, fact.project_id)) if fact.restoration_id else None
        reason = reasons.get(mode or "", "Восстановление")
        counts[f"{projects[fact.project_id]} / {reason}"] += 1
    return [Sample(series=series, value=count, samples=count) for series, count in counts.items()]


async def _edits_before_first(
    session: AsyncSession, first: dict[UUID, datetime], start: datetime, end: datetime,
) -> list[Sample]:
    dates = [
        {"project_id": str(id_), "published_at": timestamp.isoformat()}
        for id_, timestamp in first.items() if start <= timestamp <= end
    ]
    if not dates:
        return []
    sql = text("""
        SELECT count(c.run_id)
        FROM jsonb_to_recordset(CAST(:dates AS jsonb))
          AS d(project_id uuid, published_at timestamptz)
        LEFT JOIN generation_runs r
          ON r.project_id = d.project_id AND r.status = 'completed'
          AND r.finished_at <= d.published_at
        LEFT JOIN generation_credit_reservations c ON c.run_id = r.id AND c.kind = 'edit'
        GROUP BY d.project_id
    """)
    values = [float(value) for value in (await session.scalars(sql, {"dates": dumps(dates)})).all()]
    return _distribution("Доработки", values)
