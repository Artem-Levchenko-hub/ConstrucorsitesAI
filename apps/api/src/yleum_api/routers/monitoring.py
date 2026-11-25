from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Select, Uuid, bindparam, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.deps import AdminUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.deploy_target import DeployTarget
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client
from yleum_api.services.monitoring.catalog import METRICS
from yleum_api.services.monitoring.database import DATABASE_METRICS
from yleum_api.services.monitoring.prometheus import (
    PLATFORM_VPS,
    PROMETHEUS_METRICS,
    Sample,
    prometheus_samples,
)
from yleum_api.services.monitoring.publications import (
    PUBLICATION_METRICS,
    PUBLISH_SOURCE,
    publication_samples,
)

router = APIRouter(prefix="/api/admin/monitoring", tags=["admin-monitoring"])
logger = structlog.get_logger(__name__)

MonitorState = Literal["measured", "no_data", "stale", "source_unavailable", "not_applicable"]
Period = Literal["24h", "7d", "30d"]
_PERIOD_HOURS: dict[Period, int] = {"24h": 24, "7d": 168, "30d": 720}


class Definition(BaseModel):
    id: int
    group: str
    title: str
    unit: str
    formula: str
    source: str | None
    note: str | None = None


class Result(BaseModel):
    state: MonitorState
    measured_at: datetime
    points: list[Sample] = Field(default_factory=list)
    message: str | None = None


class Choice(BaseModel):
    id: UUID
    label: str
    client_id: UUID | None = None


class Filters(BaseModel):
    clients: list[Choice]
    projects: list[Choice]
    servers: list[Choice]


def _definition(metric_id: int) -> Definition:
    metric = METRICS[metric_id]
    database = DATABASE_METRICS.get(metric_id)
    prometheus = metric_id in PROMETHEUS_METRICS
    source = database.source if database else "Prometheus" if prometheus else None
    note = database.note if database else None
    if metric_id in PUBLICATION_METRICS:
        source, note = PUBLISH_SOURCE, PUBLICATION_METRICS[metric_id]
    return Definition(
        id=metric.id,
        group=metric.group,
        title=metric.title,
        unit=metric.unit,
        formula=metric.formula,
        source=source,
        note=note,
    )


def _active_targets() -> Select[tuple[DeployTarget]]:
    return select(DeployTarget).where(
        DeployTarget.verify_status == "ok",
        select(Project.id).where(
            Project.deploy_target_id == DeployTarget.id,
            Project.owner_id == DeployTarget.owner_id,
        ).exists(),
    )


async def _vps_coverage(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    client: UUID | None,
    project: UUID | None,
    vps: UUID | None,
) -> Result:
    active = _active_targets().with_only_columns(DeployTarget.id)
    if client is not None:
        active = active.where(DeployTarget.owner_id == client)
    if vps is not None:
        active = active.where(DeployTarget.id == vps)
    if project is not None:
        active = active.where(
            select(Project.id).where(
                Project.id == project,
                Project.deploy_target_id == DeployTarget.id,
                Project.owner_id == DeployTarget.owner_id,
            ).exists()
        )
    expected = {str(target) for target in (await session.scalars(active)).all()}
    if not expected:
        return Result(
            state="not_applicable",
            measured_at=end,
            message="В выбранном срезе нет активных клиентских VPS.",
        )
    url = get_settings().monitoring_prometheus_url
    if not url:
        return Result(
            state="source_unavailable",
            measured_at=end,
            message="Prometheus пока не подключён к дашборду.",
        )
    observed = await prometheus_samples(
        url, PROMETHEUS_METRICS[100], start=start, end=end, client=client, vps=vps,
    )
    covered = expected.intersection(point.series for point in observed)
    return Result(
        state="measured", measured_at=end,
        points=[
            Sample(
                series="Клиентские VPS",
                value=100 * len(covered) / len(expected),
                samples=len(expected),
            )
        ],
    )


@router.get("/catalog")
async def catalog(_admin: AdminUserDep, response: Response) -> list[Definition]:
    response.headers["Cache-Control"] = "no-store"
    return [_definition(metric_id) for metric_id in METRICS]


@router.get("/filters")
async def filters(
    _admin: AdminUserDep,
    session: SessionDep,
    response: Response,
) -> Filters:
    response.headers["Cache-Control"] = "no-store"
    clients = (
        await session.execute(
            select(User.id, User.email)
            .where(select(Project.id).where(Project.owner_id == User.id).exists())
            .order_by(User.email, User.id)
            .limit(750)
        )
    ).all()
    projects = (
        await session.execute(
            select(Project.id, Project.name, Project.owner_id)
            .order_by(Project.name, Project.id)
            .limit(750)
        )
    ).all()
    servers = (
        await session.execute(
            _active_targets().with_only_columns(
                DeployTarget.id, DeployTarget.label, DeployTarget.owner_id
            )
            .order_by(DeployTarget.label, DeployTarget.id)
            .limit(750)
        )
    ).all()
    return Filters(
        clients=[Choice(id=id_, label=email or str(id_)) for id_, email in clients],
        projects=[Choice(id=id_, label=name, client_id=owner) for id_, name, owner in projects],
        servers=[
            *(Choice(id=id_, label=label, client_id=owner) for id_, label, owner in servers),
            *(
                Choice(id=id_, label=f"Платформа · {host}")
                for id_, host in PLATFORM_VPS.items()
                if get_settings().monitoring_prometheus_url
            ),
        ],
    )


