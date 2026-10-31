"""Controller-only startup authority and actual Docker process identity."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from yleum_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from yleum_orchestrator.services.cell_state import (
    CellOperationRecord,
    CellWorkspaceState,
    _read_plain_json_file,
    _require_regular_file_path,
)
from yleum_orchestrator.services.generation_reader_inventory import reader_dispatch

_restoration: ContextVar[tuple[Path, str, int] | None] = ContextVar(
    "startup_restoration_writer",
    default=None,
)
_NO_RECOVERY = object()


@dataclass(frozen=True)
class EnvironmentIdentity:
    """One fresh inventory, with content separate from its physical process."""

    content_digest: str
    incarnation: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "protocol": "project-machine-environment-incarnation/v1",
                    "content_digest": self.content_digest,
                    "startup_incarnation": self.incarnation,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def restoration_transition(self, after: EnvironmentIdentity) -> dict[str, Any] | None:
        if self.content_digest != after.content_digest:
            return None
        return {
            "protocol": "restored-environment-transition/v1",
            "version": 1,
            "content_digest": self.content_digest,
            "before_incarnation": self.incarnation,
            "after_incarnation": after.incarnation,
        }


@contextmanager
def restoration_writer(path: Path, operation_id: UUID, epoch: int) -> Iterator[None]:
    """Only the exact owned restoration may continue its durable pending intent."""
    token = _restoration.set((path, str(operation_id), epoch))
    try:
        yield
    finally:
        _restoration.reset(token)


def require_restoration_startup_clear(
    path: Path,
    recovery: object = _NO_RECOVERY,
) -> frozenset[str]:
    recovered: dict[str, int] = {}
    if recovery is not _NO_RECOVERY:
        if (
            type(recovery) is not dict
            or set(recovery) != {"version", "workspace_id", "reference_sha256", "operations"}
            or type(recovery.get("version")) is not int
            or recovery["version"] != 1
            or recovery.get("workspace_id") != path.parent.name
            or type(recovery.get("reference_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", recovery["reference_sha256"]) is None
            or type(recovery.get("operations")) is not dict
        ):
            raise CellIdentityConflict("startup recovery completion is invalid")
        for identifier, epoch in recovery["operations"].items():
            try:
                valid_id = str(UUID(identifier)) == identifier
            except (ValueError, TypeError, AttributeError):
                valid_id = False
            if not valid_id or type(epoch) is not int or epoch < 1:
                raise CellIdentityConflict("startup recovery completion is invalid")
            recovered[identifier] = epoch
    if _require_regular_file_path(path, missing_ok=True) is None:
        return frozenset()
    saved = _read_plain_json_file(path)
    operations = saved.get("operations")
    if not isinstance(operations, dict):
        raise CellIdentityConflict("startup recovery history is unavailable")
    matched: set[str] = set()
    for identifier, record in operations.items():
        if not isinstance(record, dict):
            raise CellIdentityConflict("startup recovery history is invalid")
        if "startup_role" in record and (
            type(record["startup_role"]) is not str
            or record["startup_role"]
            not in {
                "shell",
                "build",
                "bootstrap",
                "fast_check",
                "full_build",
                "restore_runtime",
            }
        ):
            raise CellIdentityConflict("startup recovery role is invalid")
        if record.get("startup_role") != "restore_runtime":
            continue
        if identifier in recovered and recovered[identifier] == record.get("epoch"):
            matched.add(identifier)
            continue
        result = record.get("result")
        if (
            record.get("state") == "completed"
            and isinstance(result, dict)
            and result.get("state") == "completed"
            and type(result.get("exit_code")) is int
            and result["exit_code"] == 0
            and result.get("timed_out") is False
        ):
            continue
        if (
            result is None
            and record.get("state") == "running"
            and _restoration.get() == (path, identifier, record.get("epoch"))
        ):
            continue
        raise CellResourceError("startup recovery requires an explicit complete restoration")
    return frozenset(matched)


def restoration_recovery_completion(path: Path, reference_sha256: str) -> dict[str, Any]:
    """Operational resolution of old startup holds, never a role or runtime proof."""
    operations: dict[str, int] = {}
    if _require_regular_file_path(path, missing_ok=True) is not None:
        saved = _read_plain_json_file(path)
        rows = saved.get("operations")
        if not isinstance(rows, dict):
            raise CellIdentityConflict("startup recovery history is invalid")
        for identifier, row in rows.items():
            if not isinstance(row, dict):
                raise CellIdentityConflict("startup recovery history is invalid")
            if row.get("startup_role") == "restore_runtime":
                epoch = row.get("epoch")
                try:
                    valid_id = str(UUID(identifier)) == identifier
                except (ValueError, TypeError, AttributeError):
                    valid_id = False
                if not valid_id or type(epoch) is not int or epoch < 1:
                    raise CellIdentityConflict("startup recovery epoch is invalid")
                operations[identifier] = epoch
    return {
        "version": 1,
        "workspace_id": path.parent.name,
        "reference_sha256": reference_sha256,
        "operations": operations,
    }


class MachineInventoryUnavailable(CellResourceError):
    """No running process inventory; this is never a successful proof identity."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("machine environment inventory is not running")


