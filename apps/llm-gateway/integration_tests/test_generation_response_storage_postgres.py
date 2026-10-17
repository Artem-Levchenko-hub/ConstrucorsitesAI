"""Product SDK + SQL proof against an explicitly owned isolated MinIO fixture."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import test_billing_settlement_postgres as fixture_source
from fastapi import FastAPI
from minio import Minio
from minio.credentials.providers import StaticProvider
from minio.minioadmin import MinioAdmin
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from test_generation_attempts_postgres import intent
from urllib3 import PoolManager, Timeout

from yleum_gateway.services.generation_response_store import (
    ChunkWriter,
    ResponseIndeterminate,
    canonical,
)

database_url = fixture_source.database_url
pool = fixture_source.pool


@pytest.fixture
async def private_storage(pool, database_url, monkeypatch):
    endpoint = os.environ.get("QA_RESPONSE_S3_ENDPOINT")
    if endpoint is None:
        pytest.skip("owned exact-image MinIO prerequisite; required physical runner supplies it")
    assert endpoint == "127.0.0.1:9000"  # Never an ambient/customer storage endpoint.
    from yleum_api.core import config as api_config
    from yleum_api.routers import generation_response_capacity as capacity_router
    from yleum_api.services.generation import response_capacity

    from yleum_gateway.core import config as gateway_config
    from yleum_gateway.core import db
    from yleum_gateway.routers import generation_responses
    from yleum_gateway.services import (
        generation_attempts,
        generation_response_ledger,
        provider_calls,
    )

    nonce = uuid4().hex
    bucket, principal = "task8-product-" + nonce, "task8" + nonce
    password = "synthetic-local-s3-" + nonce
    transport = PoolManager(timeout=Timeout(connect=2, read=5), retries=False)
    owner = Minio(
        endpoint,
        access_key="task8-local-root",
        secret_key="task8-local-root-synthetic-only",
        secure=False,
        http_client=transport,
    )
    admin = MinioAdmin(
        endpoint=endpoint,
        credentials=StaticProvider("task8-local-root", "task8-local-root-synthetic-only"),
        secure=False,
        http_client=transport,
    )
    budget = 8 * 1024 * 1024
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:GetBucketPolicy"],
                "Resource": ["arn:aws:s3:::" + bucket],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": ["arn:aws:s3:::" + bucket + "/*"],
            },
        ],
    }
    await asyncio.to_thread(owner.make_bucket, bucket)
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        await asyncio.to_thread(admin.user_add, principal, password)
        await asyncio.to_thread(admin.policy_add, principal, policy=policy)
        await asyncio.to_thread(admin.attach_policy, [principal], user=principal)
        await asyncio.to_thread(admin.bucket_quota_set, bucket, budget)
        raw_info = json.loads(await asyncio.to_thread(admin.info))
        raw_quota = json.loads(await asyncio.to_thread(admin.bucket_quota_get, bucket))
        # Allowlisted synthetic fixture diagnostics only: admin.info also carries
        # environment and endpoints, which must never enter captured test logs.
        print(
            "capacity_fixture",
            {
                "servers": [
                    {
                        "state": server.get("state"),
                        "drives": [
                            {
                                key: drive.get(key)
                                for key in ("state", "totalspace", "usedspace", "availspace")
                            }
                            for drive in server.get("drives", [])
                        ],
                    }
                    for server in raw_info.get("servers", [])
                ],
                "quota": {key: raw_quota.get(key) for key in ("quota", "quotatype")},
            },
        )
        settings = SimpleNamespace(
            generation_response_retention_enabled=True,
            generation_response_cleanup_enabled=True,
            generation_response_retention_seconds=604800,
            generation_response_storage_epoch=str(uuid4()),
            generation_response_storage_budget_bytes=budget,
            generation_response_capacity_min_free_bytes=0,
            generation_response_s3_bucket=bucket,
            generation_response_s3_endpoint=endpoint,
            generation_response_s3_secure=False,
            generation_response_s3_access_key=SecretStr(principal),
            generation_response_s3_secret_key=SecretStr(password),
            generation_response_capacity_url="http://api.test/api/_internal/generation-response-capacity",
            generation_response_signing_secret=SecretStr("synthetic-response-purpose-secret-32"),
            generation_response_capacity_signing_secret=SecretStr(
                "synthetic-capacity-purpose-secret-32"
            ),
            minio_endpoint=endpoint,
            minio_secure=False,
            minio_access_key="task8-local-root",
            minio_secret_key=SecretStr("task8-local-root-synthetic-only"),
        )
        monkeypatch.setattr(api_config, "get_settings", lambda: settings)
        monkeypatch.setattr(gateway_config, "get_settings", lambda: settings)
        monkeypatch.setattr(response_capacity, "get_settings", lambda: settings)
        monkeypatch.setattr(response_capacity, "get_engine", lambda: engine)

        async def checked_capacity(identity):
            try:
                return await response_capacity.current_capacity(identity)
            except Exception as exc:
                # Never emit the SDK exception or admin document (contains env).
                print("capacity_failure_type=" + type(exc).__name__)
                if isinstance(exc, ValueError):
                    print("capacity_validation=" + str(exc))
                raise

        monkeypatch.setattr(capacity_router, "current_capacity", checked_capacity)
        from yleum_gateway.services import generation_response_capture, generation_response_objects

        monkeypatch.setattr(generation_response_capture, "get_settings", lambda: settings)
        monkeypatch.setattr(generation_response_objects, "get_settings", lambda: settings)
        monkeypatch.setattr(generation_responses, "get_settings", lambda: settings)
        for module in (db, generation_attempts, generation_response_ledger, provider_calls):
            monkeypatch.setattr(module, "get_pool", lambda: pool)
        app = FastAPI()
        app.include_router(capacity_router.router)
        app.include_router(generation_responses.router)
        original = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kw: original(transport=httpx.ASGITransport(app), **kw)
        )
        yield settings, owner
    finally:
        # Exact random bucket/principal only, inside a daemon with no external network.
        for item in await asyncio.to_thread(
            lambda: list(owner.list_objects(bucket, recursive=True))
        ):
            await asyncio.to_thread(owner.remove_object, bucket, item.object_name)
        await asyncio.to_thread(owner.remove_bucket, bucket)
        await asyncio.to_thread(admin.user_remove, principal)
        await asyncio.to_thread(admin.policy_remove, principal)
        await engine.dispose()


async def test_real_capacity_sdk_sql_read_and_cleanup_chain(pool, monkeypatch, private_storage):
    from yleum_gateway.core.generation_response_auth import HEADER, mint_generation_response_token
    from yleum_gateway.services import (
        generation_attempts as attempts,
    )
    from yleum_gateway.services import (
        generation_response_cleanup as cleanup,
    )
    from yleum_gateway.services.generation_response_capture import preflight_response_store

    settings, owner = private_storage
    identity = await intent(pool)
    objects, ledger = await preflight_response_store(identity)
    call = await attempts.dispatch(
        identity,
        route="/v1/messages",
        model="claude-sonnet-5.5",
        user_id=identity.user_id,
        run_id=identity.run_id,
        project_id=identity.project_id,
        free=True,
    )
    # A synthetic transport receipt exercises the real committed journal owner.
    # No provider HTTP is performed and no customer wallet is touched.
    await attempts.finish(
        identity,
        call,
        actual_model="claude-sonnet-5.5",
        tokens_in=1,
        tokens_out=1,
        calculated_cost_rub=Decimal("0"),
    )
    body = {"choices": [{"finish_reason": "length", "message": {"content": "retained"}}]}
    envelope = canonical(
        {
            "version": 1,
            "format": "json",
            "identity": identity.to_wire(),
            "status": 200,
            "body": body,
        }
    )

    async def chunks():
        for offset in range(0, len(envelope), 11):
            yield envelope[offset : offset + 11]

    await ChunkWriter(identity.attempt_id, objects, ledger, chunk_bytes=64).store(
        chunks(), finish_reason="complete_json", route="/v1/messages"
    )
    await ledger.reconciled()
    async with httpx.AsyncClient() as client:
        result = await client.get(
            "http://gateway.test/_internal/generation-responses/" + str(identity.attempt_id),
            headers={
                HEADER: mint_generation_response_token(identity, purpose="generation_response.read")
            },
        )
    assert result.status_code == 200 and result.json()["response"]["body"] == body
    assert result.json()["response"]["body"]["choices"][0]["finish_reason"] == "length"
    monkeypatch.setattr(cleanup, "get_pool", lambda: pool)
    monkeypatch.setattr(cleanup, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_runs SET status='completed',finished_at=$2 WHERE id=$1",
            identity.run_id,
            now - timedelta(days=8),
        )
        await conn.execute(
            "UPDATE generation_responses SET reconciled_at=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            now - timedelta(days=8),
        )
    assert await cleanup.delete_expired_generation_responses(now) == 1
    assert (
        await asyncio.to_thread(
            lambda: list(owner.list_objects(settings.generation_response_s3_bucket, recursive=True))
        )
        == []
    )
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT receipt_hash FROM provider_calls WHERE id=$1", call)
        assert (
            await conn.fetchval(
                "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1",
                ledger.bucket,
            )
            == 0
        )


async def test_real_put_lost_ack_retains_sql_hold_and_never_certifies_body(pool, private_storage):
    from yleum_gateway.services.generation_response_capture import preflight_response_store
    from yleum_gateway.services.generation_response_store import load_generation_response

    settings, owner = private_storage
    identity = await intent(pool)
    objects, ledger = await preflight_response_store(identity)
    original = objects.put

    async def lost_ack(key, data):
        await original(key, data)
        raise OSError("synthetic acknowledgement lost after actual PUT")

    objects.put = lost_ack

    async def body():
        yield b"owned orphan after actual successful PUT"

    with pytest.raises(ResponseIndeterminate):
        await ChunkWriter(identity.attempt_id, objects, ledger).store(
            body(), finish_reason="stop", route="/v1/messages"
        )
    with pytest.raises(ResponseIndeterminate):
        await load_generation_response(identity, objects=objects)
    assert (
        len(
            await asyncio.to_thread(
                lambda: list(
                    owner.list_objects(settings.generation_response_s3_bucket, recursive=True)
                )
            )
        )
        == 1
    )
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT state FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
            )
            == "indeterminate"
        )
        assert (
            await conn.fetchval(
                "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1",
                ledger.bucket,
            )
            > 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
            )
            == 0
        )
