"""Privileged inventory is authority; application heartbeats can only deny it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from yleum_api.core.config import get_settings
from yleum_api.services.generation.recovery_contract import RecoveryContractError

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
            raise RecoveryContractError("reader inventory has duplicate keys")
        result[key] = value
    return result


def _decode(raw: str | bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise RecoveryContractError("reader inventory is not an object")
    return value


def _canonical_uuid(value: object) -> None:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("noncanonical UUID")


def validate_registration(raw: str | bytes) -> Registration:
    value = _decode(raw)
    registration = Registration.model_validate_json(canonical(value))
    if value != registration.model_dump(mode="json"):
        raise RecoveryContractError("reader registration is noncanonical")
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
        raise RecoveryContractError("reader inventory is incompatible or stale") from exc


def _secure_stat(info: os.stat_result, *, directory: bool) -> None:
    mode = stat.S_ISDIR if directory else stat.S_ISREG
    if info.st_uid != 0 or info.st_mode & 0o022 or not mode(info.st_mode):
        raise RecoveryContractError("reader inventory ownership is unsafe")


@contextmanager
def _inventory_directory(directory: str) -> Iterator[int]:
    """Pin root ownership and read-only mount before any inventory operation."""
    if not directory or not hasattr(os, "O_NOFOLLOW"):
        raise RecoveryContractError("reader inventory is unavailable")
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _secure_stat(os.fstat(dir_fd), directory=True)
        if not os.fstatvfs(dir_fd).f_flag & os.ST_RDONLY:
            raise RecoveryContractError("reader inventory mount is writable")
        yield dir_fd
    finally:
        os.close(dir_fd)


def require_no_rollout_inhibit(directory: str) -> None:
    with _inventory_directory(directory) as dir_fd:
        try:
            os.stat("rollout-inhibit.json", dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise RecoveryContractError("reader rollout is inhibited")


def read_snapshot(directory: str) -> bytes:
    """Open through a pinned directory FD; root mode alone is not read-only."""
    with _inventory_directory(directory) as dir_fd:
        fd = os.open("current.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        try:
            info = os.fstat(fd)
            _secure_stat(info, directory=False)
            # This bounds the fixed process inventory, never model/context output.
            if not 0 < info.st_size <= 131072:
                raise RecoveryContractError("reader inventory size is invalid")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(131073)
            if len(raw) != info.st_size:
                raise RecoveryContractError("reader inventory changed while reading")
            return raw
        finally:
            os.close(fd)


_OWN: Registration | None = None


def own_registration(role: str) -> Registration:
    global _OWN
    if _OWN is not None and _OWN.identity.pid == os.getpid():
        if _OWN.identity.role != role:
            raise RecoveryContractError("reader entrypoint role conflicts")
        return _OWN
    settings = get_settings()
    if role not in {"api", "worker", "generation-worker"} or (
        settings.generation_recovery_reader_role != role
    ):
        raise RecoveryContractError("reader role is not configured")
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    identity = ReaderIdentity.model_validate_json(
        canonical(
            {
                "host": "core",
                "role": role,
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
        capabilities=API_CAPABILITIES,
    )
    return _OWN


_CLIENT: ContextVar[Redis | None] = ContextVar("recovery_reader_redis", default=None)


@asynccontextmanager
async def reader_connection() -> AsyncIterator[Redis]:
    # The platform worker owns several event-loop threads. Never initialize or
    # share core.redis's process-global application pool from this new loop.
    existing = _CLIENT.get()
    if existing is not None:
        yield existing
        return
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


async def require_recovery_readers(
    *,
    release_sha: str | None = None,
    own: bool = True,
) -> str:
    """No Redis value can create a ready set; every member is checked each time."""
    settings = get_settings()
    target = release_sha or settings.omnia_release_sha
    try:
        async with asyncio.timeout(2):
            if own:
                await asyncio.to_thread(
                    require_no_rollout_inhibit, settings.generation_recovery_inventory_dir
                )
            raw = await asyncio.to_thread(read_snapshot, settings.generation_recovery_inventory_dir)
            snapshot = validate_snapshot(
                raw, release_sha=target, now_ms=time.time_ns() // 1_000_000
            )
            async with reader_connection() as redis:
                if (await redis.info("server")).get(
                    "run_id"
                ) != snapshot.redis_run_id or await redis.get(EPOCH_KEY) != str(snapshot.epoch):
                    raise RecoveryContractError("reader inventory epoch changed")
                registration_deadline = float("inf")
                for observed in snapshot.observed:
                    registration = observed.registration()
                    key = PREFIX + incarnation(registration.identity)
                    live = await redis.get(key)
                    before_ttl = time.monotonic()
                    lifetime = await redis.pttl(key)
                    if live is None or type(lifetime) is not int or not 0 < lifetime <= TTL * 1000:
                        raise RecoveryContractError("reader registration is missing or changed")
                    registration_deadline = min(registration_deadline, before_ttl + lifetime / 1000)
                    parsed = validate_registration(live)
                    if parsed != registration:
                        raise RecoveryContractError("reader registration is missing or changed")
                if own:
                    current = _OWN
                    if (
                        current is None
                        or current.identity.pid != os.getpid()
                        or current not in [item.registration() for item in snapshot.observed]
                    ):
                        raise RecoveryContractError("reader incarnation is not admitted")
                # Awaited reads may cross expiry/startup; check the fences again.
                epoch = await redis.get(EPOCH_KEY)
                redis_run_id = (await redis.info("server")).get("run_id")
            # Closing an owned Redis connection also awaits. No successful
            # return may bypass the final local fences after that cleanup.
            if own:
                await asyncio.to_thread(
                    require_no_rollout_inhibit, settings.generation_recovery_inventory_dir
                )
            if (
                epoch != str(snapshot.epoch)
                or redis_run_id != snapshot.redis_run_id
                or time.time_ns() // 1_000_000 >= snapshot.expires_at_ms
                or time.monotonic() >= registration_deadline
            ):
                raise RecoveryContractError("reader inventory expired during validation")
            return snapshot.digest
    except RecoveryContractError:
        raise
    except Exception as exc:
        raise RecoveryContractError("reader compatibility could not be confirmed") from exc


async def wait_for_readers(on_standby: Callable[[], Awaitable[None]] | None = None) -> None:
    if not get_settings().generation_recovery_inventory_dir:
        return
    while True:
        try:
            await require_recovery_readers()
            return
        except RecoveryContractError:
            if on_standby is not None:
                await on_standby()
            await asyncio.sleep(1)


@asynccontextmanager
async def reader_lifecycle(role: str, *, wait: bool = True) -> AsyncIterator[None]:
    """Unconfigured legacy deployment stays healthy; V2 gates still fail closed."""
    if not get_settings().generation_recovery_inventory_dir:
        yield
        return
    registration = own_registration(role)
    async with reader_connection() as redis:
        token = _CLIENT.set(redis)
        try:
            async with _registered_reader(registration, redis, wait=wait):
                yield
        finally:
            _CLIENT.reset(token)


@asynccontextmanager
async def _registered_reader(
    registration: Registration,
    redis: Redis,
    *,
    wait: bool,
) -> AsyncIterator[None]:
    await redis.set(EPOCH_KEY, str(uuid4()))

    async def heartbeat() -> None:
        while True:
            try:
                await redis.set(
                    PREFIX + incarnation(registration.identity),
                    canonical(registration.model_dump(mode="json")),
                    ex=TTL,
                )
            except Exception:
                pass  # Missing heartbeat denies; never synthesize readiness.
            await asyncio.sleep(5)

    task = asyncio.create_task(heartbeat())
    try:
        if wait:
            await wait_for_readers()
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
