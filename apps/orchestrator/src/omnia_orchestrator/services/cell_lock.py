"""Cross-process workspace lock for Project Cell mutations."""

from __future__ import annotations

import asyncio
import errno
import os
import re
import stat
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from omnia_orchestrator.core.cell_resources import WorkspaceLockTimeout, WorkspaceLockUnavailable

_LOCK_FILE_MODE = 0o600
_LOCK_DIR_MODE = 0o700
_LOCK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


@dataclass(frozen=True, slots=True)
class FileLockOwnerToken:
    backend: str
    fd: int


class OSFileLockBackend(Protocol):
    def try_acquire(self, fd: int) -> FileLockOwnerToken | None: ...

    def release(self, owner: FileLockOwnerToken) -> None: ...


class FcntlFileLockBackend:
    def __init__(self) -> None:
        self._owners: set[int] = set()

    def try_acquire(self, fd: int) -> FileLockOwnerToken | None:
        try:
            import fcntl as fcntl_module
        except ImportError as exc:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable") from exc
        fcntl_any = cast(Any, fcntl_module)
        try:
            fcntl_any.flock(fd, fcntl_any.LOCK_EX | fcntl_any.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return None
            raise
        self._owners.add(fd)
        return FileLockOwnerToken(backend="fcntl", fd=fd)

    def release(self, owner: FileLockOwnerToken) -> None:
        if owner.backend != "fcntl" or owner.fd not in self._owners:
            raise ValueError("foreign file lock owner token")
        import fcntl as fcntl_module

        fcntl_any = cast(Any, fcntl_module)
        fcntl_any.flock(owner.fd, fcntl_any.LOCK_UN)
        self._owners.remove(owner.fd)


class MsvcrtFileLockBackend:
    def __init__(self) -> None:
        self._owners: set[int] = set()

    def try_acquire(self, fd: int) -> FileLockOwnerToken | None:
        try:
            import msvcrt as msvcrt_module
        except ImportError as exc:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable") from exc
        msvcrt_any = cast(Any, msvcrt_module)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt_any.locking(fd, msvcrt_any.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EDEADLOCK} or getattr(exc, "winerror", None) in {
                33,
                36,
            }:
                return None
            raise
        self._owners.add(fd)
        return FileLockOwnerToken(backend="msvcrt", fd=fd)

    def release(self, owner: FileLockOwnerToken) -> None:
        if owner.backend != "msvcrt" or owner.fd not in self._owners:
            raise ValueError("foreign file lock owner token")
        import msvcrt as msvcrt_module

        msvcrt_any = cast(Any, msvcrt_module)
        os.lseek(owner.fd, 0, os.SEEK_SET)
        msvcrt_any.locking(owner.fd, msvcrt_any.LK_UNLCK, 1)
        self._owners.remove(owner.fd)


class WorkspaceOperationLock:
    def __init__(
        self,
        root: str | Path,
        *,
        backend: OSFileLockBackend | None = None,
        acquire_timeout_seconds: float = 10.0,
        retry_interval_seconds: float = 0.05,
    ) -> None:
        if acquire_timeout_seconds <= 0:
            raise ValueError("acquire_timeout_seconds must be positive")
        if retry_interval_seconds <= 0 or retry_interval_seconds > acquire_timeout_seconds:
            raise ValueError("retry_interval_seconds must be positive and <= timeout")
        self.root = Path(root)
        resolved_backend = backend or _default_backend()
        if resolved_backend is None:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")
        self.backend: OSFileLockBackend = resolved_backend
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self._process_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def hold(self, workspace_id: UUID) -> AsyncIterator[None]:
        async with self.hold_named(f"workspace-{workspace_id}"):
            yield

    @asynccontextmanager
    async def hold_named(self, name: str) -> AsyncIterator[None]:
        if _LOCK_NAME_RE.fullmatch(name) is None:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")
        deadline = time.monotonic() + self.acquire_timeout_seconds
        lock_path = self.root / "locks" / f"{name}.lock"
        _ensure_secure_dir(lock_path.parent, create=True)
        local_lock = self._process_locks.setdefault(str(lock_path), asyncio.Lock())
        acquired_local = False
        fd: int | None = None
        owner: FileLockOwnerToken | None = None
        primary_error: BaseException | None = None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WorkspaceLockTimeout("workspace_lock_timeout")
        try:
            try:
                await asyncio.wait_for(local_lock.acquire(), timeout=remaining)
            except TimeoutError as exc:
                raise WorkspaceLockTimeout("workspace_lock_timeout") from exc
            acquired_local = True
            fd = self._open_lock_file(lock_path)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkspaceLockTimeout("workspace_lock_timeout")
                owner = await self._attempt_acquire(fd)
                if owner is not None:
                    break
                await asyncio.sleep(min(self.retry_interval_seconds, remaining))
            try:
                yield
            except BaseException as exc:
                primary_error = exc
                raise
        finally:
            if owner is not None:
                try:
                    await asyncio.shield(asyncio.to_thread(self.backend.release, owner))
                except Exception:
                    if primary_error is None:
                        raise
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    if primary_error is None:
                        raise
            if acquired_local:
                local_lock.release()

    async def _attempt_acquire(self, fd: int) -> FileLockOwnerToken | None:
        attempt = asyncio.create_task(asyncio.to_thread(self.backend.try_acquire, fd))
        try:
            return await asyncio.shield(attempt)
        except asyncio.CancelledError as exc:
            owner: FileLockOwnerToken | None = None
            try:
                owner = await asyncio.shield(attempt)
            except Exception:
                raise exc from None
            finally:
                if owner is not None:
                    try:
                        await asyncio.shield(asyncio.to_thread(self.backend.release, owner))
                    except Exception:
                        pass
            raise

    @staticmethod
    def _open_lock_file(path: Path) -> int:
        _ensure_secure_dir(path.parent, create=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            fd = os.open(path, flags, _LOCK_FILE_MODE)
        except OSError as exc:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable") from exc
        try:
            _validate_lock_fd(fd)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
        except Exception as exc:
            os.close(fd)
            if isinstance(exc, WorkspaceLockUnavailable):
                raise
            raise WorkspaceLockUnavailable("workspace_lock_unavailable") from exc
        return fd


def _default_backend() -> OSFileLockBackend | None:
    if os.name == "nt":
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            return None
        return MsvcrtFileLockBackend()
    try:
        import fcntl  # noqa: F401
    except ImportError:
        return None
    return FcntlFileLockBackend()


def _ensure_secure_dir(path: Path, *, create: bool) -> None:
    anchor = Path(path.anchor) if path.anchor else Path(".")
    current = anchor
    for part in path.parts[len(anchor.parts) :]:
        current = current / part
        if current.exists():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise WorkspaceLockUnavailable("workspace_lock_unavailable")
            if stat.S_ISDIR(info.st_mode) is False:
                raise WorkspaceLockUnavailable("workspace_lock_unavailable")
            if current == path:
                _validate_dir_stat(info)
            else:
                _validate_ancestor_dir_stat(info)
            continue
        if create is False:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")
        current.mkdir(mode=_LOCK_DIR_MODE)
        try:
            os.chmod(current, _LOCK_DIR_MODE)
        except OSError:
            pass
        _fsync_directory(current.parent)


def _validate_lock_fd(fd: int) -> None:
    info = os.fstat(fd)
    if stat.S_ISREG(info.st_mode) is False:
        raise WorkspaceLockUnavailable("workspace_lock_unavailable")
    if getattr(info, "st_nlink", 1) != 1:
        raise WorkspaceLockUnavailable("workspace_lock_unavailable")
    if os.name != "nt":
        uid = _current_uid()
        if uid is not None and info.st_uid != uid:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")
        if stat.S_IMODE(info.st_mode) != _LOCK_FILE_MODE:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")


def _validate_dir_stat(info: os.stat_result) -> None:
    if os.name != "nt":
        uid = _current_uid()
        if uid is not None and info.st_uid != uid:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise WorkspaceLockUnavailable("workspace_lock_unavailable")


def _validate_ancestor_dir_stat(info: os.stat_result) -> None:
    if os.name == "nt":
        return
    uid = _current_uid()
    if uid is not None and info.st_uid not in {0, uid}:
        raise WorkspaceLockUnavailable("workspace_lock_unavailable")
    mode = stat.S_IMODE(info.st_mode)
    writable_by_others = bool(mode & 0o022)
    trusted_sticky_root = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
    if writable_by_others and not trusted_sticky_root:
        raise WorkspaceLockUnavailable("workspace_lock_unavailable")


def _current_uid() -> int | None:
    getter = getattr(os, "getuid", None)
    if not callable(getter):
        return None
    return cast(Callable[[], int], getter)()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)
