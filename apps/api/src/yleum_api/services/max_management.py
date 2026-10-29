"""Typed owner RPC broker pinned to the accepted publication, never draft state."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.crypto import decrypt_strong
from yleum_api.core.errors import ApiError, ErrorCode
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.max_management import (
    ManagementContract,
    ManagementModule,
    ManagementOverviewPublic,
    OwnerRecordPublic,
    OwnerRecordsPagePublic,
    OwnerRecordsQuery,
    OwnerRecordUpdate,
    ResourceId,
)
from yleum_api.services import orchestrator_client, repo
from yleum_api.services.max_management_assertion import (
    OWNER_HEADER,
    OWNER_PATH,
    sign_owner_assertion,
)
from yleum_api.services.max_management_contract import (
    parse_management_contract,
    validate_management_modules,
)

MAX_RESPONSE_BYTES = 512 * 1024
MAX_REQUEST_BYTES = 16 * 1024
RPC_TIMEOUT_SECONDS = 12

_ERRORS: dict[str, tuple[int, str]] = {
    "unauthorized": (401, "Доступ к управлению не подтверждён"),
    "unsupported": (409, "Эта версия приложения не поддерживает обращения"),
    "unavailable": (503, "Приложение временно недоступно. Повторите попытку"),
    "not_found": (404, "Обращение не найдено"),
    "revision_conflict": (409, "Обращение изменилось. Обновите данные перед сохранением"),
    "operation_conflict": (409, "Эта операция уже использована для другого изменения"),
    "quote_changed": (409, "Стоимость изменилась. Подтвердите актуальные данные"),
    "invalid_request": (400, "Некорректные данные запроса"),
}


def _error(code: str = "unavailable") -> ApiError:
    status, message = _ERRORS[code]
    return ApiError(cast(ErrorCode, code), message, status)


class _State(Exception):
    def __init__(
        self,
        state: str,
        target: _Target | None = None,
        contract: ManagementContract | None = None,
    ):
        self.state = state
        self.target = target
        self.contract = contract


@dataclass(frozen=True)
class _Target:
    publication_id: UUID
    snapshot_id: UUID
    commit_sha: str
    url: str


@dataclass
class _Context:
    project: Project
    target: _Target
    contract: ManagementContract
    token: str = field(repr=False)


class _Describe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_id: UUID
    contract: dict[str, object]


async def _owned(session: AsyncSession, project_id: UUID, owner_id: UUID) -> Project:
    project = await session.get(Project, project_id, populate_existing=True)
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "Проект не найден", 404)
    if project.template != "max_miniapp":
        raise ApiError("max_project_required", "Управление доступно для проекта MAX Mini App", 409)
    return project


def _destination(value: object, slug: str) -> str:
    domain = get_settings().owner_management_apps_domain
    if (
        not isinstance(domain, str)
        or domain.endswith((".localhost", ".local"))
        or not re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]*",
            domain,
        )
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", slug)
    ):
        raise _error()
    if not isinstance(value, str) or value != value.strip():
        raise _error()
    parsed = urlsplit(value)
    expected = f"{slug}.{domain}"
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
    ):
        raise _error()
    return f"https://{expected}{OWNER_PATH}"


async def _target(project: Project) -> _Target:
    try:
        async with asyncio.timeout(RPC_TIMEOUT_SECONDS):
            deployment = await orchestrator_client.get_deploy(project.id)
    except Exception:
        raise _error() from None
    if not isinstance(deployment, dict) or deployment.get("project_id") != str(project.id):
        raise _error()
    if "active_publication" not in deployment:
        # A legacy successful attempt does not supply a runtime assertion identity.
        if deployment.get("prod_url"):
            raise _State("unsupported")
        raise _State("not_published")
    active = deployment["active_publication"]
    if active is None:
        raise _State("not_published")
    if not isinstance(active, dict):
        raise _error()
    try:
        publication = UUID(active["release_id"])
        snapshot = UUID(active["snapshot_id"])
        commit = active["commit_sha"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("invalid commit")
        return _Target(
            publication, snapshot, commit, _destination(active["prod_url"], project.slug)
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        raise _error() from None


async def _prepare(session: AsyncSession, project: Project, owner_id: UUID) -> _Context:
    target = await _target(project)
    snapshot = await session.get(Snapshot, target.snapshot_id)
    if (
        snapshot is None
        or snapshot.id != target.snapshot_id
        or snapshot.project_id != project.id
        or snapshot.commit_sha != target.commit_sha
    ):
        raise _error()
    async with asyncio.timeout(RPC_TIMEOUT_SECONDS):
        content = await asyncio.to_thread(
            repo.read_file, project.id, target.commit_sha, ".omnia/management.json"
        )
    try:
        if content is None or len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("missing or oversized contract")
        contract = parse_management_contract(json.loads(content))
    except (ValueError, TypeError, UnicodeError):
        raise _State("unsupported") from None
    if not contract.resources:
        raise _State("unsupported", target, contract)
    # Source loading is an await boundary: a new owner must not inherit a stale
    # request's right to decrypt the previous owner's integration credential.
    await _owned(session, project.id, owner_id)
    integration = (
        await session.execute(
            select(MaxIntegration).where(
                MaxIntegration.project_id == project.id,
                MaxIntegration.owner_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    if (
        integration is None
        or integration.project_id != project.id
        or integration.owner_id != owner_id
        or integration.status not in ("verified", "active")
    ):
        raise _error()
    try:
        token = decrypt_strong(integration.bot_token_enc)
        if not token:
            raise ValueError("missing credential")
    except Exception:
        raise _error() from None
    return _Context(project, target, contract, token)


def _http_client() -> httpx.AsyncClient:
    # HTTPX performs no network retries by default. Never retry ambiguous writes.
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(connect=3, read=8, write=5, pool=3),
    )


async def _unchanged(session: AsyncSession, ctx: _Context, owner_id: UUID) -> None:
    project = await _owned(session, ctx.project.id, owner_id)
    try:
        current = await _target(project)
    except _State:
        raise _error() from None
    if current != ctx.target:
        raise _error()


async def _rpc(
    session: AsyncSession,
    ctx: _Context,
    owner_id: UUID,
    payload: dict[str, object],
) -> object:
    await _unchanged(session, ctx, owner_id)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise _error("invalid_request")
    assertion = sign_owner_assertion(
        ctx.token,
        ctx.project.id,
        owner_id,
        ctx.target.publication_id,
        ctx.contract.contract_hash,
        body,
    )
    async with asyncio.timeout(RPC_TIMEOUT_SECONDS), _http_client() as client:
        async with client.stream(
            "POST",
            ctx.target.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                OWNER_HEADER: assertion,
            },
            follow_redirects=False,
        ) as response:
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise _error()
            status = response.status_code
    await _unchanged(session, ctx, owner_id)
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError):
        raise _error() from None
    if status != 200:
        if isinstance(parsed, dict) and set(parsed) == {"error"}:
            error = parsed["error"]
            if isinstance(error, dict) and set(error) == {"code", "message"}:
                code = error["code"]
                if isinstance(code, str) and code in _ERRORS and _ERRORS[code][0] == status:
                    raise _error(code)
        raise _error()
    return parsed


async def _describe(session: AsyncSession, ctx: _Context, owner_id: UUID) -> None:
    value = _Describe.model_validate(await _rpc(session, ctx, owner_id, {"op": "describe"}))
    contract = parse_management_contract(value.contract)
    if value.publication_id != ctx.target.publication_id or contract != ctx.contract:
        raise _error()


async def _configured(session: AsyncSession, project: Project) -> list[ManagementModule]:
    config = await session.get(MaxProjectConfig, project.id)
    if config is None:
        return []
    if config.owner_id != project.owner_id:
        raise _error()
    modules = config.config.get("management_modules")
    return validate_management_modules(modules) if modules is not None else []


async def get_overview(
    session: AsyncSession, project_id: UUID, owner_id: UUID
) -> ManagementOverviewPublic:
    project = await _owned(session, project_id, owner_id)
    configured: list[ManagementModule] = []
    ctx: _Context | None = None
    target: _Target | None = None
    contract: ManagementContract | None = None
    state = "unavailable"
    try:
        configured = await _configured(session, project)
        ctx = await _prepare(session, project, owner_id)
        target, contract = ctx.target, ctx.contract
        await _describe(session, ctx, owner_id)
        state = "active"
    except _State as exc:
        state = exc.state
        target, contract = exc.target, exc.contract
    except Exception:
        # Upstream exception text and source/credential contents never reach the owner.
        state = "unavailable"
    return ManagementOverviewPublic.model_validate(
        {
            "state": state,
            "configured_modules": configured,
            "active_modules": contract.modules if contract else [],
            "resources": contract.resources if contract and state == "active" else [],
            "publication_id": target.publication_id if target else None,
            "contract_hash": contract.contract_hash if contract else None,
        }
    )


async def _ready(
    session: AsyncSession, project: Project, owner_id: UUID, resource: ResourceId
) -> _Context:
    ctx = await _prepare(session, project, owner_id)
    if resource not in {entry.id for entry in ctx.contract.resources}:
        raise _error("unsupported")
    await _describe(session, ctx, owner_id)
    return ctx


async def list_records(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    query: OwnerRecordsQuery,
) -> OwnerRecordsPagePublic:
    project = await _owned(session, project_id, owner_id)
    try:
        ctx = await _ready(session, project, owner_id, query.resource)
        result = OwnerRecordsPagePublic.model_validate(
            await _rpc(
                session,
                ctx,
                owner_id,
                {
                    "op": "list",
                    "query": query.model_dump(mode="json"),
                },
            )
        )
        if any(record.resource != query.resource for record in result.records):
            raise _error()
        return result
    except _State as exc:
        raise _error("unsupported" if exc.state == "unsupported" else "unavailable") from None
    except ApiError:
        raise
    except Exception:
        raise _error() from None


async def update_record(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    record_id: UUID,
    resource: ResourceId,
    update: OwnerRecordUpdate,
) -> OwnerRecordPublic:
    project = await _owned(session, project_id, owner_id)
    try:
        ctx = await _ready(session, project, owner_id, resource)
        result = OwnerRecordPublic.model_validate(
            await _rpc(
                session,
                ctx,
                owner_id,
                {
                    "op": "update",
                    "record_id": str(record_id),
                    "resource": resource,
                    "update": update.model_dump(mode="json", exclude_unset=True),
                },
            )
        )
        if result.id != record_id or result.resource != resource:
            raise _error()
        return result
    except _State as exc:
        raise _error("unsupported" if exc.state == "unsupported" else "unavailable") from None
    except ApiError:
        raise
    except Exception:
        raise _error() from None
