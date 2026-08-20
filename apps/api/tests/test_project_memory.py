from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_memory import ProjectMemoryRevision
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services.generation_runs import compile_terminal_run_memory
from omnia_api.services.project_memory import (
    compile_project_memory_revision,
    record_run_artifacts,
    render_project_memory_context,
)

pytestmark = pytest.mark.asyncio


async def _project(session: AsyncSession) -> tuple[User, Project]:
    owner = User(email=f"memory-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Умный тренер",
        slug=f"memory-{uuid.uuid4().hex[:8]}",
        template="max_miniapp",
        discovery_spec={"tone": "premium", "sections": ["Статистика", "Тренировки"]},
    )
    session.add(project)
    await session.commit()
    return owner, project


async def test_terminal_memory_compiles_for_canary_when_global_is_off(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project = await _project(db_session)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="memory-canary-enabled",
        prompt_hash="hash",
        status="completed",
        response_mode="build",
        finished_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.flush()
    monkeypatch.setattr(
        "omnia_api.core.config.get_settings",
        lambda: SimpleNamespace(
            use_project_memory=False,
            project_memory_canary_users=str(owner.id),
        ),
    )

    await compile_terminal_run_memory(db_session, run)

    revision = (
        await db_session.execute(
            select(ProjectMemoryRevision).where(ProjectMemoryRevision.run_id == run.id)
        )
    ).scalar_one_or_none()
    assert revision is not None


async def test_terminal_memory_stays_off_for_non_canary(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project = await _project(db_session)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="memory-canary-disabled",
        prompt_hash="hash",
        status="completed",
        response_mode="build",
        finished_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.flush()
    monkeypatch.setattr(
        "omnia_api.core.config.get_settings",
        lambda: SimpleNamespace(
            use_project_memory=False,
            project_memory_canary_users=str(uuid.uuid4()),
        ),
    )

    await compile_terminal_run_memory(db_session, run)

    revision = (
        await db_session.execute(
            select(ProjectMemoryRevision).where(ProjectMemoryRevision.run_id == run.id)
        )
    ).scalar_one_or_none()
    assert revision is None


async def test_memory_revision_links_verified_prompt_and_snapshot(
    db_session: AsyncSession,
) -> None:
    owner, project = await _project(db_session)
    user = Message(
        project_id=project.id,
        role="user",
        content="Собери экран статистики тренировок",
    )
    db_session.add(user)
    await db_session.flush()
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha="a" * 40,
        prompt_text=user.content,
        model_id="test",
    )
    db_session.add(snapshot)
    await db_session.flush()
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Готово",
        snapshot_id=snapshot.id,
        tokens_in=1,
        tokens_out=1,
    )
    db_session.add(assistant)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        idempotency_key="memory-success",
        prompt_hash="hash",
        status="completed",
        response_mode="build",
        finished_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.flush()
    record_run_artifacts(
        run,
        snapshot_id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        changed_files=["src/app/page.tsx", "src/app/globals.css"],
    )

    revision = await compile_project_memory_revision(db_session, run)
    duplicate = await compile_project_memory_revision(db_session, run)
    await db_session.commit()

    assert revision is not None
    assert duplicate is not None
    assert duplicate.id == revision.id
    assert revision.version == 1
    assert revision.snapshot_id == snapshot.id
    assert revision.memory["recent_requests"][-1]["text"] == user.content
    assert revision.memory["verified_changes"][-1]["changed_files"] == [
        "src/app/globals.css",
        "src/app/page.tsx",
    ]
    assert revision.memory["product_contract"]["tone"] == "premium"

    context = await render_project_memory_context(db_session, project.id)
    assert "PROJECT MEMORY v1" in context
    assert "Собери экран статистики" in context
    assert "src/app/page.tsx" in context


async def test_failure_is_redacted_linked_and_resolved_by_next_snapshot(
    db_session: AsyncSession,
) -> None:
    owner, project = await _project(db_session)
    failed_user = Message(
        project_id=project.id,
        role="user",
        content="Никогда не добавляй email-вход. api key: sk-abcdefghijklmnop1234",
    )
    failed_assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Сборка остановлена",
        tokens_in=0,
        tokens_out=0,
        agent_steps=[
            {
                "tool": "build",
                "path": "src/app/page.tsx",
                "detail": "TS2307: Cannot find module '@/missing'",
                "ok": False,
            }
        ],
    )
    db_session.add_all([failed_user, failed_assistant])
    await db_session.flush()
    failed_run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=failed_user.id,
        assistant_message_id=failed_assistant.id,
        idempotency_key="memory-failed",
        prompt_hash="hash-1",
        status="failed",
        response_mode="build",
        error="TS2307: Cannot find module '@/missing' at build 123456",
        finished_at=datetime.now(UTC),
    )
    db_session.add(failed_run)
    await db_session.flush()
    failed_revision = await compile_project_memory_revision(db_session, failed_run)
    assert failed_revision is not None
    assert failed_revision.version == 1
    assert "sk-" not in str(failed_revision.memory)
    assert "[CREDENTIAL REDACTED]" in str(failed_revision.memory)
    assert failed_revision.memory["known_failures"][-1]["status"] == "open"
    assert "src/app/page.tsx" in failed_revision.memory["known_failures"][-1]["summary"]
    assert "TS2307" in failed_revision.memory["known_failures"][-1]["summary"]
    assert failed_revision.memory["user_rules"][-1]["source_run_id"] == str(failed_run.id)

    success_user = Message(project_id=project.id, role="user", content="Почини и продолжай")
    db_session.add(success_user)
    await db_session.flush()
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha="b" * 40,
        prompt_text=success_user.content,
        model_id="test",
    )
    db_session.add(snapshot)
    await db_session.flush()
    success_assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Исправлено",
        snapshot_id=snapshot.id,
        tokens_in=1,
        tokens_out=1,
    )
    db_session.add(success_assistant)
    await db_session.flush()
    success_run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=success_user.id,
        assistant_message_id=success_assistant.id,
        idempotency_key="memory-repaired",
        prompt_hash="hash-2",
        status="completed",
        response_mode="build",
        finished_at=datetime.now(UTC),
    )
    db_session.add(success_run)
    await db_session.flush()
    record_run_artifacts(
        success_run,
        snapshot_id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        changed_files=["src/app/page.tsx"],
    )

    success_revision = await compile_project_memory_revision(db_session, success_run)
    await db_session.commit()

    assert success_revision is not None
    assert success_revision.version == 2
    assert success_revision.parent_id == failed_revision.id
    assert success_revision.memory["known_failures"][-1]["status"] == "resolved"
    assert success_revision.memory["known_failures"][-1]["resolved_by_run_id"] == str(
        success_run.id
    )

    context = await render_project_memory_context(db_session, project.id)
    assert "Никогда не добавляй email-вход" in context
    assert "Known unresolved failures" not in context
