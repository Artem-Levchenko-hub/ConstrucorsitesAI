import asyncio
from io import BytesIO
from urllib.parse import quote
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.task_board import TaskBoardAttachmentCleanup, TaskBoardTask


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
        "attachments": [],
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


async def test_task_board_uploads_downloads_and_deletes_html_attachment(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import task_board

    stored: dict[str, bytes] = {}

    def store_attachment(
        task_id: UUID,
        attachment_id: UUID,
        filename: str,
        content_type: str,
        raw: bytes,
    ) -> str:
        object_key = f"task-board/{task_id}/{attachment_id}/{filename}"
        stored[object_key] = raw
        return object_key

    def load_attachment(object_key: str) -> BytesIO | None:
        raw = stored.get(object_key)
        return BytesIO(raw) if raw is not None else None

    def delete_attachment(object_key: str) -> None:
        stored.pop(object_key, None)

    monkeypatch.setattr(task_board, "_store_attachment", store_attachment, raising=False)
    monkeypatch.setattr(task_board, "_load_attachment", load_attachment, raising=False)
    monkeypatch.setattr(task_board, "_delete_attachment", delete_attachment, raising=False)

    created = await client.post(
        "/api/task-board/tasks",
        json={"title": "Проверить лендинг", "assignee": "artem"},
    )
    task_id = created.json()["id"]
    html = b"<!doctype html><title>Task artifact</title>"

    uploaded = await client.post(
        f"/api/task-board/tasks/{task_id}/attachments",
        content=html,
        headers={
            "Content-Type": "text/html",
            "X-File-Name": quote("landing.html"),
        },
    )

    assert uploaded.status_code == 201
    attachment = uploaded.json()
    UUID(attachment["id"])
    assert attachment == {
        "id": attachment["id"],
        "filename": "landing.html",
        "content_type": "text/html",
        "size": len(html),
        "created_at": attachment["created_at"],
    }

    listed = await client.get("/api/task-board/tasks")
    assert listed.json()[0]["attachments"] == [attachment]

    downloaded = await client.get(f"/api/task-board/tasks/{task_id}/attachments/{attachment['id']}")
    assert downloaded.status_code == 200
    assert downloaded.content == html
    assert downloaded.headers["content-type"].startswith("text/html")
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "landing.html" in downloaded.headers["content-disposition"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-security-policy"] == "sandbox; default-src 'none'"

    deleted = await client.delete(f"/api/task-board/tasks/{task_id}/attachments/{attachment['id']}")
    assert deleted.status_code == 204
    after_delete = await client.get("/api/task-board/tasks")
    assert after_delete.json()[0]["attachments"] == []


async def test_task_board_rejects_oversized_and_accepts_any_safe_filename(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import task_board

    monkeypatch.setattr(task_board, "_MAX_ATTACHMENT_BYTES", 5, raising=False)
    monkeypatch.setattr(
        task_board,
        "_store_attachment",
        lambda task_id, attachment_id, filename, content_type, raw: (
            f"tasks/{task_id}/{attachment_id}"
        ),
    )
    created = await client.post(
        "/api/task-board/tasks",
        json={"title": "Вложения", "assignee": "roman"},
    )
    task_id = created.json()["id"]

    oversized = await client.post(
        f"/api/task-board/tasks/{task_id}/attachments",
        content=b"123456",
        headers={"Content-Type": "text/html", "X-File-Name": "large.html"},
    )
    accepted = await client.post(
        f"/api/task-board/tasks/{task_id}/attachments",
        content=b"1234",
        headers={
            "Content-Type": "application/octet-stream",
            "X-File-Name": quote("../../artifact\n.html"),
        },
    )

    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "too_large"
    assert accepted.status_code == 201
    assert accepted.json()["filename"] == "artifact.html"


async def test_task_board_enforces_per_task_and_board_attachment_quotas(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import task_board

    monkeypatch.setattr(task_board, "_MAX_ATTACHMENTS_PER_TASK", 1)
    monkeypatch.setattr(task_board, "_BOARD_ATTACHMENT_BYTES_LIMIT", 5)
    monkeypatch.setattr(
        task_board,
        "_store_attachment",
        lambda task_id, attachment_id, filename, content_type, raw: (
            f"tasks/{task_id}/{attachment_id}"
        ),
    )
    first_task = await client.post(
        "/api/task-board/tasks",
        json={"title": "Первая", "assignee": "roman"},
    )
    first_task_id = first_task.json()["id"]
    first = await client.post(
        f"/api/task-board/tasks/{first_task_id}/attachments",
        content=b"1234",
        headers={"X-File-Name": "artifact"},
    )
    per_task_overflow = await client.post(
        f"/api/task-board/tasks/{first_task_id}/attachments",
        content=b"x",
        headers={"X-File-Name": "second.log"},
    )

    assert first.status_code == 201
    assert per_task_overflow.status_code == 409

    monkeypatch.setattr(task_board, "_MAX_ATTACHMENTS_PER_TASK", 10)
    second_task = await client.post(
        "/api/task-board/tasks",
        json={"title": "Вторая", "assignee": "artem"},
    )
    board_overflow = await client.post(
        f"/api/task-board/tasks/{second_task.json()['id']}/attachments",
        content=b"xx",
        headers={"X-File-Name": "overflow.bin"},
    )

    assert board_overflow.status_code == 409
    assert board_overflow.json()["error"]["code"] == "conflict"


async def test_task_board_reports_storage_download_failures(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import task_board
    from omnia_api.services.task_board_attachments import AttachmentStorageError

    monkeypatch.setattr(
        task_board,
        "_store_attachment",
        lambda task_id, attachment_id, filename, content_type, raw: (
            f"tasks/{task_id}/{attachment_id}"
        ),
    )
    created = await client.post(
        "/api/task-board/tasks",
        json={"title": "Сбой скачивания", "assignee": "alexey"},
    )
    uploaded = await client.post(
        f"/api/task-board/tasks/{created.json()['id']}/attachments",
        content=b"data",
        headers={"X-File-Name": "artifact.html", "Content-Type": "text/html"},
    )

    def fail_load(_object_key: str) -> None:
        raise AttachmentStorageError("offline")

    monkeypatch.setattr(task_board, "_load_attachment", fail_load)
    response = await client.get(
        f"/api/task-board/tasks/{created.json()['id']}/attachments/{uploaded.json()['id']}"
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upload_failed"


async def test_task_board_keeps_failed_object_deletion_in_durable_cleanup_outbox(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.routers import task_board
    from omnia_api.services.task_board_attachments import AttachmentStorageError

    object_key = "tasks/task/attachment.html"
    monkeypatch.setattr(task_board, "_store_attachment", lambda *_args: object_key)
    created = await client.post(
        "/api/task-board/tasks",
        json={"title": "Надёжное удаление", "assignee": "alexey_jr"},
    )
    task_id = created.json()["id"]
    uploaded = await client.post(
        f"/api/task-board/tasks/{task_id}/attachments",
        content=b"<html></html>",
        headers={"X-File-Name": "artifact.html", "Content-Type": "text/html"},
    )

    def fail_delete(_object_key: str) -> None:
        raise AttachmentStorageError("offline")

    monkeypatch.setattr(task_board, "_delete_attachment", fail_delete)
    deleted = await client.delete(
        f"/api/task-board/tasks/{task_id}/attachments/{uploaded.json()['id']}"
    )
    listed = await client.get("/api/task-board/tasks")
    cleanup = await db_session.scalar(select(TaskBoardAttachmentCleanup))

    assert deleted.status_code == 204
    assert listed.json()[0]["attachments"] == []
    assert cleanup is not None
    assert cleanup.object_key == object_key
    assert cleanup.size == len(b"<html></html>")
