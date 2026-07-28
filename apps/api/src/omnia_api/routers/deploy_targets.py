"""BYO-VPS — управление своими серверами как целями деплоя.

Пользователь добавляет свой VPS (по SSH-ключу или логину+паролю), проверяет
подключение и затем может выбрать его как цель публикации проекта (вместо нашего
хостинга). Секреты шифруются «сильным» ключом (core.crypto.encrypt_strong) и
наружу не отдаются.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status
from sqlalchemy import or_, select

from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.ssh_keys import generate_ssh_keypair
from omnia_api.models.deploy_target import DeployTarget
from omnia_api.models.project import Project
from omnia_api.schemas.deploy_target import (
    DeployTargetCreate,
    DeployTargetPublic,
    DeployTargetUpdate,
    DeployTargetVerifyResult,
)
from omnia_api.services import orchestrator_client

router = APIRouter(prefix="/api/deploy-targets", tags=["deploy-targets"])


def _to_public(t: DeployTarget) -> DeployTargetPublic:
    return DeployTargetPublic(
        id=t.id,
        label=t.label,
        ssh_host=t.ssh_host,
        ssh_port=t.ssh_port,
        ssh_user=t.ssh_user,
        auth_type=t.ssh_auth_type,
        has_secret=bool(t.ssh_secret_enc),
        ssh_public_key=t.ssh_public_key,
        verify_status=t.verify_status,
        verify_detail=t.verify_detail,
        host_fingerprint=t.host_fingerprint,
        resolved_ip=t.resolved_ip,
        capabilities=t.capabilities,
        verified_at=t.verified_at,
        created_at=t.created_at,
    )


async def _owned_target(session: SessionDep, user_id: UUID, target_id: UUID) -> DeployTarget:
    target = await session.get(DeployTarget, target_id)
    if target is None or target.owner_id != user_id:
        raise ApiError("deploy_target_not_found", "VPS не найден", status.HTTP_404_NOT_FOUND)
    return target


@router.get("", response_model=list[DeployTargetPublic])
async def list_targets(user: CurrentUserDep, session: SessionDep) -> list[DeployTargetPublic]:
    rows = (
        (
            await session.execute(
                select(DeployTarget)
                .where(DeployTarget.owner_id == user.id)
                .order_by(DeployTarget.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_public(t) for t in rows]


@router.post("", response_model=DeployTargetPublic, status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: DeployTargetCreate, user: CurrentUserDep, session: SessionDep
) -> DeployTargetPublic:
    public_key: str | None = None
    if payload.auth_type == "password":
        if not payload.secret:
            raise ApiError("validation_failed", "Нужен пароль SSH", status.HTTP_400_BAD_REQUEST)
        secret_plain = payload.secret
    else:
        # Режим ключа: юзер либо приносит свой приватный ключ, либо мы генерим
        # пару и отдаём публичный, чтобы он добавил его на сервер.
        if payload.secret:
            secret_plain = payload.secret
        else:
            secret_plain, public_key = generate_ssh_keypair(comment=f"omnia-{payload.ssh_host}")

    target = DeployTarget(
        owner_id=user.id,
        label=payload.label,
        ssh_host=payload.ssh_host,
        ssh_port=payload.ssh_port,
        ssh_user=payload.ssh_user,
        ssh_auth_type=payload.auth_type,
        ssh_secret_enc=encrypt_strong(secret_plain),
        ssh_public_key=public_key,
        verify_status="unverified",
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return _to_public(target)


@router.patch("/{target_id}", response_model=DeployTargetPublic)
async def update_target(
    target_id: UUID,
    payload: DeployTargetUpdate,
    user: CurrentUserDep,
    session: SessionDep,
) -> DeployTargetPublic:
    """Rotate credentials or edit connection data and force re-verification."""
    target = await _owned_target(session, user.id, target_id)
    changed_credentials = False
    changed_host_identity = False
    mapping = {
        "label": "label",
        "ssh_host": "ssh_host",
        "ssh_port": "ssh_port",
        "ssh_user": "ssh_user",
        "auth_type": "ssh_auth_type",
    }
    for source, destination in mapping.items():
        value = getattr(payload, source)
        if value is not None:
            setattr(target, destination, value)
            changed_credentials = changed_credentials or source != "label"
            changed_host_identity = changed_host_identity or source in {"ssh_host", "ssh_port"}
    if payload.secret is not None:
        target.ssh_secret_enc = encrypt_strong(payload.secret)
        changed_credentials = True
    if changed_credentials:
        target.verify_status = "unverified"
        target.verify_detail = None
        target.verified_at = None
        target.capabilities = None
    if changed_host_identity:
        target.known_host_key = None
        target.host_fingerprint = None
        target.resolved_ip = None
    await session.commit()
    await session.refresh(target)
    return _to_public(target)


@router.post("/{target_id}/verify", response_model=DeployTargetVerifyResult)
async def verify_target(
    target_id: UUID,
    user: CurrentUserDep,
    session: SessionDep,
    confirm_host_key: bool = False,
) -> DeployTargetVerifyResult:
    target = await _owned_target(session, user.id, target_id)
    creds = {
        "host": target.ssh_host,
        "port": target.ssh_port,
        "user": target.ssh_user,
        "auth_type": target.ssh_auth_type,
        # The discovery request must not forward credentials at all. They are
        # decrypted only after the owner explicitly trusts the fingerprint.
        "secret": decrypt_strong(target.ssh_secret_enc) if confirm_host_key else "",
        "known_host_key": target.known_host_key if confirm_host_key else None,
        "resolved_ip": target.resolved_ip if confirm_host_key else None,
    }
    try:
        result = await orchestrator_client.verify_deploy_target(creds)
    except ApiError as exc:
        target.verify_status = "failed"
        target.verify_detail = exc.message[:500]
        await session.commit()
        return DeployTargetVerifyResult(ok=False, verify_status="failed", detail=exc.message[:500])

    ok = bool(result.get("ok"))
    requires_confirmation = bool(result.get("requires_confirmation"))
    target.verify_status = (
        "ok" if ok else "pending_confirmation" if requires_confirmation else "failed"
    )
    target.verify_detail = result.get("detail") or None
    if result.get("host_key"):
        target.known_host_key = result["host_key"]
    if result.get("host_fingerprint"):
        target.host_fingerprint = result["host_fingerprint"]
    if result.get("resolved_ip"):
        target.resolved_ip = result["resolved_ip"]
    if result.get("capabilities"):
        target.capabilities = result["capabilities"]
    if ok:
        from datetime import UTC, datetime

        target.verified_at = datetime.now(UTC)
    await session.commit()
    return DeployTargetVerifyResult(
        ok=ok,
        verify_status=target.verify_status,
        detail=target.verify_detail,
        docker_ok=bool(result.get("docker_ok")),
        docker_version=result.get("docker_version"),
        requires_confirmation=requires_confirmation,
        host_fingerprint=result.get("host_fingerprint"),
        resolved_ip=result.get("resolved_ip"),
        capabilities=result.get("capabilities"),
    )


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(target_id: UUID, user: CurrentUserDep, session: SessionDep) -> Response:
    target = await _owned_target(session, user.id, target_id)
    # Проекты, ссылающиеся на эту цель, вернутся на наш хостинг (FK SET NULL).
    # Явно занулим, чтобы это было очевидно и в рамках одной транзакции.
    projects = (
        (
            await session.execute(
                select(Project).where(
                    or_(
                        Project.deploy_target_id == target_id,
                        Project.previous_deploy_target_id == target_id,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if projects and target.known_host_key and target.resolved_ip:
        creds = {
            "host": target.ssh_host,
            "port": target.ssh_port,
            "user": target.ssh_user,
            "auth_type": target.ssh_auth_type,
            "secret": decrypt_strong(target.ssh_secret_enc),
            "known_host_key": target.known_host_key,
            "resolved_ip": target.resolved_ip,
        }
        for project in projects:
            await orchestrator_client.teardown_remote_project(project.id, creds)
    for p in projects:
        if p.deploy_target_id == target_id:
            p.deploy_target_id = None
        if p.previous_deploy_target_id == target_id:
            p.previous_deploy_target_id = None
    await session.delete(target)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
