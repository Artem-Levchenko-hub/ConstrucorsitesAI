from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from omnia_api.core.admin import is_admin_user
from omnia_api.core.config import MAX_DEMO_GENERATION_LIMIT, get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.account import (
    BusinessEntitlement,
    BusinessMember,
    BusinessProfile,
)
from omnia_api.models.billing import BillingPlan, Subscription
from omnia_api.models.user import User
from omnia_api.schemas.max_account import (
    BusinessDecision,
    BusinessProfileCreate,
    BusinessProfilePublic,
    BusinessReviewPublic,
    MaxAccessPublic,
    MaxDemoEntitlementPublic,
)
from omnia_api.services.billing_accounts import (
    promote_personal_account_to_business,
    resolve_billing_account,
)
from omnia_api.services.max_access import get_user_business

router = APIRouter(prefix="/api/max/account", tags=["max-account"])


def _valid_inn(value: str) -> bool:
    if len(value) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(int(value[i]) * weights[i] for i in range(9)) % 11 % 10 == int(value[9])
    if len(value) == 12:
        first = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        second = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(int(value[i]) * first[i] for i in range(10)) % 11 % 10 == int(value[10]) and sum(
            int(value[i]) * second[i] for i in range(11)
        ) % 11 % 10 == int(value[11])
    return False


def _valid_ogrn(value: str, kind: str) -> bool:
    if kind == "legal_entity":
        return len(value) == 13 and int(value[:12]) % 11 % 10 == int(value[-1])
    return len(value) == 15 and int(value[:14]) % 13 % 10 == int(value[-1])


async def _verify_self_employed(inn: str) -> tuple[str, str | None, dict[str, object]]:
    payload = {"inn": inn, "requestDate": date.today().isoformat()}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                "https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        return (
            "pending",
            "ФНС временно недоступна. Проверка будет повторена администратором",
            {},
        )
    is_registered = body.get("status") is True
    return (
        "verified" if is_registered else "rejected",
        str(body.get("message") or "")[:500] or None,
        {"status": is_registered, "request_date": payload["requestDate"]},
    )


def _is_admin(user: User) -> bool:
    return is_admin_user(user)


