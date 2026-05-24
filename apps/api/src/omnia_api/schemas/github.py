from __future__ import annotations

from pydantic import BaseModel, Field


class GitHubStatus(BaseModel):
    connected: bool
    login: str | None = None


class GitHubConnectResponse(BaseModel):
    authorize_url: str


class PushRequest(BaseModel):
    repo_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    private: bool = True
    description: str = Field(default="", max_length=350)


class PushResponse(BaseModel):
    repo_url: str
    full_name: str
