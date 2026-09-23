"""Named orchestrator hosts and the sticky binding of a Project Cell to one of them.

Phase 3 / stage B: agent cells run on more than one host, each with its own
orchestrator (same code, own Docker, own preview wildcard). The API keeps one
registry of hosts and remembers, per workspace, which host it was placed on.
That binding never changes: a cell's volumes, checkpoints and preview vhost live
on exactly one machine.

Single-host deployments set nothing: the registry then holds one host, named by
`default_orchestrator`, built from the pre-existing `orchestrator_url`,
`project_cell_preview_host_suffix` and `gate_preview_resolver_rules` settings,
and no lookup ever touches the database.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core import config as _config

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SUFFIX = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_WORKSPACE_PATH = re.compile(r"^/internal/workspaces/([0-9a-fA-F-]{36})(?:/|$)")
_PUBLICATION_PATH = re.compile(
    r"^/internal/projects/([0-9a-fA-F-]{36})/(?:cell-deploy|deploy)(?:/|$)"
)
_CACHE_LIMIT = 8192


class OrchestratorHostError(ValueError):
    """The registry setting is malformed or names an unknown host."""


@dataclass(frozen=True, slots=True)
class OrchestratorHost:
    name: str
    url: str
    preview_host_suffix: str
    preview_resolver_rules: str = ""
    enabled: bool = True
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class OrchestratorRegistry:
    hosts: tuple[OrchestratorHost, ...]
    default: str
    single: bool  # nothing configured: legacy one-host behaviour, no lookups

    def get(self, name: str | None) -> OrchestratorHost:
        wanted = name or self.default
        for host in self.hosts:
            if host.name == wanted:
                return host
        raise OrchestratorHostError(f"unknown orchestrator host {wanted!r}")

    def enabled(self) -> tuple[OrchestratorHost, ...]:
        return tuple(host for host in self.hosts if host.enabled)

    def preview_suffixes(self) -> frozenset[str]:
        return frozenset(host.preview_host_suffix for host in self.hosts)

    def resolver_rules(self) -> str:
        """One Chromium `--host-resolver-rules` value covering every host's wildcard."""
        rules: list[str] = []
        for host in self.hosts:
            for rule in host.preview_resolver_rules.split(","):
                rule = rule.strip()
                if rule and rule not in rules:
                    rules.append(rule)
        return ",".join(rules)


def _parse(raw: str, *, default: str, fallback: OrchestratorHost) -> OrchestratorRegistry:
    text = raw.strip()
    if not text:
        return OrchestratorRegistry(hosts=(fallback,), default=fallback.name, single=True)
    try:
        items = json.loads(text)
    except ValueError as exc:
        raise OrchestratorHostError("ORCHESTRATOR_HOSTS is not valid JSON") from exc
    if not isinstance(items, list) or not items:
        raise OrchestratorHostError("ORCHESTRATOR_HOSTS must be a non-empty JSON list")
    hosts: list[OrchestratorHost] = []
    for item in items:
        if not isinstance(item, dict):
            raise OrchestratorHostError("ORCHESTRATOR_HOSTS entries must be objects")
        name = str(item.get("name", ""))
        url = str(item.get("url", "")).rstrip("/")
        suffix = str(item.get("preview_host_suffix", "")).strip().lower()
        if _NAME.fullmatch(name) is None:
            raise OrchestratorHostError(f"orchestrator host name {name!r} is invalid")
        if not url.startswith(("http://", "https://")):
            raise OrchestratorHostError(f"orchestrator host {name!r} needs an http(s) url")
        if _SUFFIX.fullmatch(suffix) is None:
            raise OrchestratorHostError(f"orchestrator host {name!r} needs a preview suffix")
        if any(host.name == name for host in hosts):
            raise OrchestratorHostError(f"orchestrator host {name!r} is listed twice")
        weight = float(item.get("weight", 1.0))
        if weight <= 0:
            raise OrchestratorHostError(f"orchestrator host {name!r} weight must be positive")
        hosts.append(
            OrchestratorHost(
                name=name,
                url=url,
                preview_host_suffix=suffix,
                preview_resolver_rules=str(item.get("preview_resolver_rules", "")).strip(),
                enabled=bool(item.get("enabled", True)),
                weight=weight,
            )
        )
    if all(host.name != default for host in hosts):
        raise OrchestratorHostError(f"default orchestrator {default!r} is not in the list")
    if not any(host.enabled for host in hosts):
        raise OrchestratorHostError("at least one orchestrator host must be enabled")
    return OrchestratorRegistry(hosts=tuple(hosts), default=default, single=False)


