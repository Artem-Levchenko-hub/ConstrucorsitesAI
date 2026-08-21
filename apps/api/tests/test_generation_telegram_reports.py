import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import Settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.generation_telegram_report import GenerationTelegramReport
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User


def _report_service() -> ModuleType:
    try:
        return import_module("omnia_api.services.generation_telegram_reports")
    except ModuleNotFoundError:
        pytest.fail("generation Telegram report lifecycle service is missing", pytrace=False)


async def _create_run(
    session: AsyncSession,
    *,
    mode: str = "build",
    status: str = "pending",
    with_snapshot: bool = False,
) -> tuple[GenerationRun, Project, Message, Message, Snapshot | None]:
    suffix = uuid.uuid4().hex[:8]
    owner = User(email=f"report-{suffix}@example.com", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name=f"Report project {suffix}",
        slug=f"report-project-{suffix}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    user_message = Message(
        project_id=project.id,
        role="user",
        content="точный пользовательский запрос",
    )
    assistant_message = Message(
        project_id=project.id,
        role="assistant",
        content="",
    )
    session.add_all([user_message, assistant_message])
    await session.flush()
    snapshot: Snapshot | None = None
    if with_snapshot:
        snapshot = Snapshot(
            project_id=project.id,
            commit_sha=(suffix * 5)[:40],
            prompt_text="точный пользовательский запрос",
            model_id="test-model",
        )
        session.add(snapshot)
        await session.flush()
        assistant_message.snapshot_id = snapshot.id
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=assistant_message.id,
        user_message_id=user_message.id,
        idempotency_key=f"report-key-{suffix}",
        prompt_hash=f"hash-{suffix}",
        status=status,
        response_mode=mode,
    )
    session.add(run)
    await session.flush()
    return run, project, user_message, assistant_message, snapshot


def test_generation_telegram_reports_are_disabled_without_runtime_configuration() -> None:
    fields = Settings.model_fields

    assert fields["dev_generation_telegram_reports"].default is False
    assert fields["telegram_bot_token"].default is None
    assert fields["telegram_chat_id"].default == 0


def test_generation_telegram_report_schema_rejects_unknown_lifecycle_values() -> None:
    try:
        module = import_module("omnia_api.models.generation_telegram_report")
    except ModuleNotFoundError:
        pytest.fail("GenerationTelegramReport model is missing", pytrace=False)

    table = module.GenerationTelegramReport.__table__
    assert set(table.columns.keys()) == {
        "run_id",
        "start_state",
        "start_message_id",
        "finish_state",
        "terminal_status",
        "last_stage",
        "start_attempts",
        "finish_attempts",
        "start_next_attempt_at",
        "finish_next_attempt_at",
        "lease_until",
        "last_delivery_error_code",
        "preview_error_code",
        "preview_deadline_at",
        "created_at",
        "updated_at",
    }
    assert str(table.c.start_state.server_default.arg) == "pending"
    assert str(table.c.finish_state.server_default.arg) == "waiting_terminal"
    assert str(table.c.last_stage.server_default.arg) == "accepted"
    assert str(table.c.start_attempts.server_default.arg) == "0"
    assert str(table.c.finish_attempts.server_default.arg) == "0"

    check_sql = "\n".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )
    for required in (
        "start_state IN ('pending', 'sending', 'sent', 'failed', 'suppressed')",
        "finish_state IN ('waiting_terminal', 'waiting_preview', 'pending', 'sending', "
        "'sent', 'warning_sent', 'failed', 'suppressed')",
        "terminal_status IS NULL OR terminal_status IN ('completed', 'failed', 'cancelled')",
        "last_stage IN ('accepted', 'routing', 'director', 'writer', 'images', "
        "'acceptance', 'snapshot', 'preview')",
        "start_attempts >= 0",
        "finish_attempts >= 0",
    ):
        assert required in check_sql

    assert {index.name for index in table.indexes} == {
        "ix_generation_telegram_reports_due_work"
    }


