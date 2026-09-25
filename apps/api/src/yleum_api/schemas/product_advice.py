"""Public wire contract for contextual MAX product advice."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ProductAdviceItem(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=64)
    kind: Literal["feature", "improvement"]
    title: str = Field(min_length=1, max_length=80)
    benefit: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1, max_length=3000)


class ProductAdviceResponse(BaseModel):
    version: str = Field(min_length=1, max_length=32)
    project_id: UUID
    current_snapshot_id: UUID
    analysis_snapshot_id: UUID
    archetype: str = Field(min_length=1, max_length=64)
    source: Literal["model", "fallback", "cache"]
    items: list[ProductAdviceItem] = Field(max_length=3)


__all__ = ["ProductAdviceItem", "ProductAdviceResponse"]
