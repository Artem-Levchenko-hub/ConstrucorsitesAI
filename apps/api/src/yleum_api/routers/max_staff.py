"""Browser API for the project owner and the separate staff workspace."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.schemas.max_staff import (
    StaffAccess,
    StaffHistoryEntry,
    StaffInviteAccept,
    StaffInviteAccepted,
    StaffInviteCreate,
    StaffInviteIssued,
    StaffMember,
    StaffPermissions,
    StaffProject,
    StaffRecord,
    StaffRecordQuery,
    StaffRecords,
    StaffRecordUpdate,
    StaffResource,
    StaffSettings,
    StaffTeam,
)
from yleum_api.services import max_staff as work
from yleum_api.services import max_staff_team as team


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def require_staff_enabled() -> None:
    if not get_settings().max_staff_enabled:
        raise ApiError("not_found", "Раздел недоступен", 404)


router = APIRouter(prefix="/api", tags=["max-staff"], dependencies=[Depends(private_response)])
staff_only = [Depends(require_staff_enabled)]
TEAM = "/projects/{project_id}/max/team"
STAFF = "/projects/{project_id}/max/staff"


@router.get("/max/staff/projects", dependencies=staff_only)
async def staff_projects(session: SessionDep, user: CurrentUserDep) -> list[StaffProject]:
    return await team.staff_projects(session, user)


@router.post("/max/staff/invitations/accept", dependencies=staff_only)
async def accept_invitation(
    data: StaffInviteAccept,
    session: SessionDep,
    user: CurrentUserDep,
) -> StaffInviteAccepted:
    return StaffInviteAccepted(project_id=await team.accept_invitation(session, user, data.token))


@router.get(TEAM)
async def get_team(project_id: UUID, session: SessionDep, user: CurrentUserDep) -> StaffTeam:
    return await team.get_team(session, project_id, user)


@router.put(f"{TEAM}/settings")
async def put_settings(
    project_id: UUID,
    data: StaffSettings,
    session: SessionDep,
    user: CurrentUserDep,
) -> StaffTeam:
    return await team.configure_team(session, project_id, user, data)


@router.post(f"{TEAM}/invitations", status_code=201, dependencies=staff_only)
async def create_invitation(
    project_id: UUID,
    data: StaffInviteCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> StaffInviteIssued:
    return await team.create_invitation(session, project_id, user, data)


@router.delete(
    f"{TEAM}/invitations/{{invitation_id}}",
    status_code=204,
    response_class=Response,
    dependencies=staff_only,
)
async def revoke_invitation(
    project_id: UUID,
    invitation_id: UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    await team.revoke_invitation(session, project_id, user, invitation_id)


@router.patch(f"{TEAM}/members/{{member_id}}", dependencies=staff_only)
async def change_member(
    project_id: UUID,
    member_id: UUID,
    data: StaffPermissions,
    session: SessionDep,
    user: CurrentUserDep,
) -> StaffMember:
    member = await team.change_member(session, project_id, user, member_id, data.permissions)
    assert member is not None
    return member


@router.delete(
    f"{TEAM}/members/{{member_id}}",
    status_code=204,
    response_class=Response,
    dependencies=staff_only,
)
async def revoke_member(
    project_id: UUID,
    member_id: UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    await team.change_member(session, project_id, user, member_id, None)


@router.get(STAFF)
async def get_access(project_id: UUID, session: SessionDep, user: CurrentUserDep) -> StaffAccess:
    return (await team.get_access(session, project_id, user)).public


@router.get(f"{STAFF}/records")
async def list_records(
    project_id: UUID,
    session: SessionDep,
    user: CurrentUserDep,
    query: Annotated[StaffRecordQuery, Query()],
) -> StaffRecords:
    return await work.list_records(session, project_id, user, query)


@router.patch(f"{STAFF}/records/{{record_id}}")
async def update_record(
    project_id: UUID,
    record_id: UUID,
    data: StaffRecordUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> StaffRecord:
    return await work.update_record(session, project_id, user, record_id, data)


@router.get(f"{STAFF}/records/{{record_id}}/history")
async def history(
    project_id: UUID,
    record_id: UUID,
    resource: StaffResource,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[StaffHistoryEntry]:
    return await work.history(session, project_id, user, resource, record_id)
