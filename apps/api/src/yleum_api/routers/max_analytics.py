"""Signed runtime ingestion and owner-only aggregate access."""

from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, model_validator

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.max_analytics import EVENT_TYPE_KINDS, AnalyticsEventType, AnalyticsKind
from yleum_api.models.project import Project
from yleum_api.routers.integration_runtime import _runtime_context
from yleum_api.services.max_analytics import aggregate_events, list_events, record_event

router = APIRouter(tags=["max-analytics"])


class AnalyticsEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    kind: AnalyticsKind
    event_type: AnalyticsEventType | None = None

    @model_validator(mode="after")
    def validate_event_kind(self) -> Self:
        if self.event_type is not None and self.kind not in EVENT_TYPE_KINDS[self.event_type]:
            raise ValueError("Event type does not match kind")
        return self


@router.post("/api/runtime/projects/{project_id}/analytics", status_code=204)
async def collect_event(
    project_id: UUID, payload: AnalyticsEventRequest, session: SessionDep, request: Request
) -> Response:
    core = request.headers.get("X-Omnia-Integration-Assertion")
    business = request.headers.get("X-Yleum-Business-Assertion")
    if bool(core) == bool(business):
        raise ApiError("unauthorized", "Signed core event required", 401)
    if business and (payload.kind != "action" or payload.event_type not in {
        "order_created", "lead_created", "booking_created",
    }):
        raise ApiError("unauthorized", "Signed business receipt required", 401)
    context = await _runtime_context(
        session, project_id, "", request, **({"business_analytics": True} if business else {}),
    )
    await record_event(
        session, project_id, context.max_user_id, payload.event_id, payload.kind, payload.event_type
    )
    return Response(status_code=204)


async def _require_max_owner(project_id: UUID, session: SessionDep, owner_id: UUID) -> None:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "Project not found", 404)
    if project.template != "max_miniapp":
        raise ApiError("max_project_required", "MAX project required", 409)


@router.get("/api/projects/{project_id}/max/analytics")
async def owner_analytics(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    response: Response,
    days: int = Query(default=30, ge=1, le=90),
) -> dict[str, Any]:
    await _require_max_owner(project_id, session, current_user.id)
    response.headers["Cache-Control"] = "private, no-store"
    return await aggregate_events(session, project_id, days)


@router.get("/api/projects/{project_id}/max/analytics/events")
async def owner_events(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    response: Response,
    days: int = Query(default=30, ge=1, le=90),
    kind: AnalyticsKind | None = None,
    event_type: AnalyticsEventType | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
) -> dict[str, Any]:
    await _require_max_owner(project_id, session, current_user.id)
    response.headers["Cache-Control"] = "private, no-store"
    return await list_events(
        session, project_id, days, kind=kind, event_type=event_type, limit=limit, cursor=cursor
    )
