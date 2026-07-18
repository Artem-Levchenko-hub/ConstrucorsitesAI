"""Схемы API для подключения своего домена к проекту."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CustomDomainConnect(BaseModel):
    project_id: UUID
    host: str = Field(min_length=3, max_length=253)


class CustomDomainPublic(BaseModel):
    id: UUID
    project_id: UUID
    host: str
    source: str
    expected_ip: str
    dns_status: str
    cert_status: str
    last_detail: str | None = None
    created_at: datetime
    verified_at: datetime | None = None
    # Готовая DNS-инструкция для UI: «создайте A-запись host → expected_ip».
    dns_instructions: str | None = None
