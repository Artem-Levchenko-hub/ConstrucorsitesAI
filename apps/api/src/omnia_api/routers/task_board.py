from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import case, func, select, text

from omnia_api.core.deps import SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.ratelimit import rate_limit_task_board
from omnia_api.models.task_board import TaskBoardTask
from omnia_api.schemas.task_board import (
    TaskBoardStatus,
    TaskBoardTaskCreate,
    TaskBoardTaskPublic,
    TaskBoardTaskUpdate,
)

router = APIRouter(prefix="/api/task-board/tasks", tags=["task-board"])

_BOARD_TASK_LIMIT = 500
_BOARD_LOCK_KEY = "omnia-task-board"

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
    task = await _get_task(session, task_id)
    await session.delete(task)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
