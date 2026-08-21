import uuid
from importlib import import_module

import pytest
from sqlalchemy import CheckConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import Settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.generation_telegram_report import GenerationTelegramReport
from omnia_api.models.project import Project
from omnia_api.models.user import User


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
