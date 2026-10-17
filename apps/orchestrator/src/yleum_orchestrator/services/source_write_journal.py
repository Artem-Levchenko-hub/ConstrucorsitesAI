"""Private per-operation source receipts; callers serialize with workspace lock.

Pending means unknown, never permission to repeat a mutation. No guest payload,
machine marker, lifecycle transition or automatic expiry is stored here.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation
from yleum_orchestrator.schemas.workspace import WorkspaceAgentWriteReceipt
from yleum_orchestrator.services.cell_state import (
    _ensure_secure_dir,
    _read_plain_json_file,
    _require_regular_file_path,
)
from yleum_orchestrator.services.project_machine import write_controller_json


def _sync_directory(path: Path) -> None:
    """Unlike legacy best-effort fsync, a receipt requires durable metadata."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SourceWriteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: UUID
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    physical_volume: str = Field(min_length=1)
    physical_volume_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    helpers: dict[str, dict[str, str]] = Field(default_factory=dict)
    receipt: WorkspaceAgentWriteReceipt | None = None

    def binding(self) -> dict[str, Any]:
        return self.model_dump(exclude={"helpers", "receipt"})


def request_digest(
    *,
    workspace_id: UUID,
    generation_run_id: UUID,
    fencing_epoch: int,
    expected_revision: str,
    files: dict[str, str],
    deletes: tuple[str, ...],
) -> str:
    value = {
        "protocol": "source-write/v1",
        "workspace_id": str(workspace_id),
        "generation_run_id": str(generation_run_id),
        "fencing_epoch": fencing_epoch,
        "expected_revision": expected_revision,
        "files": {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()},
        "deletes": sorted(deletes),
    }
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


class SourceWriteJournal:
    def __init__(self, state_root: Path, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id
        self.root = state_root.parent / "source-write-operations" / str(workspace_id)

    def load(self, operation_id: UUID) -> SourceWriteRecord | None:
        path = self.root / f"{operation_id}.json"
        info = _require_regular_file_path(path, missing_ok=True)
        if info is None:
            return None
        if info.st_size > 32768:
            raise CellIndeterminateOperation("source operation record exceeds limit")
        value = SourceWriteRecord.model_validate(_read_plain_json_file(path))
        if value.operation_id != operation_id or value.workspace_id != self.workspace_id:
            raise CellIndeterminateOperation("source operation identity mismatch")
        if value.receipt is not None:
            bound = value.receipt.model_dump(exclude={"after_revision", "written", "deleted"})
            if bound != value.binding():
                raise CellIndeterminateOperation("source operation receipt mismatch")
            # A prior replace may have succeeded before directory fsync failed.
            # Reconfirm durability before exporting a historical terminal receipt.
            _sync_directory(self.root)
        return value

    def require_clear(self) -> None:
        from yleum_orchestrator.services.command_receipts import CommandJournal

        CommandJournal(self.root.parent.parent / "state", self.workspace_id).require_clear()
        if not self.root.exists() and not self.root.is_symlink():
            return
        _ensure_secure_dir(self.root, create=False)
        for path in self.root.iterdir():
            if path.name.startswith(".machine-"):
                # An interrupted atomic write is not authority. Its target may
                # still be unknown; never permit a new writer on that ambiguity.
                raise CellIndeterminateOperation("source operation commit uncertain")
            if path.suffix != ".json":
                raise CellIndeterminateOperation("unknown source operation entry")
            value = self.load(UUID(path.stem))
            if value is None or value.receipt is None:
                raise CellIndeterminateOperation("source operation remains unresolved")

    def save(self, value: SourceWriteRecord) -> None:
        _ensure_secure_dir(self.root, create=True)
        # Both new directory entries must survive a controller/host restart.
        _sync_directory(self.root.parent.parent)
        _sync_directory(self.root.parent)
        path = self.root / f"{value.operation_id}.json"
        _require_regular_file_path(path, missing_ok=True)
        write_controller_json(path, value.model_dump(mode="json"))
        _sync_directory(self.root)

    def start(self, value: SourceWriteRecord) -> SourceWriteRecord:
        existing = self.load(value.operation_id)
        if existing is not None:
            if existing.binding() != value.binding():
                raise CellIndeterminateOperation("source operation replay mismatch")
            if existing.receipt is None:
                raise CellIndeterminateOperation("source operation remains unresolved")
            return existing
        self.require_clear()
        self.save(value)
        return value

    def finish(
        self, value: SourceWriteRecord, *, after: str, written: int, deleted: int
    ) -> SourceWriteRecord:
        current = self.load(value.operation_id)
        if current is None or current.binding() != value.binding() or current.receipt is not None:
            raise CellIndeterminateOperation("source operation completion mismatch")
        receipt = WorkspaceAgentWriteReceipt(
            **value.binding(),
            after_revision=after,
            written=written,
            deleted=deleted,
        )
        terminal = current.model_copy(update={"receipt": receipt})
        self.save(terminal)
        return terminal


_active: ContextVar[tuple[SourceWriteJournal, UUID] | None] = ContextVar(
    "source_write_operation",
    default=None,
)


@contextmanager
def source_write_scope(journal: SourceWriteJournal, operation_id: UUID) -> Iterator[None]:
    token = _active.set((journal, operation_id))
    try:
        yield
    finally:
        _active.reset(token)


def plan_source_helper(
    name: str,
    labels: dict[str, str],
    physical_volume: str,
) -> tuple[str | None, dict[str, str]]:
    active = _active.get()
    if active is None:
        return None, labels
    journal, operation_id = active
    record = journal.load(operation_id)
    if record is None or record.receipt is not None:
        raise CellIndeterminateOperation("source helper has no active intent")
    if physical_volume != record.physical_volume or any(
        labels.get("omnia." + key) != str(getattr(record, key))
        for key in ("workspace_id", "project_id", "owner_id")
    ):
        raise CellIndeterminateOperation("source helper physical identity mismatch")
    helper_name = name + "-" + uuid5(operation_id, name).hex
    if helper_name in record.helpers:
        raise CellIndeterminateOperation("source helper was already submitted")
    bound = {
        **labels,
        "omnia.source_operation_id": str(operation_id),
        "omnia.source_request_sha256": record.request_sha256,
        "omnia.source_fencing_epoch": str(record.fencing_epoch),
        "omnia.source_generation_run_id": str(record.generation_run_id),
    }
    journal.save(record.model_copy(update={"helpers": {**record.helpers, helper_name: bound}}))
    return helper_name, bound


def require_source_writes_clear(state_root: Path, workspace_id: UUID) -> None:
    SourceWriteJournal(state_root, workspace_id).require_clear()


def expected_source_volume_identity(name: str) -> str | None:
    active = _active.get()
    if active is None:
        return None
    journal, operation_id = active
    record = journal.load(operation_id)
    if record is None or record.receipt is not None or record.physical_volume != name:
        raise CellIndeterminateOperation("source effect identity unavailable")
    return record.physical_volume_identity