def prepare_operation_id(run_id: UUID, epoch: int) -> UUID:
    return uuid5(run_id, f"machine-startup-prepare/v1:{epoch}")


def startup_incarnation(container: Any) -> str:
    """Hash fresh daemon-owned identity; guest files cannot supply this witness."""
    reader_dispatch(container.reload)
    attrs = container.attrs
    state = attrs.get("State")
    identifier = attrs.get("Id")
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r"[0-9a-f]{64}", identifier) is None
        or container.id != identifier
        or not isinstance(state, dict)
    ):
        raise CellIdentityConflict("machine startup incarnation is unavailable")
    started = state.get("StartedAt")
    if (
        not isinstance(started, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", started) is None
    ):
        raise CellIdentityConflict("machine startup incarnation is unavailable")
    try:
        parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError as error:
        raise CellIdentityConflict("machine startup incarnation is invalid") from error
    if parsed.year < 1970:
        raise CellIdentityConflict("machine startup incarnation is invalid")
    status = state.get("Status")
    if (
        type(state.get("Running")) is not bool
        or type(state.get("Paused")) is not bool
        or type(status) is not str
        or status
        not in {"running", "paused", "restarting", "created", "exited", "dead", "removing"}
        or (status == "running" and (not state["Running"] or state["Paused"]))
        or (status == "paused" and (not state["Running"] or not state["Paused"]))
        or (
            status in {"created", "exited", "dead", "removing"}
            and (state["Running"] or state["Paused"])
        )
    ):
        raise CellIdentityConflict("machine startup incarnation state is invalid")
    if status != "running":
        reason = "stopped" if status in {"created", "exited", "dead"} else "unavailable"
        raise MachineInventoryUnavailable(reason)
    return hashlib.sha256(
        json.dumps(
            {"container_id": identifier, "started_at": started},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def require_machine_effects_clear(
    saved: dict[str, Any],
    recovered: frozenset[str] = frozenset(),
) -> None:
    """A terminal failure is known; an unacknowledged physical writer is not."""
    operations = saved.get("operations")
    if not isinstance(operations, dict):
        raise CellIdentityConflict("startup machine history is unavailable")
    for identifier, row in operations.items():
        if not isinstance(row, dict) or row.get("kind") not in {"request", "command"}:
            raise CellIdentityConflict("startup machine history is invalid")
        if "source_guard_failure" in row:
            raise CellResourceError("startup protected source recovery is required")
        if identifier in recovered and row.get("startup_role") == "restore_runtime":
            continue
        result = row.get("result")
        if (
            row.get("state") not in {"completed", "failed", "timed_out", "cancelled"}
            or not isinstance(result, dict)
            or result.get("state") != "completed"
            or result.get("operation_id") != identifier
            or type(result.get("exit_code")) is not int
            or type(result.get("timed_out")) is not bool
        ):
            raise CellResourceError("startup machine effect remains uncertain")
        if (
            row["kind"] == "request"
            and "startup_role" not in row
            and (row["state"] != "completed" or result["exit_code"] != 0 or result["timed_out"])
        ):
            # Old heartbeat phases lost the original role. A failed legacy
            # request may be an intentional failed-restoration stop; command
            # retirement cannot supply the missing restoration authority.
            raise CellResourceError("startup legacy request requires explicit recovery")


def require_startup_admission(
    state: CellWorkspaceState,
    run_id: UUID,
    epoch: int,
) -> CellOperationRecord:
    """Keep the original admission separate from mutable source and a later lease."""
    if (
        state.project_id is None
        or state.owner_id is None
        or state.resource_names is None
        or state.bundle_state != "resources_ready"
        or state.phase != "completed"
        or state.active_generation_run_id != run_id
        or state.active_generation_fencing_epoch != epoch
        or type(epoch) is not int
        or epoch < 1
        or any(row.status not in {"completed", "failed"} for row in state.operations)
    ):
        raise CellIdentityConflict("startup admission is unavailable or pending")
    ensures = [
        row for row in state.operations if row.kind == "ensure" and row.generation_run_id == run_id
    ]
    if not ensures:
        raise CellIdentityConflict("startup admission is unavailable")
    original = ensures[0]
    current = state.operation(state.last_operation_id) if state.last_operation_id else None
    if (
        type(original.startup_contract_version) is not int
        or original.startup_contract_version != 1
        or original.status != "completed"
        or original.bundle_state != "resources_ready"
        or original.fencing_epoch > epoch
        or any(row.startup_contract_version is not None for row in ensures[1:])
        or current is None
        or current.kind not in {"ensure", "reconcile"}
        or current.status != "completed"
        or current.generation_run_id != run_id
        or current.fencing_epoch != epoch
        or current.bundle_state != "resources_ready"
    ):
        raise CellIdentityConflict("startup admission does not authorize this lease")
    return original


class PrepareBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    admission_operation_id: UUID
    admission_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_volume: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,191}$")
    physical_volume_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unknown startup binding version")
        return value

    @property
    def operation_id(self) -> UUID:
        # A source edit cannot replenish the one preparation effect per lease.
        return prepare_operation_id(self.generation_run_id, self.fencing_epoch)

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


class PrepareRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    binding: PrepareBinding
    state: Literal["intent", "dispatched", "completed", "recovered"]
    incarnation: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    recovery_reference: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def completion_has_incarnation(self) -> PrepareRecord:
        if (self.state == "completed") != (self.incarnation is not None):
            raise ValueError("startup completion witness is unavailable")
        if (self.state == "recovered") != (self.recovery_reference is not None):
            raise ValueError("startup recovery witness is unavailable")
        return self


class StartupJournal:
    """Caller holds the workspace lock; uncertain dispatch is never repeated."""

    def __init__(self, root: Path, workspace_id: UUID) -> None:
        self.workspace_id = workspace_id
        self.root = root / str(workspace_id) / "startup-prepares"

    def load(self, operation_id: UUID) -> PrepareRecord | None:
        path = self.root / f"{operation_id}.json"
        info = _require_regular_file_path(path, missing_ok=True)
        if info is None:
            return None
        if info.st_size > 32768:
            raise CellIdentityConflict("startup journal is unbounded")
        try:
            record = PrepareRecord.model_validate_json(json.dumps(_read_plain_json_file(path)))
        except (ValidationError, ValueError, TypeError) as error:
            raise CellIdentityConflict("startup journal is invalid") from error
        if (
            record.binding.workspace_id != self.workspace_id
            or record.binding.operation_id != operation_id
        ):
            raise CellIdentityConflict("startup journal identity changed")
        return record

    def require_clear(self, own: PrepareBinding | None = None) -> None:
        if not self.root.exists() and not self.root.is_symlink():
            return
        from yleum_orchestrator.services.cell_state import _ensure_secure_dir

        _ensure_secure_dir(self.root, create=False)
        for path in self.root.iterdir():
            try:
                if path.suffix != ".json":
                    raise ValueError("unknown journal entry")
                record = self.load(UUID(path.stem))
            except (ValueError, TypeError) as error:
                raise CellIdentityConflict("startup history remains uncertain") from error
            if record is None:
                raise CellIdentityConflict("startup history disappeared")
            if own is not None and record.binding.operation_id == own.operation_id:
                if record.binding != own:
                    raise CellIdentityConflict("startup prepare immutable binding changed")
                continue
            if record.state not in {"completed", "recovered"}:
                raise CellResourceError("startup preparation remains uncertain")

    def _save(self, record: PrepareRecord) -> PrepareRecord:
        from yleum_orchestrator.services.project_machine import write_controller_json

        write_controller_json(
            self.root / f"{record.binding.operation_id}.json",
            record.model_dump(mode="json"),
            durable=True,
        )
        return record

    def begin(self, binding: PrepareBinding) -> PrepareRecord:
        if binding.workspace_id != self.workspace_id:
            raise CellIdentityConflict("startup journal workspace changed")
        previous = self.load(binding.operation_id)
        if previous is not None:
            if previous.binding != binding:
                raise CellIdentityConflict("startup prepare immutable binding changed")
            return previous
        return self._save(PrepareRecord(binding=binding, state="intent"))

    def dispatch(self, binding: PrepareBinding) -> PrepareRecord:
        previous = self.begin(binding)
        if previous.state == "recovered":
            raise CellResourceError("startup operation was resolved by explicit restoration")
        if previous.state != "intent":
            raise CellResourceError("startup dispatch remains uncertain; reconcile its ACK")
        return self._save(PrepareRecord(binding=binding, state="dispatched"))

    def reconcile(
        self,
        binding: PrepareBinding,
        witness: object,
        incarnation: str,
    ) -> PrepareRecord:
        previous = self.begin(binding)
        if previous.state == "recovered":
            raise CellResourceError("startup operation was resolved by explicit restoration")
        if previous.state == "completed":
            if previous.incarnation != incarnation:
                raise CellIdentityConflict("startup completed incarnation changed")
            # The immutable completion retains its exact ACK after normal roles
            # replace machine.json's last ready operation. Never reuse it across
            # a physical restart, even when packages and source are unchanged.
            return previous
        expected = {
            "operation_id": str(binding.operation_id),
            "binding_sha256": binding.digest(),
            "incarnation": incarnation,
        }
        if previous.state == "intent" or witness != expected:
            raise CellResourceError("startup dispatch remains uncertain; exact ACK is required")
        return self._save(
            PrepareRecord(binding=binding, state="completed", incarnation=incarnation)
        )

    def resolve_by_restoration(self, reference_sha256: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", reference_sha256) is None:
            raise CellIdentityConflict("startup recovery reference is invalid")
        if not self.root.exists() and not self.root.is_symlink():
            return
        from yleum_orchestrator.services.cell_state import _ensure_secure_dir

        _ensure_secure_dir(self.root, create=False)
        for path in self.root.iterdir():
            try:
                if path.suffix != ".json":
                    raise ValueError("unknown startup journal entry")
                record = self.load(UUID(path.stem))
            except (ValueError, TypeError) as error:
                raise CellIdentityConflict("startup recovery journal is invalid") from error
            if record is None:
                raise CellIdentityConflict("startup recovery journal disappeared")
            if record.state in {"intent", "dispatched"}:
                self._save(
                    PrepareRecord(
                        binding=record.binding,
                        state="recovered",
                        recovery_reference=reference_sha256,
                    )
                )


def require_startup_history_clear(
    state_root: Path,
    workspace_id: UUID,
    *,
    own: PrepareBinding | None = None,
) -> None:
    """Shared mint/activation guard; caller already owns the workspace lock."""
    from yleum_orchestrator.services.command_receipts import CommandJournal
    from yleum_orchestrator.services.machine_identity import machine_identity_root
    from yleum_orchestrator.services.source_write_journal import require_source_writes_clear

    require_source_writes_clear(state_root, workspace_id)
    CommandJournal(state_root, workspace_id).require_clear()
    root = machine_identity_root(state_root, workspace_id)
    StartupJournal(root.parent, workspace_id).require_clear(own)
    recovery: object = _NO_RECOVERY
    metadata_path = root / "docker.json"
    if _require_regular_file_path(metadata_path, missing_ok=True) is not None:
        metadata = _read_plain_json_file(metadata_path)
        recovery = metadata.get("startup_recovery_completed", _NO_RECOVERY)
        quiesce = metadata.get("quiesce_state")
        if (
            "startup_recovery_barrier" in metadata
            or metadata.get("restore_in_progress")
            or (quiesce is not None and type(quiesce) is not str)
            or quiesce in {"pending", "failed"}
            or (
                "restore_in_progress" in metadata
                and type(metadata["restore_in_progress"]) is not bool
            )
            or quiesce not in {None, "complete", "pending", "failed"}
        ):
            raise CellResourceError("startup recovery history requires explicit restoration")
    machine_path = root / "machine.json"
    if _require_regular_file_path(machine_path, missing_ok=True) is not None:
        recovered = require_restoration_startup_clear(machine_path, recovery)
        require_machine_effects_clear(_read_plain_json_file(machine_path), recovered)


def require_prepare_identity_clear(
    state_root: Path,
    state: CellWorkspaceState,
    revision: str,
    manifest: str,
    volume: str,
) -> None:
    """A running container cannot hide an unfinished lifecycle operation on cold resume."""
    from yleum_orchestrator.services.machine_identity import machine_identity_root

    run, epoch = state.active_generation_run_id, state.active_generation_fencing_epoch
    if run is None or epoch is None:
        raise MachineInventoryUnavailable("unavailable")
    journal = StartupJournal(
        machine_identity_root(state_root, state.workspace_id).parent, state.workspace_id
    )
    record = journal.load(prepare_operation_id(run, epoch))
    if record is not None and record.state == "recovered":
        raise MachineInventoryUnavailable("unavailable")
    if record is not None and record.state != "completed":
        binding = record.binding
        original = require_startup_admission(state, run, epoch)
        if (
            (
                binding.workspace_id,
                binding.project_id,
                binding.owner_id,
                binding.generation_run_id,
                binding.fencing_epoch,
            )
            != (
                state.workspace_id,
                state.project_id,
                state.owner_id,
                run,
                epoch,
            )
            or (binding.workspace_revision, binding.manifest_digest, binding.physical_volume)
            != (revision, manifest, volume)
            or (binding.admission_operation_id, binding.admission_request_sha256)
            != (original.operation_id, original.request_digest)
        ):
            raise MachineInventoryUnavailable("unavailable")
        journal.require_clear(binding)
        raise MachineInventoryUnavailable("prepare_pending")
    journal.require_clear()
