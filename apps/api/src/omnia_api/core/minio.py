from __future__ import annotations

import io
import json
import logging
from functools import lru_cache
from typing import Any

from minio import Minio
from minio.error import S3Error

from omnia_api.core.config import get_settings

log = logging.getLogger(__name__)

_EXE_CONTENT_TYPE = "application/vnd.microsoft.portable-executable"


@lru_cache(maxsize=1)
def get_minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key.get_secret_value(),
        secure=s.minio_secure,
    )


def ensure_public_bucket(client: Minio, bucket: str) -> None:
    """Create ``bucket`` if absent and (re)assert an anonymous ``s3:GetObject``
    policy so its objects load by their plain public URL (no presign).

    Self-heals the previews bucket: minio-init only ``set download``s it, never
    ``mb``s it, so when the app lazily created ``previews`` the bucket stayed
    PRIVATE — every snapshot thumbnail + project-card photo 403'd forever with no
    repair (owner report 2026-07-18). Re-asserting on every upload fixes that the
    same way ``agent_media._ensure_video_bucket`` self-heals the video bucket.

    GetObject-only (NO ``ListBucket``): the ``exe/*`` artifacts that share the
    previews bucket stay unlistable — only fetchable by their exact unguessable
    key. Fail-soft (R-10): a policy hiccup is logged, never raised — the upload
    still proceeds (the bucket may already be public from minio-init)."""
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except S3Error as exc:
        log.warning("minio: bucket-ensure failed %s err=%r", bucket, exc)
        return
    policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )
    try:
        client.set_bucket_policy(bucket, policy)
    except S3Error as exc:
        log.warning("minio: public-policy re-assert failed %s err=%r", bucket, exc)


def preview_public_url(preview_key: str | None) -> str | None:
    if not preview_key:
        return None
    s = get_settings()
    base = s.minio_public_url.rstrip("/")
    return f"{base}/{s.minio_bucket_previews}/{preview_key}"


# ── Exe-build artifacts ──────────────────────────────────────────────────────
# Stored in the previews bucket under an `exe/` prefix so they reuse the
# existing bucket without a new provisioning step. Keys are never exposed as
# public MinIO URLs — the api's download endpoint streams them owner-scoped.

def _exe_key(project_id: str, build_id: str, filename: str) -> str:
    return f"exe/{project_id}/{build_id}/{filename}"


def put_exe_artifacts(
    project_id: str,
    build_id: str,
    name: str,
    setup_bytes: bytes,
    exe_bytes: bytes,
) -> dict[str, Any]:
    """Upload Setup.exe and bare .exe to MinIO; return delivery metadata.

    URLs point at the api's owner-scoped download endpoint rather than public
    MinIO URLs — exe artifacts are never world-readable. `size` is the combined
    byte length of both blobs (the metric the caller publishes in exe.ready).
    """
    s = get_settings()
    bucket = s.minio_bucket_previews
    client = get_minio_client()

    setup_key = _exe_key(project_id, build_id, f"{name}-Setup.exe")
    exe_key = _exe_key(project_id, build_id, f"{name}.exe")

    client.put_object(
        bucket, setup_key, io.BytesIO(setup_bytes), len(setup_bytes),
        content_type=_EXE_CONTENT_TYPE,
    )
    if exe_bytes:
        client.put_object(
            bucket, exe_key, io.BytesIO(exe_bytes), len(exe_bytes),
            content_type=_EXE_CONTENT_TYPE,
        )

    # Build the api-relative download path; the absolute URL is assembled by the
    # caller or the frontend from the api base. We return a relative path so the
    # worker stays decoupled from the public hostname.
    setup_url = f"/api/projects/{project_id}/exe/{build_id}/setup"
    exe_url = f"/api/projects/{project_id}/exe/{build_id}/exe" if exe_bytes else None

    return {
        "setup_url": setup_url,
        "exe_url": exe_url,
        "name": name,
        "size": len(setup_bytes) + len(exe_bytes),
    }


def get_exe_object(
    project_id: str, build_id: str, artifact: str
) -> tuple[Any, int] | None:
    """Fetch an exe artifact stream from MinIO.

    `artifact` must be ``"setup"`` or ``"exe"``. Returns ``(stream, size)``
    suitable for ``StreamingResponse``, or ``None`` when the object is absent
    (e.g. the build never stored the bare .exe).
    """
    s = get_settings()
    bucket = s.minio_bucket_previews
    client = get_minio_client()

    # Determine MinIO key from the logical artifact name.
    # We need the *app* name embedded in the key, but at download time we only
    # know project_id/build_id/artifact. We use a glob-style prefix scan to
    # find the exact key (there is at most one .exe and one Setup.exe per build).
    prefix = f"exe/{project_id}/{build_id}/"
    suffix = "-Setup.exe" if artifact == "setup" else ".exe"

    try:
        objects = list(client.list_objects(bucket, prefix=prefix))
    except S3Error:
        return None

    # Find the key whose name ends with the right suffix. For the bare .exe we
    # must exclude the Setup file (it also ends in .exe).
    key: str | None = None
    for obj in objects:
        obj_name = obj.object_name or ""
        if artifact == "setup" and obj_name.endswith("-Setup.exe"):
            key = obj_name
            break
        elif artifact == "exe" and obj_name.endswith(".exe") and not obj_name.endswith("-Setup.exe"):
            key = obj_name
            break

    if key is None:
        return None

    try:
        response = client.get_object(bucket, key)
        size = int(response.headers.get("Content-Length", 0))
        return response, size
    except S3Error:
        return None