@pytest.mark.asyncio
async def test_deleting_generation_run_cascades_report_state(
    db_session: AsyncSession,
) -> None:
    owner = User(
        email=f"report-cascade-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        owner_id=owner.id,
        name="Report cascade",
        slug=f"report-cascade-{uuid.uuid4().hex[:8]}",
        template="blank",
    )
    db_session.add(project)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="report-cascade-key",
        prompt_hash="hash",
    )
    db_session.add(run)
    await db_session.flush()
    report = GenerationTelegramReport(run_id=run.id)
    db_session.add(report)
    await db_session.commit()

    await db_session.delete(run)
    await db_session.commit()

    remaining = await db_session.scalar(
        select(func.count())
        .select_from(GenerationTelegramReport)
        .where(GenerationTelegramReport.run_id == report.run_id)
    )
    assert remaining == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "enabled", "created"),
    [
        ("build", True, True),
        ("edit", True, True),
        ("clarify", True, False),
        ("build", False, False),
    ],
)
async def test_report_is_created_only_for_enabled_builds_and_edits(
    db_session: AsyncSession,
    mode: str,
    enabled: bool,
    created: bool,
) -> None:
    service = _report_service()
    run, _project, _user_message, _assistant_message, _snapshot = await _create_run(
        db_session,
        mode=mode,
    )

    actual = await service.create_report_for_run(db_session, run, enabled=enabled)
    await db_session.commit()

    assert actual is created
    report = await db_session.get(GenerationTelegramReport, run.id)
    assert (report is not None) is created
    if report is not None:
        assert report.start_state == "pending"
        assert report.finish_state == "waiting_terminal"
        assert report.last_stage == "accepted"


