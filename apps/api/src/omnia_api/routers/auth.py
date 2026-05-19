from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.rate_limit import limiter
from omnia_api.core.security import (
    consume_dummy_verify,
    create_access_token,
    hash_password,
    verify_password,
)
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.schemas.user import UserCreate, UserLogin, UserPublic

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.jwt_cookie_name,
        value=token,
        max_age=settings.jwt_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.jwt_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def register(
    request: Request, payload: UserCreate, response: Response, session: SessionDep
) -> User:
    settings = get_settings()
    pwd_hash = await hash_password(payload.password)
    user = User(email=payload.email, password_hash=pwd_hash)
    user.wallet = Wallet(balance_rub=Decimal(str(settings.initial_wallet_balance_rub)))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise ApiError(
            "conflict",
            "email already registered",
            status.HTTP_409_CONFLICT,
        ) from e
    await session.refresh(user)
    _set_session_cookie(response, create_access_token(user.id))
    return user


@router.post("/login", response_model=UserPublic)
@limiter.limit("5/minute")
async def login(
    request: Request, payload: UserLogin, response: Response, session: SessionDep
) -> User:
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        await consume_dummy_verify()
        raise ApiError("unauthorized", "invalid credentials", status.HTTP_401_UNAUTHORIZED)
    if not await verify_password(payload.password, user.password_hash):
        raise ApiError("unauthorized", "invalid credentials", status.HTTP_401_UNAUTHORIZED)
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)
    _set_session_cookie(response, create_access_token(user.id))
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(key=settings.jwt_cookie_name, path="/")


@router.get("/me", response_model=UserPublic)
async def me(current_user: CurrentUserDep) -> User:
    return current_user
