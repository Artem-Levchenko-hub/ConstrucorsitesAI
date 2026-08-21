from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.generation_telegram_report import GenerationTelegramReport
from omnia_api.models.message import Message

REPORTABLE_MODES = frozenset({"build", "edit"})
REPORT_STAGES = (
    "accepted",
    "routing",
    "director",
    "writer",
    "images",
    "acceptance",
    "snapshot",
    "preview",
)
PREVIEW_WAIT = timedelta(minutes=5)
PREVIEW_ERROR_CODES = frozenset(
    {
        "snapshot_missing",
        "source_missing",
        "container_unreachable",
        "render_failed",
        "upload_failed",
    }
)

_STAGE_INDEX = {stage: index for index, stage in enumerate(REPORT_STAGES)}
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_CLOSED_STATES = frozenset({"failed", "suppressed"})
log = logging.getLogger(__name__)


def _short_run_id(run_id: UUID) -> str:
    return str(run_id).split("-", 1)[0]


def _closed(report: GenerationTelegramReport) -> bool:
    return report.start_state in _CLOSED_STATES or report.finish_state in _CLOSED_STATES


def _advance_stage(report: GenerationTelegramReport, stage: str) -> None:
    if _STAGE_INDEX[stage] > _STAGE_INDEX[report.last_stage]:
        report.last_stage = stage


def _suppress_report(report: GenerationTelegramReport) -> bool:
    changed = False
    if report.start_state not in {"sent", "suppressed"}:
        report.start_state = "suppressed"
        changed = True
    if report.finish_state not in {"sent", "suppressed"}:
        report.finish_state = "suppressed"
        changed = True
    if changed:
        report.start_next_attempt_at = None
        report.finish_next_attempt_at = None
        report.lease_until = None
    return changed


async def create_report_for_run(
    session: AsyncSession,
    run: GenerationRun,
    *,
    enabled: bool,
) -> bool:
    """Add one optional observer row without endangering the outer transaction."""

    if not enabled or run.response_mode not in REPORTABLE_MODES:
        return False
    try:
        if await session.get(GenerationTelegramReport, run.id) is not None:
            return False
        async with session.begin_nested():
            session.add(GenerationTelegramReport(run_id=run.id))
            await session.flush()
        return True
    except Exception:
        log.warning(
            "generation_report code=create_failed run=%s",
            _short_run_id(run.id),
        )
        return False


async def record_report_stage(run_id: UUID, stage: str) -> None:
    """Persist one normalized forward-only stage in an isolated short session."""

    if stage not in _STAGE_INDEX:
        return
    try:
        from omnia_api.core.db import get_engine

        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with factory() as session:
            report = await session.scalar(
                select(GenerationTelegramReport)
                .where(GenerationTelegramReport.run_id == run_id)
                .with_for_update()
            )
            if report is None or _closed(report):
                return
            _advance_stage(report, stage)
            await session.commit()
    except Exception:
        log.warning(
            "generation_report code=stage_failed run=%s",
            _short_run_id(run_id),
        )


async def sync_terminal_report(
    session: AsyncSession,
    run: GenerationRun,
    *,
    enabled: bool,
) -> None:
    """Mirror a terminal run inside the caller's transaction, behind a savepoint."""

    if run.status not in _TERMINAL_STATUSES:
        return
    try:
        async with session.begin_nested():
            report = await session.get(GenerationTelegramReport, run.id)
            if report is None:
                return
            if not enabled:
                _suppress_report(report)
                await session.flush()
                return
            if report.finish_state in {"sent", "suppressed"}:
                return

            now = datetime.now(UTC)
            report.terminal_status = run.status
            report.lease_until = None
            report.last_delivery_error_code = None
            if run.status in {"failed", "cancelled"}:
                report.finish_state = "pending"
                report.finish_next_attempt_at = now
                await session.flush()
                return

            assistant = (
                await session.get(Message, run.assistant_message_id)
                if run.assistant_message_id is not None
                else None
            )
            if assistant is not None and assistant.snapshot_id is not None:
                report.finish_state = "waiting_preview"
                report.finish_next_attempt_at = None
                report.preview_deadline_at = report.preview_deadline_at or (now + PREVIEW_WAIT)
                _advance_stage(report, "snapshot")
            else:
                report.finish_state = "pending"
                report.finish_next_attempt_at = now
            await session.flush()
    except Exception:
        log.warning(
            "generation_report code=terminal_sync_failed run=%s",
            _short_run_id(run.id),
        )


async def _report_for_snapshot(
    session: AsyncSession,
    snapshot_id: UUID,
) -> GenerationTelegramReport | None:
    return cast(
        GenerationTelegramReport | None,
        await session.scalar(
            select(GenerationTelegramReport)
            .join(GenerationRun, GenerationRun.id == GenerationTelegramReport.run_id)
            .join(Message, Message.id == GenerationRun.assistant_message_id)
            .where(Message.snapshot_id == snapshot_id)
            .with_for_update()
        ),
    )


async def mark_snapshot_preview_ready(session: AsyncSession, snapshot_id: UUID) -> None:
    """Record that the existing preview worker committed a PNG for this run."""

    try:
        async with session.begin_nested():
            report = await _report_for_snapshot(session, snapshot_id)
            if report is None or _closed(report):
                return
            _advance_stage(report, "preview")
            report.preview_error_code = None
            await session.flush()
    except Exception:
        log.warning("generation_report code=preview_ready_failed")


async def mark_snapshot_preview_failed(
    session: AsyncSession,
    snapshot_id: UUID,
    code: str,
) -> None:
    """Persist only a normalized local preview failure category."""

    if code not in PREVIEW_ERROR_CODES:
        return
    try:
        async with session.begin_nested():
            report = await _report_for_snapshot(session, snapshot_id)
            if report is None or _closed(report):
                return
            report.preview_error_code = code
            await session.flush()
    except Exception:
        log.warning("generation_report code=preview_failure_sync_failed")


async def suppress_pending_reports(session: AsyncSession) -> int:
    """Suppress every unfinished row when the development kill switch is off."""

    try:
        reports = list(
            (
                await session.execute(
                    select(GenerationTelegramReport)
                    .where(
                        or_(
                            GenerationTelegramReport.start_state.not_in(
                                ("sent", "suppressed")
                            ),
                            GenerationTelegramReport.finish_state.not_in(
                                ("sent", "suppressed")
                            ),
                        )
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        changed = sum(1 for report in reports if _suppress_report(report))
        await session.flush()
        return changed
    except Exception:
        log.warning("generation_report code=suppress_failed")
        return 0


__all__ = [
    "PREVIEW_ERROR_CODES",
    "PREVIEW_WAIT",
    "REPORTABLE_MODES",
    "REPORT_STAGES",
    "create_report_for_run",
    "mark_snapshot_preview_failed",
    "mark_snapshot_preview_ready",
    "record_report_stage",
    "suppress_pending_reports",
    "sync_terminal_report",
]
