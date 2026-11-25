from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.core.ratelimit import rate_limit_studio_activity
from yleum_api.models.project import Project
from yleum_api.models.studio_activity import StudioActivity

router = APIRouter(prefix="/api/studio", tags=["studio-activity"])
_CABINET = UUID(int=0)


class Receipt(BaseModel):
    project_id: UUID | None = None
    kind: Literal["open", "active", "error"]
    release: str = Field(pattern=r"^(?:[0-9a-f]{7,40}|unknown)$")


@router.post("/activity", status_code=204, dependencies=[Depends(rate_limit_studio_activity)])
async def studio_activity(
    receipt: Receipt,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Response:
    if current_user.is_anon:
        return Response(status_code=204)
    if receipt.project_id is not None:
        owner = await session.scalar(
            select(Project.owner_id).where(Project.id == receipt.project_id)
        )
        if owner != current_user.id:
            raise ApiError("not_found", "Studio project not found", 404)

    minute = datetime.now(UTC).replace(second=0, microsecond=0)
    activity = StudioActivity.__table__
    statement = insert(StudioActivity).values(
        actor_id=current_user.id,
        workspace_id=receipt.project_id or _CABINET,
        minute=minute,
        release=receipt.release,
        active=receipt.kind == "active",
        error=receipt.kind == "error",
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                activity.c.actor_id,
                activity.c.workspace_id,
                activity.c.minute,
                activity.c.release,
            ],
            set_={
                "active": activity.c.active | statement.excluded.active,
                "error": activity.c.error | statement.excluded.error,
            },
            where=(~activity.c.active & statement.excluded.active)
            | (~activity.c.error & statement.excluded.error),
        )
    )
    await session.commit()
    return Response(status_code=204)
