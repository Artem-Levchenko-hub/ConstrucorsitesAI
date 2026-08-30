import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select, text
from starlette.background import BackgroundTask

from omnia_api.core.deps import SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.ratelimit import rate_limit_task_board
from omnia_api.models.task_board import (
    TaskBoardAttachment,
    TaskBoardAttachmentCleanup,
    TaskBoardTask,
)
from omnia_api.schemas.task_board import (
    TaskBoardAttachmentPublic,
    TaskBoardStatus,
    TaskBoardTaskCreate,
    TaskBoardTaskPublic,
    TaskBoardTaskUpdate,
)
from omnia_api.services import task_board_attachments as attachment_storage

router = APIRouter(prefix="/api/task-board/tasks", tags=["task-board"])
log = logging.getLogger(__name__)

_BOARD_TASK_LIMIT = 500
_BOARD_LOCK_KEY = "omnia-task-board"
_BOARD_ATTACHMENT_QUOTA_LOCK_KEY = "omnia-task-board-attachment-quota"
_MAX_ATTACHMENTS_PER_TASK = 10
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_BOARD_ATTACHMENT_BYTES_LIMIT = 1024 * 1024 * 1024

_store_attachment = attachment_storage.store_attachment
_load_attachment = attachment_storage.load_attachment
_attachment_object_key = attachment_storage.attachment_object_key

_STATUS_ORDER = case(
    (TaskBoardTask.status == "backlog", 0),
    (TaskBoardTask.status == "in_progress", 1),
    (TaskBoardTask.status == "review", 2),
    else_=3,
)


async def _next_position(session: SessionDep, board_status: TaskBoardStatus) -> int:
    # One shared board means one small transaction-scoped lock is enough to
    # serialize MAX(position)+1 across API workers and concurrent browsers.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:board_key))"),
        {"board_key": _BOARD_LOCK_KEY},
    )
    last_position = (
        await session.execute(
            select(func.max(TaskBoardTask.position)).where(TaskBoardTask.status == board_status)
        )
    ).scalar_one()
    return (last_position if last_position is not None else -1) + 1


async def _get_task(session: SessionDep, task_id: UUID) -> TaskBoardTask:
    task = await session.get(TaskBoardTask, task_id)
    if task is None:
        raise ApiError(
            "not_found",
            "Задача не найдена",
            status.HTTP_404_NOT_FOUND,
        )
    return task


async def _get_attachment(
    session: SessionDep,
    task_id: UUID,
    attachment_id: UUID,
) -> TaskBoardAttachment:
    attachment = await session.scalar(
        select(TaskBoardAttachment).where(
            TaskBoardAttachment.id == attachment_id,
            TaskBoardAttachment.task_id == task_id,
        )
    )
    if attachment is None:
        raise ApiError(
            "not_found",
            "Вложение не найдено",
            status.HTTP_404_NOT_FOUND,
        )
    return attachment


async def _lock_task_attachments(session: SessionDep, task_id: UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:attachment_key))"),
        {"attachment_key": f"omnia-task-board-attachments:{task_id}"},
    )


async def _lock_board_attachment_quota(session: SessionDep) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:attachment_key))"),
        {"attachment_key": _BOARD_ATTACHMENT_QUOTA_LOCK_KEY},
    )


def _enqueue_attachment_cleanup(
    session: SessionDep,
    objects: list[tuple[str, int]],
) -> None:
    session.add_all(
        TaskBoardAttachmentCleanup(object_key=object_key, size=size) for object_key, size in objects
    )


def _attachment_filename(request: Request) -> str:
    encoded_name = request.headers.get("x-file-name", "")
    filename = unquote(encoded_name).replace("\\", "/").rsplit("/", 1)[-1].strip()
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    if not filename or len(filename) > 255:
        raise ApiError(
            "bad_request",
            "Укажите корректное имя файла",
            status.HTTP_400_BAD_REQUEST,
        )
    return filename


