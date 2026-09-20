"""Controller-owned offer and endpoint facade for adaptation activation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

import structlog

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.schemas.restoration_adaptation import RestorationAdaptationProof
from omnia_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    RestorationAdaptationActivationCommand,
    RestorationAdaptationActivationOffer,
    RestorationAdaptationActivationOfferRequest,
    RestorationAdaptationActivationReceipt,
    RestorationAdaptationActivationRecoveryBatch,
    RestorationAdaptationActivationRecoveryFailure,
    RestorationAdaptationActivationRequest,
    RestorationAdaptationActivationStatus,
)
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.project_machine import machine_effect, write_controller_json
from omnia_orchestrator.services.restoration_binding import canonical_digest

_log = structlog.get_logger("restoration_adaptation.activation_service")

_ORPHAN_ARTIFACT_RETENTION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class ActivationOfferMaterialization:
    expected_source_fencing_epoch: int
    target_fencing_epoch: int
    source_workspace_revision: str
    source_code_volume: str
    live_database_volume: str
    live_database_identity_digest: str
    candidate_artifact_digest: str
    candidate_source_manifest_digest: str
    candidate_files_digest: str
    archive_digest: str
    business_probe: ActivationBusinessProbe
    probe_contract_digest: str
    probe_rehearsal_digest: str
    probe_rehearsal_database_digest: str


class ActivationWorkspaceStore(Protocol):
    async def claim_activation_offer(
        self, request: RestorationAdaptationActivationOfferRequest, activation_id: UUID
    ) -> Any: ...

    async def seal_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        offer_digest: str,
    ) -> None: ...

    async def validate_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        offer_digest: str,
    ) -> Any: ...

    async def release_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        *,
        terminal_state: str,
    ) -> None: ...

    async def abort_activation_offer(
        self, request: RestorationAdaptationActivationOfferRequest, activation_id: UUID
    ) -> None: ...

    async def recover_activation_orphans(
        self, protected_activation_ids: frozenset[UUID]
    ) -> frozenset[UUID] | None: ...


class ActivationOfferAdapter(Protocol):
    async def materialize(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        proof: RestorationAdaptationProof,
        source_fencing_epoch: int,
        activation_id: UUID,
        destination: Path,
    ) -> ActivationOfferMaterialization: ...


class ActivationStateEngine(Protocol):
    async def activate(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt: ...

    async def status(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt: ...

    async def cancel(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt: ...

    async def recover(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt: ...

    async def recover_all(self) -> RestorationAdaptationActivationRecoveryBatch: ...

    def protected_activation_ids(self) -> frozenset[UUID]: ...


class RestorationAdaptationActivationService:
    def __init__(
        self,
        *,
        root: Path,
        workspace_service: ActivationWorkspaceStore,
        offer_adapter: ActivationOfferAdapter,
        activation_engine: ActivationStateEngine,
    ) -> None:
        self.root = Path(os.path.abspath(root))
        self.offers_root = self.root / "offers"
        self.artifacts_root = self.root / "offer-artifacts"
        self._prepare_root(self.root)
        self._durable_directory(self.offers_root)
        self._durable_directory(self.artifacts_root)
        self.workspace_service = workspace_service
        self.offer_adapter = offer_adapter
        self.activation_engine = activation_engine
        self._lock = WorkspaceOperationLock(self.root / "locks")

    def _path(self, workspace_id: UUID, generation_run_id: UUID) -> Path:
        directory = self.offers_root / str(workspace_id) / str(generation_run_id)
        if self.offers_root.is_symlink() or directory.parent.is_symlink() or directory.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation offer path")
        self._durable_directory(directory)
        return directory / "offer.json"

    def _artifact_path(self, activation_id: UUID) -> Path:
        path = self.artifacts_root / f"{activation_id}.tar"
        if self.artifacts_root.is_symlink() or path.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation offer artifact")
        return path

    @classmethod
    def _require_artifact_digest(cls, path: Path, expected_digest: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict(
                "sealed restoration activation offer artifact is unavailable"
            )
        if cls._file_digest(path) != expected_digest:
            raise CellIdentityConflict(
                "sealed restoration activation offer artifact changed"
            )

    def _delete_offer_artifact(
        self, offer: RestorationAdaptationActivationOffer
    ) -> None:
        path = self._artifact_path(offer.activation_id)
        if not path.exists():
            return
        if not path.is_file() or self._file_digest(path) != offer.archive_digest:
            raise CellIdentityConflict(
                "sealed restoration activation offer artifact changed"
            )
        path.unlink()
        self._fsync_directory(self.artifacts_root)

    def gc_artifacts(
        self,
        *,
        protected_activation_ids: frozenset[UUID] | None = None,
    ) -> tuple[str, ...]:
        """Remove only aged artifacts that have no durable facade journal binding."""

        strict_journal_registry = protected_activation_ids is None
        referenced = set(protected_activation_ids or ())
        for journal in sorted(self.offers_root.glob("*/*/offer.json")):
            if not strict_journal_registry:
                binding = self._valid_facade_journal(journal)
                if binding is not None:
                    referenced.add(binding[0])
                continue
            if journal.is_symlink() or not journal.is_file():
                return ()
            try:
                value = json.loads(journal.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    return ()
                referenced.add(UUID(str(value["activation_id"])))
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                # Without the workspace/engine registry a corrupt journal may
                # still own an artifact. Retain everything.
                return ()

        cutoff = time.time() - _ORPHAN_ARTIFACT_RETENTION_SECONDS
        removed: list[str] = []
        for path in sorted(self.artifacts_root.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix not in {".tar", ".partial"}:
                continue
            try:
                activation_id = UUID(path.stem)
                modified = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            if activation_id in referenced or modified >= cutoff:
                continue
            path.unlink()
            removed.append(path.name)
        if removed:
            self._fsync_directory(self.artifacts_root)
        return tuple(removed)

    def _valid_facade_journal(
        self, journal: Path
    ) -> tuple[UUID, dict[str, Any]] | None:
        if journal.is_symlink() or not journal.is_file():
            return None
        try:
            workspace_id = UUID(journal.parent.parent.name)
            generation_run_id = UUID(journal.parent.name)
            value = json.loads(journal.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            request = RestorationAdaptationActivationOfferRequest.model_validate(
                value["offer_request"]
            )
            activation_id = self._validate_offer_journal(request, value)
            if (
                request.workspace_id != workspace_id
                or request.generation_run_id != generation_run_id
            ):
                return None
            state = value.get("state")
            pre_offer_states = {"offer_intent", "offer_exported"}
            offer_states = {
                "offer_sealing",
                "offered",
                "offer_expiring",
                "offer_aborted",
                "expired",
            }
            command_states = {
                "applying",
                "cancelling",
                "intent",
                "target_prepared",
                "writers_stopping",
                "target_writers_admitted",
                "activated",
                "cancelled",
            }
            if state not in pre_offer_states | offer_states | command_states:
                return None
            raw_offer = value.get("offer")
            if state in pre_offer_states:
                if raw_offer is not None:
                    return None
                if state == "offer_exported":
                    self._load_materialization(value.get("materialization"))
                return activation_id, value
            offered = RestorationAdaptationActivationOffer.model_validate(raw_offer)
            if (
                offered.activation_id != activation_id
                or self._request_from_offer(offered) != request
            ):
                return None
            self._load_materialization(value.get("materialization"))
            raw_command = value.get("command")
            if state in command_states:
                command = RestorationAdaptationActivationCommand.model_validate(raw_command)
                engine_request = RestorationAdaptationActivationRequest.model_validate(
                    value["engine_request"]
                )
                if (
                    command.offer != offered
                    or value.get("command_digest") != command.digest()
                    or engine_request != self._engine_request(command)
                ):
                    return None
            elif raw_command is not None or value.get("command_digest") is not None:
                return None
            return activation_id, value
        except (KeyError, OSError, TypeError, ValueError, CellIdentityConflict):
            return None

    def _facade_protected_activation_ids(self) -> frozenset[UUID]:
        protected: set[UUID] = set()
        for journal in sorted(self.offers_root.glob("*/*/offer.json")):
            binding = self._valid_facade_journal(journal)
            if binding is None:
                continue
            activation_id, value = binding
            state = value.get("state")
            if state in {"offer_aborted", "expired"}:
                continue
            if state in {"activated", "cancelled"} and value.get("workspace_released") is True:
                continue
            protected.add(activation_id)
        return frozenset(protected)

    def _read(self, workspace_id: UUID, generation_run_id: UUID) -> dict[str, Any] | None:
        path = self._path(workspace_id, generation_run_id)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict("unsafe restoration activation offer journal")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CellIdentityConflict("restoration activation offer journal is invalid") from exc
        if not isinstance(value, dict):
            raise CellIdentityConflict("restoration activation offer journal is invalid")
        return cast(dict[str, Any], value)

    def _write(self, workspace_id: UUID, generation_run_id: UUID, value: dict[str, Any]) -> None:
        path = self._path(workspace_id, generation_run_id)
        write_controller_json(path, value)
        self._fsync_directory(path.parent)

    def _transition(
        self,
        workspace_id: UUID,
        generation_run_id: UUID,
        previous: dict[str, Any] | None,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare-and-swap one facade journal while its workspace lock is held."""

        current = self._read(workspace_id, generation_run_id)
        if current != previous:
            raise CellIdentityConflict("restoration activation journal changed")
        revision = 1 if previous is None else self._journal_revision(previous) + 1
        updated = {**value, "journal_revision": revision}
        self._write(workspace_id, generation_run_id, updated)
        return updated

    @staticmethod
    def _journal_revision(value: dict[str, Any]) -> int:
        revision = value.get("journal_revision", 0)
        if type(revision) is not int or revision < 0:
            raise CellIdentityConflict("restoration activation journal is invalid")
        return revision

    @classmethod
    def _prepare_root(cls, root: Path) -> None:
        root = Path(os.path.abspath(root))
        if root.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation root")
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.exists() and not root.is_dir():
            raise CellIdentityConflict("unsafe restoration activation root")
        root.mkdir(exist_ok=True, mode=0o700)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        cls._fsync_directory(root.parent)

    def _durable_directory(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise CellIdentityConflict("restoration activation path leaves managed root") from exc
        current = self.root
        for part in relative.parts:
            child = current / part
            if child.is_symlink() or (child.exists() and not child.is_dir()):
                raise CellIdentityConflict("unsafe restoration activation path")
            child.mkdir(exist_ok=True, mode=0o700)
            try:
                child.chmod(0o700)
            except OSError:
                pass
            self._fsync_directory(current)
            current = child

    @staticmethod
    def _request_from_offer(
        offer: RestorationAdaptationActivationOffer,
    ) -> RestorationAdaptationActivationOfferRequest:
        return RestorationAdaptationActivationOfferRequest(
            workspace_id=offer.workspace_id,
            operation_id=offer.operation_id,
            project_id=offer.project_id,
            owner_id=offer.owner_id,
            generation_run_id=offer.generation_run_id,
            candidate_workspace_id=offer.candidate_workspace_id,
            candidate_fencing_epoch=offer.candidate_fencing_epoch,
            candidate_workspace_revision=offer.candidate_workspace_revision,
            proof_attempt=offer.proof_attempt,
            proof_digest=offer.proof_digest,
        )

    @staticmethod
    def _offer_payload(
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        materialized: ActivationOfferMaterialization,
    ) -> dict[str, Any]:
        return {
            "state": "offered",
            **request.model_dump(mode="json"),
            "activation_id": str(activation_id),
            "expected_source_fencing_epoch": materialized.expected_source_fencing_epoch,
            "target_fencing_epoch": materialized.target_fencing_epoch,
            "source_workspace_revision": materialized.source_workspace_revision,
            "source_code_volume": materialized.source_code_volume,
            "live_database_volume": materialized.live_database_volume,
            "live_database_identity_digest": materialized.live_database_identity_digest,
            "candidate_artifact_digest": materialized.candidate_artifact_digest,
            "candidate_source_manifest_digest": materialized.candidate_source_manifest_digest,
            "candidate_files_digest": materialized.candidate_files_digest,
            "archive_digest": materialized.archive_digest,
            "business_probe": materialized.business_probe.model_dump(mode="json"),
            "probe_contract_digest": materialized.probe_contract_digest,
            "probe_rehearsal_digest": materialized.probe_rehearsal_digest,
            "probe_rehearsal_database_digest": materialized.probe_rehearsal_database_digest,
        }

    @staticmethod
    def _materialization_payload(
        materialized: ActivationOfferMaterialization,
    ) -> dict[str, Any]:
        return {
            "expected_source_fencing_epoch": materialized.expected_source_fencing_epoch,
            "target_fencing_epoch": materialized.target_fencing_epoch,
            "source_workspace_revision": materialized.source_workspace_revision,
            "source_code_volume": materialized.source_code_volume,
            "live_database_volume": materialized.live_database_volume,
            "live_database_identity_digest": materialized.live_database_identity_digest,
            "candidate_artifact_digest": materialized.candidate_artifact_digest,
            "candidate_source_manifest_digest": materialized.candidate_source_manifest_digest,
            "candidate_files_digest": materialized.candidate_files_digest,
            "archive_digest": materialized.archive_digest,
            "business_probe": materialized.business_probe.model_dump(mode="json"),
            "probe_contract_digest": materialized.probe_contract_digest,
            "probe_rehearsal_digest": materialized.probe_rehearsal_digest,
            "probe_rehearsal_database_digest": materialized.probe_rehearsal_database_digest,
        }

    @staticmethod
    def _load_materialization(value: object) -> ActivationOfferMaterialization:
        if not isinstance(value, dict):
            raise CellIdentityConflict("restoration activation offer export is invalid")
        try:
            return ActivationOfferMaterialization(
                expected_source_fencing_epoch=int(value["expected_source_fencing_epoch"]),
                target_fencing_epoch=int(value["target_fencing_epoch"]),
                source_workspace_revision=str(value["source_workspace_revision"]),
                source_code_volume=str(value["source_code_volume"]),
                live_database_volume=str(value["live_database_volume"]),
                live_database_identity_digest=str(value["live_database_identity_digest"]),
                candidate_artifact_digest=str(value["candidate_artifact_digest"]),
                candidate_source_manifest_digest=str(value["candidate_source_manifest_digest"]),
                candidate_files_digest=str(value["candidate_files_digest"]),
                archive_digest=str(value["archive_digest"]),
                business_probe=ActivationBusinessProbe.model_validate(value["business_probe"]),
                probe_contract_digest=str(value["probe_contract_digest"]),
                probe_rehearsal_digest=str(value["probe_rehearsal_digest"]),
                probe_rehearsal_database_digest=str(
                    value["probe_rehearsal_database_digest"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CellIdentityConflict("restoration activation offer export is invalid") from exc

    @staticmethod
    def _activation_id(request: RestorationAdaptationActivationOfferRequest) -> UUID:
        return uuid5(
            request.operation_id,
            f"restoration-adaptation-activation:{request.generation_run_id}",
        )

    def _validate_offer_journal(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        saved: dict[str, Any],
    ) -> UUID:
        self._journal_revision(saved)
        activation_id = self._activation_id(request)
        if (
            saved.get("version") != 2
            or saved.get("offer_request_digest") != request.digest()
            or saved.get("offer_request") != request.model_dump(mode="json")
            or saved.get("activation_id") != str(activation_id)
        ):
            raise CellIdentityConflict("restoration activation offer envelope mismatch")
        return activation_id

    async def _resume_offer_locked(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        saved: dict[str, Any],
    ) -> RestorationAdaptationActivationOffer:
        activation_id = self._validate_offer_journal(request, saved)
        raw_offer = saved.get("offer")
        if raw_offer is not None:
            try:
                offered = RestorationAdaptationActivationOffer.model_validate(raw_offer)
            except (TypeError, ValueError) as exc:
                raise CellIdentityConflict("restoration activation offer is invalid") from exc
            if (
                self._request_from_offer(offered) != request
                or offered.activation_id != activation_id
            ):
                raise CellIdentityConflict("restoration activation offer envelope mismatch")
            if saved.get("state") in {"activated", "cancelled"}:
                return offered
            self._require_artifact_digest(
                self._artifact_path(activation_id), offered.archive_digest
            )
            await self.workspace_service.seal_activation_offer(
                request, activation_id, offered.offer_digest
            )
            if saved.get("state") == "offer_sealing":
                saved = self._transition(
                    request.workspace_id,
                    request.generation_run_id,
                    saved,
                    {**saved, "state": "offered"},
                )
            return offered

        if saved.get("state") not in {"offer_intent", "offer_exported"}:
            raise CellIdentityConflict("restoration activation offer journal is invalid")
        durable_export = saved.get("materialization") is not None
        partial = self._artifact_path(activation_id).with_suffix(".partial")
        sealed = self._artifact_path(activation_id)
        try:
            claim = await self.workspace_service.claim_activation_offer(request, activation_id)
            proof = cast(RestorationAdaptationProof, getattr(claim, "proof", claim))
            source_fencing_epoch = int(getattr(claim, "source_fencing_epoch", 0))
            if durable_export:
                materialized = self._load_materialization(saved["materialization"])
            else:
                if partial.is_symlink():
                    raise CellIdentityConflict("unsafe restoration activation offer artifact")
                partial.unlink(missing_ok=True)
                materialized = await self.offer_adapter.materialize(
                    request,
                    proof,
                    source_fencing_epoch,
                    activation_id,
                    partial,
                )
                self._require_artifact_digest(partial, materialized.archive_digest)
                saved = self._transition(
                    request.workspace_id,
                    request.generation_run_id,
                    saved,
                    {
                        **saved,
                        "state": "offer_exported",
                        "materialization": self._materialization_payload(materialized),
                    },
                )
                durable_export = True

            if sealed.exists():
                self._require_artifact_digest(sealed, materialized.archive_digest)
                if partial.exists():
                    self._require_artifact_digest(partial, materialized.archive_digest)
                    partial.unlink()
                    self._fsync_directory(partial.parent)
            else:
                self._require_artifact_digest(partial, materialized.archive_digest)
                os.replace(partial, sealed)
                self._fsync_file(sealed)
                self._fsync_directory(sealed.parent)

            payload = self._offer_payload(request, activation_id, materialized)
            payload["offer_digest"] = canonical_digest(payload)
            offered = RestorationAdaptationActivationOffer.model_validate(payload)
            saved = self._transition(
                request.workspace_id,
                request.generation_run_id,
                saved,
                {**saved, "state": "offer_sealing", "offer": offered.model_dump(mode="json")},
            )
            await self.workspace_service.seal_activation_offer(
                request, activation_id, offered.offer_digest
            )
            self._transition(
                request.workspace_id,
                request.generation_run_id,
                saved,
                {**saved, "state": "offered"},
            )
            return offered
        except BaseException:
            if not durable_export:
                if partial.is_symlink():
                    raise CellIdentityConflict(
                        "unsafe restoration activation offer artifact"
                    ) from None
                partial.unlink(missing_ok=True)
                await asyncio.shield(
                    self.workspace_service.abort_activation_offer(request, activation_id)
                )
            raise

    async def offer(
        self, request: RestorationAdaptationActivationOfferRequest
    ) -> RestorationAdaptationActivationOffer:
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                activation_id = self._activation_id(request)
                saved = self._transition(
                    request.workspace_id,
                    request.generation_run_id,
                    None,
                    {
                        "version": 2,
                        "state": "offer_intent",
                        "activation_id": str(activation_id),
                        "offer_request_digest": request.digest(),
                        "offer_request": request.model_dump(mode="json"),
                        "materialization": None,
                        "offer": None,
                        "command_digest": None,
                        "command": None,
                        "engine_request": None,
                        "receipt": None,
                        "workspace_released": False,
                    },
                )
            return await self._resume_offer_locked(request, saved)

    @staticmethod
    def _engine_request(
        command: RestorationAdaptationActivationCommand,
    ) -> RestorationAdaptationActivationRequest:
        payload = command.activation_binding_payload()
        payload["activation_digest"] = command.activation_digest()
        return RestorationAdaptationActivationRequest.model_validate(payload)

    def _load_command(
        self, command: RestorationAdaptationActivationCommand
    ) -> tuple[dict[str, Any], RestorationAdaptationActivationRequest]:
        offer = command.offer
        saved = self._read(offer.workspace_id, offer.generation_run_id)
        if (
            saved is None
            or saved.get("version") != 2
            or saved.get("offer") != offer.model_dump(mode="json")
            or saved.get("offer_request_digest") != self._request_from_offer(offer).digest()
        ):
            raise CellIdentityConflict("restoration activation offer envelope mismatch")
        self._journal_revision(saved)
        existing = saved.get("command_digest")
        if existing is not None and existing != command.digest():
            raise CellIdentityConflict("restoration activation envelope mismatch")
        expected_request = self._engine_request(command)
        if existing is None:
            engine_request = expected_request
        else:
            try:
                engine_request = RestorationAdaptationActivationRequest.model_validate(
                    saved["engine_request"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise CellIdentityConflict("restoration activation envelope mismatch") from exc
            if engine_request != expected_request:
                raise CellIdentityConflict("restoration activation envelope mismatch")
        return saved, engine_request

    async def _record_command(
        self, command: RestorationAdaptationActivationCommand
    ) -> RestorationAdaptationActivationRequest:
        offer = command.offer
        async with self._lock.hold(offer.workspace_id):
            saved, engine_request = self._load_command(command)
            if saved.get("command_digest") is None:
                await self.workspace_service.validate_activation_offer(
                    self._request_from_offer(offer),
                    offer.activation_id,
                    offer.offer_digest,
                )
                self._transition(
                    offer.workspace_id,
                    offer.generation_run_id,
                    saved,
                    {
                        **saved,
                        "state": "applying",
                        "command_digest": command.digest(),
                        "command": command.model_dump(mode="json"),
                        "engine_request": engine_request.model_dump(mode="json"),
                    },
                )
            return engine_request

    async def _release_terminal_locked(
        self,
        saved: dict[str, Any],
        command: RestorationAdaptationActivationCommand,
        status: RestorationAdaptationActivationStatus,
    ) -> dict[str, Any]:
        if status.state not in {"activated", "cancelled"}:
            return saved
        released = saved.get("workspace_released", False)
        if type(released) is not bool:
            raise CellIdentityConflict("restoration activation journal is invalid")
        if released:
            return saved
        await self.workspace_service.release_activation_offer(
            self._request_from_offer(command.offer),
            command.offer.activation_id,
            terminal_state=status.state,
        )
        return self._transition(
            command.offer.workspace_id,
            command.offer.generation_run_id,
            saved,
            {**saved, "workspace_released": True},
        )

    def _persist_status_locked(
        self,
        saved: dict[str, Any],
        command: RestorationAdaptationActivationCommand,
        status: RestorationAdaptationActivationStatus,
    ) -> dict[str, Any]:
        next_state = status.state
        if saved.get("state") == "cancelling" and status.state != "cancelled":
            if status.effects_admitted:
                raise CellIdentityConflict(
                    "restoration activation cannot be cancelled after target writer admission"
                )
            # The facade tombstone is the durable user decision. An older
            # engine receipt cannot reopen apply after a crash between the
            # facade write and engine.cancel().
            next_state = "cancelling"
        return self._transition(
            command.offer.workspace_id,
            command.offer.generation_run_id,
            saved,
            {
                **saved,
                "state": next_state,
                "receipt": status.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _saved_status(saved: dict[str, Any]) -> RestorationAdaptationActivationStatus | None:
        value = saved.get("receipt")
        if value is None:
            return None
        try:
            return RestorationAdaptationActivationStatus.model_validate(value)
        except (TypeError, ValueError) as exc:
            raise CellIdentityConflict("restoration activation receipt is invalid") from exc

    async def apply(
        self, command: RestorationAdaptationActivationCommand
    ) -> RestorationAdaptationActivationStatus:
        offer = command.offer
        async with self._lock.hold(offer.workspace_id):
            saved, engine_request = self._load_command(command)
            if saved.get("state") in {"cancelling", "cancelled"}:
                raise CellIdentityConflict("restoration activation was cancelled")
            prior_status = self._saved_status(saved)
            if prior_status is not None and prior_status.state == "activated":
                await self._release_terminal_locked(saved, command, prior_status)
                return prior_status
            if saved.get("command_digest") is None:
                proof_request = self._request_from_offer(offer)
                await self.workspace_service.validate_activation_offer(
                    proof_request, offer.activation_id, offer.offer_digest
                )
                saved = self._transition(
                    offer.workspace_id,
                    offer.generation_run_id,
                    saved,
                    {
                        **saved,
                        "state": "applying",
                        "command_digest": command.digest(),
                        "command": command.model_dump(mode="json"),
                        "engine_request": engine_request.model_dump(mode="json"),
                    },
                )
            receipt = await self.activation_engine.activate(engine_request)
            status = self._status(command, receipt)
            saved = self._persist_status_locked(saved, command, status)
            await self._release_terminal_locked(saved, command, status)
            return status

    async def status(
        self, command: RestorationAdaptationActivationCommand
    ) -> RestorationAdaptationActivationStatus:
        offer = command.offer
        async with self._lock.hold(offer.workspace_id):
            saved, engine_request = self._load_command(command)
            if saved.get("command_digest") is None:
                raise CellIdentityConflict("restoration activation has not been applied")
            if saved.get("state") == "cancelling":
                receipt = await self.activation_engine.cancel(engine_request)
                status = self._status(command, receipt)
                saved = self._persist_status_locked(saved, command, status)
                await self._release_terminal_locked(saved, command, status)
                return status
            prior_status = self._saved_status(saved)
            if prior_status is not None and prior_status.state in {"activated", "cancelled"}:
                await self._release_terminal_locked(saved, command, prior_status)
                return prior_status
            receipt = await self.activation_engine.status(engine_request)
            status = self._status(command, receipt)
            saved = self._persist_status_locked(saved, command, status)
            await self._release_terminal_locked(saved, command, status)
            return status

    async def cancel(
        self, command: RestorationAdaptationActivationCommand
    ) -> RestorationAdaptationActivationStatus:
        offer = command.offer
        async with self._lock.hold(offer.workspace_id):
            saved, engine_request = self._load_command(command)
            prior_status = self._saved_status(saved)
            if saved.get("state") == "cancelled" and prior_status is not None:
                await self._release_terminal_locked(saved, command, prior_status)
                return prior_status
            if prior_status is not None and prior_status.effects_admitted:
                raise CellIdentityConflict(
                    "restoration activation cannot be cancelled after target writer admission"
                )
            if (
                saved.get("state") != "cancelling"
                and saved.get("command_digest") is not None
                and prior_status is None
            ):
                try:
                    observed_receipt = await self.activation_engine.status(engine_request)
                except CellIdentityConflict as exc:
                    if str(exc) != "restoration activation does not exist":
                        raise
                else:
                    observed_status = self._status(command, observed_receipt)
                    saved = self._persist_status_locked(saved, command, observed_status)
                    if observed_status.effects_admitted:
                        await self._release_terminal_locked(saved, command, observed_status)
                        raise CellIdentityConflict(
                            "restoration activation cannot be cancelled after "
                            "target writer admission"
                        )
            if saved.get("state") != "cancelling":
                saved = self._transition(
                    offer.workspace_id,
                    offer.generation_run_id,
                    saved,
                    {
                        **saved,
                        "state": "cancelling",
                        "command_digest": command.digest(),
                        "command": command.model_dump(mode="json"),
                        "engine_request": engine_request.model_dump(mode="json"),
                    },
                )
            receipt = await self.activation_engine.cancel(engine_request)
            status = self._status(command, receipt)
            saved = self._persist_status_locked(saved, command, status)
            await self._release_terminal_locked(saved, command, status)
            return status

    @staticmethod
    def _status(
        command: RestorationAdaptationActivationCommand,
        receipt: RestorationAdaptationActivationReceipt,
    ) -> RestorationAdaptationActivationStatus:
        payload = {
            "state": receipt.state,
            "effects_admitted": receipt.effects_admitted,
            "offer": command.offer.model_dump(mode="json"),
            "planned_commit_sha": command.planned_commit_sha,
            "activation_digest": receipt.activation_digest,
            "fencing_epoch": receipt.fencing_epoch,
            "source_volume_identity": receipt.source_volume_identity.model_dump(mode="json"),
            "target_volume_identity": receipt.target_volume_identity.model_dump(mode="json"),
            "health_digest": receipt.health_digest,
            "receipt_digest": receipt.receipt_digest,
        }
        return RestorationAdaptationActivationStatus.model_validate(payload)

    async def recover_all(self) -> RestorationAdaptationActivationRecoveryBatch:
        successes: list[RestorationAdaptationActivationReceipt] = []
        failures: list[RestorationAdaptationActivationRecoveryFailure] = []
        for path in sorted(self.offers_root.glob("*/*/offer.json")):
            workspace = path.parent.parent.name
            run = path.parent.name
            try:
                workspace_id, generation_run_id = UUID(workspace), UUID(run)
            except ValueError:
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=workspace,
                        activation_id=run,
                        error_code="invalid_journal",
                        retryable=False,
                    )
                )
                continue
            try:
                async with self._lock.hold(workspace_id):
                    saved = self._read(workspace_id, generation_run_id)
                    if saved is None:
                        continue
                    self._journal_revision(saved)
                    if saved.get("command") is None:
                        if saved.get("state") in {"offer_aborted", "expired"}:
                            offered = RestorationAdaptationActivationOffer.model_validate(
                                saved["offer"]
                            )
                            self._delete_offer_artifact(offered)
                            continue
                        if saved.get("state") in {"offered", "offer_expiring"}:
                            offered = RestorationAdaptationActivationOffer.model_validate(
                                saved["offer"]
                            )
                            proof_request = self._request_from_offer(offered)
                            if saved.get("state") == "offered":
                                try:
                                    await self.workspace_service.validate_activation_offer(
                                        proof_request,
                                        offered.activation_id,
                                        offered.offer_digest,
                                    )
                                except CellIdentityConflict:
                                    saved = self._transition(
                                        workspace_id,
                                        generation_run_id,
                                        saved,
                                        {**saved, "state": "offer_expiring"},
                                    )
                                else:
                                    continue
                            if saved.get("state") == "offer_expiring":
                                await self.workspace_service.abort_activation_offer(
                                    proof_request, offered.activation_id
                                )
                                saved = self._transition(
                                    workspace_id,
                                    generation_run_id,
                                    saved,
                                    {**saved, "state": "offer_aborted"},
                                )
                                self._delete_offer_artifact(offered)
                            continue
                        request = RestorationAdaptationActivationOfferRequest.model_validate(
                            saved["offer_request"]
                        )
                        if (
                            request.workspace_id != workspace_id
                            or request.generation_run_id != generation_run_id
                        ):
                            raise CellIdentityConflict(
                                "restoration activation journal path changed"
                            )
                        await self._resume_offer_locked(request, saved)
                        continue
                    command = RestorationAdaptationActivationCommand.model_validate(
                        saved["command"]
                    )
                    saved, engine_request = self._load_command(command)
                    prior_status = self._saved_status(saved)
                    if prior_status is not None and prior_status.state in {
                        "activated",
                        "cancelled",
                    }:
                        await self._release_terminal_locked(saved, command, prior_status)
                        continue
                    if saved.get("state") == "cancelling":
                        receipt = await self.activation_engine.cancel(engine_request)
                    else:
                        # A facade intent is durable before the engine journal. The
                        # public idempotent commands create or resume that journal.
                        receipt = await self.activation_engine.activate(engine_request)
                    status = self._status(command, receipt)
                    saved = self._persist_status_locked(saved, command, status)
                    await self._release_terminal_locked(saved, command, status)
                    successes.append(receipt)
            except (KeyError, TypeError, ValueError):
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=workspace,
                        activation_id=run,
                        error_code="invalid_journal",
                        retryable=False,
                    )
                )
            except CellIdentityConflict as exc:
                invalid_journal = str(exc) in {
                    "restoration activation offer journal is invalid",
                    "unsafe restoration activation offer journal",
                    "restoration activation journal is invalid",
                }
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=workspace,
                        activation_id=run,
                        error_code=("invalid_journal" if invalid_journal else "identity_conflict"),
                        retryable=False,
                    )
                )
            except Exception:
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=workspace,
                        activation_id=run,
                        error_code="recovery_failed",
                        retryable=True,
                    )
                )

        try:
            engine_batch = await self.activation_engine.recover_all()
        except Exception:
            failures.append(
                RestorationAdaptationActivationRecoveryFailure(
                    operation_id="engine",
                    activation_id="engine",
                    error_code="recovery_failed",
                    retryable=True,
                )
            )
        else:
            known = {(item.operation_id, item.activation_id) for item in successes}
            for receipt in engine_batch.successes:
                identity = (receipt.operation_id, receipt.activation_id)
                if identity not in known:
                    successes.append(receipt)
                    known.add(identity)
            failures.extend(engine_batch.failures)
        protected = set(self._facade_protected_activation_ids())
        registry_available = True
        try:
            protected.update(self.activation_engine.protected_activation_ids())
        except Exception:
            registry_available = False
            failures.append(
                RestorationAdaptationActivationRecoveryFailure(
                    operation_id="engine",
                    activation_id="activation-registry",
                    error_code="recovery_failed",
                    retryable=True,
                )
            )
        retained: frozenset[UUID] | None = None
        if registry_available:
            try:
                retained = await self.workspace_service.recover_activation_orphans(
                    frozenset(protected)
                )
            except Exception:
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id="workspace",
                        activation_id="activation-pins",
                        error_code="recovery_failed",
                        retryable=True,
                    )
                )
        if retained is None:
            if registry_available and not any(
                item.operation_id == "workspace"
                and item.activation_id == "activation-pins"
                for item in failures
            ):
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id="workspace",
                        activation_id="activation-pins",
                        error_code="invalid_journal",
                        retryable=False,
                    )
                )
        else:
            protected.update(retained)
            self.gc_artifacts(protected_activation_ids=frozenset(protected))
        return RestorationAdaptationActivationRecoveryBatch(
            successes=tuple(successes), failures=tuple(failures)
        )

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


