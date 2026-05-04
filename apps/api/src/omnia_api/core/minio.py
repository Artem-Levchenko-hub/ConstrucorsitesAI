from functools import lru_cache

from minio import Minio

from omnia_api.core.config import get_settings


@lru_cache(maxsize=1)
def get_minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key.get_secret_value(),
        secure=s.minio_secure,
    )


def preview_public_url(preview_key: str | None) -> str | None:
    if not preview_key:
        return None
    s = get_settings()
    base = s.minio_public_url.rstrip("/")
    return f"{base}/{s.minio_bucket_previews}/{preview_key}"
