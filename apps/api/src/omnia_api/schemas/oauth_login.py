from typing import Literal

from pydantic import BaseModel, EmailStr, Field

OAuthProvider = Literal["vk", "yandex"]


class OAuthProviderPublic(BaseModel):
    provider: OAuthProvider
    label: str


class OAuthProvidersPublic(BaseModel):
    """Только настроенные провайдеры — UI рисует ровно эти кнопки."""

    providers: list[OAuthProviderPublic]
    legal_document_version: str


class OAuthStartPublic(BaseModel):
    authorization_url: str


class OAuthPendingPublic(BaseModel):
    """Что показать на экране подтверждения документов перед созданием аккаунта."""

    provider: OAuthProvider
    label: str
    email: EmailStr
    next: str
    legal_document_version: str


class OAuthCompleteRequest(BaseModel):
    """Те же согласия, что и у обычной регистрации MAX Studio."""

    ticket: str = Field(min_length=32, max_length=512)
    terms_accepted: bool = False
    privacy_accepted: bool = False
    personal_data_accepted: bool = False
    marketing_accepted: bool = False
    document_version: str | None = Field(default=None, max_length=32)