ProbeValidator = Callable[[dict[str, str], Any], ActivationBusinessProbe]


class DockerActivationOfferAdapter:
    def __init__(
        self,
        *,
        code_engine: Any,
        candidate_manager_factory: Callable[[Any], Any],
        probe_validator: ProbeValidator,
    ) -> None:
        self.code_engine = code_engine
        self.candidate_manager_factory = candidate_manager_factory
        self.probe_validator = probe_validator

    async def materialize(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        proof: RestorationAdaptationProof,
        source_fencing_epoch: int,
        activation_id: UUID,
        destination: Path,
    ) -> ActivationOfferMaterialization:
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files
        from omnia_orchestrator.services.code_restoration_engine import validate_supported_runtime
        from omnia_orchestrator.services.restoration_adaptation_activation_effects import (
            live_database_volume_identity_digest,
        )
        from omnia_orchestrator.services.restoration_adaptation_source import (
            canonical_source_files,
            source_manifest_digest,
            write_source_archive,
        )
        from omnia_orchestrator.services.restoration_catalog import catalog_contract

        manager = self.code_engine.activation_manager(request.workspace_id)
        async with manager.operation_lock.hold(request.workspace_id):
            state = manager.state_store.load(request.workspace_id)
            if (
                state is None
                or state.project_id != request.project_id
                or state.owner_id != request.owner_id
                or state.active_generation_run_id != request.generation_run_id
                or state.active_generation_fencing_epoch != source_fencing_epoch
                or state.fencing_epoch != source_fencing_epoch
                or manager.machine_runtime is None
            ):
                raise CellIdentityConflict("restoration activation source lease changed")
            _machine, source = manager.machine_runtime.parts(state)
            live_files = await _read_agent_workspace_files(manager, source.workspace_volume)
            if _workspace_revision(live_files) != proof.source_workspace_revision:
                raise CellIdentityConflict("restoration activation source revision changed")
            _contract, blockers = await machine_effect(catalog_contract, source)
            if blockers:
                raise CellIdentityConflict("restoration activation source contract unavailable")
            live_database_identity_digest = live_database_volume_identity_digest(
                source,
                expected_volume=source.project_postgres_volume,
            )

        candidate_manager = self.candidate_manager_factory(manager)
        async with candidate_manager.operation_lock.hold(request.candidate_workspace_id):
            state = candidate_manager.state_store.load(request.candidate_workspace_id)
            if (
                state is None
                or state.project_id != request.project_id
                or state.owner_id != request.owner_id
                or state.active_generation_run_id != request.generation_run_id
                or state.active_generation_fencing_epoch != request.candidate_fencing_epoch
                or candidate_manager.machine_runtime is None
            ):
                raise CellIdentityConflict("restoration activation candidate lease changed")
            _machine, candidate = candidate_manager.machine_runtime.parts(state)
            candidate_files = await _read_agent_workspace_files(
                candidate_manager, candidate.workspace_volume
            )
            candidate_source_files = canonical_source_files(
                await candidate_manager.docker.read_workspace_source_files(
                    candidate.workspace_volume
                )
            )
            candidate_files = {
                path: content
                for path, content in candidate_files.items()
                if path in candidate_source_files
            }
            validate_supported_runtime(candidate_files)
            if (
                _workspace_revision(candidate_files) != request.candidate_workspace_revision
                or self._candidate_artifact_digest(candidate_files)
                != proof.candidate_artifact_digest
                or source_manifest_digest(candidate_source_files)
                != proof.candidate_source_manifest_digest
            ):
                raise CellIdentityConflict("restoration activation candidate code changed")
            candidate_contract, blockers = await machine_effect(catalog_contract, candidate)
            if blockers:
                raise CellIdentityConflict("restoration activation candidate contract unavailable")
            business_probe = self.probe_validator(candidate_files, candidate_contract)
            if (
                business_probe.contract_digest != proof.probe_contract_digest
                or proof.probe_rehearsal_digest is None
                or proof.probe_rehearsal_database_digest is None
            ):
                raise CellIdentityConflict("restoration activation rehearsal proof changed")
            archive_digest = write_source_archive(candidate_source_files, destination)
        if (
            proof.proof_digest != request.proof_digest
            or proof.proof_attempt != request.proof_attempt
        ):
            raise CellIdentityConflict("restoration activation proof changed")
        return ActivationOfferMaterialization(
            expected_source_fencing_epoch=source_fencing_epoch,
            target_fencing_epoch=source_fencing_epoch + 1,
            source_workspace_revision=proof.source_workspace_revision,
            source_code_volume=source.workspace_volume,
            live_database_volume=source.project_postgres_volume,
            live_database_identity_digest=live_database_identity_digest,
            candidate_artifact_digest=proof.candidate_artifact_digest,
            candidate_source_manifest_digest=proof.candidate_source_manifest_digest,
            candidate_files_digest=canonical_digest(
                [
                    {"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                    for path, content in sorted(candidate_files.items())
                ]
            ),
            archive_digest=archive_digest,
            business_probe=business_probe,
            probe_contract_digest=business_probe.contract_digest,
            probe_rehearsal_digest=proof.probe_rehearsal_digest,
            probe_rehearsal_database_digest=proof.probe_rehearsal_database_digest,
        )

    @staticmethod
    def _candidate_artifact_digest(files: dict[str, str]) -> str:
        digest = hashlib.sha256()
        for path, content in sorted(files.items()):
            path_bytes, content_bytes = path.encode(), content.encode()
            digest.update(len(path_bytes).to_bytes(8, "big"))
            digest.update(path_bytes)
            digest.update(len(content_bytes).to_bytes(8, "big"))
            digest.update(content_bytes)
        return digest.hexdigest()


_service: RestorationAdaptationActivationService | None = None


def get_restoration_adaptation_activation_service() -> RestorationAdaptationActivationService:
    global _service
    if _service is None:
        from omnia_orchestrator.core.config import get_settings
        from omnia_orchestrator.services.cell_publication_capacity import production_manager
        from omnia_orchestrator.services.code_restorations import get_code_restoration_service
        from omnia_orchestrator.services.restoration_adaptation_activation import (
            RestorationAdaptationActivationEngine,
        )
        from omnia_orchestrator.services.restoration_adaptation_activation_effects import (
            DockerRestorationAdaptationActivationEffects,
        )
        from omnia_orchestrator.services.restoration_adaptation_health import (
            DockerRestorationAdaptationHealthProber,
        )
        from omnia_orchestrator.services.restoration_adaptation_probe import (
            validate_probe_contract,
        )
        from omnia_orchestrator.services.restoration_adaptation_workspace import (
            get_restoration_adaptation_workspace_service,
        )

        settings = get_settings()
        root = Path(settings.cell_state_path).parent / "restoration-adaptation-activations"
        # The engine requires its managed root to exist and be durably published.
        # Do this before constructing any child component on a fresh installation.
        RestorationAdaptationActivationService._prepare_root(root)
        code_engine = get_code_restoration_service()._engine()

        def candidate_factory(manager: Any) -> Any:
            return production_manager(manager, settings)

        prober = DockerRestorationAdaptationHealthProber(code_engine=code_engine)
        effects = DockerRestorationAdaptationActivationEffects(
            code_engine=cast(Any, code_engine),
            candidate_manager_factory=candidate_factory,
            health_prober=cast(Any, prober),
        )
        engine = RestorationAdaptationActivationEngine(
            root=root / "engine",
            offer_artifacts_root=root / "offer-artifacts",
            effects=effects,
            managed_root=root,
        )
        adapter = DockerActivationOfferAdapter(
            code_engine=code_engine,
            candidate_manager_factory=candidate_factory,
            probe_validator=validate_probe_contract,
        )
        _service = RestorationAdaptationActivationService(
            root=root,
            workspace_service=get_restoration_adaptation_workspace_service(),
            offer_adapter=adapter,
            activation_engine=engine,
        )
    return _service


def start_restoration_adaptation_activation_recovery() -> asyncio.Task[None]:
    async def run() -> None:
        while True:
            try:
                batch = await get_restoration_adaptation_activation_service().recover_all()
                for failure in batch.failures:
                    _log.warning("journal_recovery_failed", **failure.model_dump())
            except Exception as exc:
                _log.warning("recovery_failed", error_type=type(exc).__name__)
            await asyncio.sleep(30)

    return asyncio.create_task(run(), name="restoration-adaptation-activation-recovery")
