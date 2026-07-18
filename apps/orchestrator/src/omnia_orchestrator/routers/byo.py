"""BYO-VPS + свой домен — внутренние эндпоинты оркестратора.

`/internal/deploy-targets/verify` — зайти на VPS пользователя по SSH и проверить
Docker (креды приходят от apps/api в теле запроса, канал за X-Internal-Token).
`/internal/domains/publish` — написать nginx-vhost для чужого домена → контейнер
проекта и выпустить Let's Encrypt, переиспользуя nginx_writer.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Header
from pydantic import BaseModel

from omnia_orchestrator.core import ssh
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import container_status
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services import nginx_writer

log = structlog.get_logger("omnia_orchestrator.byo")

router = APIRouter(prefix="/internal", tags=["byo"])


def _verify_token(token: str | None) -> None:
    expected = get_settings().internal_token.get_secret_value()
    if not token or token != expected:
        raise OrchestratorError(
            code="unauthorized",
            message="missing or invalid X-Internal-Token",
            status_code=401,
        )


class VerifyTargetRequest(BaseModel):
    host: str
    port: int = 22
    user: str
    auth_type: str
    secret: str


class VerifyTargetResponse(BaseModel):
    ok: bool
    detail: str | None = None
    docker_ok: bool = False
    docker_version: str | None = None
    host_key: str | None = None


@router.post("/deploy-targets/verify", response_model=VerifyTargetResponse)
async def verify_target(
    payload: VerifyTargetRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> VerifyTargetResponse:
    _verify_token(x_internal_token)
    result = await ssh.verify_target(
        host=payload.host,
        port=payload.port,
        user=payload.user,
        auth_type=payload.auth_type,
        secret=payload.secret,
    )
    log.info(
        "byo.verify_target",
        host=payload.host,
        ok=result.get("ok"),
        docker_ok=result.get("docker_ok"),
    )
    return VerifyTargetResponse(
        ok=bool(result.get("ok")),
        detail=result.get("detail"),
        docker_ok=bool(result.get("docker_ok")),
        docker_version=result.get("docker_version"),
        host_key=result.get("host_key"),
    )


class PublishDomainRequest(BaseModel):
    host: str
    project_id: str
    slug: str


class PublishDomainResponse(BaseModel):
    ok: bool
    cert_status: str
    detail: str | None = None


async def _project_port(slug: str) -> int | None:
    """Порт живого контейнера проекта: сперва прод, потом dev."""
    for name in (f"omnia-app-{slug}", f"omnia-dev-{slug}"):
        status = await container_status(name)
        if status.get("state") == "running" and status.get("port"):
            try:
                return int(status["port"])
            except (TypeError, ValueError):
                continue
    return None


@router.post("/domains/publish", response_model=PublishDomainResponse)
async def publish_domain(
    payload: PublishDomainRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> PublishDomainResponse:
    _verify_token(x_internal_token)
    port = await _project_port(payload.slug)
    if port is None:
        return PublishDomainResponse(
            ok=False,
            cert_status="failed",
            detail="Проект не запущен — сначала опубликуйте его, затем подключайте домен.",
        )
    try:
        # HTTP-блок → сразу отвечает и способен пройти acme http-01.
        await nginx_writer.publish_http(payload.host, port)
        # Выпуск сертификата + переключение на HTTPS (fail-soft внутри).
        live = await nginx_writer.ensure_tls(payload.host, port)
    except OrchestratorError as exc:
        log.warning("byo.publish_domain_failed", host=payload.host, err=exc.message)
        return PublishDomainResponse(ok=False, cert_status="failed", detail=exc.message[:300])

    if live:
        return PublishDomainResponse(
            ok=True, cert_status="active", detail=f"Домен {payload.host} подключён по HTTPS."
        )
    return PublishDomainResponse(
        ok=False,
        cert_status="failed",
        detail="Сайт открыт по HTTP, но SSL пока не выпустился — проверьте A-запись и повторите.",
    )
