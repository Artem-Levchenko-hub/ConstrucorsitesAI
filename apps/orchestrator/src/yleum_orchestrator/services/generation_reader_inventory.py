"""Privileged inventory is authority; application heartbeats can only deny it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Future
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import structlog
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
    "acceptance:2",
    "adaptation-cleanup:1",
    "adaptation-recovery:1",
    "context:1",
    "cursor:1",
    "dispatch:1",
    "effect:1",
    "provider-identity:1",
    "publication:1",
    "publication:2",
    "reader-standby:1",
    "recovery-state:1",
    "recovery-state:2",
    "recovery-state:3",
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


_TRANSIENT_READER_DENIALS = frozenset({
    "reader readiness is unavailable",
    "reader inventory is incompatible or stale",
    "reader epoch changed",
    "complete reader registration is unconfirmed",
    "inventory changed during validation",
})
_BUILD_ROLES = frozenset({"bootstrap", "fast_check", "full_build", "build", "test"})
_log = structlog.get_logger("yleum_orchestrator.reader_inventory")


@dataclass
class _DispatchWait:
    seconds: float
    owner: asyncio.Task[Any] | None
    remaining: Callable[[float], float]
    retry_deadline: float | None = None
    closed: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def retry_budget(self, now: float) -> float:
        with self.lock:
            if self.retry_deadline is None:
                self.retry_deadline = now + self.seconds
            return self.retry_deadline - now

    def within(self, deadline: float) -> bool:
        return not self.closed.is_set() and monotonic() < deadline

    def may_dispatch(self, deadline: float) -> bool:
        # Cancellation may arrive while require_if_configured reads Redis.
        # The final CPython task-state read must not schedule another callback.
        owner = self.owner
        return self.within(deadline) and (owner is None or not (owner.done() or owner.cancelling()))

    def active(self, deadline: float) -> bool:
        if not self.within(deadline):
            return False
        owner = self.owner
        if owner is None:
            return True
        # The blocking Docker thread must check cancellation on the owner's
        # loop even when an inner helper shields and drains that thread.
        reply: Future[bool] = Future()
        try:
            owner.get_loop().call_soon_threadsafe(
                lambda: reply.set_result(not owner.done() and not owner.cancelling())
            )
            active = reply.result(timeout=min(1.0, max(0.0, deadline - monotonic())))
        except (RuntimeError, TimeoutError):
            return False
        return active and self.within(deadline)


_DISPATCH_WAIT: ContextVar[_DispatchWait | None] = ContextVar("reader_dispatch_wait", default=None)
_WAIT_DEADLINE: ContextVar[float | None] = ContextVar("reader_dispatch_wait_deadline", default=None)


@contextmanager
def limit_reader_dispatch_wait(deadline: float) -> Iterator[None]:
    """Shorten only admission waiting; keep the Docker result and cleanup budget."""
    outer = _WAIT_DEADLINE.get()
    token = _WAIT_DEADLINE.set(deadline if outer is None else min(outer, deadline))
    try:
        yield
    finally:
        _WAIT_DEADLINE.reset(token)


@contextmanager
def wait_for_reader_dispatch(
    seconds: float, *, remaining: Callable[[float], float] | None = None,
) -> Iterator[None]:
    """Bound admission waits inside one locked command, not a replay of it."""
    if _DISPATCH_WAIT.get() is not None:
        yield
        return
    try:
        owner = asyncio.current_task()
    except RuntimeError:
        owner = None
    wait = _DispatchWait(seconds, owner, remaining or (lambda limit: limit))
    token = _DISPATCH_WAIT.set(wait)
    try:
        yield
    finally:
        wait.closed.set()
        _DISPATCH_WAIT.reset(token)


@contextmanager
def build_reader_dispatch_wait(
    role: str | None, *, remaining: Callable[[float], float], deadline: float,
) -> Iterator[None]:
    if role not in _BUILD_ROLES:
        yield
        return
    with wait_for_reader_dispatch(TTL, remaining=remaining), limit_reader_dispatch_wait(deadline):
        yield


def _wait_for_dispatch_retry(wait: _DispatchWait, seconds: float) -> None:
    wait.closed.wait(seconds)


def _recheck_dispatch(wait: _DispatchWait, denial: ReaderInventoryUnavailable) -> int:
    # Waiting is only supported in the blocking worker. A synchronous call on
    # the asyncio loop must still return its original denial immediately.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise denial
    now = monotonic()
    remaining = wait.retry_budget(now)
    if remaining <= 0:
        raise denial
    work_deadline = _WAIT_DEADLINE.get()
    if work_deadline is not None:
        remaining = min(remaining, work_deadline - now)
    if remaining <= 0:
        raise denial
    try:
        deadline = now + wait.remaining(remaining)
    except TimeoutError:
        raise denial from None
    _log.info("cell.reader_inventory_wait", reason=denial.message)
    for _ in range(TTL):
        if not wait.active(deadline):
            break
        _wait_for_dispatch_retry(wait, min(1.0, max(0.0, deadline - monotonic())))
        if not wait.active(deadline):
            break
        try:
            require_if_configured()
        except ReaderInventoryUnavailable as error:
            if error.message not in _TRANSIENT_READER_DENIALS:
                raise
            denial = error
        else:
            # No blocking loop handoff between the last guard and Docker.
            if wait.may_dispatch(deadline):
                return int((monotonic() - now) * 1000)
            break
    raise denial


def reader_dispatch[Result](call: Callable[..., Result], *args: Any, **kwargs: Any) -> Result:
    """Revalidate at the physical call, including after queueing a worker thread."""
    wait = _DISPATCH_WAIT.get()
    if wait is not None and wait.closed.is_set():
        raise ReaderInventoryUnavailable("reader command scope has ended")
    try:
        require_if_configured()
    except ReaderInventoryUnavailable as denial:
        if wait is None or denial.message not in _TRANSIENT_READER_DENIALS:
            raise
        wait_ms = _recheck_dispatch(wait, denial)
        try:
            return call(*args, **kwargs)
        finally:
            _log.info("cell.reader_inventory_restored", wait_ms=wait_ms)
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
