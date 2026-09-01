from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

import omnia_orchestrator.services.cell_lock as cell_lock_module
from omnia_orchestrator.core.cell_resources import WorkspaceLockTimeout, WorkspaceLockUnavailable
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock


@pytest.mark.asyncio
async def test_hold_serializes_and_releases(tmp_path) -> None:
    lock = WorkspaceOperationLock(tmp_path, acquire_timeout_seconds=1, retry_interval_seconds=0.01)
    workspace_id = uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = False

    async def first() -> None:
        async with lock.hold(workspace_id):
            entered.set()
            await release.wait()

    async def second() -> None:
        nonlocal second_entered
        await entered.wait()
        async with lock.hold(workspace_id):
            second_entered = True

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await entered.wait()
    await asyncio.sleep(0.05)
    assert second_entered is False
    release.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered is True


@pytest.mark.asyncio
async def test_timeout_includes_process_local_wait(tmp_path) -> None:
    lock = WorkspaceOperationLock(
        tmp_path, acquire_timeout_seconds=0.05, retry_interval_seconds=0.01
    )
    workspace_id = uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with lock.hold(workspace_id):
            entered.set()
            await release.wait()

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    with pytest.raises(WorkspaceLockTimeout):
        async with lock.hold(workspace_id):
            pass
    release.set()
    await holder_task


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_poison_future_acquire(tmp_path) -> None:
    lock = WorkspaceOperationLock(tmp_path, acquire_timeout_seconds=1, retry_interval_seconds=0.01)
    workspace_id = uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with lock.hold(workspace_id):
            entered.set()
            await release.wait()

    holder_task = asyncio.create_task(holder())
    await entered.wait()
    waiter = asyncio.create_task(lock.hold(workspace_id).__aenter__())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await holder_task
    async with lock.hold(workspace_id):
        pass


@pytest.mark.asyncio
async def test_hardlinked_lock_file_is_rejected(tmp_path) -> None:
    lock = WorkspaceOperationLock(tmp_path, acquire_timeout_seconds=1, retry_interval_seconds=0.01)
    workspace_id = uuid4()
    lock_path = tmp_path / "locks" / f"{workspace_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("0", encoding="utf-8")
    hardlink_path = lock_path.with_suffix(".shadow")
    try:
        os.link(lock_path, hardlink_path)
    except OSError:
        pytest.skip("hardlinks unsupported")

    with pytest.raises(WorkspaceLockUnavailable):
        async with lock.hold(workspace_id):
            pass


def test_lock_owner_and_mode_guards_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(cell_lock_module.os, "name", "posix")
    monkeypatch.setattr(cell_lock_module, "_current_uid", lambda: 1000)

    with pytest.raises(WorkspaceLockUnavailable):
        cell_lock_module._validate_dir_stat(SimpleNamespace(st_uid=1001, st_mode=0o040700))

    monkeypatch.setattr(
        cell_lock_module.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=0o100644, st_uid=1000, st_nlink=1),
    )
    with pytest.raises(WorkspaceLockUnavailable):
        cell_lock_module._validate_lock_fd(1)
