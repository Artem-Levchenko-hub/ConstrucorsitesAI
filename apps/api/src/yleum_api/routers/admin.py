from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from yleum_api.core.admin import is_admin_user
from yleum_api.core.deps import AdminUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.admin_audit import AdminAuditEvent
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.schemas.admin import (
    AdminAuditEventPublic,
    AdminUserPublic,
    AdminUserUpdate,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _public_user(user: User, wallet: Wallet | None) -> AdminUserPublic:
    return AdminUserPublic(
        id=user.id,
        email=str(user.email),
        role=user.role,
        is_admin=is_admin_user(user),
        status=user.status,
        email_verified_at=user.email_verified_at,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        wallet_balance_rub=str(wallet.balance_rub if wallet else 0),
    )


async def _user_row(
    session: SessionDep,
    user_id: UUID,
) -> tuple[User, Wallet | None]:
    row = (
        await session.execute(
            select(User, Wallet)
            .outerjoin(Wallet, Wallet.user_id == User.id)
            .where(User.id == user_id)
        )
    ).one_or_none()
    if row is None or row[0].is_anon or row[0].email is None:
        raise ApiError("not_found", "account not found", status.HTTP_404_NOT_FOUND)
    return row[0], row[1]


@router.get("/access")
async def get_admin_access(admin_user: AdminUserDep) -> dict[str, object]:
    return {
        "is_admin": True,
        "role": admin_user.role,
        "email": admin_user.email,
    }


@router.get("/users", response_model=list[AdminUserPublic])
async def list_users(
    admin_user: AdminUserDep,
    session: SessionDep,
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[AdminUserPublic]:
    del admin_user
    statement = (
        select(User, Wallet)
        .outerjoin(Wallet, Wallet.user_id == User.id)
        .where(User.is_anon.is_(False), User.email.is_not(None))
        .order_by(User.role.desc(), User.created_at.desc())
        .limit(limit)
    )
    if query.strip():
        statement = statement.where(User.email.ilike(f"%{query.strip()}%"))
    rows = (await session.execute(statement)).all()
    return [_public_user(user, wallet) for user, wallet in rows]


@router.patch("/users/{user_id}", response_model=AdminUserPublic)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    admin_user: AdminUserDep,
    session: SessionDep,
) -> AdminUserPublic:
    target, wallet = await _user_row(session, user_id)
    before = {
        "role": target.role,
        "status": target.status,
        "email_verified": target.email_verified_at is not None,
    }
    now = datetime.now(UTC)

    if payload.role is not None and payload.role != target.role:
        if target.id == admin_user.id and payload.role != "admin":
            raise ApiError(
                "conflict",
                "you cannot remove your own administrator role",
                status.HTTP_409_CONFLICT,
            )
        if target.role == "admin" and payload.role != "admin":
            admin_count = (
                await session.execute(
                    select(func.count()).select_from(User).where(User.role == "admin")
                )
            ).scalar_one()
            if admin_count <= 1:
                raise ApiError(
                    "conflict",
                    "at least one persisted administrator is required",
                    status.HTTP_409_CONFLICT,
                )
        target.role = payload.role

    if payload.email_verified is not None:
        target.email_verified_at = now if payload.email_verified else None

    if payload.status is not None and payload.status != target.status:
        if target.id == admin_user.id and payload.status != "active":
            raise ApiError(
                "conflict",
                "you cannot suspend your own account",
                status.HTTP_409_CONFLICT,
            )
        target.status = payload.status
        target.session_version += 1

    after = {
        "role": target.role,
        "status": target.status,
        "email_verified": target.email_verified_at is not None,
    }
    session.add(
        AdminAuditEvent(
            actor_user_id=admin_user.id,
            target_user_id=target.id,
            action="account.update",
            details={
                "before": before,
                "after": after,
                "note": payload.note,
            },
        )
    )
    await session.commit()
    await session.refresh(target)
    return _public_user(target, wallet)


@router.get("/audit", response_model=list[AdminAuditEventPublic])
async def list_audit_events(
    admin_user: AdminUserDep,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AdminAuditEventPublic]:
    del admin_user
    actor = aliased(User)
    target = aliased(User)
    rows = (
        await session.execute(
            select(AdminAuditEvent, actor.email, target.email)
            .join(actor, actor.id == AdminAuditEvent.actor_user_id)
            .join(target, target.id == AdminAuditEvent.target_user_id)
            .order_by(AdminAuditEvent.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        AdminAuditEventPublic(
            id=event.id,
            actor_email=str(actor_email),
            target_email=str(target_email),
            action=event.action,
            details=event.details,
            created_at=event.created_at,
        )
        for event, actor_email, target_email in rows
    ]
