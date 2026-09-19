from __future__ import annotations

import json
import logging
from functools import lru_cache

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