@router.get("/access", response_model=MaxAccessPublic)
async def get_access(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> MaxAccessPublic:
    settings = get_settings()
    business = await get_user_business(session, current_user.id)
    registered = not current_user.is_anon and current_user.email is not None
    reason = None if registered else "registration_required"
    if current_user.email_verified_at is None:
        launch_reason = "email_verification_required"
    elif business is None:
        launch_reason = "business_profile_required"
    elif business.status != "verified":
        launch_reason = "business_verification_required"
    else:
        launch_reason = None
    paid_launch = False
    if registered:
        billing_account = await resolve_billing_account(session, current_user.id)
        plan = (
            await session.execute(
                select(BillingPlan)
                .join(Subscription, Subscription.plan_id == BillingPlan.id)
                .where(
                    Subscription.billing_account_id == billing_account.id,
                    Subscription.status.in_(("trialing", "active", "past_due", "paused")),
                )
            )
        ).scalar_one()
        slots = plan.entitlements.get("static_publish_slots")
        paid_launch = isinstance(slots, int) and slots > 0
        if launch_reason is None and not paid_launch:
            launch_reason = "subscription_entitlement_required"
    demo_used = max(0, current_user.max_demo_generations_used or 0)
    demo_remaining = max(0, MAX_DEMO_GENERATION_LIMIT - demo_used)
    return MaxAccessPublic(
        email_verified=current_user.email_verified_at is not None,
        email_delivery_configured=bool(settings.smtp_host),
        business=BusinessProfilePublic.model_validate(business) if business else None,
        can_create_project=registered,
        can_launch=registered and launch_reason is None and paid_launch,
        reason=reason,
        launch_reason=launch_reason,
        demo=MaxDemoEntitlementPublic(
            limit=MAX_DEMO_GENERATION_LIMIT,
            used=demo_used,
            remaining=demo_remaining,
            available=demo_remaining > 0,
        ),
        legal_document_version=settings.legal_document_version,
        payments_configured=bool(
            settings.yookassa_shop_id
            and settings.yookassa_secret_key
            and settings.legal_operator_name
            and settings.legal_operator_inn
        ),
    )


@router.put("/business", response_model=BusinessProfilePublic)
async def put_business(
    payload: BusinessProfileCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> BusinessProfile:
    if current_user.is_anon or current_user.email is None:
        raise ApiError(
            "max_registration_required",
            "Сначала зарегистрируйтесь",
            status.HTTP_403_FORBIDDEN,
        )
    if current_user.email_verified_at is None:
        raise ApiError(
            "email_verification_required",
            "Сначала подтвердите email",
            status.HTTP_403_FORBIDDEN,
        )
    if not _valid_inn(payload.inn):
        raise ApiError(
            "inn_invalid", "Контрольная сумма ИНН неверна", status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    expected_inn_length = 10 if payload.kind == "legal_entity" else 12
    if len(payload.inn) != expected_inn_length:
        raise ApiError(
            "inn_kind_mismatch",
            "Для выбранного типа указан ИНН неверной длины",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if payload.kind != "self_employed":
        if not payload.ogrn or not _valid_ogrn(payload.ogrn, payload.kind):
            raise ApiError(
                "ogrn_invalid",
                "Контрольная сумма ОГРН или ОГРНИП неверна",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

    existing = await get_user_business(session, current_user.id)
    if existing and existing.status == "verified":
        raise ApiError(
            "business_locked",
            "Подтверждённый бизнес-профиль меняется через поддержку",
            status.HTTP_409_CONFLICT,
        )
    if payload.kind == "self_employed":
        profile_status, note, verification_data = await _verify_self_employed(payload.inn)
        source = "fns_npd"
    else:
        profile_status = "pending"
        note = "Ожидает проверки реквизитов администратором"
        verification_data = {}
        source = "manual"

    if existing is None:
        profile = BusinessProfile(
            kind=payload.kind,
            inn=payload.inn,
            ogrn=payload.ogrn,
            legal_name=payload.legal_name.strip(),
            status=profile_status,
            verification_source=source,
            verification_note=note,
            verification_data=verification_data,
            verified_at=datetime.now(UTC) if profile_status == "verified" else None,
        )
        session.add(profile)
        try:
            await session.flush()
            session.add(
                BusinessMember(
                    business_id=profile.id,
                    user_id=current_user.id,
                    role="owner",
                )
            )
            session.add(BusinessEntitlement(business_id=profile.id))
            await promote_personal_account_to_business(
                session,
                user_id=current_user.id,
                business_id=profile.id,
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                "business_already_registered",
                "Этот ИНН уже привязан к аккаунту",
                status.HTTP_409_CONFLICT,
            ) from exc
    else:
        existing.kind = payload.kind
        existing.inn = payload.inn
        existing.ogrn = payload.ogrn
        existing.legal_name = payload.legal_name.strip()
        existing.status = profile_status
        existing.verification_source = source
        existing.verification_note = note
        existing.verification_data = verification_data
        existing.verified_at = datetime.now(UTC) if profile_status == "verified" else None
        try:
            await promote_personal_account_to_business(
                session,
                user_id=current_user.id,
                business_id=existing.id,
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(
                "business_already_registered",
                "Этот ИНН уже привязан к аккаунту",
                status.HTTP_409_CONFLICT,
            ) from exc
        profile = existing
    await session.refresh(profile)
    return profile


@router.post("/business/{inn}/decision", response_model=BusinessProfilePublic)
async def decide_business(
    inn: str,
    payload: BusinessDecision,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> BusinessProfile:
    if not _is_admin(current_user):
        raise ApiError("forbidden", "admin access required", status.HTTP_403_FORBIDDEN)
    profile = (
        await session.execute(select(BusinessProfile).where(BusinessProfile.inn == inn))
    ).scalar_one_or_none()
    if profile is None:
        raise ApiError("not_found", "business not found", status.HTTP_404_NOT_FOUND)
    profile.status = "verified" if payload.approved else "rejected"
    profile.verification_source = "manual"
    profile.verification_note = payload.note
    profile.verified_at = datetime.now(UTC) if payload.approved else None
    await session.commit()
    await session.refresh(profile)
    return profile


@router.get("/admin/businesses", response_model=list[BusinessReviewPublic])
async def list_businesses_for_review(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> list[BusinessReviewPublic]:
    if not _is_admin(current_user):
        raise ApiError("forbidden", "admin access required", status.HTTP_403_FORBIDDEN)

    rows = (
        await session.execute(
            select(BusinessProfile, User.email)
            .join(BusinessMember, BusinessMember.business_id == BusinessProfile.id)
            .join(User, User.id == BusinessMember.user_id)
            .where(BusinessMember.role == "owner", User.email.is_not(None))
            .order_by(
                BusinessProfile.status != "pending",
                BusinessProfile.created_at.desc(),
            )
        )
    ).all()
    return [
        BusinessReviewPublic(
            **BusinessProfilePublic.model_validate(profile).model_dump(),
            owner_email=str(owner_email),
        )
        for profile, owner_email in rows
    ]


@router.get("/admin/access")
async def get_admin_access(current_user: CurrentUserDep) -> dict[str, bool]:
    if not _is_admin(current_user):
        raise ApiError("forbidden", "admin access required", status.HTTP_403_FORBIDDEN)
    return {"is_admin": True}