async def _database_samples(
    session: AsyncSession,
    metric_id: int,
    *,
    start: datetime,
    end: datetime,
    client: UUID | None,
    project: UUID | None,
    vps: UUID | None,
) -> list[Sample]:
    source = DATABASE_METRICS[metric_id]
    statement = text(source.sql)
    bindings = [
        bindparam(name, value, type_=type_)
        for name, value, type_ in (
            (
                "start",
                min(start, end - timedelta(days=7)) if metric_id == 27 else start,
                DateTime(timezone=True),
            ),
            ("end", end, DateTime(timezone=True)),
            ("client", client, Uuid()),
            ("project", project, Uuid()),
            ("vps", vps, Uuid()),
        )
        if f":{name}" in source.sql
    ]
    rows = (await session.execute(statement.bindparams(*bindings))).mappings()
    points = []
    for row in rows:
        value = row["value"]
        if value is None or not isfinite(value):
            continue
        points.append(
            Sample(
                series=row["series"],
                at=row["at"],
                value=value,
                p95=row.get("p95"),
                mean=row.get("mean"),
                samples=row["samples"],
            )
        )
    return points


async def _metric_result(
    session: AsyncSession,
    metric_id: int,
    *,
    start: datetime,
    end: datetime,
    client: UUID | None,
    project: UUID | None,
    vps: UUID | None,
) -> Result:
    if vps in PLATFORM_VPS and (client is not None or project is not None):
        return Result(
            state="not_applicable",
            measured_at=end,
            message="Сервер платформы не закреплён за отдельным клиентом или проектом.",
        )
    if metric_id == 100:
        return await _vps_coverage(
            session, start=start, end=end, client=client, project=project, vps=vps,
        )
    if metric_id in PUBLICATION_METRICS:
        if vps in PLATFORM_VPS:
            return Result(
                state="not_applicable",
                measured_at=end,
                message="Журнал публикаций не содержит привязки к этому серверу.",
            )
        points = await publication_samples(
            session, metric_id, start=start, end=end, client=client, project=project, vps=vps,
        )
    elif metric_id in DATABASE_METRICS:
        if vps in PLATFORM_VPS or (vps is not None and metric_id != 1):
            return Result(
                state="not_applicable",
                measured_at=end,
                message="Источник не содержит привязки к клиентской VPS.",
            )
        if metric_id == 96 and (client is not None or project is not None):
            return Result(
                state="not_applicable",
                measured_at=end,
                message="Пул БД относится ко всей платформе.",
            )
        points = await _database_samples(
            session,
            metric_id,
            start=start,
            end=end,
            client=client,
            project=project,
            vps=vps,
        )
    elif (metric := PROMETHEUS_METRICS.get(metric_id)) is not None:
        if project is not None or (not metric.per_vps and (client is not None or vps is not None)):
            return Result(
                state="not_applicable",
                measured_at=end,
                message="Источник не поддерживает этот разрез.",
            )
        url = get_settings().monitoring_prometheus_url
        if not url:
            return Result(
                state="source_unavailable",
                measured_at=end,
                message="Prometheus пока не подключён к дашборду.",
            )
        points = await prometheus_samples(
            url,
            metric,
            start=start,
            end=end,
            client=client,
            vps=vps,
        )
    else:
        return Result(
            state="source_unavailable",
            measured_at=end,
            message="Сбор данных для этой метрики пока не подключён.",
        )

    if not points:
        return Result(
            state="no_data",
            measured_at=end,
            message="Источник не вернул измерений для выбранного среза.",
        )
    timestamp = max((point.at for point in points if point.at is not None), default=None)
    if timestamp and metric_id in PROMETHEUS_METRICS and end - timestamp > timedelta(minutes=15):
        return Result(
            state="stale",
            measured_at=end,
            points=points,
            message="Последнее измерение старше 15 минут.",
        )
    return Result(state="measured", measured_at=end, points=points)


@router.get("/metrics/{metric_id}")
async def metric_data(
    metric_id: int,
    _admin: AdminUserDep,
    session: SessionDep,
    response: Response,
    period: Period = "24h",
    client: UUID | None = None,
    project: UUID | None = None,
    vps: UUID | None = None,
) -> Result:
    response.headers["Cache-Control"] = "no-store"
    if metric_id not in METRICS:
        raise ApiError("not_found", "Monitoring metric not found", 404)
    end = datetime.now(UTC)
    start = end - timedelta(hours=_PERIOD_HOURS[period])
    try:
        return await _metric_result(
            session,
            metric_id,
            start=start,
            end=end,
            client=client,
            project=project,
            vps=vps,
        )
    except (
        SQLAlchemyError,
        httpx.HTTPError,
        ValueError,
        orchestrator_client.OrchestratorUnavailable,
        orchestrator_client.OrchestratorBadRequest,
    ):
        logger.warning("monitoring.source_unavailable", metric_id=metric_id, exc_info=True)
        return Result(
            state="source_unavailable",
            measured_at=end,
            message="Источник данных сейчас недоступен.",
        )
