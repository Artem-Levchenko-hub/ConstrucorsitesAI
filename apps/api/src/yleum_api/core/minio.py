from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

from minio import Minio

from yleum_api.core.config import get_settings

log = logging.getLogger(__name__)

# S3 resource wildcards: fixed-length writer keys, with no unrestricted '*'.
_UUID_KEY = "????????-????-????-????-????????????"
_CONTENT_HASH = "?" * 32
_IMAGE_SUFFIXES = ("jpg", "png", "webp", "gif")
_LEGACY_IMAGE_SUFFIXES = frozenset((*_IMAGE_SUFFIXES, "jpeg"))
_RESERVED_LEGACY_TOKENS = frozenset(
    {
        "source",
        "sources",
        "src",
        "archive",
        "archives",
        "exe",
        "executables",
        "export",
        "exports",
        "repo",
        "repos",
        "backup",
        "backups",
        "zip",
        "tar",
        "gz",
        "tgz",
        "bz2",
        "xz",
        "7z",
        "rar",
        "zst",
        "msi",
        "dll",
        "py",
        "js",
        "ts",
        "tsx",
        "jsx",
        "json",
        "sql",
        "env",
        "sh",
        "html",
        "htm",
        "svg",
        "wasm",
        "map",
    }
)


def _legacy_public_media_keys(raw: str, allowed_buckets: set[str]) -> dict[str, set[str]]:
    """Validate operator-approved literal keys without inspecting stored objects.

    No key, bucket input or raw configuration is included in validation errors.
    Exact resources grant no sibling key access; namespace/UUID shapes are not
    inferred from unproven historical observations.
    """
    invalid = "invalid legacy public media configuration"
    if not raw.strip():
        return {}
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError(invalid) from None
    if not isinstance(document, dict):
        raise ValueError(invalid)
    result: dict[str, set[str]] = {}
    for bucket, keys in document.items():
        if bucket not in allowed_buckets or not isinstance(keys, list):
            raise ValueError(invalid)
        approved: set[str] = set()
        for key in keys:
            # Accepted paths are ASCII below, so character and byte limits agree.
            if not isinstance(key, str) or not 0 < len(key) <= 1024:
                raise ValueError(invalid)
            segments = key.split("/")
            if any(
                segment in {"", ".", ".."}
                or re.fullmatch(r"[A-Za-z0-9_.-]+", segment) is None
                or any(
                    token.lower() in _RESERVED_LEGACY_TOKENS
                    for token in re.split(r"[._-]", segment)
                )
                for segment in segments
            ):
                raise ValueError(invalid)
            if (
                "." not in segments[-1]
                or segments[-1].rsplit(".", 1)[1].lower() not in _LEGACY_IMAGE_SUFFIXES
            ):
                raise ValueError(invalid)
            approved.add(key)
        result[bucket] = approved
    return result


@lru_cache(maxsize=1)
def get_minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key.get_secret_value(),
        secure=s.minio_secure,
    )


def _public_media_keys() -> dict[str, set[str]]:
    """Only configured public buckets; never publish private storage aliases."""
    settings = get_settings()
    roles = (
        (
            settings.minio_bucket_previews,
            # Historical writer contract: previews/{snapshot_id}.png.
            {f"{_UUID_KEY}.png"},
        ),
        (
            settings.minio_bucket_images,
            {f"{_UUID_KEY}/{_CONTENT_HASH}.png"}
            | {f"max-content/{_UUID_KEY}/{_CONTENT_HASH}.{ext}" for ext in _IMAGE_SUFFIXES},
        ),
        (settings.minio_bucket_photos, {f"{_UUID_KEY}/{_CONTENT_HASH}.jpg"}),
        (settings.minio_bucket_videos, {f"{_UUID_KEY}/{_CONTENT_HASH}.mp4"}),
    )
    private = {
        settings.minio_bucket_projects,
        settings.minio_bucket_backups,
        settings.minio_bucket_task_board,
    }
    result: dict[str, set[str]] = {}
    for bucket, keys in roles:
        if bucket in private:
            raise ValueError("public media bucket aliases private storage")
        result.setdefault(bucket, set()).update(keys)
    legacy = _legacy_public_media_keys(
        settings.minio_public_legacy_media_keys,
        {settings.minio_bucket_images, settings.minio_bucket_photos},
    )
    for bucket, keys in legacy.items():
        result[bucket].update(keys)
    return result


def public_media_bucket_policy(bucket: str) -> str:
    """Replace broad anonymous access with approved media keys only.

    Archive/source keys (including historical exe/*) receive no anonymous Allow.
    No explicit Deny is installed: authenticated repository, publication and DR
    clients retain their own permissions. This grants neither listing nor writes.
    """
    keys = _public_media_keys()
    if bucket not in keys:
        raise ValueError("bucket is not configured for public media")
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/{key}" for key in sorted(keys[bucket])],
                }
            ],
        }
    )


def public_media_read_boundary_map() -> str:
    """Render the public nginx read allowlist from the same approved media keys.

    Bucket policies govern anonymous reads, not root/IAM presigned requests.
    The public /minio/ edge must therefore reject every other object path,
    independently of query signatures or Authorization headers. Internal S3
    clients do not traverse that edge. This only prepares text; it performs no
    storage calls and neither installs nor reloads nginx.

    nginx's normalized $uri is also used by its URI-bearing proxy_pass. The
    anchored ASCII patterns reject remaining percent escapes after one decode,
    including double-encoded slashes. No query parameter grants path access.
    """
    lines = ["map $uri $yleum_public_media_readable {", "    default 0;"]
    for bucket, keys in sorted(_public_media_keys().items()):
        if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is None:
            raise ValueError("invalid public media boundary configuration")
        for key in sorted(keys):
            if re.fullmatch(r"[A-Za-z0-9_./?-]+", key) is None:
                raise ValueError("invalid public media boundary configuration")
            # Current writer placeholders match one ASCII path character, never
            # '/' or a remaining '%' escape; literal legacy keys have no '?'.
            pattern = "".join(
                "[A-Za-z0-9_.-]" if char == "?" else re.escape(char)
                for char in f"/minio/{bucket}/{key}"
            )
            lines.append(f"    ~^{pattern}$ 1;")
    lines.append("}")
    return "\n".join(lines) + "\n"


def ensure_public_bucket(client: Minio, bucket: str) -> None:
    """Create a configured media bucket and strictly reassert its read policy.

    Policy errors propagate: an upload must not silently retain an old policy
    that exposes source archives. Validate aliases before any storage operation.
    """
    policy = public_media_bucket_policy(bucket)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.set_bucket_policy(bucket, policy)


def reconcile_public_bucket_policies(client: Minio | None = None) -> None:
    """Repair existing policies before serving, including buckets without uploads.

    This changes bucket configuration only, never reads, lists, alters or deletes
    objects. Missing public buckets are prepared for image/photo writers that do
    not set a policy themselves. Failures deliberately block API startup.
    """
    buckets = _public_media_keys()  # Check every private collision first.
    client = client if client is not None else get_minio_client()
    for bucket in buckets:
        ensure_public_bucket(client, bucket)
        log.info("minio: reconciled public media policy bucket=%s", bucket)


def preview_public_url(preview_key: str | None) -> str | None:
    if not preview_key:
        return None
    s = get_settings()
    base = s.minio_public_url.rstrip("/")
    return f"{base}/{s.minio_bucket_previews}/{preview_key}"
