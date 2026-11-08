"""Deliver saved MAX catalog versions independently of the owner's connection."""

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services import orchestrator_client, project_cell_runtime
from yleum_api.services.generation_deployment_drain import require_generation_admission
from yleum_api.services.max_content_delivery import config_digest, public_business_config

log = structlog.get_logger(__name__)
_RETRY_DELAY = timedelta(seconds=20)
_CONTROLLER_START_WINDOW = timedelta(seconds=20)


def schedule_catalog_delivery(record: MaxProjectConfig) -> None:
    record.catalog_delivery_after = datetime.now(UTC)


async def _deliver_public(record: MaxProjectConfig, *, scheduled: bool = False) -> None:
    config = public_business_config(MaxProjectConfigPayload.model_validate(record.config))
    version = record.config_version
    payload = {
        "owner_id": str(record.owner_id),
        "business_config": config,
        "business_config_version": version,
    }
    if scheduled:
        # Do not let a delayed request begin writing after our release lease ends.
        payload["delivery_start_before"] = (
            datetime.now(UTC) + _CONTROLLER_START_WINDOW
        ).isoformat()
        desired = {
            "owner_id": str(record.owner_id), "desired_config_version": version,
            "desired_config_digest": config_digest(config),
        }
        try:
            status = await asyncio.wait_for(
                orchestrator_client.published_cell_config_status(record.project_id, desired), 6,
            )
        except Exception:
            status = {}
        if (
            status.get("state") == "applied"
            and status.get("desired_config_version") == version
            and status.get("confirmed_config_version") == version
        ):
            return
    receipt = await orchestrator_client.configure_published_cell(record.project_id, payload)
    if type(receipt.get("applied")) is not bool:
        raise ValueError("Missing public catalog configuration receipt")


async def deliver_catalog_now(
    record: MaxProjectConfig, workspace_id: UUID | None, *, include_public: bool,
) -> None:
    """Keep preview and legacy publication effects on the existing short path."""
    effects: dict[str, Awaitable[object]] = {}
    if include_public:
        effects["public"] = _deliver_public(record)
    if workspace_id is not None:
        config = public_business_config(MaxProjectConfigPayload.model_validate(record.config))
        effects["preview"] = orchestrator_client.project_cell_apply_business_config(
            workspace_id, project_id=record.project_id, owner_id=record.owner_id,
            version=record.config_version, config=config,
        )
    results = await asyncio.gather(
        *(asyncio.wait_for(effect, timeout=6) for effect in effects.values()),
        return_exceptions=True,
    )
    for target, result in zip(effects, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("max_content_delivery_pending", project_id=str(record.project_id),
                        version=record.config_version, target=target,
                        error_type=type(result).__name__)


async def _deliver_due_catalog(
    factory: async_sessionmaker[AsyncSession],
    project_id: UUID,
    version: int,
    due_at: datetime,
) -> None:
    try:
        async with factory.begin() as session:
            # The shared release lock covers the runtime effects. The project lock
            # serializes delivery with owner edits, restoration and preview wakes.
            await require_generation_admission(session)
            await project_cell_runtime._try_preview_project_lock(session, project_id)
            record = await session.get(MaxProjectConfig, project_id)
            if (record is None or record.catalog_delivery_after != due_at
                    or record.config_version != version):
                return
            project = await session.get(Project, project_id)
            if (project is None or project.template != "max_miniapp"
                    or project.owner_id != record.owner_id):
                raise ValueError("MAX catalog delivery owner mismatch")
            if await project_cell_runtime._active_generation(session, project_id) is not None:
                record.catalog_delivery_after = datetime.now(UTC) + _RETRY_DELAY
                return
            await asyncio.wait_for(_deliver_public(record, scheduled=True), timeout=30)
            record.catalog_delivery_after = None
    except Exception as exc:
        log.warning("max_catalog_delivery_attempt_deferred", project_id=str(project_id),
                    error_type=type(exc).__name__)
        # A restored or busy project must not occupy the first slot forever.
        # Never postpone an edit that replaced the work while the lock was busy.
        async with factory.begin() as session:
            waiting = await session.scalar(
                select(MaxProjectConfig)
                .where(MaxProjectConfig.project_id == project_id,
                       MaxProjectConfig.config_version == version,
                       MaxProjectConfig.catalog_delivery_after == due_at)
                .with_for_update(skip_locked=True),
            )
            if waiting is not None:
                waiting.catalog_delivery_after = datetime.now(UTC) + _RETRY_DELAY


async def reconcile_catalog_deliveries(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        due = (await session.execute(
            select(MaxProjectConfig.project_id, MaxProjectConfig.config_version,
                   MaxProjectConfig.catalog_delivery_after)
            .where(MaxProjectConfig.catalog_delivery_after <= datetime.now(UTC))
            .order_by(MaxProjectConfig.catalog_delivery_after, MaxProjectConfig.project_id)
            .limit(4)
        )).all()
    await asyncio.gather(*(
        _deliver_due_catalog(factory, project_id, version, due_at)
        for project_id, version, due_at in due if due_at is not None
    ))
