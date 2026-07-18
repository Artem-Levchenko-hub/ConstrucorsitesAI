"""Свой домен — подключение домена пользователя к проекту.

Поток: (1) connect — сохраняем host + считаем нужный IP (наш VPS или IP чужого
VPS, если проект деплоится на свой сервер); отдаём DNS-инструкцию. (2) check —
резолвим A-запись, сравниваем с нужным IP. (3) issue — если DNS сошёлся, просим
оркестратор написать nginx-vhost host → контейнер проекта и выпустить SSL.
"""

from __future__ import annotations

import asyncio
import re
import socket
from uuid import UUID

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.custom_domain import CustomDomain
from omnia_api.models.deploy_target import DeployTarget
from omnia_api.models.project import Project
from omnia_api.schemas.domain import CustomDomainConnect, CustomDomainPublic
from omnia_api.services import orchestrator_client

router = APIRouter(prefix="/api/domains", tags=["domains"])

# Валидатор хоста (без схемы/пути): метки a-z0-9-, точки, TLD — либо ≥2 букв,
# либо punycode-форма IDN-зоны (xn--…, напр. .рф = xn--p1ai). Не-ASCII домены
# должны прийти уже в punycode.
_HOST_RE = re.compile(
    r"^(?=.{4,253}$)(?!-)([a-z0-9-]{1,63}\.)+(?:[a-z]{2,}|xn--[a-z0-9]+)$",
    re.IGNORECASE,
)


def _instructions(host: str, ip: str) -> str:
    return (
        f"У регистратора вашего домена создайте A-запись: "
        f"хост «{host}» → {ip}. Обычно запись обновляется за 5–30 минут. "
        f"Затем нажмите «Проверить»."
    )


def _to_public(d: CustomDomain) -> CustomDomainPublic:
    return CustomDomainPublic(
        id=d.id,
        project_id=d.project_id,
        host=d.host,
        source=d.source,
        expected_ip=d.expected_ip,
        dns_status=d.dns_status,
        cert_status=d.cert_status,
        last_detail=d.last_detail,
        created_at=d.created_at,
        verified_at=d.verified_at,
        dns_instructions=_instructions(d.host, d.expected_ip),
    )


async def _owned_project(session: SessionDep, user_id: UUID, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


async def _expected_ip(session: SessionDep, project: Project) -> str:
    """IP, на который должна указывать A-запись: наш VPS, либо IP чужого VPS,
    если проект деплоится на свой сервер."""
    if project.deploy_target_id is not None:
        target = await session.get(DeployTarget, project.deploy_target_id)
        if target is not None:
            return target.ssh_host
    return get_settings().our_public_ip


async def _resolve_a(host: str) -> list[str]:
    """A-записи хоста (IPv4). Пустой список — не резолвится."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET)
    except (socket.gaierror, OSError):
        return []
    return sorted({info[4][0] for info in infos})


@router.get("/{project_id}", response_model=list[CustomDomainPublic])
async def list_domains(
    project_id: UUID, user: CurrentUserDep, session: SessionDep
) -> list[CustomDomainPublic]:
    await _owned_project(session, user.id, project_id)
    rows = (
        await session.execute(
            select(CustomDomain)
            .where(CustomDomain.project_id == project_id)
            .order_by(CustomDomain.created_at.desc())
        )
    ).scalars().all()
    return [_to_public(d) for d in rows]


@router.post("", response_model=CustomDomainPublic, status_code=status.HTTP_201_CREATED)
async def connect_domain(
    payload: CustomDomainConnect, user: CurrentUserDep, session: SessionDep
) -> CustomDomainPublic:
    host = payload.host.strip().lower().rstrip(".")
    if not _HOST_RE.match(host):
        raise ApiError("domain_invalid", "Некорректный домен", status.HTTP_400_BAD_REQUEST)
    project = await _owned_project(session, user.id, payload.project_id)

    existing = (
        await session.execute(select(CustomDomain).where(CustomDomain.host == host))
    ).scalar_one_or_none()
    if existing is not None:
        raise ApiError(
            "domain_taken", "Этот домен уже подключён", status.HTTP_409_CONFLICT
        )

    domain = CustomDomain(
        project_id=project.id,
        host=host,
        source="external",
        expected_ip=await _expected_ip(session, project),
        dns_status="pending",
        cert_status="none",
    )
    session.add(domain)
    await session.commit()
    await session.refresh(domain)
    return _to_public(domain)


async def _owned_domain(session: SessionDep, user_id: UUID, domain_id: UUID) -> CustomDomain:
    domain = await session.get(CustomDomain, domain_id)
    if domain is None:
        raise ApiError("domain_not_found", "Домен не найден", status.HTTP_404_NOT_FOUND)
    await _owned_project(session, user_id, domain.project_id)
    return domain


@router.post("/{domain_id}/check", response_model=CustomDomainPublic)
async def check_domain(
    domain_id: UUID, user: CurrentUserDep, session: SessionDep
) -> CustomDomainPublic:
    domain = await _owned_domain(session, user.id, domain_id)
    ips = await _resolve_a(domain.host)
    if not ips:
        domain.dns_status = "pending"
        domain.last_detail = "A-запись пока не найдена — подождите распространения DNS."
    elif domain.expected_ip in ips:
        domain.dns_status = "ok"
        domain.last_detail = f"A-запись указывает на {domain.expected_ip} — можно выпускать SSL."
    else:
        domain.dns_status = "mismatch"
        domain.last_detail = (
            f"A-запись указывает на {', '.join(ips)}, а нужно на {domain.expected_ip}."
        )
    await session.commit()
    return _to_public(domain)


@router.post("/{domain_id}/issue", response_model=CustomDomainPublic)
async def issue_cert(
    domain_id: UUID, user: CurrentUserDep, session: SessionDep
) -> CustomDomainPublic:
    domain = await _owned_domain(session, user.id, domain_id)
    project = await _owned_project(session, user.id, domain.project_id)
    # Требуем сошедшийся DNS — иначе acme (HTTP-01) не пройдёт и мы просто
    # сожжём попытку у Let's Encrypt (rate-limit).
    ips = await _resolve_a(domain.host)
    if domain.expected_ip not in ips:
        domain.dns_status = "ok" if domain.expected_ip in ips else "mismatch"
        await session.commit()
        raise ApiError(
            "domain_dns_mismatch",
            "Сначала настройте A-запись на нужный IP и дождитесь проверки.",
            status.HTTP_409_CONFLICT,
        )
    domain.dns_status = "ok"
    domain.cert_status = "issuing"
    await session.commit()

    try:
        result = await orchestrator_client.publish_custom_domain(
            {"host": domain.host, "project_id": str(domain.project_id), "slug": project.slug}
        )
    except ApiError as exc:
        domain.cert_status = "failed"
        domain.last_detail = exc.message[:500]
        await session.commit()
        raise ApiError(
            "domain_cert_failed", exc.message[:300], status.HTTP_502_BAD_GATEWAY
        ) from exc

    ok = bool(result.get("ok"))
    domain.cert_status = "active" if ok else "failed"
    domain.last_detail = result.get("detail") or None
    if ok:
        from datetime import UTC, datetime

        domain.verified_at = datetime.now(UTC)
    await session.commit()
    return _to_public(domain)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: UUID, user: CurrentUserDep, session: SessionDep
) -> Response:
    domain = await _owned_domain(session, user.id, domain_id)
    await session.delete(domain)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
