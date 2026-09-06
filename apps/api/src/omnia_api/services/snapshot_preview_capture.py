"""Capture version images while the caller still holds the generation lease.

The renderer is intentionally synchronous with snapshot finalization: a deferred job
cannot prove that a live Project Cell still contains the selected commit. Source is
checked before and after browser work, and no bytes are published until both match.
Only Next's generated next-env.d.ts and TypeScript *.tsbuildinfo caches are ignored.
They describe tooling output, never product source. No current workspace writes occur.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from PIL import Image
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.minio import get_minio_client
from omnia_api.models.snapshot import Snapshot
from omnia_api.services.max_runtime_routes import resolve_max_runtime_probe_paths
from omnia_api.workers.preview import capture_live_url_report

if TYPE_CHECKING:
    from omnia_api.services.project_cell_executor import ProjectCellExecutorHandle

log = logging.getLogger(__name__)
_WIDTHS = (390, 1280)


def _canonical(files: Mapping[str, str]) -> dict[str, str]:
    return {
        path: content
        for path, content in files.items()
        if PurePosixPath(path).name != "next-env.d.ts" and not path.endswith(".tsbuildinfo")
    }


def _main_route(files: Mapping[str, str]) -> str:
    requested, _fallbacks = resolve_max_runtime_probe_paths(files)
    routes = set()
    for path, content in files.items():
        if not content.strip() or not path.startswith("src/app/"):
            continue
        parts = PurePosixPath(path).parts[2:]
        if not parts or parts[-1] not in {"page.tsx", "page.jsx", "page.js", "page.ts"}:
            continue
        if any("[" in part or part.startswith("@") for part in parts[:-1]):
            continue
        segments = [
            part for part in parts[:-1] if not (part.startswith("(") and part.endswith(")"))
        ]
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in segments):
            continue
        if segments and segments[0] in {"api", "legal", "support"}:
            continue
        routes.add("/" + "/".join(segments))
    if requested in routes:
        return requested
    if routes:
        return sorted(routes)[0]
    raise ValueError("No static product route to capture")


def _upload(key: str, png: bytes) -> None:
    # The projects bucket is private infrastructure; never change its ACL/policy.
    get_minio_client().put_object(
        get_settings().minio_bucket_projects,
        key,
        io.BytesIO(png),
        len(png),
        content_type="image/png",
    )


async def _persist(
    snapshot_id: UUID,
    project_id: UUID,
    commit_sha: str,
    status: str,
    manifest: list[dict[str, Any]],
) -> bool:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        result = await session.execute(
            update(Snapshot)
            .where(
                Snapshot.id == snapshot_id,
                Snapshot.project_id == project_id,
                Snapshot.commit_sha == commit_sha,
                or_(Snapshot.preview_status.is_(None), Snapshot.preview_status != "ready"),
            )
            .values(preview_status=status, preview_manifest=manifest, preview_commit_sha=commit_sha)
            .returning(Snapshot.id)
        )
        saved = result.scalar_one_or_none() is not None
        await session.commit()
        return saved


def _dimensions(png: bytes, width: int) -> tuple[int, int]:
    if len(png) > 16 * 1024 * 1024:
        raise ValueError("Screenshot exceeds storage budget")
    with Image.open(io.BytesIO(png)) as image:
        if image.format != "PNG" or image.width != width or not 1 <= image.height <= 40000:
            raise ValueError("Invalid screenshot dimensions")
        dimensions = image.size
        image.verify()
    return dimensions


async def capture_snapshot_frontend(
    snapshot_id: UUID,
    project_id: UUID,
    commit_sha: str,
    expected_files: dict[str, str],
    project_cell_handle: ProjectCellExecutorHandle,
) -> bool:
    """Save only provenance-checked PNGs; failure is separate from generation success.

    Caller must keep its generation lease through this await. Ready manifests are
    immutable, including on retries or later failures. Unreferenced private objects
    from a failed storage/DB commit are harmless and eligible for retention cleanup.
    Cancellation always propagates so lease/caller cancellation remains authoritative.
    """
    try:
        async with asyncio.timeout(100):
            if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
                raise ValueError("Invalid snapshot commit")
            expected = _canonical(dict(expected_files))
            refresh = project_cell_handle.refresh_snapshot_files
            if refresh is None:
                raise ValueError("Fresh source capture capability unavailable")
            before = _canonical(await refresh())
            if before != expected:
                raise ValueError("Source changed before screenshot")
            route = _main_route(expected)
            preview = await project_cell_handle.create_preview_session()
            parsed = urlsplit(preview.preview_url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise ValueError("Invalid signed preview origin")
            url = urljoin(preview.preview_url, route)
            report = await capture_live_url_report(
                url,
                _WIDTHS,
                height=900,
                full_page=True,
                bootstrap_url=preview.bootstrap_url,
                require_success_status=True,
            )
            after = _canonical(await refresh())
            if after != expected:
                raise ValueError("Source changed during screenshot")
            if report.issues or any(width not in report.screenshots for width in _WIDTHS):
                raise ValueError("Primary screenshot capture incomplete")
            manifest = []
            objects = []
            for width in _WIDTHS:
                png = report.screenshots[width]
                actual_width, height = _dimensions(png, width)
                digest = hashlib.sha256(png).hexdigest()
                key = f"snapshot-previews/{project_id}/{snapshot_id}/{digest}.png"
                manifest.append(
                    {"key": key, "width": actual_width, "height": height, "route": route}
                )
                objects.append((key, png))
            for key, png in objects:
                await asyncio.to_thread(_upload, key, png)
            return await _persist(snapshot_id, project_id, commit_sha, "ready", manifest)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning(
            "snapshot_frontend_capture_failed snapshot=%s kind=%s", snapshot_id, type(exc).__name__
        )
        try:
            await _persist(snapshot_id, project_id, commit_sha, "failed", [])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("snapshot_frontend_failure_status_not_saved snapshot=%s", snapshot_id)
        return False
