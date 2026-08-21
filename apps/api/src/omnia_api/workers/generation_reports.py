"""Durable PostgreSQL-backed delivery of development generation reports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.minio import get_minio_client
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.generation_telegram_report import GenerationTelegramReport
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services.generation_telegram_delivery import (
    TelegramBotClient,
    TelegramFailure,
    build_finish_text,
    build_start_delivery,
)
from omnia_api.services.generation_telegram_reports import suppress_pending_reports

POLL_SECONDS = 2.0
LEASE_SECONDS = 45
MAX_ATTEMPTS = 8
BACKOFF_SECONDS = (5, 15, 60, 180, 300, 600, 900, 900)
PREVIEW_WAIT_SECONDS = 300

Event = Literal["start", "finish"]
SessionFactory = async_sessionmaker[AsyncSession]
PreviewLoader = Callable[[str], Awaitable[bytes]]
EnabledCheck = bool | Callable[[], bool]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportClaim:
    run_id: UUID
    event: Event
    attempt: int
    previous_state: str


@dataclass(frozen=True)
class _Bundle:
    report: GenerationTelegramReport
    run: GenerationRun
    project: Project
    user_message: Message | None
    assistant_message: Message | None
    snapshot: Snapshot | None


class _DeliveryFailure(Exception):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _short(run_id: UUID) -> str:
    return str(run_id).split("-", 1)[0]


def _due(value: datetime | None, now: datetime) -> bool:
    return value is None or value <= now


def _is_enabled(value: EnabledCheck) -> bool:
    return bool(value() if callable(value) else value)


async def reconcile_waiting_previews(factory: SessionFactory, now: datetime) -> int:
    """Make completed reports deliverable only after PNG readiness or timeout."""

    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(GenerationTelegramReport, Snapshot.preview_key)
                    .join(
                        GenerationRun,
                        GenerationRun.id == GenerationTelegramReport.run_id,
                    )
                    .outerjoin(Message, Message.id == GenerationRun.assistant_message_id)
                    .outerjoin(Snapshot, Snapshot.id == Message.snapshot_id)
                    .where(GenerationTelegramReport.finish_state == "waiting_preview")
                    .with_for_update(
                        skip_locked=True,
                        of=GenerationTelegramReport,
                    )
                )
            ).all()
        )
        changed = 0
        for report, preview_key in rows:
            if preview_key or (
                report.preview_deadline_at is not None
                and report.preview_deadline_at <= now
            ):
                report.finish_state = "pending"
                report.finish_next_attempt_at = now
                report.lease_until = None
                changed += 1
        await session.commit()
        return changed


def _start_due_clause(now: datetime):  # type: ignore[no-untyped-def]
    return or_(
        and_(
            GenerationTelegramReport.start_state == "pending",
            or_(
                GenerationTelegramReport.start_next_attempt_at.is_(None),
                GenerationTelegramReport.start_next_attempt_at <= now,
            ),
        ),
        and_(
            GenerationTelegramReport.start_state == "sending",
            GenerationTelegramReport.lease_until.is_not(None),
            GenerationTelegramReport.lease_until <= now,
        ),
    )


def _finish_due_clause(now: datetime):  # type: ignore[no-untyped-def]
    next_due = or_(
        GenerationTelegramReport.finish_next_attempt_at.is_(None),
        GenerationTelegramReport.finish_next_attempt_at <= now,
    )
    return or_(
        and_(GenerationTelegramReport.finish_state == "pending", next_due),
        and_(
            GenerationTelegramReport.finish_state == "sending",
            GenerationTelegramReport.lease_until.is_not(None),
            GenerationTelegramReport.lease_until <= now,
        ),
        and_(
            GenerationTelegramReport.finish_state == "warning_sent",
            next_due,
            Snapshot.preview_key.is_not(None),
        ),
    )


async def claim_due_report(
    factory: SessionFactory,
    now: datetime,
) -> ReportClaim | None:
    """Claim one external event, prioritizing starts and suppressing duplicates."""

    async with factory() as session:
        exhausted = list(
            (
                await session.execute(
                    select(GenerationTelegramReport)
                    .where(
                        GenerationTelegramReport.lease_until.is_not(None),
                        GenerationTelegramReport.lease_until <= now,
                        or_(
                            and_(
                                GenerationTelegramReport.start_state == "sending",
                                GenerationTelegramReport.start_attempts >= MAX_ATTEMPTS,
                            ),
                            and_(
                                GenerationTelegramReport.finish_state == "sending",
                                GenerationTelegramReport.finish_attempts >= MAX_ATTEMPTS,
                            ),
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for stale in exhausted:
            if stale.start_state == "sending" and stale.start_attempts >= MAX_ATTEMPTS:
                stale.start_state = "failed"
                stale.start_next_attempt_at = None
            if stale.finish_state == "sending" and stale.finish_attempts >= MAX_ATTEMPTS:
                stale.finish_state = "failed"
                stale.finish_next_attempt_at = None
            stale.lease_until = None
            stale.last_delivery_error_code = "lease_attempts_exhausted"
        if exhausted:
            await session.commit()

        report = await session.scalar(
            select(GenerationTelegramReport)
            .where(
                GenerationTelegramReport.start_attempts < MAX_ATTEMPTS,
                _start_due_clause(now),
            )
            .order_by(GenerationTelegramReport.created_at, GenerationTelegramReport.run_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        event: Event = "start"
        if report is None:
            report = await session.scalar(
                select(GenerationTelegramReport)
                .join(
                    GenerationRun,
                    GenerationRun.id == GenerationTelegramReport.run_id,
                )
                .outerjoin(Message, Message.id == GenerationRun.assistant_message_id)
                .outerjoin(Snapshot, Snapshot.id == Message.snapshot_id)
                .where(
                    GenerationTelegramReport.start_state == "sent",
                    GenerationTelegramReport.start_message_id.is_not(None),
                    GenerationTelegramReport.finish_attempts < MAX_ATTEMPTS,
                    _finish_due_clause(now),
                )
                .order_by(
                    GenerationTelegramReport.created_at,
                    GenerationTelegramReport.run_id,
                )
                .with_for_update(
                    skip_locked=True,
                    of=GenerationTelegramReport,
                )
                .limit(1)
            )
            event = "finish"
        if report is None:
            return None

        if event == "start":
            previous_state = report.start_state
            report.start_attempts += 1
            attempt = report.start_attempts
            report.start_state = "sending"
        else:
            previous_state = report.finish_state
            report.finish_attempts += 1
            attempt = report.finish_attempts
            report.finish_state = "sending"
        report.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        await session.commit()
        return ReportClaim(report.run_id, event, attempt, previous_state)


async def _load_bundle(factory: SessionFactory, run_id: UUID) -> _Bundle:
    async with factory() as session:
        report = await session.get(GenerationTelegramReport, run_id)
        run = await session.get(GenerationRun, run_id)
        if report is None or run is None:
            raise _DeliveryFailure("source_run_missing", retryable=False)
        project = await session.get(Project, run.project_id)
        if project is None:
            raise _DeliveryFailure("source_project_missing", retryable=False)
        user_message = (
            await session.get(Message, run.user_message_id)
            if run.user_message_id is not None
            else None
        )
        assistant = (
            await session.get(Message, run.assistant_message_id)
            if run.assistant_message_id is not None
            else None
        )
        snapshot = (
            await session.get(Snapshot, assistant.snapshot_id)
            if assistant is not None and assistant.snapshot_id is not None
            else None
        )
        return _Bundle(report, run, project, user_message, assistant, snapshot)


async def _persist_start_message_id(
    factory: SessionFactory,
    claim: ReportClaim,
    message_id: int,
) -> None:
    async with factory() as session:
        report = await session.get(
            GenerationTelegramReport,
            claim.run_id,
            with_for_update=True,
        )
        if report is not None and report.start_message_id is None:
            report.start_message_id = int(message_id)
        await session.commit()


async def _mark_success(
    factory: SessionFactory,
    claim: ReportClaim,
    *,
    warning: bool = False,
) -> None:
    async with factory() as session:
        report = await session.get(
            GenerationTelegramReport,
            claim.run_id,
            with_for_update=True,
        )
        if report is None:
            return
        if claim.event == "start":
            report.start_state = "sent"
            report.start_next_attempt_at = None
        else:
            report.finish_state = "warning_sent" if warning else "sent"
            report.finish_next_attempt_at = None
        report.lease_until = None
        report.last_delivery_error_code = None
        await session.commit()


async def _mark_failure(
    factory: SessionFactory,
    claim: ReportClaim,
    *,
    code: str,
    retryable: bool,
    now: datetime,
    retry_after_seconds: int | None = None,
) -> None:
    async with factory() as session:
        report = await session.get(
            GenerationTelegramReport,
            claim.run_id,
            with_for_update=True,
        )
        if report is None:
            return
        report.lease_until = None
        report.last_delivery_error_code = code
        exhausted = claim.attempt >= MAX_ATTEMPTS
        if not retryable or exhausted:
            if claim.event == "start":
                report.start_state = "failed"
                report.start_next_attempt_at = None
            else:
                report.finish_state = "failed"
                report.finish_next_attempt_at = None
            outcome = "failed"
        else:
            delay = (
                max(1, min(900, retry_after_seconds))
                if retry_after_seconds is not None
                else BACKOFF_SECONDS[min(claim.attempt - 1, len(BACKOFF_SECONDS) - 1)]
            )
            next_attempt = now + timedelta(seconds=delay)
            if claim.event == "start":
                report.start_state = "pending"
                report.start_next_attempt_at = next_attempt
            else:
                report.finish_state = (
                    "warning_sent" if claim.previous_state == "warning_sent" else "pending"
                )
                report.finish_next_attempt_at = next_attempt
            outcome = "retry"
        await session.commit()
    log.info(
        "generation_report event=%s run=%s attempt=%s code=%s",
        outcome,
        _short(claim.run_id),
        claim.attempt,
        code,
    )


async def _suppress_all(factory: SessionFactory) -> None:
    async with factory() as session:
        await suppress_pending_reports(session)
        await session.commit()


def _elapsed_seconds(run: GenerationRun, now: datetime) -> int:
    start = run.started_at or run.created_at or now
    end = run.finished_at or now
    return max(0, int((end - start).total_seconds()))


async def _deliver_start(
    factory: SessionFactory,
    claim: ReportClaim,
    bundle: _Bundle,
    telegram: TelegramBotClient,
    *,
    enabled: EnabledCheck,
) -> None:
    if bundle.user_message is None:
        raise _DeliveryFailure("source_user_message_missing", retryable=False)
    payload = build_start_delivery(
        run_id=bundle.run.id,
        mode=bundle.run.response_mode or "",
        project_name=bundle.project.name,
        user_text=bundle.user_message.content,
    )
    message_id = bundle.report.start_message_id
    if message_id is None:
        if not _is_enabled(enabled):
            await _suppress_all(factory)
            return
        message_id = await telegram.send_message(payload.text)
        await _persist_start_message_id(factory, claim, message_id)
    if payload.prompt_document is not None and payload.prompt_filename is not None:
        if not _is_enabled(enabled):
            await _suppress_all(factory)
            return
        await telegram.send_document(
            payload.prompt_document,
            payload.prompt_filename,
            caption="Полный пользовательский промпт",
            reply_to=message_id,
        )
    await _mark_success(factory, claim)
    log.info(
        "generation_report event=start_sent run=%s attempt=%s",
        _short(claim.run_id),
        claim.attempt,
    )


async def _deliver_finish(
    factory: SessionFactory,
    claim: ReportClaim,
    bundle: _Bundle,
    telegram: TelegramBotClient,
    *,
    now: datetime,
    load_preview: PreviewLoader,
    enabled: EnabledCheck,
) -> None:
    reply_to = bundle.report.start_message_id
    if reply_to is None:
        raise _DeliveryFailure("source_start_message_missing", retryable=False)
    run = bundle.run
    elapsed = _elapsed_seconds(run, now)
    outcome: str
    warning = False
    if run.status == "failed":
        outcome = "failed"
    elif run.status == "cancelled":
        outcome = "cancelled"
    elif run.status != "completed":
        raise _DeliveryFailure("source_terminal_status_invalid", retryable=False)
    elif bundle.snapshot is None:
        if run.response_mode != "edit":
            raise _DeliveryFailure("source_snapshot_missing", retryable=False)
        outcome = "completed_no_snapshot"
    elif bundle.snapshot.preview_key:
        try:
            png = await load_preview(bundle.snapshot.preview_key)
        except Exception as exc:
            raise _DeliveryFailure("preview_load_failed", retryable=True) from exc
        if not png:
            raise _DeliveryFailure("preview_bytes_empty", retryable=True)
        outcome = "late_preview" if claim.previous_state == "warning_sent" else "completed"
        caption = build_finish_text(
            run_id=run.id,
            mode=run.response_mode or "",
            outcome=outcome,
            elapsed_seconds=elapsed,
            stage=bundle.report.last_stage,
            error=run.error,
            preview_error_code=bundle.report.preview_error_code,
        )
        if not _is_enabled(enabled):
            await _suppress_all(factory)
            return
        await telegram.send_photo(png, caption=caption, reply_to=reply_to)
        await _mark_success(factory, claim)
        log.info(
            "generation_report event=finish_sent run=%s attempt=%s",
            _short(claim.run_id),
            claim.attempt,
        )
        return
    else:
        outcome = "preview_warning"
        warning = True

    text = build_finish_text(
        run_id=run.id,
        mode=run.response_mode or "",
        outcome=outcome,
        elapsed_seconds=elapsed,
        stage=bundle.report.last_stage,
        error=run.error,
        preview_error_code=bundle.report.preview_error_code,
    )
    if not _is_enabled(enabled):
        await _suppress_all(factory)
        return
    await telegram.send_message(text, reply_to=reply_to)
    await _mark_success(factory, claim, warning=warning)
    event = "preview_warning" if warning else "finish_sent"
    log.info(
        "generation_report event=%s run=%s attempt=%s",
        event,
        _short(claim.run_id),
        claim.attempt,
    )


async def deliver_claim(
    factory: SessionFactory,
    claim: ReportClaim,
    telegram: TelegramBotClient,
    *,
    now: datetime,
    load_preview: PreviewLoader,
    enabled: EnabledCheck,
) -> None:
    """Perform one claimed external event and persist only fixed outcomes."""

    if not _is_enabled(enabled):
        await _suppress_all(factory)
        return
    try:
        bundle = await _load_bundle(factory, claim.run_id)
        if claim.event == "start":
            await _deliver_start(factory, claim, bundle, telegram, enabled=enabled)
        else:
            await _deliver_finish(
                factory,
                claim,
                bundle,
                telegram,
                now=now,
                load_preview=load_preview,
                enabled=enabled,
            )
    except TelegramFailure as exc:
        await _mark_failure(
            factory,
            claim,
            code=exc.code,
            retryable=exc.retryable,
            retry_after_seconds=exc.retry_after_seconds,
            now=now,
        )
    except _DeliveryFailure as exc:
        await _mark_failure(
            factory,
            claim,
            code=exc.code,
            retryable=exc.retryable,
            now=now,
        )
    except ValueError:
        await _mark_failure(
            factory,
            claim,
            code="invalid_delivery_configuration",
            retryable=False,
            now=now,
        )
    except Exception:
        await _mark_failure(
            factory,
            claim,
            code="delivery_unexpected",
            retryable=True,
            now=now,
        )


async def run_cycle(
    factory: SessionFactory,
    telegram: TelegramBotClient,
    *,
    now: datetime,
    load_preview: PreviewLoader,
    enabled: EnabledCheck,
) -> bool:
    if not _is_enabled(enabled):
        await _suppress_all(factory)
        return False
    await reconcile_waiting_previews(factory, now)
    claim = await claim_due_report(factory, now)
    if claim is None:
        return False
    await deliver_claim(
        factory,
        claim,
        telegram,
        now=now,
        load_preview=load_preview,
        enabled=enabled,
    )
    return True


async def load_preview_bytes(preview_key: str) -> bytes:
    """Read one internal MinIO object and always release the HTTP response."""

    settings = get_settings()

    def _read() -> bytes:
        response = get_minio_client().get_object(
            settings.minio_bucket_previews,
            preview_key,
        )
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()

    return await asyncio.to_thread(_read)


async def run_forever() -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    client: TelegramBotClient | None = None
    try:
        while True:
            settings = get_settings()
            enabled = bool(settings.dev_generation_telegram_reports)
            if not enabled:
                await _suppress_all(factory)
            else:
                if client is None:
                    token = (
                        settings.telegram_bot_token.get_secret_value()
                        if settings.telegram_bot_token is not None
                        else ""
                    )
                    try:
                        client = TelegramBotClient(
                            token=token,
                            chat_id=settings.telegram_chat_id,
                        )
                    except ValueError:
                        claim = await claim_due_report(factory, datetime.now(UTC))
                        if claim is not None:
                            await _mark_failure(
                                factory,
                                claim,
                                code="invalid_delivery_configuration",
                                retryable=False,
                                now=datetime.now(UTC),
                            )
                if client is not None:
                    try:
                        await run_cycle(
                            factory,
                            client,
                            now=datetime.now(UTC),
                            load_preview=load_preview_bytes,
                            enabled=lambda: bool(
                                get_settings().dev_generation_telegram_reports
                            ),
                        )
                    except Exception:
                        log.warning("generation_report event=cycle_failed code=cycle_error")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        if client is not None:
            await client.aclose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()


__all__ = [
    "BACKOFF_SECONDS",
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "POLL_SECONDS",
    "PREVIEW_WAIT_SECONDS",
    "ReportClaim",
    "claim_due_report",
    "deliver_claim",
    "load_preview_bytes",
    "main",
    "reconcile_waiting_previews",
    "run_cycle",
    "run_forever",
]
