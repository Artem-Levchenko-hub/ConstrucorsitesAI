"""Durable host-capacity reservations for Project Cell bundles."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from omnia_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellFenceRejected,
    CellResourceProfile,
    HostCapacitySnapshot,
    LifecycleMutation,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_PROVISIONAL_TTL = timedelta(minutes=5)
_RESERVATION_KEYS = frozenset(
    {
        "workspace_id",
        "operation_id",
        "fencing_epoch",
        "request_digest",
        "status",
        "created_at",
        "expires_at",
        "cpu_cores",
        "memory_bytes",
        "disk_bytes",
        "inodes",
    }
)


@dataclass(frozen=True, slots=True)
class ReservedCapacity:
    cpu_cores: float = 0.0
    memory_bytes: int = 0
    disk_bytes: int = 0
    inodes: int = 0

    @classmethod
    def from_profile(cls, profile: CellResourceProfile) -> ReservedCapacity:
        quota = profile.full_quota
        return cls(
            cpu_cores=quota.cpu_cores,
            memory_bytes=quota.memory_bytes,
            disk_bytes=quota.disk_bytes,
            inodes=quota.inodes,
        )

    def plus(self, other: ReservedCapacity) -> ReservedCapacity:
        return ReservedCapacity(
            cpu_cores=self.cpu_cores + other.cpu_cores,
            memory_bytes=self.memory_bytes + other.memory_bytes,
            disk_bytes=self.disk_bytes + other.disk_bytes,
            inodes=self.inodes + other.inodes,
        )


@dataclass(frozen=True, slots=True)
class CellCapacityReservation:
    workspace_id: UUID
    operation_id: UUID
    fencing_epoch: int
    request_digest: str
    status: Literal["provisional", "confirmed"]
    created_at: datetime
    expires_at: datetime | None
    cpu_cores: float
    memory_bytes: int
    disk_bytes: int
    inodes: int

    @property
    def quantities(self) -> ReservedCapacity:
        return ReservedCapacity(
            cpu_cores=self.cpu_cores,
            memory_bytes=self.memory_bytes,
            disk_bytes=self.disk_bytes,
            inodes=self.inodes,
        )

    def same_envelope(self, mutation: LifecycleMutation) -> bool:
        return (
            self.operation_id == mutation.operation_id
            and self.fencing_epoch == mutation.fencing_epoch
            and self.request_digest == mutation.request_digest
        )

    @property
    def mutation(self) -> LifecycleMutation:
        return LifecycleMutation(
            operation_id=self.operation_id,
            fencing_epoch=self.fencing_epoch,
            request_digest=self.request_digest,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "operation_id": str(self.operation_id),
            "fencing_epoch": self.fencing_epoch,
            "request_digest": self.request_digest,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "cpu_cores": self.cpu_cores,
            "memory_bytes": self.memory_bytes,
            "disk_bytes": self.disk_bytes,
            "inodes": self.inodes,
        }

    @classmethod
    def from_dict(cls, payload: object) -> CellCapacityReservation:
        if type(payload) is not dict or set(payload) != _RESERVATION_KEYS:
            raise RuntimeError("capacity reservation payload is invalid")
        value = payload
        try:
            raw_status = value["status"]
            if raw_status not in {"provisional", "confirmed"}:
                raise ValueError("invalid status")
            created_at = datetime.fromisoformat(str(value["created_at"]))
            expires_at = (
                datetime.fromisoformat(str(value["expires_at"]))
                if value["expires_at"] is not None
                else None
            )
            reservation = cls(
                workspace_id=UUID(str(value["workspace_id"])),
                operation_id=UUID(str(value["operation_id"])),
                fencing_epoch=int(value["fencing_epoch"]),
                request_digest=str(value["request_digest"]),
                status=raw_status,
                created_at=created_at,
                expires_at=expires_at,
                cpu_cores=float(value["cpu_cores"]),
                memory_bytes=int(value["memory_bytes"]),
                disk_bytes=int(value["disk_bytes"]),
                inodes=int(value["inodes"]),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("capacity reservation payload is invalid") from exc
        if (
            reservation.fencing_epoch <= 0
            or reservation.created_at.tzinfo is None
            or (reservation.expires_at is not None and reservation.expires_at.tzinfo is None)
            or (reservation.status == "provisional" and reservation.expires_at is None)
            or (reservation.status == "confirmed" and reservation.expires_at is not None)
            or (
                reservation.expires_at is not None
                and reservation.expires_at <= reservation.created_at
            )
            or len(reservation.request_digest) != 64
            or any(char not in "0123456789abcdef" for char in reservation.request_digest)
            or reservation.cpu_cores <= 0
            or reservation.memory_bytes <= 0
            or reservation.disk_bytes <= 0
            or reservation.inodes <= 0
        ):
            raise RuntimeError("capacity reservation payload is invalid")
        return reservation


class CellCapacityReservationStore:
    """Filesystem ledger. Callers serialize mutations with the host capacity lock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, workspace_id: UUID) -> CellCapacityReservation | None:
        path = self._path(workspace_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("capacity reservation ledger is invalid") from exc
        reservation = CellCapacityReservation.from_dict(payload)
        if reservation.workspace_id != workspace_id:
            raise RuntimeError("capacity reservation workspace mismatch")
        return reservation

    def all(self) -> tuple[CellCapacityReservation, ...]:
        if not self.root.exists():
            return ()
        reservations: list[CellCapacityReservation] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                workspace_id = UUID(path.stem)
            except ValueError as exc:
                raise RuntimeError("capacity reservation filename is invalid") from exc
            reservation = self.load(workspace_id)
            if reservation is not None:
                reservations.append(reservation)
        return tuple(reservations)

    def totals(
        self,
        *,
        exclude_workspace_id: UUID | None = None,
        status: Literal["provisional", "confirmed"] | None = None,
    ) -> ReservedCapacity:
        total = ReservedCapacity()
        for reservation in self.all():
            if reservation.workspace_id == exclude_workspace_id:
                continue
            if status is not None and reservation.status != status:
                continue
            total = total.plus(reservation.quantities)
        return total

    def reserve(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
        *,
        profile: CellResourceProfile,
        snapshot: HostCapacitySnapshot,
        admission_gate: CellAdmissionGate,
        running_bundle: bool,
        now: datetime | None = None,
    ) -> CellCapacityReservation:
        existing = self.load(workspace_id)
        if existing is not None and existing.same_envelope(mutation):
            return existing
        if existing is not None and mutation.fencing_epoch <= existing.fencing_epoch:
            raise CellFenceRejected("capacity reservation fence is stale")
        if existing is not None and existing.operation_id == mutation.operation_id:
            raise CellFenceRejected("capacity reservation envelope mismatch")

        quantities = ReservedCapacity.from_profile(profile)
        if not running_bundle:
            decision = admission_gate.check(
                snapshot,
                existing_bundle=existing is not None,
                running_bundle=False,
                reserved=self.totals(exclude_workspace_id=workspace_id),
                provisional=self.totals(
                    exclude_workspace_id=workspace_id,
                    status="provisional",
                ),
            )
            if not decision.allowed:
                raise CellCapacityUnavailable(decision.reason)
        created_at = (now or datetime.now(UTC)).astimezone(UTC)
        reservation = CellCapacityReservation(
            workspace_id=workspace_id,
            operation_id=mutation.operation_id,
            fencing_epoch=mutation.fencing_epoch,
            request_digest=mutation.request_digest,
            status="confirmed" if running_bundle else "provisional",
            created_at=created_at,
            expires_at=None if running_bundle else created_at + _PROVISIONAL_TTL,
            cpu_cores=quantities.cpu_cores,
            memory_bytes=quantities.memory_bytes,
            disk_bytes=quantities.disk_bytes,
            inodes=quantities.inodes,
        )
        self._persist(reservation)
        return reservation

    def confirm(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> CellCapacityReservation:
        reservation = self._require_exact(workspace_id, mutation)
        confirmed = replace(reservation, status="confirmed", expires_at=None)
        self._persist(confirmed)
        return confirmed

    def rebind(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> CellCapacityReservation | None:
        reservation = self.load(workspace_id)
        if reservation is None:
            return None
        if reservation.same_envelope(mutation):
            return reservation
        if mutation.fencing_epoch <= reservation.fencing_epoch:
            raise CellFenceRejected("capacity reservation fence is stale")
        rebound = replace(
            reservation,
            operation_id=mutation.operation_id,
            fencing_epoch=mutation.fencing_epoch,
            request_digest=mutation.request_digest,
        )
        self._persist(rebound)
        return rebound

    def release(self, workspace_id: UUID, mutation: LifecycleMutation) -> None:
        self._require_exact(workspace_id, mutation)
        path = self._path(workspace_id)
        path.unlink()
        self._fsync_root()

    def expired_provisionals(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[CellCapacityReservation, ...]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return tuple(
            reservation
            for reservation in self.all()
            if reservation.status == "provisional"
            and reservation.expires_at is not None
            and reservation.expires_at <= current
        )

    def _require_exact(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> CellCapacityReservation:
        reservation = self.load(workspace_id)
        if reservation is None or not reservation.same_envelope(mutation):
            raise CellFenceRejected("capacity reservation envelope mismatch")
        return reservation

    def _path(self, workspace_id: UUID) -> Path:
        return self.root / f"{workspace_id}.json"

    def _persist(self, reservation: CellCapacityReservation) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        try:
            os.chmod(self.root, _DIR_MODE)
        except OSError:
            pass
        fd, temp_name = tempfile.mkstemp(prefix=".reservation-", dir=self.root)
        try:
            try:
                os.chmod(temp_name, _FILE_MODE)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(reservation.to_dict(), handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path(reservation.workspace_id))
            self._fsync_root()
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def _fsync_root(self) -> None:
        try:
            descriptor = os.open(self.root, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
