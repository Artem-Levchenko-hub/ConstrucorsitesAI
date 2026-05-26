"""Example owner-scoped CRUD — pattern AI should copy for new resources.

Every read filters by `Item.owner_id == user.id`. Without this any
authenticated user could read any other user's data — DON'T drop this
filter when generating new endpoints.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.models import Item, User
from api.security import current_user

router = APIRouter(prefix="/items", tags=["items"])


class ItemRead(BaseModel):
    id: uuid.UUID
    title: str
    body: str | None
    model_config = {"from_attributes": True}


class ItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str | None = None


@router.get("", response_model=list[ItemRead])
async def list_items(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Item]:
    rows = (
        await session.execute(select(Item).where(Item.owner_id == user.id))
    ).scalars().all()
    return list(rows)


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    body: ItemCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Item:
    item = Item(owner_id=user.id, title=body.title, body=body.body)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    item = (
        await session.execute(
            select(Item).where(Item.id == item_id, Item.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    await session.delete(item)
    await session.commit()
