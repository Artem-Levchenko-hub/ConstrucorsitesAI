"""Схемы API для BYO-VPS (свой сервер как цель деплоя).

Секрет (ключ/пароль) принимается ТОЛЬКО на вход и никогда не возвращается —
наружу отдаём лишь `has_secret` + `verify_status`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DeployTargetCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    ssh_host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=64)
    auth_type: str = Field(description="'key' или 'password'")
    # Для auth_type='password' — пароль; для 'key' — приватный ключ в PEM
    # (необязателен: если не прислан в режиме 'key', сервер сгенерит пару и
    # вернёт публичный ключ, чтобы юзер добавил его на свой сервер).
    secret: str | None = Field(default=None, max_length=20000)

    @field_validator("auth_type")
    @classmethod
    def _auth_type(cls, v: str) -> str:
        if v not in ("key", "password"):
            raise ValueError("auth_type must be 'key' or 'password'")
        return v


class DeployTargetPublic(BaseModel):
    id: UUID
    label: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    auth_type: str
    has_secret: bool
    # Публичный ключ (режим 'key') — показываем юзеру, чтобы он добавил его в
    # authorized_keys на своём сервере.
    ssh_public_key: str | None = None
    verify_status: str
    verify_detail: str | None = None
    verified_at: datetime | None = None
    created_at: datetime


class DeployTargetVerifyResult(BaseModel):
    ok: bool
    verify_status: str
    detail: str | None = None
    # Что нашли на сервере при проверке (docker, версия) — для UI-подсказок.
    docker_ok: bool = False
    docker_version: str | None = None
