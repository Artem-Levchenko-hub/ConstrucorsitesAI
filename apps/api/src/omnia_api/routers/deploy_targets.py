"""BYO-VPS — управление своими серверами как целями деплоя.

Пользователь добавляет свой VPS (по SSH-ключу или логину+паролю), проверяет
подключение и затем может выбрать его как цель публикации проекта (вместо нашего
хостинга). Секреты шифруются «сильным» ключом (core.crypto.encrypt_strong) и
наружу не отдаются.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.ssh_keys import generate_ssh_keypair
from omnia_api.models.deploy_target import DeployTarget
from omnia_api.models.project import Project
from omnia_api.schemas.deploy_target import (
    DeployTargetCreate,
    DeployTargetPublic,
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
        verified_at=t.verified_at,
        created_at=t.created_at,
    )


async def _owned_target(session: SessionDep, user_id: UUID, target_id: UUID) -> DeployTarget:
    target = await session.get(DeployTarget, target_id)
    if target is None or target.owner_id != user_id:
        raise ApiError(
            "deploy_target_not_found", "VPS не найден", status.HTTP_404_NOT_FOUND
        )
    return target


@router.get("", response_model=list[DeployTargetPublic])
async def list_targets(user: CurrentUserDep, session: SessionDep) -> list[DeployTargetPublic]:
    rows = (
        await session.execute(
            select(DeployTarget)
            .where(DeployTarget.owner_id == user.id)
            .order_by(DeployTarget.created_at.desc())
        )
    ).scalars().all()
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
            secret_plain, public_key = generate_ssh_keypair(
                comment=f"omnia-{payload.ssh_host}"
            )

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


@router.post("/{target_id}/verify", response_model=DeployTargetVerifyResult)
async def verify_target(
    target_id: UUID, user: CurrentUserDep, session: SessionDep
) -> DeployTargetVerifyResult:
    target = await _owned_target(session, user.id, target_id)
    creds = {
        "host": target.ssh_host,
        "port": target.ssh_port,
        "user": target.ssh_user,
        "auth_type": target.ssh_auth_type,
        "secret": decrypt_strong(target.ssh_secret_enc),
    }
    try:
        result = await orchestrator_client.verify_deploy_target(creds)
    except ApiError as exc:
        target.verify_status = "failed"
        target.verify_detail = exc.message[:500]
        await session.commit()
        return DeployTargetVerifyResult(
            ok=False, verify_status="failed", detail=exc.message[:500]
        )

    ok = bool(result.get("ok"))
    target.verify_status = "ok" if ok else "failed"
    target.verify_detail = (result.get("detail") or None)
    if result.get("host_key"):
        target.known_host_key = result["host_key"]
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
    )


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: UUID, user: CurrentUserDep, session: SessionDep
) -> Response:
    target = await _owned_target(session, user.id, target_id)
    # Проекты, ссылающиеся на эту цель, вернутся на наш хостинг (FK SET NULL).
    # Явно занулим, чтобы это было очевидно и в рамках одной транзакции.
    projects = (
        await session.execute(
            select(Project).where(Project.deploy_target_id == target_id)
        )
    ).scalars().all()
    for p in projects:
        p.deploy_target_id = None
    await session.delete(target)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