_registry_cache: tuple[tuple[str, str, str, str, str], OrchestratorRegistry] | None = None


def registry() -> OrchestratorRegistry:
    """The current host registry (parsed once per distinct settings value)."""
    global _registry_cache
    # Read through the config module (not an imported name) and tolerate partial
    # settings objects: tests stub `get_settings` with only the fields they need.
    settings = _config.get_settings()
    hosts = str(getattr(settings, "orchestrator_hosts", "") or "")
    default = str(getattr(settings, "default_orchestrator", "core") or "core")
    url = str(getattr(settings, "orchestrator_url", "http://localhost:8003") or "")
    suffix = str(getattr(settings, "project_cell_preview_host_suffix", "") or "")
    rules = str(getattr(settings, "gate_preview_resolver_rules", "") or "").strip()
    key = (hosts, default, url, suffix, rules)
    if _registry_cache is not None and _registry_cache[0] == key:
        return _registry_cache[1]
    fallback = OrchestratorHost(
        name=default,
        url=url.rstrip("/"),
        preview_host_suffix=suffix,
        preview_resolver_rules=rules,
    )
    parsed = _parse(hosts, default=default, fallback=fallback)
    _registry_cache = (key, parsed)
    return parsed


# ------------------------------------------------------------- bindings


_workspace_hosts: OrderedDict[UUID, str] = OrderedDict()
_project_hosts: OrderedDict[UUID, str] = OrderedDict()


def _remember(cache: OrderedDict[UUID, str], key: UUID, host: str) -> None:
    cache[key] = host
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def remember_workspace_host(workspace_id: UUID, project_id: UUID | None, host: str) -> None:
    """Prime the binding cache when a workspace is created or loaded."""
    _remember(_workspace_hosts, workspace_id, host)
    if project_id is not None:
        _remember(_project_hosts, project_id, host)


def forget_bindings() -> None:
    _workspace_hosts.clear()
    _project_hosts.clear()


def _session_factory() -> async_sessionmaker[AsyncSession]:
    from omnia_api.core.db import get_engine

    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def _lookup(column: Any, value: UUID) -> tuple[UUID, UUID, str] | None:
    from omnia_api.models.project_cell import ProjectCellWorkspace

    async with _session_factory()() as session:
        row = (
            await session.execute(
                select(
                    ProjectCellWorkspace.id,
                    ProjectCellWorkspace.project_id,
                    ProjectCellWorkspace.orchestrator,
                ).where(column == value)
            )
        ).first()
    if row is None:
        return None
    return UUID(str(row[0])), UUID(str(row[1])), str(row[2])


async def host_for_workspace(workspace_id: UUID) -> str:
    """Where this cell lives. Unknown workspaces resolve to the default host so a
    request for a cell that was never created still reaches an orchestrator that
    can answer 'not found' the way it always did."""
    reg = registry()
    if reg.single:
        return reg.default
    cached = _workspace_hosts.get(workspace_id)
    if cached is not None:
        return cached
    from omnia_api.models.project_cell import ProjectCellWorkspace

    row = await _lookup(ProjectCellWorkspace.id, workspace_id)
    if row is None:
        return reg.default
    remember_workspace_host(row[0], row[1], row[2])
    return row[2]


async def host_for_project(project_id: UUID) -> str:
    reg = registry()
    if reg.single:
        return reg.default
    cached = _project_hosts.get(project_id)
    if cached is not None:
        return cached
    from omnia_api.models.project_cell import ProjectCellWorkspace

    row = await _lookup(ProjectCellWorkspace.project_id, project_id)
    if row is None:
        return reg.default
    remember_workspace_host(row[0], row[1], row[2])
    return row[2]


async def host_for_path(path: str) -> str | None:
    """Route an internal orchestrator call by the identity in its path.

    `/internal/workspaces/<id>/…` follows the workspace; a project's publication
    (`/internal/projects/<id>/cell-deploy…`, `…/deploy…`) follows the cell of that
    project. Everything else (legacy slug-keyed runtime, capabilities, health)
    stays on the default host. Returns None when nothing is configured.
    """
    reg = registry()
    if reg.single:
        return None
    match = _WORKSPACE_PATH.match(path)
    if match is not None:
        return await host_for_workspace(UUID(match.group(1)))
    match = _PUBLICATION_PATH.match(path)
    if match is not None:
        return await host_for_project(UUID(match.group(1)))
    return reg.default


__all__ = [
    "OrchestratorHost",
    "OrchestratorHostError",
    "OrchestratorRegistry",
    "forget_bindings",
    "host_for_path",
    "host_for_project",
    "host_for_workspace",
    "registry",
    "remember_workspace_host",
]
