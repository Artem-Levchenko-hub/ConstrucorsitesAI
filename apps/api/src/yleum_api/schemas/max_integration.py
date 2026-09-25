"""Public MAX Mini App integration contract. Bot secrets are input-only."""

from datetime import datetime

from pydantic import BaseModel, Field


class MaxConnectRequest(BaseModel):
    token: str = Field(min_length=10, max_length=4096)


class MaxIntegrationPublic(BaseModel):
    eligible: bool
    connected: bool
    status: str = "disconnected"
    bot_id: str | None = None
    bot_name: str | None = None
    bot_username: str | None = None
    app_url: str | None = None
    webhook_url: str | None = None
    deep_link: str | None = None
    last_error: str | None = None
    verified_at: datetime | None = None
    published_at: datetime | None = None
