"""Свой домен пользователя, подключённый к проекту.

Пользователь подключает домен, которым уже владеет (source='external'): мы
показываем DNS-инструкцию (A-запись → нужный IP: наш 170.168.72.200 или IP его
VPS, если проект деплоится на свой сервер), после появления записи выпускаем
Let's Encrypt и пишем nginx-vhost на host домена → контейнер проекта.

Статусы DNS и сертификата раздельны, чтобы UI честно показывал этап:
«ждём A-запись» → «выпускаю SSL» → «работает». source='purchased' зарезервирован
под домены, купленные через нас (Phase 3, REG.RU) — тогда DNS настраивается
автоматически.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class CustomDomain(Base):
    __tablename__ = "custom_domains"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Полный хост, напр. "shop.example.ru". Уникален глобально — один домен не
    # может указывать на два проекта.
    host: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="external", default="external"
    )
    # IP, на который должна указывать A-запись (наш VPS или IP чужого VPS).
    expected_ip: Mapped[str] = mapped_column(Text, nullable=False)
    # 'pending' (ждём A-запись) | 'ok' (запись указывает на нас) | 'mismatch'.
    dns_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending", default="pending"
    )
    # 'none' | 'issuing' | 'active' | 'failed'.
    cert_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="none", default="none"
    )
    last_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "source IN ('external', 'purchased')",
            name="ck_custom_domains_source_allowed",
        ),
        CheckConstraint(
            "dns_status IN ('pending', 'ok', 'mismatch')",
            name="ck_custom_domains_dns_status_allowed",
        ),
        CheckConstraint(
            "cert_status IN ('none', 'issuing', 'active', 'failed')",
            name="ck_custom_domains_cert_status_allowed",
        ),
        Index("ix_custom_domains_project_id", "project_id"),
    )
