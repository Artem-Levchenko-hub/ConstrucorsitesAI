"""Portable project contract. It cannot express Docker or controller authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
MANIFEST_PATH = ".omnia/cell.json"
MAX_MANIFEST_BYTES = 1024 * 1024
RESERVED_PATHS = ("/api/omnia", "/api/max", "/__omnia", "/auth")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, allow_inf_nan=False)


class MachineResources(_StrictModel):
    cpu_cores: float = Field(default=0.25, gt=0)
    memory_bytes: int = Field(default=128 * 1024 * 1024, gt=0, strict=True)
    disk_bytes: int = Field(default=1024 * 1024 * 1024, gt=0, strict=True)
    pids: int = Field(default=64, gt=0, strict=True)


class MachineCommand(_StrictModel):
    argv: list[str] = Field(min_length=1)
    cwd: str = "."

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        if not value[0] or any("\x00" in item for item in value):
            raise ValueError("argv must have an executable and no NUL bytes")
        if sum(len(item.encode()) for item in value) > 65536:
            raise ValueError("command exceeds transport budget")
        return value

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or ":" in value
            or "\x00" in value
            or ".." in value.split("/")
        ):
            raise ValueError("cwd must be a relative project path without traversal")
        return str(PurePosixPath(value))


class MachineTask(MachineCommand):
    name: Name
    role: Literal[
        "bootstrap",
        "fast_check",
        "full_build",
        "build",
        "test",
        "migrate",
        "quiesce",
        "restore_check",
    ]
    timeout_seconds: int = Field(default=900, ge=1, le=3600)


class MachineMount(_StrictModel):
    volume: Name
    target: str

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or "\x00" in value
            or ".." in value.split("/")
            or value != str(PurePosixPath(value))
        ):
            raise ValueError("mount target must be a canonical absolute guest path")
        reserved = (
            "/workspace",
            "/root",
            "/run",
            "/proc",
            "/sys",
            "/dev",
            "/etc",
            "/bin",
            "/sbin",
            "/usr",
            "/lib",
            "/lib64",
            "/omnia",
        )
        if value == "/" or any(
            value == item or value.startswith(item + "/") or item.startswith(value + "/")
            for item in reserved
        ):
            raise ValueError("mount overlaps a runtime or controller path")
        return value


class MachineReadiness(_StrictModel):
    port: int = Field(ge=1, le=65535)
    path: str = "/"
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/") or any(c in value for c in "\r\n\x00"):
            raise ValueError("readiness path must be an HTTP path")
        return value


class MachineService(MachineCommand):
    name: Name
    depends_on: list[Name] = Field(default_factory=list)
    mounts: list[MachineMount] = Field(default_factory=list)
    readiness: MachineReadiness | None = None
    restart: Literal["never", "on-failure", "always"] = "on-failure"
    resources: MachineResources = Field(default_factory=MachineResources)


class MachineRoute(_StrictModel):
    path: str
    service: Name
    port: int = Field(ge=1, le=65535)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or ".." in value.split("/")
            or any(c in value for c in "\\?#%\r\n\x00")
            or value != str(PurePosixPath(value))
        ):
            raise ValueError("route must be a canonical URL path")
        if any(value == item or value.startswith(item + "/") for item in RESERVED_PATHS):
            raise ValueError("route overlaps a reserved managed boundary")
        return value


class MachineDataStore(_StrictModel):
    name: Name
    volumes: list[Name] = Field(min_length=1)
    quiesce_task: Name
    restore_check_task: Name


class MachineManifest(_StrictModel):
    version: Literal[1]
    tasks: list[MachineTask] = Field(default_factory=list)
    services: list[MachineService] = Field(min_length=1)
    routes: list[MachineRoute] = Field(min_length=1)
    data_stores: list[MachineDataStore] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        for label, items in (
            ("service", self.services),
            ("task", self.tasks),
            ("data store", self.data_stores),
        ):
            names = [item.name for item in items]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {label} name")
        services = {item.name: item for item in self.services}
        tasks = {item.name: item for item in self.tasks}
        paths = [route.path for route in self.routes]
        if len(paths) != len(set(paths)) or "/" not in paths:
            raise ValueError("routes require one unique root path")
        for route in self.routes:
            if route.service not in services:
                raise ValueError(f"unknown route service: {route.service}")
        mounts: dict[str, str] = {}
        for service in self.services:
            if len(service.depends_on) != len(set(service.depends_on)):
                raise ValueError("duplicate dependency")
            for dependency in service.depends_on:
                if dependency not in services:
                    raise ValueError(f"unknown dependency: {dependency}")
            for mount in service.mounts:
                if mount.target in mounts and mounts[mount.target] != mount.volume:
                    raise ValueError("different volumes use the same guest target")
                mounts[mount.target] = mount.volume
        for target in mounts:
            if any(other != target and target.startswith(other + "/") for other in mounts):
                raise ValueError("overlapping mount targets")
        for store in self.data_stores:
            if not set(store.volumes) <= set(mounts.values()):
                raise ValueError("data store refers to an unmounted volume")
            for role in ("quiesce", "restore_check"):
                name = getattr(store, role + "_task")
                if name not in tasks or tasks[name].role != role:
                    raise ValueError(f"invalid {role} task reference")
        self.service_order()
        return self

    def service_order(self) -> tuple[str, ...]:
        pending = {service.name: set(service.depends_on) for service in self.services}
        result: list[str] = []
        while pending:
            ready = sorted(name for name, deps in pending.items() if not deps - set(result))
            if not ready:
                raise ValueError("service dependency cycle")
            for name in ready:
                result.append(name)
                del pending[name]
        return tuple(result)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def resource_request(self) -> MachineResources:
        return MachineResources(
            **{
                key: sum(getattr(service.resources, key) for service in self.services)
                for key in ("cpu_cores", "memory_bytes", "disk_bytes", "pids")
            }
        )

    @classmethod
    def from_files(cls, files: dict[str, str]) -> MachineManifest | None:
        value = files.get(MANIFEST_PATH)
        if value is None:
            return None
        if len(value.encode()) > MAX_MANIFEST_BYTES:
            raise ValueError("machine manifest exceeds transport budget")
        return cls.model_validate_json(value)
