"""Project-local team grants. Generated apps never receive these credentials."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.max_staff import MaxStaffInvitation, MaxStaffMember, MaxStaffSettings
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.schemas.max_management import ResourceId
from yleum_api.schemas.max_staff import (
    StaffAccess,
    StaffInvitation,
    StaffInviteCreate,
    StaffInviteIssued,
    StaffMember,
    StaffProject,
    StaffResource,
    StaffSettings,
    StaffTeam,
)

PERMISSION = {StaffResource.orders: 1, StaffResource.leads: 2}
DEFAULT_PREP_MINUTES = 7
TEAM_LIMIT = 50
INVITATION_TTL = timedelta(hours=48)


def _permissions(mask: int) -> list[StaffResource]:
    return [resource for resource, bit in PERMISSION.items() if mask & bit]


def _mask(permissions: list[StaffResource]) -> int:
    return sum(PERMISSION[resource] for resource in permissions)


def _missing() -> ApiError:
    return ApiError("not_found", "Проект не найден", 404)


def _denied() -> ApiError:
    return ApiError("staff_access_denied", "У вас нет доступа к этой очереди", 403)


def _invalid_invite() -> ApiError:
    return ApiError(
        "staff_invite_invalid",
        "Приглашение недействительно. Попросите владельца прислать новую ссылку.",
        409,
    )


async def _project(
    session: AsyncSession,
    project_id: UUID,
    *,
    lock: bool = False,
    read: bool = False,
) -> Project:
    query = select(Project).where(Project.id == project_id, Project.template == "max_miniapp")
    if lock:
        query = query.with_for_update(read=read)
    project = await session.scalar(query.execution_options(populate_existing=True))
    if project is None:
        raise _missing()
    return project


async def _owned(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    *,
    lock: bool = False,
) -> Project:
    project = await _project(session, project_id, lock=lock)
    if user.is_anon or project.owner_id != user.id:
        raise _missing()
    return project


async def _settings(session: AsyncSession, project: Project) -> MaxStaffSettings | None:
    rows = await session.scalars(
        select(MaxStaffSettings)
        .where(
            MaxStaffSettings.project_id == project.id,
            MaxStaffSettings.owner_id == project.owner_id,
        )
        .execution_options(populate_existing=True)
    )
    return rows.one_or_none()


def cutover(settings: MaxStaffSettings | None, resource: StaffResource) -> datetime | None:
    if settings is None:
        return None
    if resource == StaffResource.orders:
        return settings.orders_enabled_at
    return settings.leads_enabled_at


def _public_settings(settings: MaxStaffSettings | None) -> StaffSettings:
    if settings is None:
        return StaffSettings(
            leads_enabled=False, orders_mode=None, prep_minutes=DEFAULT_PREP_MINUTES
        )
    mode = settings.orders_mode
    return StaffSettings(
        leads_enabled=settings.leads_enabled_at is not None,
        orders_mode=mode if mode in ("pickup", "yookassa") else None,
        prep_minutes=settings.prep_minutes,
    )


def _public_member(member: MaxStaffMember, user: User) -> StaffMember:
    assert user.email is not None
    return StaffMember(
        user_id=member.user_id,
        email=user.email,
        name=member.name,
        permissions=_permissions(member.permissions),
    )


async def _team(session: AsyncSession, project: Project) -> StaffTeam:
    members = await session.execute(
        select(MaxStaffMember, User)
        .join(User, User.id == MaxStaffMember.user_id)
        .where(
            MaxStaffMember.project_id == project.id,
            MaxStaffMember.owner_id == project.owner_id,
            User.email.is_not(None),
        )
        .order_by(MaxStaffMember.name, MaxStaffMember.user_id)
    )
    invites = await session.scalars(
        select(MaxStaffInvitation)
        .where(
            MaxStaffInvitation.project_id == project.id,
            MaxStaffInvitation.owner_id == project.owner_id,
            MaxStaffInvitation.expires_at > datetime.now(UTC),
            MaxStaffInvitation.accepted_user_id.is_(None),
        )
        .order_by(MaxStaffInvitation.expires_at, MaxStaffInvitation.id)
    )
    return StaffTeam(
        members=[_public_member(member, user) for member, user in members],
        invitations=[
            StaffInvitation(
                id=invite.id,
                email=invite.email,
                name=invite.name,
                permissions=_permissions(invite.permissions),
                expires_at=invite.expires_at,
            )
            for invite in invites
        ],
        settings=_public_settings(await _settings(session, project)),
    )


async def get_team(session: AsyncSession, project_id: UUID, user: User) -> StaffTeam:
    return await _team(session, await _owned(session, project_id, user))


def initial_team_settings(project: Project, desired: StaffSettings) -> MaxStaffSettings:
    now = datetime.now(UTC)
    return MaxStaffSettings(
        project_id=project.id,
        owner_id=project.owner_id,
        leads_enabled_at=now if desired.leads_enabled else None,
        orders_enabled_at=now if desired.orders_mode is not None else None,
        orders_mode=desired.orders_mode,
        prep_minutes=desired.prep_minutes,
    )


async def configure_team(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    desired: StaffSettings,
) -> StaffTeam:
    project = await _owned(session, project_id, user, lock=True)
    settings = await session.get(MaxStaffSettings, project_id, populate_existing=True)
    newly_enabled = {
        ResourceId(resource)
        for resource, enabled in (
            (StaffResource.leads, desired.leads_enabled),
            (StaffResource.orders, desired.orders_mode is not None),
        )
        if enabled and cutover(settings, resource) is None
    }
    if newly_enabled:
        from yleum_api.services.max_staff_source import confirm_staff_source

        await confirm_staff_source(
            session,
            project,
            newly_enabled,
            linked_checkout=ResourceId.orders in newly_enabled
            and desired.orders_mode == "yookassa",
        )
    if settings is None:
        session.add(initial_team_settings(project, desired))
        await session.commit()
        return await _team(session, project)
    if settings.owner_id != project.owner_id:
        raise _denied()
    if (settings.leads_enabled_at is not None and not desired.leads_enabled) or (
        settings.orders_mode is not None and settings.orders_mode != desired.orders_mode
    ):
        raise ApiError(
            "staff_queue_already_enabled",
            "Очередь уже запущена. Можно изменить время приготовления.",
            409,
        )
    now = datetime.now(UTC)
    if desired.leads_enabled and settings.leads_enabled_at is None:
        settings.leads_enabled_at = now
    if desired.orders_mode is not None and settings.orders_mode is None:
        settings.orders_mode = desired.orders_mode
        settings.orders_enabled_at = now
    settings.prep_minutes = desired.prep_minutes
    await session.commit()
    return await _team(session, project)


async def _current_member(
    session: AsyncSession,
    project: Project,
    user_id: UUID,
) -> MaxStaffMember | None:
    rows = await session.scalars(
        select(MaxStaffMember)
        .where(
            MaxStaffMember.project_id == project.id,
            MaxStaffMember.user_id == user_id,
            MaxStaffMember.owner_id == project.owner_id,
        )
        .execution_options(populate_existing=True)
    )
    return rows.one_or_none()


async def create_invitation(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    data: StaffInviteCreate,
) -> StaffInviteIssued:
    project = await _owned(session, project_id, user, lock=True)
    if user.email is not None and user.email.casefold() == data.email.casefold():
        raise ApiError("staff_member_exists", "У владельца уже есть доступ к проекту", 409)
    existing = await session.scalar(
        select(MaxStaffMember.user_id)
        .join(User, User.id == MaxStaffMember.user_id)
        .where(
            MaxStaffMember.project_id == project.id,
            MaxStaffMember.owner_id == project.owner_id,
            User.email == data.email,
        )
    )
    if existing is not None:
        raise ApiError(
            "staff_member_exists", "У сотрудника уже есть доступ. Измените его в команде.", 409
        )
    now = datetime.now(UTC)
    await session.execute(
        delete(MaxStaffInvitation).where(
            MaxStaffInvitation.project_id == project.id,
            (MaxStaffInvitation.expires_at <= now) | (MaxStaffInvitation.email == data.email),
        )
    )
    members = await session.scalar(
        select(func.count())
        .select_from(MaxStaffMember)
        .where(
            MaxStaffMember.project_id == project.id,
            MaxStaffMember.owner_id == project.owner_id,
        )
    )
    invites = await session.scalar(
        select(func.count())
        .select_from(MaxStaffInvitation)
        .where(
            MaxStaffInvitation.project_id == project.id,
            MaxStaffInvitation.owner_id == project.owner_id,
            MaxStaffInvitation.accepted_user_id.is_(None),
        )
    )
    if (members or 0) + (invites or 0) >= TEAM_LIMIT:
        raise ApiError("staff_team_limit", "Достигнут лимит участников и приглашений", 409)
    token = token_urlsafe(32)
    expires = now + INVITATION_TTL
    session.add(
        MaxStaffInvitation(
            project_id=project.id,
            owner_id=project.owner_id,
            digest=sha256(token.encode()).hexdigest(),
            name=data.name,
            email=data.email,
            permissions=_mask(data.permissions),
            expires_at=expires,
        )
    )
    await session.commit()
    return StaffInviteIssued(token=token, expires_at=expires)


async def revoke_invitation(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    invitation_id: UUID,
) -> None:
    project = await _owned(session, project_id, user, lock=True)
    await session.execute(
        delete(MaxStaffInvitation).where(
            MaxStaffInvitation.project_id == project.id,
            MaxStaffInvitation.owner_id == project.owner_id,
            MaxStaffInvitation.id == invitation_id,
        )
    )
    await session.commit()


def _verified_email(user: User) -> str:
    if (
        user.is_anon
        or user.email is None
        or user.email_verified_at is None
        or user.status != "active"
    ):
        raise ApiError(
            "staff_verification_required", "Подтвердите email в своём аккаунте Yleum", 403
        )
    return user.email


async def accept_invitation(session: AsyncSession, user: User, token: str) -> UUID:
    email = _verified_email(user)
    digest = sha256(token.encode()).hexdigest()
    project_id = await session.scalar(
        select(MaxStaffInvitation.project_id).where(MaxStaffInvitation.digest == digest)
    )
    if project_id is None:
        raise _invalid_invite()
    try:
        project = await _project(session, project_id, lock=True)
    except ApiError:
        raise _invalid_invite() from None
    invite = await session.scalar(
        select(MaxStaffInvitation)
        .where(
            MaxStaffInvitation.project_id == project.id,
            MaxStaffInvitation.digest == digest,
            MaxStaffInvitation.owner_id == project.owner_id,
            MaxStaffInvitation.expires_at > datetime.now(UTC),
        )
        .execution_options(populate_existing=True)
    )
    if invite is None or project.owner_id == user.id:
        raise _invalid_invite()
    if invite.accepted_user_id is not None:
        if invite.accepted_user_id == user.id and await _current_member(session, project, user.id):
            return project_id
        raise _invalid_invite()
    if email.casefold() != invite.email.casefold():
        raise ApiError(
            "staff_invite_account_mismatch",
            "Войдите с email, на который выдано приглашение",
            403,
        )
    member = await session.get(MaxStaffMember, (project.id, user.id), populate_existing=True)
    if member is None:
        session.add(
            MaxStaffMember(
                project_id=project.id,
                user_id=user.id,
                owner_id=project.owner_id,
                name=invite.name,
                permissions=invite.permissions,
            )
        )
    elif member.owner_id != project.owner_id:
        member.owner_id = project.owner_id
        member.name = invite.name
        member.permissions = invite.permissions
    # Replaying a lost response confirms the same membership. The token can
    # never assign permissions again, including after the owner revokes access.
    invite.accepted_user_id = user.id
    invite.email = ""
    await session.commit()
    return project_id


async def change_member(
    session: AsyncSession,
    project_id: UUID,
    owner: User,
    member_id: UUID,
    permissions: list[StaffResource] | None,
) -> StaffMember | None:
    project = await _owned(session, project_id, owner, lock=True)
    member = await _current_member(session, project, member_id)
    if member is None:
        raise ApiError("not_found", "Сотрудник не найден", 404)
    user = await session.get(User, member_id)
    result = None
    if permissions is None:
        await session.delete(member)
        if user is not None and user.email is not None:
            await session.execute(
                delete(MaxStaffInvitation).where(
                    MaxStaffInvitation.project_id == project.id,
                    MaxStaffInvitation.owner_id == project.owner_id,
                    MaxStaffInvitation.email == user.email.lower(),
                )
            )
    else:
        if user is None or user.email is None:
            raise ApiError("not_found", "Сотрудник не найден", 404)
        member.permissions = _mask(permissions)
        result = _public_member(member, user)
    await session.commit()
    return result


@dataclass(frozen=True)
class Access:
    project: Project
    settings: MaxStaffSettings | None
    public: StaffAccess
    actor_name: str


async def get_access(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    resource: StaffResource | None = None,
    *,
    lock: bool = False,
) -> Access:
    try:
        project = await _project(session, project_id, lock=lock, read=True)
    except ApiError:
        raise _denied() from None
    is_owner = project.owner_id == user.id and not user.is_anon
    settings = await _settings(session, project)
    if is_owner:
        granted, actor_name = list(StaffResource), "Владелец"
    else:
        _verified_email(user)
        member = await _current_member(session, project, user.id)
        if member is None:
            raise _denied()
        granted, actor_name = _permissions(member.permissions), member.name
    permissions = [entry for entry in granted if cutover(settings, entry) is not None]
    if resource is not None and resource not in permissions:
        raise _denied()
    return Access(
        project,
        settings,
        StaffAccess(
            project_id=project.id,
            project_name=project.name,
            actor_id=user.id,
            is_owner=is_owner,
            permissions=permissions,
        ),
        actor_name,
    )


async def staff_projects(session: AsyncSession, user: User) -> list[StaffProject]:
    _verified_email(user)
    rows = await session.execute(
        select(Project, MaxStaffMember, MaxStaffSettings)
        .join(MaxStaffMember, MaxStaffMember.project_id == Project.id)
        .outerjoin(
            MaxStaffSettings,
            (
                (MaxStaffSettings.project_id == Project.id)
                & (MaxStaffSettings.owner_id == Project.owner_id)
            ),
        )
        .where(
            MaxStaffMember.user_id == user.id,
            MaxStaffMember.owner_id == Project.owner_id,
            Project.template == "max_miniapp",
        )
        .order_by(Project.name, Project.id)
    )
    return [
        StaffProject(
            project_id=project.id,
            project_name=project.name,
            permissions=[
                resource
                for resource in _permissions(member.permissions)
                if cutover(settings, resource)
            ],
        )
        for project, member, settings in rows
    ]


async def assignee_name(
    session: AsyncSession,
    access: Access,
    assignee_id: UUID | None,
    resource: StaffResource,
) -> str | None:
    if assignee_id is None:
        return None
    if assignee_id == access.project.owner_id:
        return "Владелец"
    assignee = await _current_member(session, access.project, assignee_id)
    user = await session.get(User, assignee_id)
    if (
        assignee is None
        or not assignee.permissions & PERMISSION[resource]
        or user is None
        or user.status != "active"
        or user.email_verified_at is None
    ):
        raise ApiError("staff_assignee_unavailable", "У сотрудника нет доступа к этой очереди", 409)
    return assignee.name


async def order_checkout_settings(session: AsyncSession, project_id: UUID) -> MaxStaffSettings:
    project = await _project(session, project_id)
    settings = await _settings(session, project)
    if settings is None or settings.orders_mode != "yookassa" or settings.orders_enabled_at is None:
        raise ApiError(
            "staff_checkout_not_enabled",
            "Владелец ещё не включил оплату заказов через ЮKassa",
            409,
        )
    return settings


async def legacy_history_cutoff(
    session: AsyncSession,
    project_id: UUID,
    user: User,
    resource: StaffResource,
    *,
    write: bool = False,
) -> datetime | None:
    project = await _project(session, project_id, lock=write, read=True)
    if user.is_anon or project.owner_id != user.id:
        raise _missing()
    return cutover(await _settings(session, project), resource)


async def guard_staff_publication(
    session: AsyncSession,
    project: Project,
    commit_sha: str,
) -> None:
    from yleum_api.services.max_staff_source import require_compatible_staff_release

    # Serialize against first queue activation. A pending deployment is also
    # rejected at activation until its active source can be checked.
    locked = await _project(session, project.id, lock=True, read=True)
    settings = await _settings(session, locked)
    resources = {ResourceId(resource) for resource in StaffResource if cutover(settings, resource)}
    if resources:
        await require_compatible_staff_release(
            locked,
            commit_sha,
            resources,
            linked_checkout=bool(settings and settings.orders_mode == "yookassa"),
        )
