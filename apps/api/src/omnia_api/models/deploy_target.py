"""Чужой VPS пользователя как цель деплоя (BYO-VPS).

Наш хостинг = отсутствие цели (projects.deploy_target_id IS NULL) — это
сохраняет текущее поведение байт-в-байт. Если у проекта задан deploy_target,
оркестратор собирает тот же переносимый prod-образ, но запускает его на машине
пользователя по SSH.

SSH-креды (приватный ключ ИЛИ пароль) хранятся зашифрованными отдельным
«сильным» ключом (core.crypto.encrypt_strong), НЕ производным от jwt_secret,
чтобы ротация jwt не отобрала доступ к чужим машинам. В API-ответах секрет не
появляется никогда — только флаг `has_secret`.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class DeployTarget(Base):
    __tablename__ = "deploy_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Человекочитаемое имя цели в UI («Мой Hetzner», «Прод-сервер клиники»).
    label: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_host: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="22", default=22)
    ssh_user: Mapped[str] = mapped_column(Text, nullable=False)
    # 'key' — храним наш приватный ключ пары (юзер добавил наш публичный к себе);
    # 'password' — храним пароль SSH. Оба зашифрованы encrypt_strong.
    ssh_auth_type: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # Публичный ключ пары (для режима 'key') — показываем юзеру, чтобы он добавил
    # его в authorized_keys. Не секрет.
    ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Пиннинг host-key: сохраняем при первой верификации, сверяем при деплое —
    # защита от MITM/подмены сервера. Формат known_hosts-строки.
    known_host_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Результат последней проверки: 'unverified' | 'ok' | 'failed'.
    verify_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unverified", default="unverified"
    )
    verify_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "ssh_auth_type IN ('key', 'password')",
            name="ck_deploy_targets_auth_type_allowed",
        ),
        CheckConstraint(
            "verify_status IN ('unverified', 'ok', 'failed')",
            name="ck_deploy_targets_verify_status_allowed",
        ),
        Index("ix_deploy_targets_owner_id", "owner_id"),
    )
