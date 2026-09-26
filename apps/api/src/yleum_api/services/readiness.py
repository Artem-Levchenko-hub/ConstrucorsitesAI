"""Public-safe dependency readiness and worker heartbeat."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import NamedTuple

import httpx
from sqlalchemy import text

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.core.minio import get_minio_client
from yleum_api.core.redis import get_redis
from yleum_api.core.release import normalize_release_sha

WORKER_HEARTBEAT_KEY = "omnia:health:worker"
_PROBE_TIMEOUT_SECONDS = 3


class ReadinessReport(NamedTuple):
    checks: dict[str, str]
    dependencies: dict[str, str]


def parse_worker_heartbeat(raw: bytes | str | None) -> tuple[bool, str]:
    if not raw:
        return False, "unknown"
    text_value = raw.decode() if isinstance(raw, bytes) else raw
    try:
        value = json.loads(text_value)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True, "unknown"
    release_sha = value.get("release_sha") if isinstance(value, dict) else None
    return True, normalize_release_sha(release_sha if isinstance(release_sha, str) else None)


def parse_worker_load(raw: bytes | str | None) -> str:
    """Загрузка воркера сборок в виде «идёт/разрешено», например «3/8».

    Старое сердцебиение этих полей не содержит, поэтому отсутствие — обычное
    дело, а не поломка: отвечаем «unknown» и ничего не ломаем.
    """

    if not raw:
        return "unknown"
    try:
        value = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "unknown"
    if not isinstance(value, dict):
        return "unknown"
    active, limit = value.get("active"), value.get("limit")
    if not isinstance(active, int) or not isinstance(limit, int):
        return "unknown"
    return f"{active}/{limit}"


def heartbeat_payload() -> str:
    return json.dumps(
        {
            "at": datetime.now(UTC).isoformat(),
            "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
        },
        separators=(",", ":"),
    )


async def write_worker_heartbeat(ttl_seconds: int, *, key: str = WORKER_HEARTBEAT_KEY) -> None:
    await get_redis().set(key, heartbeat_payload(), ex=max(ttl_seconds, 30))


async def run_worker_heartbeat_forever(
    *,
    key: str = WORKER_HEARTBEAT_KEY,
    interval_seconds: int | None = None,
) -> None:
    """Keep one heartbeat key alive from a dedicated thread/loop.

    Owns its Redis connection: the shared client is bound to whichever event
    loop first used it, and the RQ worker runs several loops in threads.
    """
    from collections.abc import Callable
    from typing import cast

    import redis.asyncio as aioredis

    settings = get_settings()
    interval = interval_seconds or max(settings.billing_lifecycle_poll_seconds // 2, 10)
    ttl = max(interval * 3, 30)
    from_url = cast(Callable[..., aioredis.Redis], aioredis.from_url)
    client = from_url(settings.redis_url, decode_responses=True)
    try:
        while True:
            try:
                await client.set(key, heartbeat_payload(), ex=ttl)
            except Exception:  # a missed beat is retried on the next pass, never fatal
                pass
            await asyncio.sleep(interval)
    finally:
        await client.aclose()


async def _database_ok() -> bool:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _redis_and_worker() -> tuple[bool, bool, str]:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            redis = get_redis()
            redis_ok = bool(await redis.ping())
            worker_ok, worker_release_sha = parse_worker_heartbeat(
                await redis.get(WORKER_HEARTBEAT_KEY)
            )
        return redis_ok, worker_ok, worker_release_sha
    except Exception:
        return False, False, "unknown"


async def _probe_orchestrator(base_url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base_url.rstrip('/')}/health")
        payload = response.json()
        healthy = (
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("status") == "ok"
        )
        raw_release = payload.get("release_sha") if isinstance(payload, dict) else None
        release_sha = normalize_release_sha(raw_release if isinstance(raw_release, str) else None)
        return healthy, release_sha
    except Exception:
        return False, "unknown"


async def _orchestrator_hosts_ok() -> dict[str, tuple[bool, str]]:
    """Every enabled orchestrator host by name (Phase 3 / stage B: cells on
    several hosts). Failing the registry itself counts as a failed default host."""
    from yleum_api.services.orchestrator_hosts import OrchestratorHostError, registry

    try:
        hosts = registry().enabled()
    except OrchestratorHostError:
        return {get_settings().default_orchestrator: (False, "unknown")}
    results = await asyncio.gather(*(_probe_orchestrator(host.url) for host in hosts))
    return {host.name: result for host, result in zip(hosts, results, strict=True)}


async def _deploy_control_plane_ok() -> tuple[bool, str]:
    """All hosts healthy; one common release, or "mixed" while a rollout is in flight."""
    hosts = await _orchestrator_hosts_ok()
    healthy = all(ok for ok, _ in hosts.values())
    releases = {sha for _, sha in hosts.values()}
    return healthy, (releases.pop() if len(releases) == 1 else "mixed")


async def _preview_storage_ok() -> bool:
    def _probe() -> bool:
        settings = get_settings()
        client = get_minio_client()
        return client.bucket_exists(settings.minio_bucket_projects) and client.bucket_exists(
            settings.minio_bucket_previews
        )

    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            return await asyncio.to_thread(_probe)
    except Exception:
        return False


async def probe_readiness() -> ReadinessReport:
    (
        database_ok,
        (redis_ok, worker_ok, worker_release_sha),
        (deploy_ok, orchestrator_release_sha),
        preview_ok,
    ) = await asyncio.gather(
        _database_ok(),
        _redis_and_worker(),
        _deploy_control_plane_ok(),
        _preview_storage_ok(),
    )
    generation_ok, generation_release = True, "unknown"
    generation_load = "unknown"
    if get_settings().use_generation_worker:
        try:
            async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
                raw_generation = await get_redis().get("omnia:health:generation-worker")
            generation_ok, generation_release = parse_worker_heartbeat(raw_generation)
            generation_load = parse_worker_load(raw_generation)
        except Exception:
            generation_ok = False
    # The billing tick (renewals, open-order reconciliation) beats under its own
    # key wherever it runs — the RQ worker thread or the commerce cluster. It is
    # reported, not gated: a paused lifecycle delays renewals, it does not break
    # the API, and the standalone worker has its own probe for that.
    billing_ok, billing_release = False, "unknown"
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            billing_ok, billing_release = parse_worker_heartbeat(
                await get_redis().get("omnia:health:billing-worker")
            )
    except Exception:
        billing_ok = False
    return ReadinessReport(
        checks={
            **(
                {"generation_worker": "ok" if generation_ok else "failed"}
                if get_settings().use_generation_worker
                else {}
            ),
            "database": "ok" if database_ok else "failed",
            "redis": "ok" if redis_ok else "failed",
            "worker": "ok" if worker_ok else "failed",
            "deploy_control_plane": "ok" if deploy_ok else "failed",
            "preview_storage": "ok" if preview_ok else "failed",
        },
        dependencies={
            **(
                {
                    "generation_worker_release_sha": generation_release,
                    # «3/8» — сколько сборок идёт и сколько разрешено. Это ответ
                    # на вопрос «предел одновременных сборок мешает или нет»:
                    # пока второе число заметно больше первого, поднимать его
                    # незачем, а равенство подолгу означает, что упёрлись.
                    "generation_worker_load": generation_load,
                }
                if get_settings().use_generation_worker
                else {}
            ),
            "worker_release_sha": worker_release_sha,
            "orchestrator_release_sha": orchestrator_release_sha,
            "billing_worker": "ok" if billing_ok else "missing",
            "billing_worker_release_sha": billing_release,
        },
    )
