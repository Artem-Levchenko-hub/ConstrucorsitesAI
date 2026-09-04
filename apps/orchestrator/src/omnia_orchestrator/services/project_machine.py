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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
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
    timed_out: bool = False


class MachineRequestStatus(BaseModel):
    operation_id: str
    state: str
    phase: str
    started_at: datetime
    deadline_at: datetime
    heartbeat_at: datetime
    log_bytes: int
    result: MachineOperationResult | None = None
    transport_response: dict[str, Any] | None = None


_deadline: ContextVar[float | None] = ContextVar("machine_operation_deadline", default=None)


@contextmanager
def machine_budget(seconds: float | None) -> Iterator[None]:
    """Carry the aggregate deadline into asyncio.to_thread without shared state.

    None explicitly leaves the expired work budget for mandatory teardown.
    Nested work can shorten but never extend its caller's remaining budget.
    """
    deadline = None if seconds is None else time.monotonic() + max(0, seconds)
    outer_deadline = _deadline.get()
    if deadline is not None and outer_deadline is not None:
        deadline = min(deadline, outer_deadline)
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
        payload.setdefault("epoch", 0)
        payload.setdefault("cancelled_epoch", 0)
        payload.setdefault("operations", {})
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
        now = datetime.now(UTC).isoformat()
        record = {
            "digest": digest,
            "state": "starting",
            "epoch": mutation.fencing_epoch,
            "kind": "command",
            "started_at": now,
            "heartbeat_at": now,
            "log_bytes": 0,
        }
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
        record["heartbeat_at"] = datetime.now(UTC).isoformat()
        record["log_bytes"] = len(result.output.encode("utf-8"))
        if not raw["running"]:
            record.update(state="completed", result=result.model_dump())
        write_controller_json(self.path, state)
        return result

    async def request_start(
        self,
        mutation: LifecycleMutation,
        *,
        phase: str,
        deadline_at: datetime,
    ) -> MachineOperationResult | None:
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
            raise ValueError("request deadline must be timezone-aware")
        state = await self.assert_ready(mutation)
        operation_id = str(mutation.operation_id)
        existing = state["operations"].get(operation_id)
        if existing is not None:
            if (
                existing.get("kind") != "request"
                or existing.get("digest") != mutation.request_digest
                or existing.get("epoch") != mutation.fencing_epoch
            ):
                raise CellFenceRejected("machine request replay envelope mismatch")
            raw_result = existing.get("result")
            return (
                MachineOperationResult.model_validate(raw_result)
                if raw_result is not None
                else None
            )
        now = datetime.now(UTC).isoformat()
        state["operations"][operation_id] = {
            "kind": "request",
            "digest": mutation.request_digest,
            "state": "running",
            "epoch": mutation.fencing_epoch,
            "phase": phase,
            "started_at": now,
            "deadline_at": deadline_at.isoformat(),
            "heartbeat_at": now,
            "log_bytes": 0,
        }
        write_controller_json(self.path, state)
        return None

    async def request_heartbeat(
        self,
        mutation: LifecycleMutation,
        *,
        phase: str,
        log_bytes: int,
        min_interval_seconds: int = 15,
        force: bool = False,
    ) -> None:
        state = await self.assert_ready(mutation)
        record = state["operations"].get(str(mutation.operation_id))
        if (
            record is None
            or record.get("kind") != "request"
            or record.get("digest") != mutation.request_digest
            or record.get("state") != "running"
        ):
            raise CellFenceRejected("unknown active machine request")
        now = datetime.now(UTC)
        previous = datetime.fromisoformat(record["heartbeat_at"])
        if (
            force
            or record.get("phase") != phase
            or (now - previous).total_seconds() >= max(1, min_interval_seconds)
        ):
            record["phase"] = phase
            record["heartbeat_at"] = now.isoformat()
            record["log_bytes"] = max(int(record.get("log_bytes", 0)), max(0, log_bytes))
            write_controller_json(self.path, state)

    async def request_finish(
        self,
        mutation: LifecycleMutation,
        result: MachineOperationResult,
    ) -> MachineOperationResult:
        state = await self.assert_ready(mutation)
        record = state["operations"].get(str(mutation.operation_id))
        if (
            record is None
            or record.get("kind") != "request"
            or record.get("digest") != mutation.request_digest
        ):
            raise CellFenceRejected("unknown machine request")
        existing = record.get("result")
        if existing is not None:
            stored = MachineOperationResult.model_validate(existing)
            if stored != result:
                raise CellFenceRejected("machine request terminal replay mismatch")
            return stored
        now = datetime.now(UTC).isoformat()
        terminal_state = (
            "timed_out"
            if result.timed_out
            else "completed"
            if result.exit_code == 0
            else "failed"
        )
        record.update(
            state=terminal_state,
            heartbeat_at=now,
            log_bytes=len(result.output.encode("utf-8")),
            result=result.model_dump(),
        )
        write_controller_json(self.path, state)
        return result

    async def store_transport_response(
        self,
        *,
        operation_id: UUID,
        fencing_epoch: int,
        response: dict[str, Any],
    ) -> None:
        state = self.state()
        current = self.lease_epoch()
        record = state["operations"].get(str(operation_id))
        if (
            current != fencing_epoch
            or record is None
            or record.get("kind") != "request"
            or record.get("epoch") != fencing_epoch
            or record.get("result") is None
        ):
            raise CellFenceRejected("cannot attach transport response to unknown request")
        existing = record.get("transport_response")
        if existing is not None and existing != response:
            raise CellFenceRejected("machine transport response replay mismatch")
        record["transport_response"] = response
        write_controller_json(self.path, state)

    async def request_status(
        self,
        *,
        operation_id: UUID,
        fencing_epoch: int,
    ) -> MachineRequestStatus:
        state = self.state()
        record = state["operations"].get(str(operation_id))
        if record is None or record.get("kind") != "request":
            raise CellFenceRejected("unknown machine request")
        await self.assert_fence(
            LifecycleMutation(operation_id, fencing_epoch, str(record.get("digest", "")))
        )
        return self._request_status_from_record(operation_id, record)

    async def inspect_request_status(self, *, operation_id: UUID) -> MachineRequestStatus:
        """Read controller-owned request state without requiring a live API lease."""
        record = self.state()["operations"].get(str(operation_id))
        if record is None or record.get("kind") != "request":
            raise CellFenceRejected("unknown machine request")
        return self._request_status_from_record(operation_id, record)

    @staticmethod
    def _request_status_from_record(
        operation_id: UUID,
        record: dict[str, Any],
    ) -> MachineRequestStatus:
        return MachineRequestStatus(
            operation_id=str(operation_id),
            state=str(record["state"]),
            phase=str(record.get("phase", "unknown")),
            started_at=datetime.fromisoformat(record["started_at"]),
            deadline_at=datetime.fromisoformat(record["deadline_at"]),
            heartbeat_at=datetime.fromisoformat(record["heartbeat_at"]),
            log_bytes=int(record.get("log_bytes", 0)),
            result=(
                MachineOperationResult.model_validate(record["result"])
                if record.get("result") is not None
                else None
            ),
            transport_response=record.get("transport_response"),
        )

    async def exec_terminate(
        self,
        operation_id: str,
        mutation: LifecycleMutation,
        *,
        grace_seconds: int,
    ) -> None:
        state = await self.assert_ready(mutation)
        record = state["operations"].get(operation_id)
        if not record or record.get("epoch") != mutation.fencing_epoch:
            raise CellFenceRejected("unknown command in this lease")
        if record.get("result") is not None:
            return
        exec_id = record.get("exec_id")
        if not exec_id:
            raise CellIndeterminateOperation("command start outcome unknown; inspect machine")
        await machine_effect(self.backend.terminate_exec, exec_id, grace_seconds)

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