@pytest.mark.asyncio
async def test_duplicate_report_insert_isolated_from_outer_generation_transaction(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _report_service()
    run, project, _user_message, _assistant_message, _snapshot = await _create_run(db_session)
    original_flush = db_session.flush
    failed = False

    async def fail_observer_flush(*args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("synthetic observer insert failure")
        await original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", fail_observer_flush)
    created = await service.create_report_for_run(db_session, run, enabled=True)
    monkeypatch.setattr(db_session, "flush", original_flush)
    project.name = "outer transaction survived"
    await db_session.commit()

    assert created is False
    assert await db_session.get(GenerationTelegramReport, run.id) is None
    await db_session.refresh(project)
    assert project.name == "outer transaction survived"


@pytest.mark.asyncio
async def test_stage_recording_is_forward_only_and_ignores_unknown_stage(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.core import db as core_db

    monkeypatch.setattr(core_db, "get_engine", lambda: db_session.bind)
    service = _report_service()
    run, _project, _user_message, _assistant_message, _snapshot = await _create_run(db_session)
    assert await service.create_report_for_run(db_session, run, enabled=True)
    await db_session.commit()
    run_id = run.id

    await service.record_report_stage(run_id, "writer")
    await service.record_report_stage(run_id, "routing")
    await service.record_report_stage(run_id, "not-a-stage")

    db_session.expire_all()
    report = await db_session.get(GenerationTelegramReport, run_id)
    assert report is not None
    assert report.last_stage == "writer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "mode", "with_snapshot", "finish_state"),
    [
        ("failed", "build", False, "pending"),
        ("cancelled", "edit", False, "pending"),
        ("completed", "edit", False, "pending"),
        ("completed", "build", True, "waiting_preview"),
    ],
)
async def test_terminal_sync_derives_delivery_state_from_durable_product_outcome(
    db_session: AsyncSession,
    status: str,
    mode: str,
    with_snapshot: bool,
    finish_state: str,
) -> None:
    service = _report_service()
    run, _project, _user_message, _assistant_message, _snapshot = await _create_run(
        db_session,
        mode=mode,
        status=status,
        with_snapshot=with_snapshot,
    )
    assert await service.create_report_for_run(db_session, run, enabled=True)
    before = datetime.now(UTC)

    await service.sync_terminal_report(db_session, run, enabled=True)
    await db_session.commit()

    report = await db_session.get(GenerationTelegramReport, run.id)
    assert report is not None
    assert report.terminal_status == status
    assert report.finish_state == finish_state
    if finish_state == "waiting_preview":
        assert report.last_stage == "snapshot"
        assert report.preview_deadline_at is not None
        assert before + timedelta(minutes=4, seconds=59) <= report.preview_deadline_at
        assert report.preview_deadline_at <= before + timedelta(minutes=5, seconds=1)
    else:
        assert report.finish_next_attempt_at is not None


@pytest.mark.asyncio
async def test_disabled_terminal_sync_suppresses_unsent_report(
    db_session: AsyncSession,
) -> None:
    service = _report_service()
    run, _project, _user_message, _assistant_message, _snapshot = await _create_run(
        db_session,
        status="failed",
    )
    assert await service.create_report_for_run(db_session, run, enabled=True)

    await service.sync_terminal_report(db_session, run, enabled=False)
    await db_session.commit()

    report = await db_session.get(GenerationTelegramReport, run.id)
    assert report is not None
    assert report.start_state == "suppressed"
    assert report.finish_state == "suppressed"


@pytest.mark.asyncio
async def test_preview_callbacks_update_only_matching_non_suppressed_report(
    db_session: AsyncSession,
) -> None:
    service = _report_service()
    run, _project, _user_message, _assistant_message, snapshot = await _create_run(
        db_session,
        mode="build",
        status="completed",
        with_snapshot=True,
    )
    assert snapshot is not None
    assert await service.create_report_for_run(db_session, run, enabled=True)
    await service.sync_terminal_report(db_session, run, enabled=True)
    report = await db_session.get(GenerationTelegramReport, run.id)
    assert report is not None
    report.preview_error_code = "render_failed"
    await db_session.flush()

    await service.mark_snapshot_preview_ready(db_session, snapshot.id)
    await db_session.commit()

    assert report.last_stage == "preview"
    assert report.finish_state == "waiting_preview"
    assert report.preview_error_code is None

    report.finish_state = "warning_sent"
    report.last_stage = "acceptance"
    await db_session.flush()
    await service.mark_snapshot_preview_ready(db_session, snapshot.id)
    assert report.finish_state == "warning_sent"
    assert report.last_stage == "preview"

    report.finish_state = "suppressed"
    report.last_stage = "snapshot"
    await service.mark_snapshot_preview_ready(db_session, snapshot.id)
    assert report.last_stage == "snapshot"


@pytest.mark.asyncio
async def test_preview_failure_keeps_only_fixed_local_code(
    db_session: AsyncSession,
) -> None:
    service = _report_service()
    run, _project, _user_message, _assistant_message, snapshot = await _create_run(
        db_session,
        mode="build",
        status="completed",
        with_snapshot=True,
    )
    assert snapshot is not None
    assert await service.create_report_for_run(db_session, run, enabled=True)
    await service.sync_terminal_report(db_session, run, enabled=True)

    await service.mark_snapshot_preview_failed(db_session, snapshot.id, "render_failed")
    await service.mark_snapshot_preview_failed(db_session, snapshot.id, "raw browser exception")
    await db_session.commit()

    report = await db_session.get(GenerationTelegramReport, run.id)
    assert report is not None
    assert report.preview_error_code == "render_failed"


@pytest.mark.asyncio
async def test_suppress_pending_reports_preserves_completed_delivery(
    db_session: AsyncSession,
) -> None:
    service = _report_service()
    first_run, *_first = await _create_run(db_session, status="failed")
    second_run, *_second = await _create_run(db_session, status="completed", mode="edit")
    assert await service.create_report_for_run(db_session, first_run, enabled=True)
    assert await service.create_report_for_run(db_session, second_run, enabled=True)
    first = await db_session.get(GenerationTelegramReport, first_run.id)
    second = await db_session.get(GenerationTelegramReport, second_run.id)
    assert first is not None and second is not None
    first.start_state = "sent"
    first.finish_state = "pending"
    first.lease_until = datetime.now(UTC) + timedelta(seconds=30)
    second.start_state = "sent"
    second.finish_state = "sent"
    await db_session.flush()

    suppressed = await service.suppress_pending_reports(db_session)
    await db_session.commit()

    assert suppressed == 1
    assert first.start_state == "sent"
    assert first.finish_state == "suppressed"
    assert first.lease_until is None
    assert second.start_state == "sent"
    assert second.finish_state == "sent"
