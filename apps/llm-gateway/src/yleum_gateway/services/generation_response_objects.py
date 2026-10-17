"""Scoped S3 only. No bucket creation, policy mutation, admin credentials or URLs."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

from yleum_gateway.core.config import get_settings
from yleum_gateway.services.generation_response_store import CHUNK_BYTES, ResponseIndeterminate


class PrivateObjects:
    def __init__(self) -> None:
        from minio import Minio
        from urllib3 import PoolManager, Timeout

        settings = get_settings()
        if (
            not settings.generation_response_s3_access_key
            or not settings.generation_response_s3_secret_key
        ):
            raise ResponseIndeterminate("private response storage is not configured")
        self.bucket = settings.generation_response_s3_bucket
        self.client: Any = Minio(
            settings.generation_response_s3_endpoint,
            access_key=settings.generation_response_s3_access_key.get_secret_value(),
            secret_key=settings.generation_response_s3_secret_key.get_secret_value(),
            secure=settings.generation_response_s3_secure,
            http_client=PoolManager(timeout=Timeout(connect=2, read=5, total=10), retries=False),
        )

    def _private(self) -> None:
        from minio.error import S3Error

        if not self.client.bucket_exists(self.bucket):
            raise ResponseIndeterminate("private response bucket unavailable")
        try:
            policy = json.loads(self.client.get_bucket_policy(self.bucket))
        except S3Error as exc:
            if exc.code == "NoSuchBucketPolicy":
                return
            raise
        if (
            not isinstance(policy, dict)
            or not isinstance(policy.get("Statement"), list)
            or any(
                not isinstance(item, dict) or item.get("Effect") != "Deny"
                for item in policy["Statement"]
            )
        ):
            raise ResponseIndeterminate("response bucket privacy is unconfirmed")

    async def preflight(self) -> None:
        await asyncio.to_thread(self._private)

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self.client.put_object, self.bucket, key, io.BytesIO(data), len(data)
        )

    def _get(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            data: bytes = response.read(CHUNK_BYTES + 1)
            if len(data) > CHUNK_BYTES:
                raise ResponseIndeterminate("invalid stored chunk size")
            return data
        finally:
            response.close()
            response.release_conn()

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get, key)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, key)
