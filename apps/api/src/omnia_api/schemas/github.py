import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_REPO_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


class GithubConnectResponse(BaseModel):
    authorize_url: str


class GithubStatus(BaseModel):
    connected: bool
    github_username: str | None = None
    scopes: str | None = None
    connected_at: datetime | None = None


class GithubExportRequest(BaseModel):
    repo_name: str | None = Field(default=None, max_length=100)
    private: bool = True
    description: str | None = Field(default=None, max_length=350)

    @field_validator("repo_name")
    @classmethod
    def valid_repo_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not _REPO_NAME_RE.fullmatch(v):
            raise ValueError("repo_name may only contain letters, digits, '.', '-', '_'")
        return v


class GithubExportResult(BaseModel):
    repo_url: str
    repo_full_name: str
    default_branch: str
    pushed_at: datetime