async def _read_attachment_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_ATTACHMENT_BYTES:
                raise ApiError(
                    "too_large",
                    "Файл слишком большой (максимум 10 МБ)",
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
        except ValueError:
            raise ApiError(
                "bad_request",
                "Некорректный размер файла",
                status.HTTP_400_BAD_REQUEST,
            ) from None

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_ATTACHMENT_BYTES:
            raise ApiError(
                "too_large",
                "Файл слишком большой (максимум 10 МБ)",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
    if not body:
        raise ApiError(
            "bad_request",
            "Нельзя загрузить пустой файл",
            status.HTTP_400_BAD_REQUEST,
        )
    return bytes(body)


def _close_attachment_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        close()
    release_conn = getattr(stream, "release_conn", None)
    if callable(release_conn):
        release_conn()


def _content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "attachment"
    ascii_name = ascii_name.replace('"', "_").replace("\\", "_")
    encoded_name = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"


@router.get("", response_model=list[TaskBoardTaskPublic])
async def list_task_board_tasks(session: SessionDep) -> list[TaskBoardTask]:
    result = await session.execute(
        select(TaskBoardTask).order_by(
            _STATUS_ORDER,
            TaskBoardTask.position,
            TaskBoardTask.created_at,
        )
    )
    return list(result.scalars())


@router.post(
    "",
    response_model=TaskBoardTaskPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_task_board)],
)
async def create_task_board_task(
    payload: TaskBoardTaskCreate,
    session: SessionDep,
) -> TaskBoardTask:
    position = await _next_position(session, payload.status)
    task_count = (
        await session.execute(select(func.count()).select_from(TaskBoardTask))
    ).scalar_one()
    if task_count >= _BOARD_TASK_LIMIT:
        raise ApiError(
            "conflict",
            "Доска заполнена. Удалите завершённые задачи перед добавлением новых",
            status.HTTP_409_CONFLICT,
        )
    task = TaskBoardTask(
        **payload.model_dump(),
        position=position,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@router.patch(
    "/{task_id}",
    response_model=TaskBoardTaskPublic,
    dependencies=[Depends(rate_limit_task_board)],
)
async def update_task_board_task(
    task_id: UUID,
    payload: TaskBoardTaskUpdate,
    session: SessionDep,
) -> TaskBoardTask:
    task = await _get_task(session, task_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    next_status = changes.get("status")
    if next_status is not None and next_status != task.status:
        changes["position"] = await _next_position(session, next_status)
    for field, value in changes.items():
        setattr(task, field, value)
    await session.commit()
    await session.refresh(task)
    return task


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_task_board)],
)
async def delete_task_board_task(
    task_id: UUID,
    session: SessionDep,
) -> Response:
    await _lock_task_attachments(session, task_id)
    task = await _get_task(session, task_id)
    _enqueue_attachment_cleanup(
        session,
        [(attachment.object_key, attachment.size) for attachment in task.attachments],
    )
    await session.delete(task)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{task_id}/attachments",
    response_model=TaskBoardAttachmentPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_task_board)],
)
async def upload_task_board_attachment(
    task_id: UUID,
    request: Request,
    session: SessionDep,
) -> TaskBoardAttachment:
    filename = _attachment_filename(request)
    raw = await _read_attachment_body(request)
    content_type = request.headers.get("content-type", "application/octet-stream")
    content_type = content_type.split(";", 1)[0].strip().lower()[:160]
    if not content_type:
        content_type = "application/octet-stream"

    # Always acquire the global quota lock before the per-task lock.
    await _lock_board_attachment_quota(session)
    await _lock_task_attachments(session, task_id)
    await _get_task(session, task_id)
    attachment_count = (
        await session.execute(
            select(func.count())
            .select_from(TaskBoardAttachment)
            .where(TaskBoardAttachment.task_id == task_id)
        )
    ).scalar_one()
    if attachment_count >= _MAX_ATTACHMENTS_PER_TASK:
        raise ApiError(
            "conflict",
            "В задаче уже максимальное количество вложений",
            status.HTTP_409_CONFLICT,
        )
    board_attachment_bytes = (
        await session.execute(select(func.coalesce(func.sum(TaskBoardAttachment.size), 0)))
    ).scalar_one()
    # Pending cleanup bytes remain charged until object deletion succeeds.
    pending_cleanup_bytes = (
        await session.execute(select(func.coalesce(func.sum(TaskBoardAttachmentCleanup.size), 0)))
    ).scalar_one()
    if board_attachment_bytes + pending_cleanup_bytes + len(raw) > _BOARD_ATTACHMENT_BYTES_LIMIT:
        raise ApiError(
            "conflict",
            "Хранилище доски заполнено. Удалите ненужные вложения",
            status.HTTP_409_CONFLICT,
        )

    attachment_id = uuid4()
    object_key = _attachment_object_key(task_id, attachment_id, filename)
    attachment = TaskBoardAttachment(
        id=attachment_id,
        task_id=task_id,
        filename=filename,
        content_type=content_type,
        size=len(raw),
        object_key=object_key,
    )
    try:
        stored_object_key = await asyncio.to_thread(
            _store_attachment,
            task_id,
            attachment.id,
            filename,
            content_type,
            raw,
        )
        attachment.object_key = stored_object_key
        object_key = stored_object_key
        session.add(attachment)
        await session.flush()
        await session.refresh(attachment)
        await session.commit()
    except attachment_storage.AttachmentUploadError as exc:
        try:
            _enqueue_attachment_cleanup(session, [(exc.object_key, len(raw))])
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception(
                "task_board_attachment_cleanup_enqueue_failed key=%s",
                exc.object_key,
            )
        raise ApiError(
            "upload_failed",
            "Не удалось сохранить файл",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
    except attachment_storage.AttachmentStorageError as exc:
        await session.rollback()
        raise ApiError(
            "upload_failed",
            "Не удалось сохранить файл",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
    except Exception:
        await session.rollback()
        if object_key is not None:
            try:
                _enqueue_attachment_cleanup(session, [(object_key, len(raw))])
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception("task_board_attachment_cleanup_enqueue_failed key=%s", object_key)
        raise
    return attachment


@router.get(
    "/{task_id}/attachments/{attachment_id}",
    dependencies=[Depends(rate_limit_task_board)],
)
async def download_task_board_attachment(
    task_id: UUID,
    attachment_id: UUID,
    session: SessionDep,
) -> StreamingResponse:
    attachment = await _get_attachment(session, task_id, attachment_id)
    try:
        stream = await asyncio.to_thread(_load_attachment, attachment.object_key)
    except attachment_storage.AttachmentStorageError as exc:
        raise ApiError(
            "upload_failed",
            "Хранилище вложений временно недоступно",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if stream is None:
        raise ApiError(
            "not_found",
            "Файл вложения не найден",
            status.HTTP_404_NOT_FOUND,
        )
    return StreamingResponse(
        stream,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": _content_disposition(attachment.filename),
            "Content-Length": str(attachment.size),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
        background=BackgroundTask(_close_attachment_stream, stream),
    )


@router.delete(
    "/{task_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_task_board)],
)
async def delete_task_board_attachment(
    task_id: UUID,
    attachment_id: UUID,
    session: SessionDep,
) -> Response:
    await _lock_task_attachments(session, task_id)
    attachment = await _get_attachment(session, task_id, attachment_id)
    _enqueue_attachment_cleanup(session, [(attachment.object_key, attachment.size)])
    await session.delete(attachment)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
