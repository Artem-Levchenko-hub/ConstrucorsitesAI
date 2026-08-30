import asyncio
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.models.task_board import TaskBoardTask


async def test_task_board_crud_and_status_move(client: httpx.AsyncClient) -> None:
    empty = await client.get("/api/task-board/tasks")
    assert empty.status_code == 200
    assert empty.json() == []

    first = await client.post(
        "/api/task-board/tasks",
        json={
            "title": "  Подготовить релиз  ",
            "description": "  Проверить сборку и smoke-сценарий.  ",
            "assignee": "roman",
            "priority": "high",
        },
    )
    assert first.status_code == 201
    first_body = first.json()
    UUID(first_body["id"])
    assert first_body == {
        "id": first_body["id"],
        "title": "Подготовить релиз",
        "description": "Проверить сборку и smoke-сценарий.",
        "status": "backlog",
        "assignee": "roman",
        "priority": "high",
        "position": 0,
        "created_at": first_body["created_at"],
        "updated_at": first_body["updated_at"],
    }

    review_task = await client.post(
        "/api/task-board/tasks",
        json={
            "title": "Проверить макет",
            "assignee": "alexey_jr",
            "priority": "medium",
            "status": "review",
        },
    )
    assert review_task.status_code == 201
    assert review_task.json()["position"] == 0

    moved = await client.patch(
        f"/api/task-board/tasks/{first_body['id']}",
        json={"status": "review", "assignee": "artem"},
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "review"
    assert moved.json()["assignee"] == "artem"
    assert moved.json()["position"] == 1

    listed = await client.get("/api/task-board/tasks")
    assert listed.status_code == 200
    assert [task["title"] for task in listed.json()] == [
        "Проверить макет",
        "Подготовить релиз",
    ]

    deleted = await client.delete(f"/api/task-board/tasks/{first_body['id']}")
    assert deleted.status_code == 204
    missing = await client.patch(
        f"/api/task-board/tasks/{first_body['id']}",
        json={"status": "done"},
    )
    assert missing.status_code == 404


async def test_task_board_rejects_unknown_members_and_empty_titles(
    client: httpx.AsyncClient,
) -> None:
    unknown_member = await client.post(
        "/api/task-board/tasks",
        json={"title": "Нельзя назначить", "assignee": "unknown"},
    )
    assert unknown_member.status_code == 422

    empty_title = await client.post(
        "/api/task-board/tasks",
        json={"title": "   ", "assignee": "alexey"},
    )
    assert empty_title.status_code == 422


async def test_task_board_allocates_unique_positions_under_concurrent_writes(
    test_engine,
) -> None:
    from omnia_api.routers.task_board import _next_position

    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def create(index: int) -> None:
        async with factory() as session:
            position = await _next_position(session, "backlog")
            session.add(
                TaskBoardTask(
                    title=f"Параллельная задача {index}",
                    description="",
                    status="backlog",
                    assignee="roman",
                    priority="medium",
                    position=position,
                )
            )
            await session.commit()

    await asyncio.gather(*(create(index) for index in range(8)))

    async with factory() as session:
        positions = sorted((await session.execute(select(TaskBoardTask.position))).scalars())
    assert positions == list(range(8))


async def test_task_board_rejects_creates_after_capacity(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import task_board

    monkeypatch.setattr(task_board, "_BOARD_TASK_LIMIT", 1)
    first = await client.post(
        "/api/task-board/tasks",
        json={"title": "Единственная задача", "assignee": "alexey"},
    )
    overflow = await client.post(
        "/api/task-board/tasks",
        json={"title": "Лишняя задача", "assignee": "artem"},
    )

    assert first.status_code == 201
    assert overflow.status_code == 409
    assert overflow.json()["error"]["code"] == "conflict"
