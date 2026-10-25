"""Owner-scoped font acquisition, immutable storage and snapshot materialization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.font_asset import FontAsset
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.task_board import TaskBoardAttachmentCleanup
from yleum_api.schemas.message import FontSelection
from yleum_api.services.font_catalog_sources import family_id, fetch_family
from yleum_api.services.owner_fonts_source import apply_owner_fonts, requested_font_names
from yleum_api.services.private_attachment_quota import (
    PRIVATE_BUCKET_BYTES_LIMIT,
    lock_private_attachment_quota,
    private_attachment_bytes,
)
from yleum_api.services.reference_images import _storage, _store

MAX_BYTES = 8 * 1024 * 1024
MAX_BUNDLE = 4 * 1024 * 1024
MAX_TOTAL = 12 * 1024 * 1024


def _validate(raw: bytes) -> dict[str, Any]:
    if not 0 < len(raw) <= MAX_BYTES:
        raise ApiError("validation_failed", "Допустимы TTF, OTF, WOFF и WOFF2 до 8 МБ", 422)
    # The parser has no API credentials, inherited handles, home or network
    # configuration. OTS and FontTools run with process-local resource budgets.
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8"}
    if os.name == "nt":
        environment["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    try:
        with tempfile.TemporaryDirectory(prefix="yleum-font-run-") as directory:
            result = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).with_name("font_validation_worker.py"))],
                input=raw,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=18,
                check=False,
                env=environment,
                cwd=directory,
                close_fds=True,
            )
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError("font validation")
        return dict(json.loads(result.stdout))
    except (subprocess.SubprocessError, ValueError, OSError, TypeError):
        raise ApiError(
            "validation_failed",
            "Шрифт повреждён, не поддерживается или превышает лимит обработки. "
            "Загрузите другой TTF, OTF, WOFF или WOFF2.",
            422,
        ) from None


def public_font(row: FontAsset) -> dict[str, Any]:
    metadata = row.metadata_json
    return {
        "id": str(row.id),
        "family": row.family,
        "filename": row.filename,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "weight": metadata.get("weight", 400),
        "style": metadata.get("style", "normal"),
        "source": metadata["source"],
        "license": metadata.get("license"),
    }


async def _save(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    filename: str,
    bundle: dict[str, Any],
    metadata: dict[str, Any],
) -> FontAsset:
    raw = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_BUNDLE:
        raise ApiError("validation_failed", "Слишком большой набор начертаний шрифта", 422)
    await lock_private_attachment_quota(session)
    used = int(
        await session.scalar(
            select(func.coalesce(func.sum(FontAsset.size_bytes), 0)).where(
                FontAsset.uploader_id == user_id
            )
        )
        or 0
    )
    count = int(
        await session.scalar(
            select(func.count())
            .select_from(FontAsset)
            .where(FontAsset.project_id == project_id, FontAsset.status != "deleting")
        )
        or 0
    )
    if (
        used + len(raw) > 64 * 1024 * 1024
        or count >= 64
        or await private_attachment_bytes(session) + len(raw) > PRIVATE_BUCKET_BYTES_LIMIT
    ):
        raise ApiError("too_large", "Лимит загруженных шрифтов исчерпан", 413)
    identifier = uuid4()
    row = FontAsset(
        id=identifier,
        project_id=project_id,
        uploader_id=user_id,
        object_key=f"fonts/{project_id}/{identifier}.json",
        family=bundle["family"],
        filename=Path(filename.replace("\\", "/")).name[:150],
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        metadata_json=metadata,
        status="pending",
        is_bound=False,
    )
    session.add(row)
    await session.commit()  # Unknown PUT outcomes remain charged and reclaimable.
    await session.refresh(row, with_for_update=True)
    try:
        await asyncio.to_thread(_store, row.object_key, raw, "application/json")
        row.status = "ready"
        await session.commit()
    except Exception:
        await session.rollback()
        raise ApiError(
            "upload_failed", "Не удалось сохранить шрифт. Повторите загрузку", 503
        ) from None
    return row


async def create_font(
    session: AsyncSession, project_id: UUID, user_id: UUID, raw: bytes, filename: str
) -> FontAsset:
    face = await asyncio.to_thread(_validate, raw)
    metadata = {
        "source": "upload",
        "license": None,
        "weight": int(str(face["weight"]).split()[0]),
        "style": face["style"],
    }
    bundle = {
        "family": face.pop("family"),
        "license_text": face.pop("license_text"),
        "faces": [face],
    }
    return await _save(session, project_id, user_id, filename, bundle, metadata)


async def resolve_named_font(
    session: AsyncSession, project_id: UUID, user_id: UUID, name: str
) -> FontAsset:
    slug = family_id(name)
    existing = await session.scalar(
        select(FontAsset)
        .where(
            FontAsset.project_id == project_id,
            FontAsset.uploader_id == user_id,
            FontAsset.filename == f"{slug}.fontsource",
            FontAsset.metadata_json["source"].astext == "fontsource",
            FontAsset.status == "ready",
        )
        .order_by(FontAsset.created_at)
        .limit(1)
    )
    if existing is not None:
        return existing
    package = await asyncio.to_thread(fetch_family, name)
    faces = []
    for original in package["faces"]:
        face = await asyncio.to_thread(_validate, original["raw"])
        face.pop("family")
        face.pop("license_text")
        face["unicode_range"] = original["unicode_range"]
        faces.append(face)
    bundle = {"family": package["family"], "faces": faces, "license_text": package["license_text"]}
    metadata = {key: package[key] for key in ("package", "version", "integrity", "license")}
    metadata.update(source="fontsource", weight=400, style="normal")
    return await _save(session, project_id, user_id, f"{slug}.fontsource", bundle, metadata)


async def resolve_selections(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    selections: list[FontSelection],
    *,
    bind: bool = False,
) -> list[dict[str, Any]]:
    ids = [item.asset_id for item in selections]
    if len(ids) > 6 or len({(item.asset_id, item.role) for item in selections}) != len(ids):
        raise ApiError("validation_failed", "Не более шести разных шрифтов и ролей", 422)
    if not ids:
        return []
    query = (
        select(FontAsset)
        .join(Project)
        .where(
            FontAsset.id.in_(ids),
            FontAsset.project_id == project_id,
            FontAsset.uploader_id == user_id,
            FontAsset.status == "ready",
            Project.owner_id == user_id,
        )
    )
    if bind:
        query = query.with_for_update(of=FontAsset)
    rows = {row.id: row for row in (await session.scalars(query)).all()}
    if set(rows) != set(ids):
        raise ApiError("not_found", "Шрифт не найден. Загрузите его повторно", 404)
    if sum(rows[key].size_bytes for key in set(ids)) > MAX_TOTAL:
        raise ApiError("validation_failed", "Общий размер выбранных шрифтов превышает лимит", 422)
    result = []
    for item in selections:
        row = rows[item.asset_id]
        if bind and not row.is_bound:
            row.bound_at = datetime.now(UTC)
        result.append({**public_font(row), "role": item.role})
    return result


async def bind_message_fonts(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    bindings: list[dict[str, Any]],
) -> None:
    """Permanent retention belongs to the same transaction as the user Message."""
    if not bindings:
        return
    selected = [FontSelection(asset_id=UUID(item["id"]), role=item["role"]) for item in bindings]
    current = await resolve_selections(session, project_id, user_id, selected, bind=True)
    for binding, actual in zip(bindings, current, strict=True):
        if binding["sha256"] != actual["sha256"]:
            raise ApiError("conflict", "Шрифт изменился до сохранения сообщения", 409)
        row = await session.get(FontAsset, UUID(binding["id"]))
        assert row is not None
        row.is_bound = True


async def pin_named_request(
    session: AsyncSession, project_id: UUID, user_id: UUID, prompt: str
) -> list[dict[str, Any]]:
    selections = []
    # Separate storage transaction cannot commit/drop admission's project lock.
    async with AsyncSession(bind=session.bind, expire_on_commit=False) as storage_session:
        for name, role in requested_font_names(prompt):
            row = await resolve_named_font(storage_session, project_id, user_id, name)
            selections.append(FontSelection(asset_id=row.id, role=role))
    return await resolve_selections(session, project_id, user_id, selections, bind=True)


def _load_bundle(row: FontAsset) -> dict[str, Any]:
    client, bucket = _storage()
    response = client.get_object(bucket, row.object_key)
    try:
        raw = response.read(MAX_BUNDLE + 1)
    finally:
        response.close()
        response.release_conn()
    if len(raw) != row.size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
        raise ApiError("conflict", "Сохранённый шрифт не прошёл проверку целостности", 409)
    return dict(json.loads(raw))


async def message_font_bundles(
    session: AsyncSession, project_id: UUID, user_id: UUID, message_id: UUID
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    message = await session.get(Message, message_id)
    if message is None or message.project_id != project_id or message.role != "user":
        raise ApiError("not_found", "Сообщение не найдено", 404)
    bindings = (message.font_selections or []) + (message.resolved_fonts or [])
    result = []
    for binding in bindings:
        rows = await resolve_selections(
            session,
            project_id,
            user_id,
            [FontSelection(asset_id=UUID(binding["id"]), role=binding["role"])],
        )
        if rows[0]["sha256"] != binding["sha256"]:
            raise ApiError("conflict", "Шрифт изменился", 409)
        row = await session.get(FontAsset, UUID(binding["id"]))
        assert row is not None
        result.append((binding, await asyncio.to_thread(_load_bundle, row)))
    return result


async def enqueue_expired_fonts(session: AsyncSession, limit: int) -> None:
    stale = func.now() - timedelta(hours=24)
    rows = (
        await session.scalars(
            select(FontAsset)
            .where(
                or_(
                    and_(FontAsset.status == "pending", FontAsset.created_at < stale),
                    and_(
                        FontAsset.status == "ready",
                        or_(
                            FontAsset.project_id.is_(None),
                            FontAsset.uploader_id.is_(None),
                            and_(
                                FontAsset.is_bound.is_(False),
                                func.coalesce(FontAsset.bound_at, FontAsset.created_at) < stale,
                            ),
                        ),
                    ),
                )
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for row in rows:
        row.status = "deleting"
        session.add(TaskBoardAttachmentCleanup(object_key=row.object_key, size=row.size_bytes))
    await session.flush()


__all__ = [
    "apply_owner_fonts",
    "bind_message_fonts",
    "create_font",
    "enqueue_expired_fonts",
    "message_font_bundles",
    "pin_named_request",
    "public_font",
    "resolve_named_font",
    "resolve_selections",
]
