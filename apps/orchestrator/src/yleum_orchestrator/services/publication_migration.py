"""Durable, fail-closed orchestration of a single confirmed additive plan.

Lock order is the publication service's project lock, then the IO source lock.
The IO owns all catalog, backup and exact-XID proof. Recovery only observes it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.schemas.cell_publication import CellDeployRequest
from yleum_orchestrator.schemas.publication_migration import (
    MigrationOutcomeProof,
    PublicationMigrationStatus,
)
from yleum_orchestrator.services.project_machine import write_controller_json
from yleum_orchestrator.services.publication_migration_pg import MigrationIOError

ACTIVE_STATES = frozenset({"preparing", "applying", "reconciling"})
STATES = ACTIVE_STATES | {"prepared", "applied", "failed", "stale", "blocked"}
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_PROJECT_OPERATIONS = 64
PREPARED_TTL_SECONDS = 600
ERROR_CODES = frozenset(
    {
        "migration_preparation_failed",
        "migration_confirmation_stale",
        "migration_outcome_unknown",
        "migration_interrupted",
        "migration_execution_failed",
        "migration_recovery_blocked",
    }
)
PRIVATE_FAILURE_CODES = frozenset(
    {
        "migration_verifier_image_untrusted",
        "migration_verifier_image_unavailable",
        "migration_source_changed",
        "migration_source_unavailable",
        "migration_source_database_stopped",
        "migration_accepted_seal_unavailable",
        "migration_data_contract_changed",
        "migration_backup_write_failed",
        "migration_backup_authentication_failed",
        "migration_verifier_start_failed",
        "migration_backup_restore_mismatch",
        "migration_full_schema_mismatch",
        "migration_docker_sql_failed",
        "migration_pending_replay_unsupported",
        "migration_sandbox_effect_changed",
        "migration_storage_changed",
        "migration_globals_changed",
        "migration_target_changed",
    }
)


def _failure_diagnostic(exc: BaseException, *, apply: bool) -> dict[str, str]:
    """Private static metadata only: never stringify an exception or copy its class name."""
    types = {
        MigrationIOError: "MigrationIOError",
        CellIdentityConflict: "CellIdentityConflict",
        ValidationError: "ValidationError",
        asyncio.CancelledError: "CancelledError",
        TimeoutError: "TimeoutError",
        OSError: "OSError",
        RuntimeError: "RuntimeError",
        ValueError: "ValueError",
        TypeError: "TypeError",
    }
    result = {
        "phase": "apply" if apply else "preparation",
        "type": types.get(type(exc), "OtherError"),
    }
    if (
        type(exc) is MigrationIOError
        and len(exc.args) == 1
        and type(exc.args[0]) is str
        and exc.args[0] in PRIVATE_FAILURE_CODES
    ):
        result["code"] = exc.args[0]
    return result


def migration_active(record: dict[str, Any]) -> bool:
    return record.get("effect_possible") is True or record.get("state") in ACTIVE_STATES


def _wire(request: CellDeployRequest) -> dict[str, Any]:
    if request.runtime_env or request.business_config:
        raise CellIdentityConflict("migration accepts only publication evidence")
    return request.model_dump(mode="json", exclude={"runtime_env", "business_config"})


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PublicationMigrationService:
    def __init__(self, publication_service: Any, *, io: Any = None) -> None:
        self.publication = publication_service
        self.root = Path(publication_service.root)
        self._io = io
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_projects: dict[str, UUID] = {}

    @property
    def io(self) -> Any:
        if self._io is None:
            from yleum_orchestrator.services.publication_migration_io import PublicationMigrationIO

            self._io = PublicationMigrationIO(self.publication)
        return self._io

    def _path(self, project_id: UUID, operation_id: UUID) -> Path:
        return self.root / str(project_id) / "migrations" / f"{operation_id}.json"

    def _safe_path(self, path: Path) -> None:
        # UUID components are controller generated. Reject links at every owned level,
        # including Windows junctions; never resolve a foreign path into the controller.
        path.relative_to(self.root)
        for item in (path, *path.parents):
            if item.is_symlink() or item.is_junction():
                raise CellIdentityConflict("unsafe migration journal")

    def _load(self, project_id: UUID, operation_id: UUID) -> dict[str, Any]:
        path = self._path(project_id, operation_id)
        self._safe_path(path)
        try:
            if path.stat().st_size > MAX_JOURNAL_BYTES:
                raise ValueError
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                record["version"] != 1
                or record["project_id"] != str(project_id)
                or record["operation_id"] != str(operation_id)
                or record["state"] not in STATES
                or type(record["effect_possible"]) is not bool
                or record["request_digest"] != _digest(record["request"])
            ):
                raise ValueError
            request = CellDeployRequest.model_validate(record["request"])
            if (
                str(request.owner_id) != record["owner_id"]
                or str(request.project_id) != record["project_id"]
                or str(request.snapshot_id) != record["snapshot_id"]
            ):
                raise ValueError
            _wire(request)
            if record.get("prepared"):
                self._require_receipt_identity(record, record["prepared"])
            self._public(record)
            return cast(dict[str, Any], record)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise CellIdentityConflict("invalid migration journal") from exc

    def _records(self, project_id: UUID) -> list[dict[str, Any]]:
        directory = self.root / str(project_id) / "migrations"
        self._safe_path(directory)
        if not directory.exists():
            return []
        paths = list(directory.glob("*.json"))
        if len(paths) > MAX_PROJECT_OPERATIONS:
            raise CellIdentityConflict("migration journal capacity exceeded")
        try:
            records = [self._load(project_id, UUID(path.stem)) for path in paths]
            return sorted(records, key=lambda value: (value["created_at"], value["operation_id"]))
        except (ValueError, KeyError) as exc:
            raise CellIdentityConflict("invalid migration journal") from exc

    def _save(self, record: dict[str, Any]) -> None:
        path = self._path(UUID(record["project_id"]), UUID(record["operation_id"]))
        self._safe_path(path)
        record["updated_at"] = _now()
        if len(json.dumps(record).encode()) > MAX_JOURNAL_BYTES:
            raise CellIdentityConflict("migration journal capacity exceeded")
        write_controller_json(path, record)
        # write_controller_json fsyncs the file; Linux must also persist rename metadata.
        if os.name == "posix":
            directory = path.parent
            while True:
                fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if directory == self.root.parent:
                    break
                directory = directory.parent

    def _public(self, record: dict[str, Any]) -> PublicationMigrationStatus:
        prepared = record.get("prepared") or {}
        return PublicationMigrationStatus(
            operation_id=record["operation_id"],
            project_id=record["project_id"],
            snapshot_id=record["snapshot_id"],
            state=record["state"],
            plan_sha256=prepared.get("plan_sha256"),
            fields=prepared.get("public_fields", []),
            backup_verified=prepared.get("backup_verified") is True,
            expires_at=prepared.get("expires_at"),
            effect_possible=record["effect_possible"],
            error_code=record.get("error_code")
            if record.get("error_code") in ERROR_CODES
            else None,
        )

    @staticmethod
    def _require_receipt_identity(record: dict[str, Any], prepared: dict[str, Any]) -> None:
        if any(
            prepared.get(key) != record[key]
            for key in ("owner_id", "project_id", "snapshot_id", "operation_id")
        ):
            raise CellIdentityConflict("migration receipt identity mismatch")

    def get(self, project_id: UUID, owner_id: UUID) -> PublicationMigrationStatus | None:
        records = self._records(project_id)
        if not records:
            return None
        if any(record["owner_id"] != str(owner_id) for record in records):
            raise CellIdentityConflict("migration owner mismatch")
        # An unresolved old effect must never be hidden by a later display record.
        active = [record for record in records if migration_active(record)]
        return self._public((active or records)[-1])

    def require_no_active(self, project_id: UUID) -> None:
        if any(migration_active(record) for record in self._records(project_id)):
            raise CellIdentityConflict("publication migration active")

    def _require_publication(self, request: CellDeployRequest) -> None:
        saved = self.publication._read(request.project_id)
        if (
            saved.get("owner_id") != str(request.owner_id)
            or not saved.get("active_release")
            or any(
                saved.get(key) for key in ("disabled", "recovery_required", "activation_pending")
            )
            or any(
                item["response"]["phase"] in {"queued", "building", "swapping"}
                for item in saved.get("history", [])
            )
        ):
            raise CellIdentityConflict("migration publication state changed")

    def _launch(self, record: dict[str, Any], apply: bool = False) -> None:
        operation = record["operation_id"]
        task = asyncio.create_task(
            self._execute(UUID(record["project_id"]), UUID(operation), apply)
        )
        self._tasks[operation] = task
        self._task_projects[operation] = UUID(record["project_id"])

        def finished(_task: asyncio.Task[None]) -> None:
            self._tasks.pop(operation, None)
            self._task_projects.pop(operation, None)

        task.add_done_callback(finished)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()))

    async def prepare(
        self, request: CellDeployRequest, idempotency_key: str
    ) -> PublicationMigrationStatus:
        wire = _wire(request)
        key = str(UUID(str(idempotency_key)))
        async with self.publication._submission_lock.hold(request.project_id):
            records = self._records(request.project_id)
            for record in records:
                if record["idempotency_key"] == key:
                    if record["request_digest"] != _digest(wire):
                        raise CellIdentityConflict("migration idempotency mismatch")
                    return self._public(record)
            self.require_no_active(request.project_id)
            self._require_publication(request)
            if len(records) >= MAX_PROJECT_OPERATIONS:
                raise CellIdentityConflict("migration journal capacity exceeded")
            record = dict(
                version=1,
                operation_id=str(uuid4()),
                project_id=str(request.project_id),
                owner_id=str(request.owner_id),
                snapshot_id=str(request.snapshot_id),
                state="preparing",
                effect_possible=False,
                idempotency_key=key,
                request=wire,
                request_digest=_digest(wire),
                prepared={},
                created_at=_now(),
                error_code=None,
            )
            self._save(record)
            self._launch(record)
            return self._public(record)

    async def confirm(
        self,
        request: CellDeployRequest,
        operation_id: UUID,
        plan_sha256: str,
        explicit_intent: bool,
    ) -> PublicationMigrationStatus:
        wire = _wire(request)
        async with self.publication._submission_lock.hold(request.project_id):
            record = self._load(request.project_id, operation_id)
            if (
                explicit_intent is not True
                or record["request_digest"] != _digest(wire)
                or record["prepared"].get("plan_sha256") != plan_sha256
            ):
                raise CellIdentityConflict("migration confirmation mismatch")
            if record["state"] in {"applying", "reconciling", "applied"}:
                return self._public(record)
            if (
                record["state"] != "prepared"
                or record["prepared"].get("backup_verified") is not True
            ):
                raise CellIdentityConflict("migration is not prepared")
            if datetime.fromisoformat(record["prepared"]["expires_at"]) <= datetime.now(UTC):
                record.update(state="stale", error_code="migration_confirmation_stale")
                self._save(record)
                raise CellIdentityConflict("migration confirmation expired")
            self.require_no_active(request.project_id)
            self._require_publication(request)
            # Conservative until the IO proves an aborted/not-started transaction.
            record.update(state="applying", effect_possible=True)
            self._save(record)
            self._launch(record, apply=True)
            return self._public(record)

    @staticmethod
    def _proven_outcome(record: dict[str, Any], patch: dict[str, Any]) -> bool:
        try:
            proof = MigrationOutcomeProof.model_validate(patch.get("outcome_proof"))
        except ValidationError:
            return False
        prepared = record["prepared"]
        if proof.kind == "preflight_no_dispatch":
            return (
                patch.get("state") == "failed"
                and prepared.get("intent_xid") is None
                and prepared.get("effect_possible") is not True
            )
        if not proof.xid or proof.xid != prepared.get("intent_xid"):
            return False
        if proof.kind == "xid_aborted":
            return patch.get("state") == "failed"
        return (
            patch.get("state") == "applied"
            and prepared.get("precommit_verified") is True
            and prepared.get("precommit_plan_sha256") == prepared.get("plan_sha256")
        )

    def _patch(self, record: dict[str, Any], patch: dict[str, Any]) -> None:
        if patch.get("state", record["state"]) not in STATES:
            raise CellIdentityConflict("invalid migration transition")
        xid = record["prepared"].get("intent_xid")
        if xid is not None and patch.get("intent_xid", xid) != xid:
            raise CellIdentityConflict("migration transaction identity changed")
        if (
            record["effect_possible"]
            and patch.get("effect_possible") is False
            and not self._proven_outcome(record, patch)
        ):
            patch = {**patch, "state": "reconciling", "effect_possible": True}
        candidate = deepcopy(record)
        candidate["prepared"].update(patch)
        for key in ("state", "effect_possible"):
            if key in patch:
                candidate[key] = patch[key]
        if candidate["state"] == "failed":
            candidate["error_code"] = "migration_execution_failed"
        elif candidate["state"] in {"blocked", "reconciling"}:
            candidate["error_code"] = "migration_outcome_unknown"
        elif candidate["state"] == "applied":
            candidate["error_code"] = None
        self._save(candidate)
        record.update(candidate)

    async def _execute(self, project_id: UUID, operation_id: UUID, apply: bool) -> None:
        async with self.publication._submission_lock.hold(project_id):
            record = self._load(project_id, operation_id)
            if record["state"] != ("applying" if apply else "preparing"):
                return
            try:
                request = CellDeployRequest.model_validate(record["request"])
                self._require_publication(request)
                if apply:
                    patch = await self.io.apply(
                        request, record["prepared"], lambda value: self._patch(record, value)
                    )
                    self._patch(record, patch)
                else:
                    prepared = await self.io.prepare(request, operation_id)
                    self._require_receipt_identity(record, prepared)
                    expires = datetime.fromisoformat(prepared["expires_at"])
                    if (
                        prepared.get("backup_verified") is not True
                        or not prepared.get("plan_sha256")
                        or expires <= datetime.now(UTC)
                    ):
                        raise CellIdentityConflict("migration backup proof absent")
                    prepared["expires_at"] = min(
                        expires, datetime.now(UTC) + timedelta(seconds=PREPARED_TTL_SECONDS)
                    ).isoformat()
                    candidate = {**record, "prepared": prepared, "state": "prepared"}
                    self._public(candidate)
                    self._save(candidate)
                    record.update(candidate)
            except (Exception, asyncio.CancelledError) as exc:
                record["failure_diagnostic"] = _failure_diagnostic(exc, apply=apply)
                if record["effect_possible"]:
                    record.update(state="reconciling", error_code="migration_outcome_unknown")
                else:
                    record.update(state="failed", error_code="migration_preparation_failed")
                self._save(record)

    async def reconcile(self) -> list[dict[str, str]]:
        outcomes: list[dict[str, str]] = []
        self._safe_path(self.root)
        if not self.root.exists():
            return outcomes
        for path in self.root.iterdir():
            try:
                project_id = UUID(path.name)
            except ValueError:
                continue
            if project_id in self._task_projects.values():
                continue
            try:
                async with self.publication._submission_lock.hold(project_id):
                    for record in self._records(project_id):
                        if record["operation_id"] in self._tasks or not migration_active(record):
                            continue
                        if not record["effect_possible"]:
                            record.update(state="failed", error_code="migration_interrupted")
                            self._save(record)
                        else:
                            try:
                                self._patch(record, await self.io.reconcile(record["prepared"]))
                            except Exception:
                                record.update(
                                    state="blocked", error_code="migration_recovery_blocked"
                                )
                                self._save(record)
                        outcomes.append({"project_id": str(project_id), "state": record["state"]})
            except Exception:
                # Keep the inaccessible project's durable exclusion unchanged while
                # other migrations and the publication recovery loop make progress.
                outcomes.append({"project_id": str(project_id), "state": "blocked"})
        return outcomes


def get_publication_migration_service(
    publication_service: Any = None,
) -> PublicationMigrationService:
    if publication_service is None:
        from yleum_orchestrator.services.cell_publication import get_cell_publication_service

        publication_service = get_cell_publication_service()
    service = getattr(publication_service, "_migration_service", None)
    if service is None:
        service = PublicationMigrationService(publication_service)
        publication_service._migration_service = service
    return service
