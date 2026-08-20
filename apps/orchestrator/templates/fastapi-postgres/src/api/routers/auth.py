"""/auth/* endpoints — signup + signin returning a JWT.

Same flow as the Next template's Auth.js Credentials provider, but
exposed as REST endpoints suitable for a mobile/SPA client. The Next
template uses sessions in a cookie; here we issue a JWT in JSON so
non-browser clients can consume it directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.models import User
from api.security import (
    hash_password,
    issue_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post(
    "/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def signup(
    body: SignupRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> TokenResponse:
    """Register a new user. Returns a JWT immediately so the client
    doesn't need a second round-trip to sign in."""
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        name=body.name,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Race-safe: unique constraint on `users.email` catches dupes.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered",
        )
    await session.refresh(user)
    return TokenResponse(access_token=issue_token(user.id))


@router.post("/signin", response_model=TokenResponse)
async def signin(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """OAuth2-spec compatible — `username` carries the email so Swagger
    UI's «Authorize» modal works out of the box."""
    user = (
        await session.execute(
            select(User).where(User.email == form.username.lower())
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
        )
    return TokenResponse(access_token=issue_token(user.id))
