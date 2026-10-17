"""Prospective per-operation command authority; workspace lock serializes writes.

No legacy result adoption or expiry. Unknown dispatch remains a writer barrier.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation
from yleum_orchestrator.schemas.workspace import (
    WorkspaceAgentCommandBinding,
    WorkspaceAgentCommandReceipt,
    WorkspaceAgentExecRequest,
    WorkspaceAgentExecResponse,
)
from yleum_orchestrator.services.cell_state import (
    _ensure_secure_dir,
    _read_plain_json_file,
    _require_regular_file_path,
)
from yleum_orchestrator.services.project_machine import write_controller_json
from yleum_orchestrator.services.source_write_journal import _sync_directory


def canonical_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def command_request_digest(workspace_id: UUID, request: WorkspaceAgentExecRequest) -> str:
    return canonical_digest(
        {
            "protocol": "tracked-command/v1",
            "workspace_id": str(workspace_id),
            "request": request.model_dump(mode="json"),
        }
    )


class CommandRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: WorkspaceAgentCommandBinding
    receipt: WorkspaceAgentCommandReceipt | None = None
    response: WorkspaceAgentExecResponse | None = None


_active: ContextVar[tuple[Path, WorkspaceAgentCommandBinding] | None] = ContextVar(
    "tracked_command_authority",
    default=None,
)


class CommandJournal:
    def __init__(self, state_root: Path, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id
        self.root = state_root.parent / "command-operations" / str(workspace_id)

    def load(self, operation_id: UUID) -> CommandRecord | None:
        path = self.root / f"{operation_id}.json"
        info = _require_regular_file_path(path, missing_ok=True)
        if info is None:
            return None
        if info.st_size > 262144:
            raise CellIndeterminateOperation("command record exceeds limit")
        record = CommandRecord.model_validate(_read_plain_json_file(path))
        binding = record.binding
        if binding.workspace_id != self.workspace_id or binding.operation_id != operation_id:
            raise CellIndeterminateOperation("command record identity mismatch")
        if (record.receipt is None) != (record.response is None):
            raise CellIndeterminateOperation("command terminal record incomplete")
        if record.response is not None:
            if (
                record.receipt != self._receipt(binding, record.response)
                or record.response.operation_id != operation_id
                or record.response.before_identity.workspace_revision != binding.before_revision
                or record.response.after_identity.workspace_revision
                != record.response.workspace_revision
            ):
                raise CellIndeterminateOperation("command terminal binding mismatch")
            # A readable post-rename terminal must be resealed after failed directory fsync.
            _sync_directory(self.root)
        return record

    def require_clear(self) -> None:
        if not self.root.exists() and not self.root.is_symlink():
            return
        _ensure_secure_dir(self.root, create=False)
        active = _active.get()
        if active is not None and active[0] == self.root:
            own = self.load(active[1].operation_id)
            if own is None or own.binding != active[1] or own.receipt is not None:
                raise CellIndeterminateOperation("command dispatch scope is no longer active")
        for path in self.root.iterdir():
            if path.suffix != ".json":
                raise CellIndeterminateOperation("command persistence remains uncertain")
            record = self.load(UUID(path.stem))
            if record is None:
                raise CellIndeterminateOperation("command record disappeared")
            if record.receipt is None and not (
                active is not None and active == (self.root, record.binding)
            ):
                raise CellIndeterminateOperation("command outcome remains unresolved")

    def _save(self, record: CommandRecord) -> None:
        payload = record.model_dump(mode="json")
        if len(json.dumps(payload).encode()) > 262144:
            raise CellIndeterminateOperation("command record exceeds limit")
        _ensure_secure_dir(self.root, create=True)
        _sync_directory(self.root.parent.parent)
        _sync_directory(self.root.parent)
        path = self.root / f"{record.binding.operation_id}.json"
        _require_regular_file_path(path, missing_ok=True)
        write_controller_json(path, payload)
        _sync_directory(self.root)

    def start(self, binding: WorkspaceAgentCommandBinding) -> None:
        if self.load(binding.operation_id) is not None:
            raise CellIndeterminateOperation("command already submitted")
        self.require_clear()
        self._save(CommandRecord(binding=binding))

    @staticmethod
    def _receipt(
        binding: WorkspaceAgentCommandBinding,
        response: WorkspaceAgentExecResponse,
    ) -> WorkspaceAgentCommandReceipt:
        return WorkspaceAgentCommandReceipt(
            **binding.model_dump(),
            after_revision=response.workspace_revision,
            response_sha256=canonical_digest(response.model_dump(mode="json")),
        )

    def finish(
        self,
        binding: WorkspaceAgentCommandBinding,
        response: WorkspaceAgentExecResponse,
    ) -> CommandRecord:
        record = self.load(binding.operation_id)
        if record is None or record.binding != binding or record.receipt is not None:
            raise CellIndeterminateOperation("command terminal replay mismatch")
        terminal = CommandRecord(
            binding=binding, response=response, receipt=self._receipt(binding, response)
        )
        self._save(terminal)
        verified = self.load(binding.operation_id)
        assert verified is not None
        return verified

    @contextmanager
    def executing(self, binding: WorkspaceAgentCommandBinding) -> Iterator[None]:
        record = self.load(binding.operation_id)
        if record is None or record.binding != binding or record.receipt is not None:
            raise CellIndeterminateOperation("command dispatch authority unavailable")
        token = _active.set((self.root, binding))
        try:
            yield
        finally:
            _active.reset(token)


def require_machine_commands_clear(machine_path: Path) -> None:
    # machine_identity_root is state_root.parent/project-machines/workspace_id.
    CommandJournal(
        machine_path.parent.parent.parent / "state", UUID(machine_path.parent.name)
    ).require_clear()
