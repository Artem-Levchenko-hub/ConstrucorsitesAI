from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.config import (
    get_settings,
)
from omnia_api.core.db import get_engine
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.ratelimit import rate_limit_prompt
from omnia_api.core.redis import (
    publish_event,
    request_generation_cancel,
)
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.schemas.message import (
    ClientErrorReport,
    GenerationRunPublic,
    MessagePublic,
    PromptRequest,
    PromptResponse,
)
from omnia_api.services import (
    app_errors,
)
from omnia_api.services.generation.acceptance import PromptAcceptance
from omnia_api.services.generation.supervisor import (
    _generation_cancel_protocol,
)
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    apply_cancelled_generation_locked,
)

router = APIRouter(prefix="/api/projects", tags=["messages"])


async def _ensure_owner(session: SessionDep, project_id: UUID, user_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


@router.post(
    "/{project_id}/client-error",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def report_client_error(
    project_id: UUID,
    payload: ClientErrorReport,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Response:
    """Surface an uncaught JS error from the live preview as a chat card.

    The inspector (inside the previewed page) forwards uncaught exceptions /
    unhandled rejections to the workspace shell, which posts them here. We attach
    the card to the latest *finalised* assistant message so it persists across a
    reload, deduping repeats (the same broken page re-fires on every load).

    Fail-soft + conservative (R-10): gated by ``use_error_cards``; no assistant
    message yet → nothing to attach to → 204; a duplicate → 204. Owner-scoped via
    ``_ensure_owner`` (404 for a foreign/unknown project). The public ``/p/<slug>``
    has no workspace parent, so it never reaches this endpoint.
    """
    await _ensure_owner(session, project_id, current_user.id)
    if not get_settings().use_error_cards:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    res = await session.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .where(Message.role == "assistant")
        .where(Message.tokens_out.is_not(None))
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    msg = res.scalar_one_or_none()
    if msg is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    title, file = app_errors.client_card_signature(payload.message, payload.source, payload.line)
    if app_errors.has_client_card(msg.content or "", title, file):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    detail = app_errors.client_card_detail(
        payload.message, payload.stack, payload.route, payload.crumbs
    )

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    await app_errors.publish(
        factory,
        project_id,
        msg.id,
        category="client",
        title=title,
        detail=detail,
        file=file,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/prompt",
    response_model=PromptResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_prompt)],
)
async def post_prompt(
    project_id: UUID,
    payload: PromptRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> PromptResponse:
    project = await _ensure_owner(session, project_id, current_user.id)
    return await PromptAcceptance(
        project_id=project_id,
        payload=payload,
        session=session,
        current_user=current_user,
        project=project,
    ).accept()


@router.get(
    "/{project_id}/generation",
    response_model=GenerationRunPublic | None,
)
async def get_latest_generation(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> GenerationRun | None:
    """Return the durable latest run so reloads restore real lifecycle state."""

    await _ensure_owner(session, project_id, current_user.id)
    return (
        await session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.user_id == current_user.id,
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.post(
    "/{project_id}/generation/cancel",
    response_model=GenerationRunPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_active_generation(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> GenerationRun:
    """Request cancellation of the project's one durable active run."""

    await _ensure_owner(session, project_id, current_user.id)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    run = (
        await session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.user_id == current_user.id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise ApiError(
            "conflict",
            "no active generation",
            status.HTTP_409_CONFLICT,
        )
    cancel_protocol = _generation_cancel_protocol(run.status)
    if cancel_protocol == "terminal_without_signal":
        await apply_cancelled_generation_locked(session, run)
        await session.commit()
        await session.refresh(run)
        try:
            await publish_event(
                project_id,
                "generation.cancelled",
                {
                    "run_id": str(run.id),
                    "message_id": (
                        str(run.assistant_message_id) if run.assistant_message_id else None
                    ),
                },
            )
        except Exception:
            pass
        return run
    if cancel_protocol == "signal_running":
        # Publish the process-independent signal before committing the status:
        # if Redis is unavailable, the request fails and the DB does not get
        # stuck forever in an unfulfillable cancel_requested state.
        await request_generation_cancel(run.id)
        run.status = "cancel_requested"
        await session.commit()
        await session.refresh(run)
    try:
        await publish_event(
            project_id,
            "generation.cancel_requested",
            {
                "run_id": str(run.id),
                "message_id": (str(run.assistant_message_id) if run.assistant_message_id else None),
            },
        )
    except Exception:
        pass
    return run


@router.get("/{project_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Sequence[MessagePublic]:
    await _ensure_owner(session, project_id, current_user.id)
    res = await session.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    rows = list(reversed(list(res.scalars().all())))
    assistant_ids = [row.id for row in rows if row.role == "assistant"]
    runs_by_message: dict[UUID, GenerationRun] = {}
    if assistant_ids:
        run_rows = await session.execute(
            select(GenerationRun)
            .where(GenerationRun.assistant_message_id.in_(assistant_ids))
            .order_by(GenerationRun.created_at.desc())
        )
        # A message should have exactly one run, but newest-wins keeps history
        # deterministic for any legacy duplicate produced before single-flight.
        for generation_run in run_rows.scalars():
            if generation_run.assistant_message_id is not None:
                runs_by_message.setdefault(generation_run.assistant_message_id, generation_run)

    payload: list[MessagePublic] = []
    for row in rows:
        message_run = runs_by_message.get(row.id)
        payload.append(
            MessagePublic.model_validate(row).model_copy(
                update={
                    "generation_started_at": (
                        message_run.started_at if message_run is not None else None
                    ),
                    "generation_finished_at": (
                        message_run.finished_at if message_run is not None else None
                    ),
                    "generation_status": (message_run.status if message_run is not None else None),
                }
            )
        )
    return payload
