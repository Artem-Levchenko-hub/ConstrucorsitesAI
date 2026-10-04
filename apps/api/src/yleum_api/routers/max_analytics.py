"""Signed runtime ingestion and owner-only aggregate access."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.routers.integration_runtime import _runtime_context
from yleum_api.services.max_analytics import aggregate_events, record_event

router = APIRouter(tags=["max-analytics"])


class AnalyticsEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    kind: Literal["open", "action", "event"]


@router.post("/api/runtime/projects/{project_id}/analytics", status_code=204)
async def collect_event(
    project_id: UUID, payload: AnalyticsEventRequest, session: SessionDep, request: Request
) -> Response:
    # Only the immutable core may attest a durable success; direct initData is
    # insufficient to claim that an action committed in an app's database.
    if not request.headers.get("X-Omnia-Integration-Assertion"):
        raise ApiError("unauthorized", "Signed core event required", 401)
    context = await _runtime_context(session, project_id, "", request)
    await record_event(session, project_id, context.max_user_id, payload.event_id, payload.kind)
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/max/analytics")
async def owner_analytics(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    response: Response,
    days: int = Query(default=30, ge=1, le=90),
) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "Project not found", 404)
    if project.template != "max_miniapp":
        raise ApiError("max_project_required", "MAX project required", 409)
    response.headers["Cache-Control"] = "private, no-store"
    return await aggregate_events(session, project_id, days)
