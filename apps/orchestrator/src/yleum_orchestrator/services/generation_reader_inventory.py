"""Privileged inventory is authority; application heartbeats can only deny it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.errors import OrchestratorError


class ReaderInventoryUnavailable(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__("workspace_busy", message, status_code=503)


PREFIX = "omnia:health:generation-readers:"
EPOCH_KEY = "omnia:generation-readers:epoch"
TTL = 30
API_CAPABILITIES = (
    "adaptation-cleanup:1",
    "adaptation-recovery:1",
    "context:1",
    "cursor:1",
    "dispatch:1",
    "effect:1",
    "provider-identity:1",
    "publication:1",
    "reader-standby:1",
    "recovery-state:1",
    "recovery-state:2",
    "response-manifest:1",
    "source-checkpoint:1",
    "tracked-command:1",
    "tracked-source-write:1",
)
CONTROLLER_CAPABILITIES = (
    "adaptation-cleanup:1",
    "adaptation-recovery:1",
    "reader-standby:1",
    "tracked-command:1",
    "tracked-source-write:1",
    "writer-barrier:1",
)
SLOTS = frozenset(
    {
        "core/api",
        "core/worker",
        "core/generation-worker",
        "core/orchestrator",
        "commerce/orchestrator",
    }
)
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(gt=0)]
Role = Literal["api", "worker", "generation-worker", "orchestrator"]


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ReaderIdentity(_Record):
    host: Literal["core", "commerce"]
    role: Role
    host_boot_id: UUID
    pid_namespace_inode: Positive
    pid: Positive
    start_ticks: Positive
    startup_nonce: UUID


class Registration(_Record):
    identity: ReaderIdentity
    release_sha: Sha
    capabilities: tuple[str, ...]


class ExpectedReader(_Record):
    slot: str
    host: Literal["core", "commerce"]
    role: Role


class ObservedReader(ReaderIdentity):
    slot: str
    host_pid: Positive
    container_id: Digest | None
    supervisor_id: Annotated[str, Field(min_length=1)]
    release_sha: Sha
    capabilities: tuple[str, ...]

    def registration(self) -> Registration:
        identity = ReaderIdentity.model_validate(
            {key: getattr(self, key) for key in ReaderIdentity.model_fields}
        )
        return Registration(
            identity=identity,
            release_sha=self.release_sha,
            capabilities=self.capabilities,
        )


class ReaderSnapshot(_Record):
    version: Literal[1]
    epoch: UUID
    state: Literal["ready", "deny"]
    release_sha: Sha
    observed_at_ms: Positive
    expires_at_ms: Positive
    redis_run_id: Sha
    expected: tuple[ExpectedReader, ...]
    observed: tuple[ObservedReader, ...]
    registered: tuple[Digest, ...]
    digest: Digest


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def incarnation(identity: ReaderIdentity) -> str:
    return digest(identity.model_dump(mode="json"))


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ReaderInventoryUnavailable("reader inventory has duplicate keys")
        result[key] = value
    return result


def _decode(raw: str | bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ReaderInventoryUnavailable("reader inventory is not an object")
    return value


def _canonical_uuid(value: object) -> None:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("noncanonical UUID")


def validate_registration(raw: str | bytes) -> Registration:
    value = _decode(raw)
    registration = Registration.model_validate_json(canonical(value))
    if value != registration.model_dump(mode="json"):
        raise ReaderInventoryUnavailable("reader registration is noncanonical")
    return registration


def validate_snapshot(raw: bytes, *, release_sha: str, now_ms: int) -> ReaderSnapshot:
    """Validate the complete immutable snapshot before consulting live Redis."""
    try:
        value = _decode(raw)
        if type(value.get("version")) is not int:
            raise ValueError("version")
        _canonical_uuid(value.get("epoch"))
        for item in value.get("observed", []):
            _canonical_uuid(item.get("host_boot_id"))
            _canonical_uuid(item.get("startup_nonce"))
        snapshot = ReaderSnapshot.model_validate_json(canonical(value))
        if (
            snapshot.state != "ready"
            or snapshot.release_sha != release_sha
            or not snapshot.observed_at_ms <= now_ms < snapshot.expires_at_ms
            or not 0 < snapshot.expires_at_ms - snapshot.observed_at_ms <= TTL * 1000
            or snapshot.digest != digest({k: v for k, v in value.items() if k != "digest"})
        ):
            raise ValueError("readiness")
        expected = {item.slot: (item.host, item.role) for item in snapshot.expected}
        observed = {item.slot: item for item in snapshot.observed}
        if (
            set(expected) != SLOTS
            or set(observed) != SLOTS
            or len(snapshot.expected) != len(SLOTS)
            or len(snapshot.observed) != len(SLOTS)
        ):
            raise ValueError("inventory")
        ids = []
        for slot, item in observed.items():
            caps = CONTROLLER_CAPABILITIES if item.role == "orchestrator" else API_CAPABILITIES
            if (
                expected[slot] != (item.host, item.role)
                or slot != f"{item.host}/{item.role}"
                or item.release_sha != release_sha
                or item.capabilities != caps
                or (item.role != "orchestrator" and item.container_id is None)
                or (item.role == "orchestrator" and item.container_id is not None)
            ):
                raise ValueError("capabilities")
            ids.append(incarnation(item.registration().identity))
        if tuple(sorted(ids)) != snapshot.registered or len(set(ids)) != len(ids):
            raise ValueError("registration set")
        return snapshot
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ReaderInventoryUnavailable("reader inventory is incompatible or stale") from exc


def _secure_stat(info: os.stat_result, *, directory: bool) -> None:
    mode = stat.S_ISDIR if directory else stat.S_ISREG
    if info.st_uid != 0 or info.st_mode & 0o022 or not mode(info.st_mode):
        raise ReaderInventoryUnavailable("reader inventory ownership is unsafe")


def read_snapshot(directory: str) -> bytes:
    """Open through a pinned directory FD; root mode alone is not read-only."""
    if not directory or not hasattr(os, "O_NOFOLLOW"):
        raise ReaderInventoryUnavailable("reader inventory is unavailable")
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _secure_stat(os.fstat(dir_fd), directory=True)
        if not os.fstatvfs(dir_fd).f_flag & os.ST_RDONLY:
            raise ReaderInventoryUnavailable("reader inventory mount is writable")
        fd = os.open("current.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        try:
            info = os.fstat(fd)
            _secure_stat(info, directory=False)
            # This bounds the fixed process inventory, never model/context output.
            if not 0 < info.st_size <= 131072:
                raise ReaderInventoryUnavailable("reader inventory size is invalid")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(131073)
            if len(raw) != info.st_size:
                raise ReaderInventoryUnavailable("reader inventory changed while reading")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def require_no_inhibit(directory: str) -> None:
    if not directory or not hasattr(os, "O_NOFOLLOW"):
        raise ReaderInventoryUnavailable("reader inventory is unavailable")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _secure_stat(os.fstat(fd), directory=True)
        if not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
            raise ReaderInventoryUnavailable("reader inventory mount is writable")
        try:
            os.stat("rollout-inhibit.json", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ReaderInventoryUnavailable("reader rollout is inhibited")
    finally:
        os.close(fd)


_OWN: Registration | None = None
_REVOKED = False
_REGISTRATION_LOCK = threading.Lock()
_REDIS: Any = None


def redis_client() -> Any:
    global _REDIS
    if _REDIS is None:
        from redis import Redis

        _REDIS = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
    return _REDIS


def own_registration() -> Registration:
    global _OWN
    if _OWN is not None and _OWN.identity.pid == os.getpid():
        return _OWN
    settings = get_settings()
    host = settings.generation_recovery_reader_host
    if host not in {"core", "commerce"}:
        raise ReaderInventoryUnavailable("controller reader host is not configured")
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    identity = ReaderIdentity.model_validate_json(
        canonical(
            {
                "host": host,
                "role": "orchestrator",
                "host_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "pid_namespace_inode": os.stat("/proc/self/ns/pid").st_ino,
                "pid": os.getpid(),
                "start_ticks": int(fields[19]),
                "startup_nonce": str(uuid4()),
            }
        )
    )
    _OWN = Registration(
        identity=identity,
        release_sha=settings.omnia_release_sha,
        capabilities=CONTROLLER_CAPABILITIES,
    )
    return _OWN


def require_readers() -> str:
    """Synchronous physical-boundary check, also usable in Docker worker threads."""
    if _REVOKED:
        raise ReaderInventoryUnavailable("controller startup failed; restart required")
    own = _OWN
    if own is None or own.identity.pid != os.getpid():
        raise ReaderInventoryUnavailable("controller incarnation is not registered")
    settings = get_settings()
    try:
        require_no_inhibit(settings.generation_recovery_inventory_dir)
        snapshot = validate_snapshot(
            read_snapshot(settings.generation_recovery_inventory_dir),
            release_sha=settings.omnia_release_sha,
            now_ms=time.time_ns() // 1_000_000,
        )
        redis = redis_client()
        if redis.info("server").get("run_id") != snapshot.redis_run_id or redis.get(
            EPOCH_KEY
        ) != str(snapshot.epoch):
            raise ReaderInventoryUnavailable("reader epoch changed")
        registration_deadline = float("inf")
        for observed in snapshot.observed:
            registration = observed.registration()
            key = PREFIX + incarnation(registration.identity)
            live = redis.get(key)
            before_ttl = time.monotonic()
            lifetime = redis.pttl(key)
            if live is None or type(lifetime) is not int or not 0 < lifetime <= TTL * 1000:
                raise ReaderInventoryUnavailable("complete reader registration is unconfirmed")
            registration_deadline = min(registration_deadline, before_ttl + lifetime / 1000)
            if validate_registration(live) != registration:
                raise ReaderInventoryUnavailable("complete reader registration is unconfirmed")
        if incarnation(own.identity) not in snapshot.registered:
            raise ReaderInventoryUnavailable("controller incarnation is not admitted")
        final_epoch = redis.get(EPOCH_KEY)
        final_run_id = redis.info("server").get("run_id")
        require_no_inhibit(settings.generation_recovery_inventory_dir)
        if (
            _REVOKED
            or time.monotonic() >= registration_deadline
            or time.time_ns() // 1_000_000 >= snapshot.expires_at_ms
            or final_epoch != str(snapshot.epoch)
            or final_run_id != snapshot.redis_run_id
        ):
            raise ReaderInventoryUnavailable("inventory changed during validation")
        return snapshot.digest
    except ReaderInventoryUnavailable:
        raise
    except Exception as exc:
        raise ReaderInventoryUnavailable("reader readiness is unavailable") from exc


def require_if_configured() -> None:
    """Legacy unconfigured OFF behavior stays unchanged; configured unknown denies."""
    if get_settings().generation_recovery_inventory_dir:
        require_readers()


def reader_dispatch[Result](call: Callable[..., Result], *args: Any, **kwargs: Any) -> Result:
    """Revalidate at the physical call, including after queueing a worker thread."""
    require_if_configured()
    return call(*args, **kwargs)


async def wait_ready() -> None:
    while True:
        try:
            await asyncio.to_thread(require_readers)
            return
        except ReaderInventoryUnavailable:
            await asyncio.sleep(1)


async def revoke_reader() -> None:
    """Keep health available, but never replay a partially failed startup."""
    global _REVOKED
    _REVOKED = True

    def invalidate() -> None:
        with _REGISTRATION_LOCK:
            redis = redis_client()
            redis.set(EPOCH_KEY, str(uuid4()))
            if _OWN is not None:
                redis.delete(PREFIX + incarnation(_OWN.identity))

    try:
        await asyncio.to_thread(invalidate)
    except Exception:
        # Local dispatch is already denied; registration cannot renew and expires.
        pass


@asynccontextmanager
async def reader_lifecycle() -> AsyncIterator[None]:
    """Register while health is available; callers defer effect callbacks until ready."""
    if not get_settings().generation_recovery_inventory_dir:
        yield
        return
    registration = own_registration()
    initialized_run_id: str | None = None

    async def heartbeat() -> None:
        nonlocal initialized_run_id
        while True:
            try:

                def publish() -> None:
                    nonlocal initialized_run_id
                    with _REGISTRATION_LOCK:
                        if _REVOKED:
                            return
                        redis = redis_client()
                        run_id = redis.info("server")["run_id"]
                        if initialized_run_id != run_id or redis.get(EPOCH_KEY) is None:
                            redis.set(EPOCH_KEY, str(uuid4()))
                            initialized_run_id = run_id
                        redis.set(
                            PREFIX + incarnation(registration.identity),
                            canonical(registration.model_dump(mode="json")),
                            ex=TTL,
                        )

                await asyncio.to_thread(publish)
            except Exception:
                pass
            await asyncio.sleep(5)

    task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
