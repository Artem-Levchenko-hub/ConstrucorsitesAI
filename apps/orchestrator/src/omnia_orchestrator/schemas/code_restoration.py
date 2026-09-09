"""Bounded, immutable historical source; never accepts a database archive."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import unicodedata
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MAX_SOURCE_BYTES = 32 * 1024 * 1024


class RestorationSourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    content_base64: str = Field(max_length=12 * 1024 * 1024, repr=False)
    mode: Literal["100644", "100755"] = "100644"

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        parts = value.split("/")
        forbidden = {".git", ".next", "node_modules", ".ssh", ".aws", ".npmrc"}
        if (
            value.startswith("/")
            or "\\" in value
            or ":" in value
            or any(part in {"", ".", ".."} for part in parts)
            or str(PurePosixPath(value)) != value
            or any(unicodedata.category(c) in {"Cc", "Cf"} for c in value)
            or any(part.casefold() in forbidden for part in parts)
            or any(
                part.casefold().startswith(".env")
                and part.casefold() not in {".env.example", ".env.sample"}
                for part in parts
            )
        ):
            raise ValueError("unsafe historical source path")
        return value

    @field_validator("content_base64")
    @classmethod
    def canonical_base64(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid historical source encoding") from exc
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("noncanonical historical source encoding")
        return value

    def decoded(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class RestorationCurrentFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["100644", "100755"] = "100644"

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return RestorationSourceFile.safe_path(value)


class RestorationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    expected_source_head: GitSha
    target_commit_sha: GitSha
    planned_commit_sha: GitSha
    fencing_epoch: int = Field(ge=1, strict=True)


class CodeRestorationPrepare(RestorationIdentity):
    files: list[RestorationSourceFile] = Field(min_length=1, max_length=20_000, repr=False)
    current_files: list[RestorationCurrentFile] = Field(
        default_factory=list, max_length=20_000, repr=False
    )

    @model_validator(mode="after")
    def unique_bounded_source(self) -> CodeRestorationPrepare:
        names = {file.path for file in self.files}
        if len(names) != len(self.files):
            raise ValueError("duplicate historical source path")
        for name in names:
            if any(str(parent) in names for parent in PurePosixPath(name).parents):
                raise ValueError("historical source file/directory collision")
        if sum(len(file.decoded()) for file in self.files) > MAX_SOURCE_BYTES:
            raise ValueError("historical source exceeds candidate budget")
        current_names = {file.path for file in self.current_files}
        if len(current_names) != len(self.current_files):
            raise ValueError("duplicate current source path")
        for name in current_names:
            if any(str(parent) in current_names for parent in PurePosixPath(name).parents):
                raise ValueError("current source file/directory collision")
        return self

    def digest(self) -> str:
        value = self.model_dump(mode="json")
        value["files"] = sorted(value["files"], key=lambda file: file["path"])
        value["current_files"] = sorted(value["current_files"], key=lambda file: file["path"])
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class CodeRestorationApply(RestorationIdentity):
    candidate_id: UUID
    report_revision: int = Field(gt=0, strict=True)
    expected_fencing_epoch: int = Field(ge=1, strict=True)


class CodeRestorationCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
