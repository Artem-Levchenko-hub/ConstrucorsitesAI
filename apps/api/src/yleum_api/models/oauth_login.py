"""Вход через внешних провайдеров (VK ID, Яндекс ID).

Аккаунт остаётся «email + пароль»: связка с провайдером хранит только его
идентификатор пользователя и снимок email, по которому аккаунт был найден или
создан. Ничего другого из профиля провайдера (имя, телефон, аватар) на
платформу не попадает.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class UserIdentity(Base):
    __tablename__ = "user_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Email, который провайдер сообщил при последнем входе; нужен только чтобы
    # объяснить владельцу, почему связка привязана к этому аккаунту.
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_user_identities_provider_subject"
        ),
        Index("ix_user_identities_user_id", "user_id"),
    )


class OAuthLoginState(Base):
    """Серверное состояние одного рукопожатия: state + PKCE до callback-а, затем
    одноразовый «билет» на экран подтверждения документов для нового аккаунта.

    Строка проходит две фазы: ``used_at`` закрывает state после callback-а,
    ``completed_at`` — билет после создания аккаунта. Ни токены провайдера, ни
    что-либо кроме id и email пользователя здесь не хранятся.
    """

    __tablename__ = "oauth_login_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    state_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # PKCE code_verifier (VK ID); у Яндекса классический code flow — NULL.
    code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Куда вернуть пользователя в web после входа (только same-origin путь).
    next_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticket_hash: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    ticket_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pending_provider_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_oauth_login_states_expires", "expires_at"),)
