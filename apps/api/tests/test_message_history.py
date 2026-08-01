from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.user import User

pytestmark = pytest.mark.asyncio


async def test_message_history_returns_latest_rows_with_persisted_steps(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = User(email="history@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="History",
        slug=f"history-{uuid.uuid4().hex[:6]}",
        template="blank",
    )
    db_session.add(project)
    await db_session.flush()

    started_at = datetime(2026, 7, 30, tzinfo=UTC)
    rows = [
        Message(
            project_id=project.id,
            role="assistant",
            content=f"reply-{index}",
            created_at=started_at + timedelta(minutes=index),
            agent_steps=(
                [
                    {
                        "step": 1,
                        "kind": "step",
                        "action": "Проверяю проект",
                        "path": "",
                        "tool": "runtime_check",
                    }
                ]
                if index == 4
                else None
            ),
        )
        for index in range(5)
    ]
    db_session.add_all(rows)
    await db_session.flush()
    run_started_at = started_at + timedelta(minutes=4, seconds=2)
    run_finished_at = run_started_at + timedelta(seconds=73)
    db_session.add(
        GenerationRun(
            project_id=project.id,
            user_id=user.id,
            assistant_message_id=rows[-1].id,
            idempotency_key="history-timer",
            prompt_hash="a" * 64,
            status="completed",
            response_mode="build",
            created_at=started_at + timedelta(minutes=4),
            started_at=run_started_at,
            finished_at=run_finished_at,
        )
    )
    await db_session.commit()

    async def _current_user() -> User:
        return user

    app.dependency_overrides[get_current_user] = _current_user
    try:
        response = await client.get(f"/api/projects/{project.id}/messages?limit=2")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert [message["content"] for message in payload] == ["reply-3", "reply-4"]
    assert payload[-1]["agent_steps"][0]["action"] == "Проверяю проект"
    assert payload[-1]["generation_started_at"] == run_started_at.isoformat().replace("+00:00", "Z")
    assert payload[-1]["generation_finished_at"] == run_finished_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert payload[-1]["generation_status"] == "completed"
    assert payload[0]["generation_started_at"] is None
