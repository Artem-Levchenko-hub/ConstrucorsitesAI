"""Crash-safe, code-only activation for a proof-ready restoration adaptation.

The effect adapter owns the provider-specific CodeRestorationEngine integration.
This state machine owns identity binding, the point of no return, and replay.
The interface exposes no database mutation hook and verifies one live database
identity on both the source and code-only target.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from yleum_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from yleum_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationObservedIdentity,
    ActivationPreparedTarget,
    RestorationAdaptationActivationReceipt,
    RestorationAdaptationActivationRecoveryBatch,
    RestorationAdaptationActivationRecoveryFailure,
    RestorationAdaptationActivationRequest,
)
from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock
from yleum_orchestrator.services.project_machine import write_controller_json
from yleum_orchestrator.services.restoration_binding import canonical_digest


def _durable_mkdir(
    path: Path,
    *,
    managed_root: Path,
    sync_directory: Callable[[Path], None],
) -> None:
    """Create ``path`` and replay publication of its managed ancestor chain.

    ``managed_root`` is an existing, trusted boundary and is never chmodded.
    Every child edge below it is replayed even when the child already exists,
    closing a power-loss window between mkdir and the parent directory fsync.
    """

    path = Path(os.path.abspath(path))
    managed_root = Path(os.path.abspath(managed_root))
    try:
        relative = path.relative_to(managed_root)
    except ValueError as exc:
        raise CellIdentityConflict("restoration activation path leaves managed root") from exc
    if managed_root.is_symlink() or not managed_root.is_dir():
        raise CellIdentityConflict("unsafe restoration activation managed root")
    current = managed_root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise CellIdentityConflict("restoration activation path leaves managed root")
        child = current / part
        if child.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation path")
        if child.exists():
            if not child.is_dir():
                raise CellIdentityConflict("unsafe restoration activation path")
        else:
            try:
                child.mkdir(mode=0o700)
            except FileExistsError:
                if child.is_symlink() or not child.is_dir():
                    raise CellIdentityConflict("unsafe restoration activation path") from None
        try:
            child.chmod(0o700)
        except OSError:
            pass
        sync_directory(current)
        current = child
    if current != path:
        raise CellIdentityConflict("unsafe restoration activation path")


class RestorationAdaptationActivationEffects(Protocol):
    """Mandatory provider hooks.

    Every mutating hook must be idempotent, recheck the bound identity and
    fence, and leave the live business database volume untouched.
    """

    def hold_transition(
        self, request: RestorationAdaptationActivationRequest
    ) -> AbstractAsyncContextManager[None]: ...

    async def observe_bound_identities(
        self, request: RestorationAdaptationActivationRequest
    ) -> ActivationObservedIdentity: ...

    async def prepare_target_code(
        self,
        request: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
        target_code_volume: str,
    ) -> ActivationPreparedTarget: ...

    async def verify_prepared_target(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> str: ...

    async def cleanup_candidate(
        self, request: RestorationAdaptationActivationRequest, sealed_artifact: Path
    ) -> None: ...

    async def stop_source_writers(
        self, request: RestorationAdaptationActivationRequest
    ) -> None: ...

    async def start_target_writers(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> None: ...

    async def start_source_writers(
        self, request: RestorationAdaptationActivationRequest
    ) -> None: ...

    async def verify_source_health(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...

    async def delete_target_code(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> None: ...

    async def verify_service_readiness(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...

    async def verify_signed_owner_read(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...

    async def verify_signed_owner_write(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...

    async def verify_signed_owner_reload(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...

    async def verify_cross_owner_denial(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...

    async def verify_unauthenticated_denial(
        self, request: RestorationAdaptationActivationRequest
    ) -> str: ...


class RestorationAdaptationActivationEngine:
    def __init__(
        self,
        *,
        root: Path,
        offer_artifacts_root: Path,
        effects: RestorationAdaptationActivationEffects,
        managed_root: Path | None = None,
    ) -> None:
        self.root = Path(os.path.abspath(root))
        self.managed_root = Path(
            os.path.abspath(managed_root if managed_root is not None else self.root.parent)
        )
        self.effects = effects
        self.offer_artifacts_root = Path(os.path.abspath(offer_artifacts_root))
        try:
            self.offer_artifacts_root.relative_to(self.managed_root)
        except ValueError as exc:
            raise CellIdentityConflict(
                "restoration offer artifacts leave managed root"
            ) from exc
        if self.root.is_symlink() or self.offer_artifacts_root.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation root")
        _durable_mkdir(
            self.root,
            managed_root=self.managed_root,
            sync_directory=self._fsync_directory,
        )
        self._lock = WorkspaceOperationLock(self.root)

    async def activate(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt:
        async with self._hold(request):
            self._validate_request_safety(request)
            journal = self._load_or_create(request)
            if journal["state"] in {"activated", "cancelled"}:
                self._cleanup_terminal_artifact(request)
                return self._receipt(journal)
            if journal["state"] == "cancelling":
                return await self._cancel_before_admission(request, journal)
            return await self._advance(request, journal)

    async def status(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt:
        async with self._hold(request):
            self._validate_request_safety(request)
            self._validate_operation_binding(request)
            self._validate_activation_binding(request)
            journal = self._load(request)
            if journal is None:
                raise CellIdentityConflict("restoration activation does not exist")
            self._validate_envelope(journal, request)
            if journal["state"] in {"activated", "cancelled"}:
                self._cleanup_terminal_artifact(request)
            return self._receipt(journal)

    async def cancel(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt:
        async with self._hold(request):
            self._validate_request_safety(request)
            journal = self._load_or_create(request)
            if journal["state"] == "cancelled":
                self._cleanup_terminal_artifact(request)
                return self._receipt(journal)
            if journal.get("effects_admitted") is True:
                raise CellIdentityConflict(
                    "restoration activation cannot be cancelled after target writer admission"
                )
            return await self._cancel_before_admission(request, journal)

    async def recover(
        self, request: RestorationAdaptationActivationRequest
    ) -> RestorationAdaptationActivationReceipt:
        async with self._hold(request):
            self._validate_request_safety(request)
            self._validate_operation_binding(request)
            self._validate_activation_binding(request)
            journal = self._load(request)
            if journal is None:
                raise CellIdentityConflict("restoration activation does not exist")
            self._validate_envelope(journal, request)
            if journal["state"] in {"activated", "cancelled"}:
                self._cleanup_terminal_artifact(request)
                return self._receipt(journal)
            if journal["state"] == "cancelling":
                return await self._cancel_before_admission(request, journal)
            return await self._advance(request, journal)

    async def recover_all(self) -> RestorationAdaptationActivationRecoveryBatch:
        receipts: list[RestorationAdaptationActivationReceipt] = []
        failures: list[RestorationAdaptationActivationRecoveryFailure] = []
        paths = sorted(self.root.glob("*/*/activation.json"))
        for path in paths:
            operation_id = path.parent.parent.name
            activation_id = path.parent.name
            try:
                if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
                    raise CellIdentityConflict("unsafe restoration activation journal")
                value = self._read_json(path)
                request = RestorationAdaptationActivationRequest.model_validate(value["request"])
                if (
                    str(request.operation_id) != operation_id
                    or str(request.activation_id) != activation_id
                ):
                    raise CellIdentityConflict("restoration activation journal path changed")
            except (KeyError, TypeError, ValueError):
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=operation_id,
                        activation_id=activation_id,
                        error_code="invalid_journal",
                        retryable=False,
                    )
                )
                continue
            except CellIdentityConflict:
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=operation_id,
                        activation_id=activation_id,
                        error_code="invalid_journal",
                        retryable=False,
                    )
                )
                continue
            try:
                receipts.append(await self.recover(request))
            except CellIdentityConflict:
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=operation_id,
                        activation_id=activation_id,
                        error_code="identity_conflict",
                        retryable=False,
                    )
                )
            except Exception:
                failures.append(
                    RestorationAdaptationActivationRecoveryFailure(
                        operation_id=operation_id,
                        activation_id=activation_id,
                        error_code="recovery_failed",
                        retryable=True,
                    )
                )
        return RestorationAdaptationActivationRecoveryBatch(
            successes=tuple(receipts), failures=tuple(failures)
        )

    def protected_activation_ids(self) -> frozenset[UUID]:
        """Return every engine binding that may still need its candidate artifact."""

        protected: set[UUID] = set()
        for path in sorted(self.root.glob("*/*/activation.json")):
            try:
                activation_id = UUID(path.parent.name)
            except ValueError:
                continue
            if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
                protected.add(activation_id)
                continue
            try:
                value = self._read_json(path)
            except (CellIdentityConflict, OSError, TypeError, ValueError, json.JSONDecodeError):
                protected.add(activation_id)
                continue
            if value.get("state") not in {"activated", "cancelled"}:
                protected.add(activation_id)
        return frozenset(protected)

    @asynccontextmanager
    async def _hold(
        self, request: RestorationAdaptationActivationRequest
    ) -> AsyncIterator[None]:
        async with self._lock.hold_named(f"activation-id-{request.activation_id.hex}"):
            async with self._lock.hold_named(f"activation-{request.operation_id.hex}"):
                async with self._lock.hold(request.source_workspace_id):
                    # Serialize against ordinary Project Cell lifecycle work as
                    # well as other activation journals. Provider effects are
                    # deliberately invoked only while this shared lock is held.
                    async with self.effects.hold_transition(request):
                        yield

    async def _advance(
        self,
        request: RestorationAdaptationActivationRequest,
        journal: dict[str, Any],
    ) -> RestorationAdaptationActivationReceipt:
        resumed_from_target_prepared = journal["state"] == "target_prepared"
        resumed_from_writers_stopping = journal["state"] == "writers_stopping"
        identity_verified = False
        try:
            if journal["state"] == "intent":
                observed = await self.effects.observe_bound_identities(request)
                if observed != ActivationObservedIdentity.from_request(request):
                    raise CellIdentityConflict(
                        "restoration activation identity or fence changed"
                    )
                identity_verified = True
                artifact = await self._sealed_artifact(request)
                target = await self.effects.prepare_target_code(
                    request, artifact, self._target_code_volume(request)
                )
                self._validate_target(request, target)
                target_attestation = await self.effects.verify_prepared_target(
                    request, target
                )
                self._require_digest(target_attestation, "prepared target")
                journal["target"] = target.model_dump(mode="json")
                journal["target_attestation_digest"] = target_attestation
                journal["state"] = "target_prepared"
                self._write_journal(request, journal)

            if journal["state"] == "target_prepared":
                artifact = self._require_sealed_artifact(request)
                target = ActivationPreparedTarget.model_validate(journal["target"])
                if resumed_from_target_prepared:
                    target_attestation = await self.effects.verify_prepared_target(
                        request, target
                    )
                    self._require_digest(target_attestation, "prepared target")
                    if target_attestation != journal["target_attestation_digest"]:
                        raise CellIdentityConflict(
                            "restoration activation prepared target runnable content changed"
                        )
                await self.effects.cleanup_candidate(request, artifact)
                journal["state"] = "writers_stopping"
                self._write_journal(request, journal)

            if journal["state"] == "writers_stopping":
                if resumed_from_writers_stopping:
                    target = ActivationPreparedTarget.model_validate(journal["target"])
                    target_attestation = await self.effects.verify_prepared_target(
                        request, target
                    )
                    self._require_digest(target_attestation, "prepared target")
                    if target_attestation != journal["target_attestation_digest"]:
                        raise CellIdentityConflict(
                            "restoration activation prepared target runnable content changed"
                        )
                await self.effects.stop_source_writers(request)
                # PONR: durable before the first target writer can start. There
                # must be no await or external effect between this fsync and start.
                journal["state"] = "target_writers_admitted"
                journal["effects_admitted"] = True
                self._write_journal(request, journal)

            if journal["state"] == "target_writers_admitted":
                target = ActivationPreparedTarget.model_validate(journal["target"])
                await self.effects.start_target_writers(request, target)
                evidence = await self._target_health(request)
                journal["health_evidence"] = evidence
                journal["health_digest"] = canonical_digest(evidence)
                journal["state"] = "activated"
                self._write_journal(request, journal)
                self._cleanup_terminal_artifact(request)

            return self._receipt(journal)
        except CellIdentityConflict:
            raise
        except Exception as exc:
            if journal.get("effects_admitted") is True:
                self._write_journal(request, journal)
                raise CellResourceError(
                    "target restoration requires forward recovery"
                ) from exc
            if identity_verified or journal.get("state") in {
                "target_prepared",
                "writers_stopping",
            }:
                await self._cancel_before_admission(request, journal)
            raise

    async def _cancel_before_admission(
        self,
        request: RestorationAdaptationActivationRequest,
        journal: dict[str, Any],
    ) -> RestorationAdaptationActivationReceipt:
        if journal.get("effects_admitted") is True:
            raise CellIdentityConflict(
                "restoration activation cannot be cancelled after target writer admission"
            )
        if journal.get("state") != "cancelling":
            journal["state"] = "cancelling"
            self._write_journal(request, journal)
        await self.effects.start_source_writers(request)
        source_health = await self.effects.verify_source_health(request)
        self._require_digest(source_health, "source health")
        journal["rollback_health_digest"] = source_health
        self._write_journal(request, journal)
        target = ActivationPreparedTarget.model_validate(journal["target"])
        await self.effects.delete_target_code(request, target)
        journal["state"] = "cancelled"
        self._write_journal(request, journal)
        self._cleanup_terminal_artifact(request)
        return self._receipt(journal)

    async def _sealed_artifact(self, request: RestorationAdaptationActivationRequest) -> Path:
        source = self.offer_artifacts_root / f"{request.activation_id}.tar"
        if source.is_symlink() or not source.is_file():
            raise CellIdentityConflict(
                "sealed restoration activation offer artifact is unavailable"
            )
        if self._file_digest(source) != request.candidate_code_digest:
            raise CellIdentityConflict(
                "sealed restoration activation offer artifact changed"
            )
        return source

    def _require_sealed_artifact(
        self, request: RestorationAdaptationActivationRequest
    ) -> Path:
        path = self.offer_artifacts_root / f"{request.activation_id}.tar"
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict("sealed restoration activation artifact is unavailable")
        if self._file_digest(path) != request.candidate_code_digest:
            raise CellIdentityConflict("sealed restoration activation artifact changed")
        return path

    def _cleanup_terminal_artifact(
        self, request: RestorationAdaptationActivationRequest
    ) -> None:
        path = self.offer_artifacts_root / f"{request.activation_id}.tar"
        if path.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation artifact")
        if path.exists():
            if not path.is_file() or self._file_digest(path) != request.candidate_code_digest:
                raise CellIdentityConflict("sealed restoration activation artifact changed")
            path.unlink()
            self._fsync_directory(path.parent)

    async def _target_health(
        self, request: RestorationAdaptationActivationRequest
    ) -> dict[str, str]:
        evidence = {
            "service_readiness": await self.effects.verify_service_readiness(request),
            "signed_owner_read": await self.effects.verify_signed_owner_read(request),
            "signed_owner_write": await self.effects.verify_signed_owner_write(request),
            "signed_owner_reload": await self.effects.verify_signed_owner_reload(request),
            "cross_owner_denial": await self.effects.verify_cross_owner_denial(request),
            "unauthenticated_denial": await self.effects.verify_unauthenticated_denial(request),
        }
        for name, digest in evidence.items():
            self._require_digest(digest, name)
        return evidence

    def _load_or_create(
        self, request: RestorationAdaptationActivationRequest
    ) -> dict[str, Any]:
        self._bind_activation(request)
        self._bind_operation(request)
        saved = self._load(request)
        if saved is not None:
            self._validate_envelope(saved, request)
            return saved
        target = self._expected_target(request)
        value: dict[str, Any] = {
            "version": 1,
            "request_digest": request.digest(),
            "request": request.model_dump(mode="json"),
            "state": "intent",
            "effects_admitted": False,
            "target": target.model_dump(mode="json"),
            "target_attestation_digest": None,
            "health_digest": None,
        }
        self._write_journal(request, value)
        return value

    def _bind_operation(self, request: RestorationAdaptationActivationRequest) -> None:
        path = self._operation_path(request.operation_id)
        expected = self._operation_binding(request)
        if path.exists():
            if self._read_json(path) != expected:
                raise CellIdentityConflict("restoration activation envelope mismatch")
            return
        self._write(path, expected)

    def _validate_operation_binding(
        self, request: RestorationAdaptationActivationRequest
    ) -> None:
        path = self._operation_path(request.operation_id)
        if not path.exists() or self._read_json(path) != self._operation_binding(request):
            raise CellIdentityConflict("restoration activation envelope mismatch")

    def _bind_activation(self, request: RestorationAdaptationActivationRequest) -> None:
        path = self._activation_binding_path(request.activation_id)
        expected = self._activation_binding(request)
        if path.exists():
            if self._read_json(path) != expected:
                raise CellIdentityConflict("restoration activation envelope mismatch")
            return
        self._write(path, expected)

    def _validate_activation_binding(
        self, request: RestorationAdaptationActivationRequest
    ) -> None:
        path = self._activation_binding_path(request.activation_id)
        if not path.exists() or self._read_json(path) != self._activation_binding(request):
            raise CellIdentityConflict("restoration activation envelope mismatch")

    @staticmethod
    def _operation_binding(
        request: RestorationAdaptationActivationRequest,
    ) -> dict[str, object]:
        return {
            "version": 1,
            "operation_id": str(request.operation_id),
            "activation_id": str(request.activation_id),
            "activation_digest": request.activation_digest,
            "request_digest": request.digest(),
        }

    @staticmethod
    def _activation_binding(
        request: RestorationAdaptationActivationRequest,
    ) -> dict[str, object]:
        return {
            "version": 1,
            "activation_id": str(request.activation_id),
            "operation_id": str(request.operation_id),
            "activation_digest": request.activation_digest,
            "request_digest": request.digest(),
        }

    def _validate_envelope(
        self,
        journal: dict[str, Any],
        request: RestorationAdaptationActivationRequest,
    ) -> None:
        if (
            journal.get("version") != 1
            or journal.get("request_digest") != request.digest()
            or journal.get("request") != request.model_dump(mode="json")
        ):
            raise CellIdentityConflict("restoration activation envelope mismatch")
        try:
            target = ActivationPreparedTarget.model_validate(journal.get("target"))
        except (TypeError, ValueError) as exc:
            raise CellIdentityConflict("restoration activation target is invalid") from exc
        self._validate_target(request, target)
        state = journal.get("state")
        admitted = state in {"target_writers_admitted", "activated"}
        if state not in {
            "intent",
            "target_prepared",
            "writers_stopping",
            "cancelling",
            "target_writers_admitted",
            "activated",
            "cancelled",
        } or journal.get("effects_admitted") is not admitted:
            raise CellIdentityConflict("restoration activation journal state is invalid")
        target_attestation = journal.get("target_attestation_digest")
        if state == "intent":
            if target_attestation is not None:
                raise CellIdentityConflict(
                    "restoration activation target attestation is premature"
                )
        elif state in {
            "target_prepared",
            "writers_stopping",
            "target_writers_admitted",
            "activated",
        }:
            if not isinstance(target_attestation, str):
                raise CellIdentityConflict(
                    "restoration activation target attestation is invalid"
                )
            self._require_digest(target_attestation, "prepared target")
        elif target_attestation is not None:
            if not isinstance(target_attestation, str):
                raise CellIdentityConflict(
                    "restoration activation target attestation is invalid"
                )
            self._require_digest(target_attestation, "prepared target")
        health_digest = journal.get("health_digest")
        if state == "activated":
            evidence = journal.get("health_evidence")
            if not isinstance(evidence, dict) or canonical_digest(evidence) != health_digest:
                raise CellIdentityConflict("restoration activation health proof changed")
        elif health_digest is not None:
            raise CellIdentityConflict("restoration activation health proof is premature")

    def _validate_target(
        self,
        request: RestorationAdaptationActivationRequest,
        target: ActivationPreparedTarget,
    ) -> None:
        if target != self._expected_target(request):
            raise CellIdentityConflict(
                "restoration activation target or live database identity changed"
            )

    def _validate_request_safety(
        self, request: RestorationAdaptationActivationRequest
    ) -> None:
        target = self._target_code_volume(request).casefold()
        protected = {
            request.source_code_volume.casefold(),
            request.live_database_volume.casefold(),
        }
        if target in protected:
            raise CellIdentityConflict(
                "restoration activation target code volume aliases protected volume"
            )
        if len(protected) != 2:
            raise CellIdentityConflict(
                "restoration activation protected volume identities alias"
            )

    def _expected_target(
        self, request: RestorationAdaptationActivationRequest
    ) -> ActivationPreparedTarget:
        return ActivationPreparedTarget(
            workspace_id=request.source_workspace_id,
            fencing_epoch=request.target_fencing_epoch,
            code_volume=self._target_code_volume(request),
            code_digest=request.candidate_code_digest,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
        )

    def _source_identity(
        self, request: RestorationAdaptationActivationRequest
    ) -> ActivationPreparedTarget:
        return ActivationPreparedTarget(
            workspace_id=request.source_workspace_id,
            fencing_epoch=request.expected_source_fencing_epoch,
            code_volume=request.source_code_volume,
            code_digest=request.source_workspace_revision,
            database_volume=request.live_database_volume,
            database_identity_digest=request.live_database_identity_digest,
        )

    def _receipt(self, journal: dict[str, Any]) -> RestorationAdaptationActivationReceipt:
        request = RestorationAdaptationActivationRequest.model_validate(journal["request"])
        payload: dict[str, Any] = {
            "state": journal["state"],
            "effects_admitted": journal["effects_admitted"],
            "operation_id": request.operation_id,
            "generation_run_id": request.generation_run_id,
            "project_id": request.project_id,
            "owner_id": request.owner_id,
            "activation_id": request.activation_id,
            "activation_digest": request.activation_digest,
            "proof_digest": request.proof_digest,
            "fencing_epoch": request.target_fencing_epoch,
            "source_volume_identity": self._source_identity(request),
            "target_volume_identity": ActivationPreparedTarget.model_validate(journal["target"]),
            "health_digest": journal.get("health_digest"),
            "receipt_digest": None,
        }
        draft = RestorationAdaptationActivationReceipt.model_validate(payload)
        payload["receipt_digest"] = canonical_digest(
            draft.model_dump(mode="json", exclude={"receipt_digest"})
        )
        return RestorationAdaptationActivationReceipt.model_validate(payload)

    def _load(self, request: RestorationAdaptationActivationRequest) -> dict[str, Any] | None:
        path = self._journal_path(request)
        if not path.exists():
            return None
        return self._read_json(path)

    def _write_journal(
        self, request: RestorationAdaptationActivationRequest, value: dict[str, Any]
    ) -> None:
        self._write(self._journal_path(request), value)

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        write_controller_json(path, value)
        self._fsync_directory(path.parent)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict("unsafe restoration activation journal")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CellIdentityConflict("restoration activation journal is invalid") from exc
        if not isinstance(value, dict):
            raise CellIdentityConflict("restoration activation journal is invalid")
        return cast(dict[str, Any], value)

    def _operation_directory(self, operation_id: Any) -> Path:
        path = self.root / str(operation_id)
        if self.root.is_symlink() or path.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation path")
        _durable_mkdir(
            path,
            managed_root=self.managed_root,
            sync_directory=self._fsync_directory,
        )
        return path

    def _activation_directory(self, request: RestorationAdaptationActivationRequest) -> Path:
        path = self._operation_directory(request.operation_id) / str(request.activation_id)
        if path.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation path")
        _durable_mkdir(
            path,
            managed_root=self.managed_root,
            sync_directory=self._fsync_directory,
        )
        return path

    def _activation_binding_directory(self) -> Path:
        path = self.root / ".activation-bindings"
        if path.is_symlink():
            raise CellIdentityConflict("unsafe restoration activation path")
        _durable_mkdir(
            path,
            managed_root=self.managed_root,
            sync_directory=self._fsync_directory,
        )
        return path

    def _activation_binding_path(self, activation_id: Any) -> Path:
        return self._activation_binding_directory() / f"{activation_id}.json"

    def _operation_path(self, operation_id: Any) -> Path:
        return self._operation_directory(operation_id) / "operation.json"

    def _journal_path(self, request: RestorationAdaptationActivationRequest) -> Path:
        return self._activation_directory(request) / "activation.json"

    def _artifact_path(self, request: RestorationAdaptationActivationRequest) -> Path:
        return self._activation_directory(request) / "candidate-code.tar"

    @staticmethod
    def _target_code_volume(request: RestorationAdaptationActivationRequest) -> str:
        # MachineAdapter admits restored code volumes only under the bound
        # production workspace stem. The activation id keeps the target
        # deterministic and disjoint from exact-restoration operation ids.
        return (
            f"omnia-machine-{request.source_workspace_id.hex}"
            f"-code-{request.activation_id.hex}"
        )

    @staticmethod
    def _seal_file(path: Path, expected_digest: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict("unsafe restoration activation artifact")
        # Windows refuses fsync on a read-only descriptor. O_RDWR changes no
        # bytes here and keeps the same no-follow/regular-file validation.
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
                raise CellIdentityConflict("unsafe restoration activation artifact")
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                raise CellIdentityConflict("restoration activation code digest changed")
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _require_digest(value: str, name: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise CellIdentityConflict(f"{name} proof digest is invalid")
