"""Fenced durable command transport. Callers hold WorkspaceOperationLock.

Command execution belongs to Docker, not an HTTP request or Python task. The
controller records intent before sending it; uncertain starts are not replayed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellIndeterminateOperation,
    LifecycleMutation,
)
from omnia_orchestrator.core.project_machine import MachineCommand, MachineManifest
from omnia_orchestrator.services.cell_state import _ensure_secure_dir, _read_plain_json_file


class MachineOperationResult(BaseModel):
    operation_id: str
    state: str
    exit_code: int | None = None
    output: str = ""


_deadline: ContextVar[float | None] = ContextVar("machine_operation_deadline", default=None)


@contextmanager
def machine_budget(seconds: float | None):
    """Carry the aggregate deadline into asyncio.to_thread without shared state.

    None explicitly leaves the expired work budget for mandatory teardown.
    Nested work can shorten but never extend its caller's remaining budget.
    """
    deadline = None if seconds is None else time.monotonic() + max(0, seconds)
    if deadline is not None and _deadline.get() is not None:
        deadline = min(deadline, _deadline.get())
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def machine_remaining_seconds(limit: float) -> float:
    deadline = _deadline.get()
    if deadline is None:
        return limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("machine operation work budget exhausted")
    return min(limit, remaining)


async def machine_effect(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Drain blocking Docker work before cancellation may release the caller's lock."""
    machine_remaining_seconds(1)
    task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                try:
                    task.result()
                except BaseException as error:
                    raise asyncio.CancelledError from error
                raise
        except BaseException as error:
            if cancelled:
                # A late helper failure must not replace cancellation and bypass
                # its caller's timeout cleanup after the mutation was drained.
                raise asyncio.CancelledError from error
            raise
    if cancelled:
        raise asyncio.CancelledError
    machine_remaining_seconds(1)
    return result


def write_controller_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_secure_dir(path.parent, create=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".machine-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ProjectMachine:
    def __init__(
        self, root: Path, workspace_id: UUID, backend: Any, *, lease_epoch: Callable[[], int | None]
    ) -> None:
        self.workspace_id = workspace_id
        self.backend = backend
        self.root = root / str(workspace_id)
        self.lease_epoch = lease_epoch
        self.path = self.root / "machine.json"

    def state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "workspace_id": str(self.workspace_id),
                "epoch": 0,
                "cancelled_epoch": 0,
                "operations": {},
            }
        payload = _read_plain_json_file(self.path)
        if payload.get("workspace_id") != str(self.workspace_id):
            raise CellFenceRejected("machine workspace identity mismatch")
        return payload

    async def assert_fence(self, mutation: LifecycleMutation) -> dict[str, Any]:
        state = self.state()
        current = self.lease_epoch()
        if (
            current != mutation.fencing_epoch
            or mutation.fencing_epoch < state["epoch"]
            or mutation.fencing_epoch <= state["cancelled_epoch"]
        ):
            # An old request must never kill a newer machine. Only the matching
            # old machine may be removed when an external lease has advanced.
            if state["epoch"] == mutation.fencing_epoch and current != mutation.fencing_epoch:
                await machine_effect(self.backend.remove, expected_epoch=mutation.fencing_epoch)
            raise CellFenceRejected("machine lease expired, cancelled, or changed")
        return state

    async def ensure(self, manifest: MachineManifest, mutation: LifecycleMutation) -> None:
        state = await self.assert_fence(mutation)
        # Journal the target epoch before Docker create. Backend reconciles the
        # physical labels and never discards a newer machine based on old JSON.
        state["epoch"] = mutation.fencing_epoch
        state["ready_epoch"] = None
        write_controller_json(self.path, state)
        await machine_effect(self.backend.ensure, manifest, mutation.fencing_epoch)
        state.update(
            epoch=mutation.fencing_epoch,
            ready_epoch=mutation.fencing_epoch,
            manifest=manifest.model_dump(mode="json"),
        )
        write_controller_json(self.path, state)

    async def assert_ready(self, mutation: LifecycleMutation) -> dict[str, Any]:
        state = await self.assert_fence(mutation)
        if state.get("ready_epoch") != mutation.fencing_epoch:
            raise CellFenceRejected("machine epoch is pending or not ready")
        return state

    async def exec_start(self, argv: list[str], cwd: str, mutation: LifecycleMutation) -> str:
        command = MachineCommand(argv=argv, cwd=cwd)
        state = await self.assert_ready(mutation)
        operation_id = str(mutation.operation_id)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "command": command.model_dump(),
                    "request": mutation.request_digest,
                    "epoch": mutation.fencing_epoch,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = state["operations"].get(operation_id)
        if existing:
            if existing["digest"] != digest:
                raise CellFenceRejected("machine operation replay envelope mismatch")
            if not existing.get("exec_id"):
                raise CellIndeterminateOperation("command start outcome unknown; inspect machine")
            return operation_id
        record = {"digest": digest, "state": "starting", "epoch": mutation.fencing_epoch}
        state["operations"][operation_id] = record
        write_controller_json(self.path, state)
        exec_id = await machine_effect(
            self.backend.exec_start,
            command.argv,
            command.cwd,
            operation_id,
        )
        record.update(exec_id=exec_id, state="running")
        write_controller_json(self.path, state)
        return operation_id

    async def exec_status(
        self, operation_id: str, mutation: LifecycleMutation
    ) -> MachineOperationResult:
        state = await self.assert_ready(mutation)
        record = state["operations"].get(operation_id)
        if not record or record["epoch"] != mutation.fencing_epoch:
            raise CellFenceRejected("unknown command in this lease")
        if record.get("result"):
            return MachineOperationResult.model_validate(record["result"])
        if not record.get("exec_id"):
            raise CellIndeterminateOperation("command start outcome unknown; inspect machine")
        raw = await machine_effect(self.backend.exec_status, record["exec_id"])
        result = MachineOperationResult(
            operation_id=operation_id,
            state="running" if raw["running"] else "completed",
            exit_code=raw["exit_code"],
            output=str(raw["output"])[-24000:],
        )
        if not raw["running"]:
            record.update(state="completed", result=result.model_dump())
            write_controller_json(self.path, state)
        return result

    async def cancel(self, mutation: LifecycleMutation) -> None:
        state = self.state()
        if not (
            state["cancelled_epoch"] == mutation.fencing_epoch
            and state["epoch"] == mutation.fencing_epoch
        ):
            state = await self.assert_fence(mutation)
        # Persist the fence before container teardown. A crash cannot reopen it.
        state["cancelled_epoch"] = mutation.fencing_epoch
        write_controller_json(self.path, state)
        await machine_effect(self.backend.remove, expected_epoch=mutation.fencing_epoch)
        if await machine_effect(self.backend.is_running):
            raise CellFenceRejected("cancel could not confirm process death")
