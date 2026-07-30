"""Public contracts for project-scoped third-party integrations."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class IntegrationFieldPublic(BaseModel):
    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    secret: bool = False
    required: bool = True


class IntegrationProviderPublic(BaseModel):
    key: str
    name: str
    category: str
    description: str
    capabilities: list[str]
    fields: list[IntegrationFieldPublic]
    available: bool
    recommended: bool = False
    requirement: str | None = None
    docs_url: str


class AppIntegrationPublic(BaseModel):
    id: str
    provider: str
    status: str
    account_label: str | None = None
    public_config: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    configured_fields: list[str] = Field(default_factory=list)
    last_error: str | None = None
    verified_at: datetime | None = None
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationCatalogPublic(BaseModel):
    providers: list[IntegrationProviderPublic]
    connections: list[AppIntegrationPublic]


class IntegrationConnectRequest(BaseModel):
    values: dict[str, str]

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 12:
            raise ValueError("too many integration fields")
        normalized: dict[str, str] = {}
        for key, value in values.items():
            clean_key = str(key).strip()
            clean_value = str(value).strip()
            if not clean_key or len(clean_key) > 64:
                raise ValueError("invalid integration field")
            if len(clean_value) > 8192:
                raise ValueError("integration value is too long")
            normalized[clean_key] = clean_value
        return normalized
