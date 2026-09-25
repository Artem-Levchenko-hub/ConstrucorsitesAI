"""Durable isolated database copy used only by one restoration-adaptation run."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid5

import structlog

from omnia_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationCleanup,
    RestorationAdaptationOwnerStatus,
    RestorationAdaptationPrepare,
    RestorationAdaptationProof,
    RestorationAdaptationProofRequest,
    RestorationAdaptationWorkspace,
)
from omnia_orchestrator.schemas.restoration_adaptation_activation import (
    RestorationAdaptationActivationOfferRequest,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.code_restoration_engine import (
    CodeRestorationEngine,
    observe_database,
    validate_supported_runtime,
)
from omnia_orchestrator.services.machine_environment import MachineEnvironmentRef
from omnia_orchestrator.services.project_machine import (
    machine_effect,
    write_controller_json,
)
from omnia_orchestrator.services.restoration_adaptation_source import (
    canonical_source_files,
    source_manifest_digest,
)
from omnia_orchestrator.services.restoration_binding import (
    canonical_digest,
)
from omnia_orchestrator.services.restoration_catalog import catalog_contract
from omnia_orchestrator.services.restoration_database import admin_sql
from omnia_orchestrator.services.versioning.contracts import InventoryReport
from omnia_orchestrator.services.versioning.inventory import quote_ident

_CAPABILITIES: dict[str, object] = {
    "portable_machine": True,
    "database_admin": "isolated_copy",
    "restoration_adaptation_database_copy_v1": True,
}
_PROOF_CAPABILITIES: dict[str, object] = {
    **_CAPABILITIES,
    "restoration_adaptation_proof_v1": True,
}
# Имя нарушенного правила — это фраза разработчика, а не данные: только строчные
# латинские слова. Всё, что не такое, до отчёта не доезжает: лучше промолчать,
# чем вынести наружу кусок чужой базы.
_RULE_NAME = re.compile(r"^[a-z][a-z0-9 ]{0,119}$")


def _rule_name(exc: BaseException) -> str | None:
    text = str(exc).strip()
    return text if _RULE_NAME.fullmatch(text) else None


_CANDIDATE_RECEIPT_STATES = {
    "preparing",
    "ready",
    "proving",
    "proof_ready",
    "migration_required",
    "source_changed",
    "proof_failed",
    "cleanup_pending",
}
_ACTIVATION_PIN_STATES = {
    "activation_offering",
    "activation_offered",
    "activation_activated",
}
_DEFAULT_RECEIPT_TTL = timedelta(hours=1)
_MAX_CLEANUP_RETRY_DEPTH = 16
_log = structlog.get_logger("omnia_orchestrator.restoration_adaptation")


def content_inventory_partition_digests(
    backend: Any,
    inventory: InventoryReport,
) -> tuple[str, str]:
    """Hash every stored row from one read-only repeatable-read snapshot.

    PostgreSQL returns only counts and SHA-256 values. Row values, including
    ids and application-hidden columns, never cross the controller boundary.
    """

    measured = sorted(
        (
            item
            for item in inventory.objects
            if item.classification in {"business", "unknown", "technical"}
        ),
        key=lambda item: (item.object, item.kind),
    )
    if any(item.kind not in {"table", "partitioned_table"} for item in measured):
        raise CellIdentityConflict("restoration inventory cannot be content-hashed")
    expressions: list[str] = []
    for ordinal, item in enumerate(measured):
        if "." not in item.object:
            raise CellIdentityConflict("restoration inventory identity is invalid")
        schema, name = item.object.split(".", 1)
        relation = f"{quote_ident(schema)}.{quote_ident(name)}"
        expressions.append(
            f"SELECT {ordinal} AS ordinal, (SELECT json_build_object("
            "'rows', count(*),"
            "'sha256', encode(sha256(convert_to(coalesce("
            "string_agg(row_sha256, '' ORDER BY row_sha256), ''), 'UTF8')), 'hex')) "
            "FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text, 'UTF8')), 'hex') "
            f"AS row_sha256 FROM {relation} AS t) AS content) AS evidence"
        )
    evidence_query = (
        "SELECT coalesce(json_agg(evidence ORDER BY ordinal), '[]'::json) FROM ("
        + " UNION ALL ".join(expressions)
        + ") AS measured"
        if expressions
        else "SELECT '[]'::json"
    )
    sql = (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
        "SET LOCAL statement_timeout = '60s';\n" + evidence_query + ";\nCOMMIT;"
    )
    try:
        evidence = json.loads(admin_sql(backend, sql, max_bytes=256 * 1024))
    except (TypeError, ValueError) as exc:
        raise CellIdentityConflict("restoration content inventory is unavailable") from exc
    if not isinstance(evidence, list) or len(evidence) != len(measured):
        raise CellIdentityConflict("restoration content inventory is invalid")
    rows: list[dict[str, object]] = []
    for item, value in zip(measured, evidence, strict=True):
        if (
            not isinstance(value, dict)
            or type(value.get("rows")) is not int
            or value["rows"] < 0
            or not isinstance(value.get("sha256"), str)
            or len(value["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in value["sha256"])
        ):
            raise CellIdentityConflict("restoration content inventory is invalid")
        rows.append(
            {
                "object": item.object,
                "kind": item.kind,
                "classification": item.classification,
                "rows": value["rows"],
                "sha256": value["sha256"],
            }
        )

    def partition(classifications: set[str]) -> str:
        return canonical_digest([row for row in rows if row["classification"] in classifications])

    return partition({"business", "unknown"}), partition({"technical"})


@dataclass(frozen=True, slots=True)
class AdaptationWorkspaceMaterialization:
    candidate_workspace_id: UUID
    candidate_fencing_epoch: int
    source_database_digest: str
    candidate_database_digest: str
    source_schema_digest: str
    candidate_schema_digest: str
    source_business_digest: str
    candidate_business_digest: str
    source_technical_digest: str
    candidate_technical_digest: str


@dataclass(frozen=True, slots=True)
class AdaptationProofMaterialization:
    state: Literal["proof_ready", "migration_required", "source_changed"]
    reason_code: str | None
    # Нарушенное правило, когда код причины сам по себе слишком общий.
    reason_detail: str | None
    source_workspace_revision: str
    candidate_workspace_revision: str
    candidate_proof_key: str
    candidate_artifact_digest: str
    candidate_source_manifest_digest: str
    probe_contract_digest: str
    probe_rehearsal_digest: str | None
    probe_rehearsal_database_digest: str | None
    source_database_digest: str
    candidate_database_digest: str
    source_schema_digest: str
    candidate_schema_digest: str
    source_business_digest: str
    candidate_business_digest: str
    source_technical_digest: str
    candidate_technical_digest: str


@dataclass(frozen=True, slots=True)
class ActivationProofClaim:
    proof: RestorationAdaptationProof
    source_fencing_epoch: int


class AdaptationWorkspaceEngine(Protocol):
    async def prepare(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
    ) -> AdaptationWorkspaceMaterialization: ...

    async def cleanup(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
        candidate_fencing_epoch: int,
    ) -> None: ...

    async def prove(
        self,
        request: RestorationAdaptationPrepare,
        proof: RestorationAdaptationProofRequest,
    ) -> AdaptationProofMaterialization: ...

    async def owner_run_active(self, request: RestorationAdaptationPrepare) -> bool: ...


def _proof_payload(
    request: RestorationAdaptationPrepare,
    materialized: AdaptationWorkspaceMaterialization,
) -> dict[str, object]:
    return {
        "version": 1,
        "operation_id": str(request.operation_id),
        "source_workspace_id": str(request.workspace_id),
        "candidate_workspace_id": str(materialized.candidate_workspace_id),
        "candidate_fencing_epoch": materialized.candidate_fencing_epoch,
        "project_id": str(request.project_id),
        "owner_id": str(request.owner_id),
        "generation_run_id": str(request.generation_run_id),
        "source_workspace_revision": request.source_workspace_revision,
        "source_snapshot_id": str(request.source_snapshot_id),
        "base_draft_snapshot_id": str(request.base_draft_snapshot_id),
        "source_commit_sha": request.source_commit_sha,
        "adaptation_bundle_digest": request.adaptation_bundle_digest,
        "source_database_digest": materialized.source_database_digest,
        "candidate_database_digest": materialized.candidate_database_digest,
        "source_schema_digest": materialized.source_schema_digest,
        "candidate_schema_digest": materialized.candidate_schema_digest,
        "source_business_digest": materialized.source_business_digest,
        "candidate_business_digest": materialized.candidate_business_digest,
        "source_technical_digest": materialized.source_technical_digest,
        "candidate_technical_digest": materialized.candidate_technical_digest,
    }


class RestorationAdaptationWorkspaceService:
    def __init__(
        self,
        *,
        root: Path | None = None,
        engine: AdaptationWorkspaceEngine | None = None,
        now: Callable[[], datetime] | None = None,
        receipt_ttl: timedelta = _DEFAULT_RECEIPT_TTL,
    ) -> None:
        if root is None:
            from omnia_orchestrator.core.config import get_settings

            root = Path(get_settings().cell_state_path).parent / "restoration-adaptations"
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = engine or DockerAdaptationWorkspaceEngine()
        self._lock = WorkspaceOperationLock(root / "locks")
        self._now = now or (lambda: datetime.now(UTC))
        self._receipt_ttl = receipt_ttl

    def _expires_at(self) -> str:
        return (self._now() + self._receipt_ttl).isoformat()

    def _expired(self, receipt: dict[str, Any]) -> bool:
        try:
            value = datetime.fromisoformat(str(receipt["expires_at"]))
        except (KeyError, TypeError, ValueError):
            return True
        return value.tzinfo is None or value <= self._now()

    def _path(self, workspace_id: UUID, generation_run_id: UUID) -> Path:
        directory = self.root / str(workspace_id)
        if self.root.is_symlink() or directory.is_symlink():
            raise CellIdentityConflict("unsafe restoration adaptation receipt path")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory / f"{generation_run_id}.json"

    def _read(self, workspace_id: UUID, generation_run_id: UUID) -> dict[str, Any] | None:
        path = self._path(workspace_id, generation_run_id)
        if not path.exists():
            return None
        if path.is_symlink():
            raise CellIdentityConflict("unsafe restoration adaptation receipt")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CellIdentityConflict("restoration adaptation receipt is invalid")
        return value

    def _write(self, receipt: dict[str, Any]) -> None:
        write_controller_json(
            self._path(UUID(receipt["workspace_id"]), UUID(receipt["generation_run_id"])),
            receipt,
        )

    @staticmethod
    def _response(receipt: dict[str, Any]) -> RestorationAdaptationWorkspace:
        return RestorationAdaptationWorkspace.model_validate(receipt["response"])

    async def status(self, workspace_id: UUID, generation_run_id: UUID) -> str | None:
        async with self._lock.hold(workspace_id):
            saved = self._read(workspace_id, generation_run_id)
            return str(saved["state"]) if saved is not None else None

    @staticmethod
    def _activation_proof(
        saved: dict[str, Any],
        request: RestorationAdaptationActivationOfferRequest,
    ) -> ActivationProofClaim:
        try:
            prepared = RestorationAdaptationPrepare.model_validate(saved["request"])
            proof = RestorationAdaptationProof.model_validate(saved["proof_result"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CellIdentityConflict("restoration adaptation proof is unavailable") from exc
        if (
            proof.state != "proof_ready"
            or proof.source_workspace_id != request.workspace_id
            or proof.operation_id != request.operation_id
            or proof.project_id != request.project_id
            or proof.owner_id != request.owner_id
            or proof.generation_run_id != request.generation_run_id
            or proof.candidate_workspace_id != request.candidate_workspace_id
            or proof.candidate_fencing_epoch != request.candidate_fencing_epoch
            or proof.candidate_workspace_revision != request.candidate_workspace_revision
            or proof.proof_attempt != request.proof_attempt
            or proof.proof_digest != request.proof_digest
        ):
            raise CellIdentityConflict("restoration adaptation proof identity changed")
        return ActivationProofClaim(proof=proof, source_fencing_epoch=prepared.fencing_epoch)

    async def claim_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
    ) -> ActivationProofClaim:
        """Durably transfer a proof-ready candidate from TTL GC to activation."""

        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                raise CellIdentityConflict("restoration adaptation proof is missing")
            claim = self._activation_proof(saved, request)
            activation = saved.get("activation")
            expected = {
                "activation_id": str(activation_id),
                "offer_request_digest": request.digest(),
            }
            if activation is not None:
                if not isinstance(activation, dict) or any(
                    activation.get(name) != value for name, value in expected.items()
                ):
                    raise CellIdentityConflict("restoration activation offer envelope mismatch")
                if saved.get("state") not in {
                    "activation_offering",
                    "activation_offered",
                    "activation_activated",
                }:
                    raise CellIdentityConflict("restoration activation offer is no longer active")
                self._write({**saved, "expires_at": self._expires_at()})
                return claim
            if saved.get("state") != "proof_ready" or self._expired(saved):
                raise CellIdentityConflict("restoration adaptation proof expired")
            self._write(
                {
                    **saved,
                    "state": "activation_offering",
                    "activation": {**expected, "offer_digest": None},
                    "expires_at": self._expires_at(),
                }
            )
            return claim

    async def seal_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        offer_digest: str,
    ) -> None:
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                raise CellIdentityConflict("restoration adaptation proof is missing")
            self._activation_proof(saved, request)
            expected = {
                "activation_id": str(activation_id),
                "offer_request_digest": request.digest(),
            }
            activation = saved.get("activation")
            if not isinstance(activation, dict) or any(
                activation.get(name) != value for name, value in expected.items()
            ):
                raise CellIdentityConflict("restoration activation offer envelope mismatch")
            if saved.get("state") not in {"activation_offering", "activation_offered"}:
                raise CellIdentityConflict("restoration activation offer is no longer active")
            current = activation.get("offer_digest")
            if current not in {None, offer_digest}:
                raise CellIdentityConflict("restoration activation offer envelope mismatch")
            self._write(
                {
                    **saved,
                    "state": "activation_offered",
                    "activation": {**expected, "offer_digest": offer_digest},
                }
            )

    async def validate_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        offer_digest: str,
    ) -> ActivationProofClaim:
        """Reload trusted proof and its TTL immediately before effect admission."""

        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                raise CellIdentityConflict("restoration adaptation proof is missing")
            claim = self._activation_proof(saved, request)
            activation = saved.get("activation")
            if (
                saved.get("state") != "activation_offered"
                or self._expired(saved)
                or not isinstance(activation, dict)
                or activation.get("activation_id") != str(activation_id)
                or activation.get("offer_request_digest") != request.digest()
                or activation.get("offer_digest") != offer_digest
            ):
                raise CellIdentityConflict("restoration adaptation proof expired")
            return claim

    async def release_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
        *,
        terminal_state: str,
    ) -> None:
        """Record activation ownership handoff completion without reopening the proof."""

        if terminal_state not in {"activated", "cancelled"}:
            raise ValueError("invalid restoration activation terminal state")
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                raise CellIdentityConflict("restoration adaptation proof is missing")
            activation = saved.get("activation")
            if (
                not isinstance(activation, dict)
                or activation.get("activation_id") != str(activation_id)
                or activation.get("offer_request_digest") != request.digest()
            ):
                raise CellIdentityConflict("restoration activation offer envelope mismatch")
            if terminal_state == "activated":
                self._write({**saved, "state": "activation_activated"})
                return
            self._write(
                {
                    **saved,
                    "state": "cleanup_pending",
                    "cleanup_terminal_state": "cancelled",
                }
            )

    async def abort_activation_offer(
        self,
        request: RestorationAdaptationActivationOfferRequest,
        activation_id: UUID,
    ) -> None:
        """Turn a failed pre-offer claim into durable candidate cleanup."""

        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                return
            activation = saved.get("activation")
            if (
                not isinstance(activation, dict)
                or activation.get("activation_id") != str(activation_id)
                or activation.get("offer_request_digest") != request.digest()
            ):
                raise CellIdentityConflict("restoration activation offer envelope mismatch")
            if saved.get("state") in {"activation_offering", "activation_offered"}:
                self._write(
                    {
                        **saved,
                        "state": "cleanup_pending",
                        "cleanup_terminal_state": "failed",
                    }
                )

    async def record_owner_status(
        self,
        request: RestorationAdaptationOwnerStatus,
    ) -> None:
        """Record API-authoritative run liveness without weakening terminal state."""
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                if request.state == "terminal":
                    return
                raise CellIdentityConflict("restoration adaptation receipt is missing")
            prepared = RestorationAdaptationPrepare.model_validate(saved.get("request"))
            if (
                request.operation_id != prepared.operation_id
                or request.project_id != prepared.project_id
                or request.owner_id != prepared.owner_id
            ):
                raise CellIdentityConflict("restoration adaptation owner identity changed")
            prior = saved.get("owner_run_status")
            if prior == "terminal" and request.state == "active":
                raise CellIdentityConflict("restoration adaptation owner run is terminal")
            self._write(
                {
                    **saved,
                    "owner_run_status": request.state,
                    "owner_status_observed_at": self._now().isoformat(),
                    **({"expires_at": self._expires_at()} if request.state == "active" else {}),
                }
            )

    async def recover(self) -> int:
        """Reconcile expired abandoned candidates and retry committed cleanup."""
        recovered = 0
        for path in sorted(self.root.glob("*/*.json")):
            try:
                recovered += await self._recover_candidate(path)
            except Exception:
                # One malformed or transiently unreadable receipt must not block
                # later candidates. Leave the failed journal untouched so a
                # repaired/retried sweep can classify it safely.
                continue
        return recovered

    async def _recover_candidate(self, path: Path) -> int:
        workspace_id = UUID(path.parent.name)
        generation_run_id = UUID(path.stem)
        async with self._lock.hold(workspace_id):
            saved = self._read(workspace_id, generation_run_id)
            if saved is None or saved.get("state") not in _CANDIDATE_RECEIPT_STATES:
                return 0
            request = RestorationAdaptationPrepare.model_validate(saved.get("request"))
            candidate_id = UUID(str(saved.get("candidate_workspace_id")))
            mode = str(saved.get("state"))
            raw_response = saved.get("response")
            candidate_epoch = int(
                saved.get("candidate_fencing_epoch")
                or (
                    raw_response["candidate_fencing_epoch"]
                    if isinstance(raw_response, dict)
                    else 1
                )
            )
            terminal_state = str(
                saved.get("cleanup_terminal_state")
                or ("cleaned" if isinstance(raw_response, dict) else "failed")
            )
            owner_terminal = saved.get("owner_run_status") == "terminal"
            if mode != "cleanup_pending" and not owner_terminal and not self._expired(saved):
                return 0
        if mode != "cleanup_pending":
            # A sealed proof is the durable activation handoff. Keep it for
            # its bounded receipt lifetime, then reclaim it even when a
            # crashed source lease still claims that the run is active.
            sealed_proof_expired = mode == "proof_ready" and self._expired(saved)
            if not owner_terminal and not sealed_proof_expired:
                try:
                    if await self.engine.owner_run_active(request):
                        return 0
                except Exception:
                    return 0
            async with self._lock.hold(workspace_id):
                current = self._read(workspace_id, generation_run_id)
                if (
                    current is None
                    or current.get("state") != mode
                    or (
                        current.get("owner_run_status") != "terminal"
                        and not self._expired(current)
                    )
                ):
                    return 0
                self._write(
                    {
                        **current,
                        "state": "cleanup_pending",
                        "candidate_fencing_epoch": candidate_epoch,
                        "cleanup_terminal_state": terminal_state,
                    }
                )
        try:
            await self.engine.cleanup(request, candidate_id, candidate_epoch)
        except Exception:
            return 0
        async with self._lock.hold(workspace_id):
            current = self._read(workspace_id, generation_run_id)
            if current is None or current.get("request_digest") != request.digest():
                raise CellIdentityConflict(
                    "restoration adaptation receipt changed during recovery"
                )
            if current.get("state") != "cleanup_pending":
                return 0
            self._write({**current, "state": terminal_state})
        return 1

    async def recover_activation_orphans(
        self,
        protected_activation_ids: frozenset[UUID],
    ) -> frozenset[UUID] | None:
        """Reclaim expired activation pins missing from durable facade/engine registries.

        ``None`` means at least one workspace receipt could not be classified, so
        callers must fail closed and skip artifact GC for this pass.
        """

        retained: set[UUID] = set()
        registry_complete = True
        for path in sorted(self.root.glob("*/*.json")):
            try:
                workspace_id = UUID(path.parent.name)
                generation_run_id = UUID(path.stem)
            except ValueError:
                continue
            try:
                async with self._lock.hold(workspace_id):
                    saved = self._read(workspace_id, generation_run_id)
                    if saved is None or saved.get("state") not in _ACTIVATION_PIN_STATES:
                        continue
                    activation = saved.get("activation")
                    if not isinstance(activation, dict):
                        registry_complete = False
                        continue
                    activation_id = UUID(str(activation["activation_id"]))
                    if activation_id in protected_activation_ids:
                        retained.add(activation_id)
                        continue
                    mode = str(saved["state"])
                    owner_terminal = saved.get("owner_run_status") == "terminal"
                    if (
                        mode != "activation_activated"
                        and not owner_terminal
                        and not self._expired(saved)
                    ):
                        retained.add(activation_id)
                        continue
                    request = RestorationAdaptationPrepare.model_validate(saved.get("request"))
                    candidate_id = UUID(str(saved.get("candidate_workspace_id")))
                    raw_response = saved.get("response")
                    candidate_epoch = int(
                        saved.get("candidate_fencing_epoch")
                        or (
                            raw_response["candidate_fencing_epoch"]
                            if isinstance(raw_response, dict)
                            else 1
                        )
                    )
                    if (
                        request.workspace_id != workspace_id
                        or request.generation_run_id != generation_run_id
                        or candidate_id
                        != uuid5(
                            request.operation_id,
                            f"restoration-adaptation:{request.generation_run_id}",
                        )
                        or candidate_epoch < 1
                    ):
                        registry_complete = False
                        continue
                    terminal_state = (
                        "cleaned" if mode == "activation_activated" else "failed"
                    )
                    self._write(
                        {
                            **saved,
                            "state": "cleanup_pending",
                            "candidate_fencing_epoch": candidate_epoch,
                            "cleanup_terminal_state": terminal_state,
                            "activation_orphan_cleanup": True,
                        }
                    )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                registry_complete = False
                continue

            try:
                await self.engine.cleanup(request, candidate_id, candidate_epoch)
            except Exception:
                retained.add(activation_id)
                continue
            async with self._lock.hold(workspace_id):
                current = self._read(workspace_id, generation_run_id)
                current_activation = current.get("activation") if current is not None else None
                if (
                    current is None
                    or current.get("request_digest") != request.digest()
                    or current.get("state") != "cleanup_pending"
                    or not isinstance(current_activation, dict)
                    or current_activation.get("activation_id") != str(activation_id)
                ):
                    registry_complete = False
                    continue
                self._write({**current, "state": terminal_state})

        if not registry_complete:
            return None
        return frozenset(retained)

    async def prepare(
        self, request: RestorationAdaptationPrepare
    ) -> RestorationAdaptationWorkspace:
        candidate_id = uuid5(
            request.operation_id,
            f"restoration-adaptation:{request.generation_run_id}",
        )
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is not None:
                if saved.get("request_digest") != request.digest():
                    raise CellIdentityConflict("restoration adaptation envelope changed")
                if saved.get("state") == "ready":
                    return self._response(saved)
                raise CellIdentityConflict("restoration adaptation is not reusable")
            self._write(
                {
                    "version": 1,
                    "workspace_id": str(request.workspace_id),
                    "generation_run_id": str(request.generation_run_id),
                    "request": request.model_dump(mode="json"),
                    "request_digest": request.digest(),
                    "candidate_workspace_id": str(candidate_id),
                    "state": "preparing",
                    "owner_run_status": "active",
                    "expires_at": self._expires_at(),
                }
            )
        try:
            materialized = await self.engine.prepare(request, candidate_id)
            self._validate_materialization(candidate_id, materialized)
            proof = _proof_payload(request, materialized)
            proof_digest = canonical_digest(proof)
            response = RestorationAdaptationWorkspace(
                source_workspace_id=request.workspace_id,
                candidate_workspace_id=candidate_id,
                operation_id=request.operation_id,
                project_id=request.project_id,
                owner_id=request.owner_id,
                generation_run_id=request.generation_run_id,
                candidate_fencing_epoch=materialized.candidate_fencing_epoch,
                source_database_digest=materialized.source_database_digest,
                proof_digest=proof_digest,
                capabilities=dict(_CAPABILITIES),
            )
        except BaseException as exc:
            cleanup_error: BaseException | None = None
            try:
                await asyncio.shield(self.engine.cleanup(request, candidate_id, 1))
            except BaseException as cleanup_exc:
                cleanup_error = cleanup_exc
            async with self._lock.hold(request.workspace_id):
                saved = self._read(request.workspace_id, request.generation_run_id) or {}
                if (
                    saved.get("request_digest") == request.digest()
                    and saved.get("state") == "preparing"
                ):
                    self._write(
                        {
                            **saved,
                            "state": ("cleanup_pending" if cleanup_error is not None else "failed"),
                            "error_type": type(exc).__name__,
                            **(
                                {"cleanup_error_type": type(cleanup_error).__name__}
                                if cleanup_error is not None
                                else {}
                            ),
                        }
                    )
            raise
        lost_terminal_state: str | None = None
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if (
                saved is None
                or saved.get("request_digest") != request.digest()
                or saved.get("state") != "preparing"
            ):
                if saved is None or saved.get("request_digest") != request.digest():
                    raise CellIdentityConflict("restoration adaptation receipt changed")
                lost_terminal_state = str(
                    saved.get("cleanup_terminal_state")
                    or ("cleaned" if isinstance(saved.get("response"), dict) else "failed")
                )
                self._write(
                    {
                        **saved,
                        "state": "cleanup_pending",
                        "candidate_fencing_epoch": materialized.candidate_fencing_epoch,
                        "cleanup_terminal_state": lost_terminal_state,
                    }
                )
            else:
                self._write(
                    {
                        **saved,
                        "state": "ready",
                        "proof": proof,
                        "proof_digest": proof_digest,
                        "response": response.model_dump(mode="json"),
                        "expires_at": self._expires_at(),
                    }
                )
        if lost_terminal_state is not None:
            try:
                await asyncio.shield(
                    self.engine.cleanup(
                        request,
                        candidate_id,
                        materialized.candidate_fencing_epoch,
                    )
                )
            except Exception:
                pass
            else:
                async with self._lock.hold(request.workspace_id):
                    saved = self._read(request.workspace_id, request.generation_run_id)
                    if (
                        saved is not None
                        and saved.get("request_digest") == request.digest()
                        and saved.get("state") == "cleanup_pending"
                        and int(saved.get("candidate_fencing_epoch", 0))
                        == materialized.candidate_fencing_epoch
                    ):
                        self._write({**saved, "state": lost_terminal_state})
            raise CellIdentityConflict("restoration adaptation receipt changed")
        return response

    @staticmethod
    def _validate_materialization(
        candidate_id: UUID,
        value: AdaptationWorkspaceMaterialization,
    ) -> None:
        if value.candidate_workspace_id != candidate_id or value.candidate_fencing_epoch < 1:
            raise CellIdentityConflict("restoration adaptation candidate identity mismatch")
        pairs = (
            (value.source_database_digest, value.candidate_database_digest, "database"),
            (value.source_schema_digest, value.candidate_schema_digest, "schema"),
            (value.source_business_digest, value.candidate_business_digest, "business data"),
            (value.source_technical_digest, value.candidate_technical_digest, "technical data"),
        )
        for source, candidate, label in pairs:
            if (
                source != candidate
                or len(source) != 64
                or any(char not in "0123456789abcdef" for char in source)
            ):
                raise CellIdentityConflict(f"{label} copy digest mismatch")

    async def cleanup(self, request: RestorationAdaptationCleanup) -> None:
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None:
                raise CellIdentityConflict("restoration adaptation receipt is missing")
            if saved.get("state") in _ACTIVATION_PIN_STATES:
                raise CellIdentityConflict("restoration activation owns candidate cleanup")
            response = self._response(saved)
            if (
                response.candidate_workspace_id != request.candidate_workspace_id
                or response.candidate_fencing_epoch != request.candidate_fencing_epoch
                or response.proof_digest != request.proof_digest
            ):
                raise CellIdentityConflict("restoration adaptation cleanup proof changed")
            if saved.get("state") == "cleaned":
                return
            self._write({**saved, "state": "cleanup_pending"})
        prepare = RestorationAdaptationPrepare.model_validate(saved["request"])
        await self.engine.cleanup(
            prepare,
            request.candidate_workspace_id,
            request.candidate_fencing_epoch,
        )
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None or saved.get("proof_digest") != request.proof_digest:
                raise CellIdentityConflict("restoration adaptation proof disappeared")
            self._write({**saved, "state": "cleaned"})

    async def prove(
        self,
        request: RestorationAdaptationProofRequest,
    ) -> RestorationAdaptationProof:
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if saved is None or saved.get("response") is None:
                raise CellIdentityConflict("restoration adaptation receipt is not ready")
            prepared = RestorationAdaptationPrepare.model_validate(saved["request"])
            response = self._response(saved)
            if (
                request.operation_id != prepared.operation_id
                or request.project_id != prepared.project_id
                or request.owner_id != prepared.owner_id
                or request.candidate_workspace_id != response.candidate_workspace_id
                or request.candidate_fencing_epoch != response.candidate_fencing_epoch
                or request.preparation_proof_digest != response.proof_digest
                or request.source_database_digest != response.source_database_digest
            ):
                raise CellIdentityConflict("restoration adaptation proof identity changed")
            proof_request_digest = request.digest()
            raw_attempts = saved.get("proof_attempts")
            attempts = dict(raw_attempts) if isinstance(raw_attempts, dict) else {}
            attempt_key = str(request.proof_attempt)
            existing_attempt = attempts.get(attempt_key)
            max_attempt = max(
                (int(key) for key in attempts if str(key).isdigit()),
                default=0,
            )
            if request.proof_attempt < max_attempt:
                raise CellIdentityConflict("stale restoration adaptation proof attempt")
            receipt_state = saved.get("state")
            if receipt_state in {"cleanup_pending", "cleaned", "failed"}:
                raise CellIdentityConflict("restoration adaptation is not proofable")
            if existing_attempt is not None:
                if (
                    not isinstance(existing_attempt, dict)
                    or existing_attempt.get("request_digest") != proof_request_digest
                ):
                    raise CellIdentityConflict("restoration adaptation proof attempt changed")
                if request.proof_attempt != max_attempt:
                    raise CellIdentityConflict("stale restoration adaptation proof attempt")
                if existing_attempt.get("result") is not None:
                    return RestorationAdaptationProof.model_validate(existing_attempt["result"])
                if receipt_state == "proof_failed":
                    pass
                elif receipt_state != "proving":
                    raise CellIdentityConflict("restoration adaptation is not proofable")
                elif not self._expired(saved):
                    raise CellIdentityConflict("restoration adaptation proof is in progress")
                # The trusted attempt number remains stable across a transport retry.
                # A private drive fence prevents a late pre-crash result from winning.
                drive = int(existing_attempt.get("drive", 1)) + 1
            else:
                if request.proof_attempt != max_attempt + 1:
                    raise CellIdentityConflict("restoration adaptation proof attempt is not next")
                if saved.get("state") not in {"ready", "migration_required"}:
                    raise CellIdentityConflict("restoration adaptation is not proofable")
                drive = 1
            attempts[attempt_key] = {
                "attempt": request.proof_attempt,
                "drive": drive,
                "request_digest": proof_request_digest,
                "request": request.model_dump(mode="json"),
                "result": None,
            }
            self._write(
                {
                    **saved,
                    "state": "proving",
                    "proof_request": request.model_dump(mode="json"),
                    "proof_request_digest": proof_request_digest,
                    "proof_attempt": request.proof_attempt,
                    "proof_drive": drive,
                    "proof_attempts": attempts,
                    "proof_result": None,
                    "expires_at": self._expires_at(),
                }
            )
        try:
            materialized = await self.engine.prove(prepared, request)
            result = self._proof_result(prepared, request, materialized)
        except BaseException as exc:
            async with self._lock.hold(request.workspace_id):
                saved = self._read(request.workspace_id, request.generation_run_id) or {}
                if (
                    saved.get("state") == "proving"
                    and saved.get("proof_request_digest") == proof_request_digest
                    and saved.get("proof_attempt") == request.proof_attempt
                    and saved.get("proof_drive") == drive
                ):
                    self._write(
                        {
                            **saved,
                            "state": "proof_failed",
                            "error_type": type(exc).__name__,
                            "expires_at": self._expires_at(),
                        }
                    )
            raise
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.generation_run_id)
            if (
                saved is None
                or saved.get("state") != "proving"
                or saved.get("proof_request_digest") != proof_request_digest
                or saved.get("proof_attempt") != request.proof_attempt
                or saved.get("proof_drive") != drive
            ):
                raise CellIdentityConflict("restoration adaptation proof was superseded")
            raw_attempts = saved.get("proof_attempts")
            attempts = dict(raw_attempts) if isinstance(raw_attempts, dict) else {}
            attempt = attempts.get(attempt_key)
            if (
                not isinstance(attempt, dict)
                or attempt.get("drive") != drive
                or attempt.get("request_digest") != proof_request_digest
                or attempt.get("result") is not None
            ):
                raise CellIdentityConflict("restoration adaptation proof receipt changed")
            result_payload = result.model_dump(mode="json")
            attempts[attempt_key] = {**attempt, "result": result_payload}
            self._write(
                {
                    **saved,
                    "state": result.state,
                    "proof_result": result_payload,
                    "proof_attempts": attempts,
                    "expires_at": self._expires_at(),
                }
            )
        return result

    @staticmethod
    def _proof_result(
        prepared: RestorationAdaptationPrepare,
        request: RestorationAdaptationProofRequest,
        value: AdaptationProofMaterialization,
    ) -> RestorationAdaptationProof:
        if (
            value.candidate_workspace_revision != request.candidate_workspace_revision
            or value.candidate_proof_key != request.candidate_proof_key
            or value.candidate_artifact_digest != request.candidate_artifact_digest
        ):
            raise CellIdentityConflict("restoration adaptation result identity mismatch")
        raw = {
            "state": value.state,
            "reason_code": value.reason_code,
            "reason_detail": value.reason_detail,
            "source_workspace_id": str(prepared.workspace_id),
            "candidate_workspace_id": str(request.candidate_workspace_id),
            "operation_id": str(prepared.operation_id),
            "project_id": str(prepared.project_id),
            "owner_id": str(prepared.owner_id),
            "generation_run_id": str(prepared.generation_run_id),
            "candidate_fencing_epoch": request.candidate_fencing_epoch,
            "proof_attempt": request.proof_attempt,
            **{
                name: getattr(value, name)
                for name in (
                    "source_workspace_revision",
                    "candidate_workspace_revision",
                    "candidate_proof_key",
                    "candidate_artifact_digest",
                    "candidate_source_manifest_digest",
                    "probe_contract_digest",
                    "probe_rehearsal_digest",
                    "probe_rehearsal_database_digest",
                    "source_database_digest",
                    "candidate_database_digest",
                    "source_schema_digest",
                    "candidate_schema_digest",
                    "source_business_digest",
                    "candidate_business_digest",
                    "source_technical_digest",
                    "candidate_technical_digest",
                )
            },
        }
        proof_digest = canonical_digest(
            {
                "version": 1,
                "expected_source_workspace_revision": prepared.source_workspace_revision,
                "expected_source_database_digest": request.source_database_digest,
                **raw,
            }
        )
        return RestorationAdaptationProof(
            **raw,
            proof_digest=proof_digest,
            capabilities=dict(
                _PROOF_CAPABILITIES if value.state == "proof_ready" else _CAPABILITIES
            ),
        )


class DockerAdaptationWorkspaceEngine:
    """Materialize a short-lived candidate without ever mounting the live DB."""

    def __init__(self, *, probe_rehearser: Any | None = None) -> None:
        self._probe_rehearser = probe_rehearser

    def _rehearser(self) -> Any:
        if self._probe_rehearser is None:
            from omnia_orchestrator.services.code_restorations import (
                get_code_restoration_service,
            )
            from omnia_orchestrator.services.restoration_adaptation_health import (
                DockerRestorationAdaptationHealthProber,
            )

            self._probe_rehearser = DockerRestorationAdaptationHealthProber(
                code_engine=get_code_restoration_service()._engine()
            )
        return self._probe_rehearser

    @staticmethod
    def _manager(workspace_id: UUID) -> Any:
        from omnia_orchestrator.routers.workspace import (
            _require_docker_resource_manager,
            _workspace_provider,
        )

        return _require_docker_resource_manager(_workspace_provider(workspace_id))

    @staticmethod
    def _database_digests(
        contract: Any,
        business: str,
        technical: str,
    ) -> tuple[str, str]:
        schema = canonical_digest(contract.model_dump(mode="json"))
        database = canonical_digest(
            {"schema_digest": schema, "business_digest": business, "technical_digest": technical}
        )
        return database, schema

    @staticmethod
    def _require_source_state(
        request: RestorationAdaptationPrepare,
        state: Any,
        manager: Any,
    ) -> None:
        if (
            state is None
            or state.workspace_id != request.workspace_id
            or state.project_id != request.project_id
            or state.owner_id != request.owner_id
            or state.fencing_epoch != request.fencing_epoch
            or state.active_generation_run_id != request.generation_run_id
            or state.active_generation_fencing_epoch != request.fencing_epoch
            or manager.machine_runtime is None
        ):
            raise CellIdentityConflict("restoration adaptation source lease changed")

    @staticmethod
    async def _source_database_volume_binding(source: Any) -> str:
        volume_name = source.project_postgres_volume
        volume = await machine_effect(
            source._lookup,
            source.client.volumes,
            volume_name,
            "project-volume",
        )
        attrs = volume.attrs if volume is not None else {}
        labels = attrs.get("Labels") or {}
        expected_labels = source.labels("project-volume")
        created_at = attrs.get("CreatedAt")
        if (
            volume is None
            or attrs.get("Name") != volume_name
            or not isinstance(created_at, str)
            or not created_at
            or any(labels.get(key) != value for key, value in expected_labels.items())
        ):
            raise CellIdentityConflict(
                "restoration adaptation source database volume changed"
            )
        return canonical_digest(
            {
                "name": volume_name,
                "created_at": created_at,
                "driver": attrs.get("Driver"),
                "scope": attrs.get("Scope"),
                "options": attrs.get("Options") or {},
                "labels": labels,
            }
        )

    @staticmethod
    async def _require_resume_checkpoint(
        request: RestorationAdaptationPrepare,
        source: Any,
        manifest: MachineManifest,
    ) -> None:
        metadata = await machine_effect(source._metadata)
        if not isinstance(metadata, dict):
            raise CellIdentityConflict(
                "restoration adaptation source checkpoint is unattested"
            )
        raw_reference = metadata.get("environment_ref")
        schema_digest = metadata.get("environment_schema_digest")
        sealed_at = metadata.get("environment_sealed_at")
        try:
            reference = MachineEnvironmentRef.model_validate(raw_reference)
            if not isinstance(sealed_at, str):
                raise TypeError("checkpoint seal time is not a string")
            sealed = datetime.fromisoformat(sealed_at)
        except (TypeError, ValueError) as exc:
            raise CellIdentityConflict(
                "restoration adaptation source checkpoint is unattested"
            ) from exc
        manifest_digest = manifest.digest()
        expected_volumes = tuple(
            await machine_effect(source.environment_volume_names, manifest)
        )
        reference_volumes = tuple(item.name for item in reference.volumes)
        if (
            metadata.get("environment_revision") != request.source_workspace_revision
            or not isinstance(schema_digest, str)
            or len(schema_digest) != 64
            or any(char not in "0123456789abcdef" for char in schema_digest)
            or sealed.tzinfo is None
            or reference.workspace_id != request.workspace_id
            or reference.manifest_digest != manifest_digest
            or reference.manifest is None
            or reference.manifest.digest() != manifest_digest
            or reference.base_image != source.base_image
            or source.workspace_volume not in reference_volumes
            or len(reference_volumes) != len(set(reference_volumes))
            or set(reference_volumes) != set(expected_volumes)
        ):
            raise CellIdentityConflict(
                "restoration adaptation source checkpoint is unattested"
            )

    @staticmethod
    def _require_current_manifest(machine: Any, expected: MachineManifest) -> dict[str, Any]:
        try:
            saved = machine.state()
            if not isinstance(saved, dict):
                raise TypeError("machine state is not a mapping")
            observed = MachineManifest.model_validate(saved["manifest"])
        except Exception as exc:
            raise CellIdentityConflict(
                "restoration adaptation source manifest is unavailable"
            ) from exc
        if observed.digest() != expected.digest():
            raise CellIdentityConflict("restoration adaptation source manifest changed")
        return saved

    @staticmethod
    async def _source_pair_running(
        manager: Any,
        state: Any,
        machine: Any,
        source: Any,
        manifest: MachineManifest,
        epoch: int,
    ) -> bool:
        saved = DockerAdaptationWorkspaceEngine._require_current_manifest(machine, manifest)
        if saved.get("epoch") != epoch:
            return False
        application = await machine_effect(source._container)
        postgres = await machine_effect(source._project_postgres)
        for container in (application, postgres):
            if container is None or container.labels.get("omnia.fencing_epoch") != str(epoch):
                return False
            await machine_effect(container.reload)
            if container.status != "running":
                return False
        if not any(
            item.get("Name") == source.workspace_volume
            and item.get("Destination") == "/workspace"
            for item in application.attrs.get("Mounts", [])
        ):
            return False
        if not any(
            item.get("Name") == source.project_postgres_volume
            and item.get("Destination") == "/var/lib/postgresql/data"
            for item in postgres.attrs.get("Mounts", [])
        ):
            return False
        for service in manifest.services:
            status = await machine_effect(
                source.service_status,
                service,
                epoch,
                include_logs=False,
            )
            if not status["ready"]:
                return False
        preview = await machine_effect(manager.machine_runtime.preview, state)
        return preview is not None and preview[0] == "running"

    async def prepare(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
    ) -> AdaptationWorkspaceMaterialization:
        manager = self._manager(request.workspace_id)
        try:
            async with manager.operation_lock.hold(request.workspace_id):
                state = manager.state_store.load(request.workspace_id)
                self._require_source_state(request, state, manager)
                machine, source = manager.machine_runtime.parts(state)
                from omnia_orchestrator.routers.runtime import _workspace_revision
                from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

                files = await _read_agent_workspace_files(manager, source.workspace_volume)
                source_files = canonical_source_files(
                    await manager.docker.read_workspace_source_files(source.workspace_volume)
                )
                manifest = validate_supported_runtime(files)
                if _workspace_revision(files) != request.source_workspace_revision:
                    raise CellIdentityConflict(
                        "restoration adaptation source workspace changed"
                    )
                self._require_current_manifest(machine, manifest)
                source_volume = source.project_postgres_volume
                source_code_volume = source.workspace_volume
                database_binding = await self._source_database_volume_binding(source)
                if not await self._source_pair_running(
                    manager,
                    state,
                    machine,
                    source,
                    manifest,
                    request.fencing_epoch,
                ):
                    await self._require_resume_checkpoint(request, source, manifest)
                    await manager.machine_runtime.resume_preview(
                        state,
                        epoch=request.fencing_epoch,
                    )

                resumed_state = manager.state_store.load(request.workspace_id)
                self._require_source_state(request, resumed_state, manager)
                resumed_machine, resumed_source = manager.machine_runtime.parts(resumed_state)
                if (
                    resumed_source.workspace_volume != source_code_volume
                    or resumed_source.project_postgres_volume != source_volume
                    or await self._source_database_volume_binding(resumed_source)
                    != database_binding
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation source runtime changed during resume"
                    )
                resumed_files = await _read_agent_workspace_files(
                    manager,
                    resumed_source.workspace_volume,
                )
                resumed_source_files = canonical_source_files(
                    await manager.docker.read_workspace_source_files(
                        resumed_source.workspace_volume
                    )
                )
                resumed_manifest = validate_supported_runtime(resumed_files)
                if (
                    _workspace_revision(resumed_files) != request.source_workspace_revision
                    or resumed_manifest.digest() != manifest.digest()
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation source changed during resume"
                    )
                if not await self._source_pair_running(
                    manager,
                    resumed_state,
                    resumed_machine,
                    resumed_source,
                    resumed_manifest,
                    request.fencing_epoch,
                ):
                    raise CellResourceError(
                        "restoration adaptation source pair is not running"
                    )
                state = resumed_state
                source = resumed_source
                files = resumed_files
                source_files = resumed_source_files
                manifest = resumed_manifest
                source_inventory = await machine_effect(
                    observe_database, source, observed_on="source"
                )
                source_contract, blockers = await machine_effect(catalog_contract, source)
                if blockers or source_inventory.coverage != "complete":
                    raise CellResourceError("restoration adaptation source evidence is incomplete")
                source_business, source_technical = await machine_effect(
                    content_inventory_partition_digests, source, source_inventory
                )
                dump = await machine_effect(CodeRestorationEngine._dump, source)
                fresh_inventory = await machine_effect(
                    observe_database, source, observed_on="source"
                )
                fresh_contract, fresh_blockers = await machine_effect(catalog_contract, source)
                fresh_business, fresh_technical = await machine_effect(
                    content_inventory_partition_digests, source, fresh_inventory
                )
                if (
                    fresh_blockers
                    or fresh_contract != source_contract
                    or fresh_business != source_business
                    or fresh_technical != source_technical
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation source changed during database copy"
                    )
            candidate, candidate_epoch = await self._candidate(
                manager,
                request,
                candidate_workspace_id,
                manifest,
                source_files,
            )
            await machine_effect(
                admin_sql,
                candidate,
                dump.decode("utf-8"),
                max_bytes=64 * 1024 * 1024,
            )
            candidate_contract, candidate_blockers = await machine_effect(
                catalog_contract, candidate
            )
            candidate_inventory = await machine_effect(
                observe_database, candidate, observed_on="candidate_copy"
            )
            if candidate_blockers or candidate_inventory.coverage != "complete":
                raise CellResourceError("restoration adaptation copy evidence is incomplete")
            candidate_business, candidate_technical = await machine_effect(
                content_inventory_partition_digests, candidate, candidate_inventory
            )
            source_database, source_schema = self._database_digests(
                source_contract, source_business, source_technical
            )
            candidate_database, candidate_schema = self._database_digests(
                candidate_contract, candidate_business, candidate_technical
            )
            return AdaptationWorkspaceMaterialization(
                candidate_workspace_id=candidate_workspace_id,
                candidate_fencing_epoch=candidate_epoch,
                source_database_digest=source_database,
                candidate_database_digest=candidate_database,
                source_schema_digest=source_schema,
                candidate_schema_digest=candidate_schema,
                source_business_digest=source_business,
                candidate_business_digest=candidate_business,
                source_technical_digest=source_technical,
                candidate_technical_digest=candidate_technical,
            )
        except BaseException:
            # The service owns the durable cleanup receipt and invokes cleanup
            # under shield. Keeping that ownership in one layer makes retries
            # idempotent after process interruption.
            raise

    async def prove(
        self,
        request: RestorationAdaptationPrepare,
        proof: RestorationAdaptationProofRequest,
    ) -> AdaptationProofMaterialization:
        from omnia_orchestrator.core.config import get_settings
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files
        from omnia_orchestrator.services.cell_publication_capacity import production_manager

        manager = self._manager(request.workspace_id)
        candidate_manager = production_manager(manager, get_settings())

        async def observe(
            backend: Any,
            *,
            observed_on: Literal["source", "candidate_copy"],
            evidence_label: Literal[
                "source_proof_before",
                "candidate_result",
                "candidate_rehearsal_after",
                "source_proof_after",
            ],
        ) -> tuple[str, str, str, str]:
            contract, blockers = await machine_effect(catalog_contract, backend)
            inventory = await machine_effect(
                observe_database,
                backend,
                observed_on=observed_on,
            )
            if blockers or inventory.coverage != "complete":
                raise CellResourceError(
                    f"restoration adaptation {evidence_label} evidence is incomplete"
                )
            business, technical = await machine_effect(
                content_inventory_partition_digests,
                backend,
                inventory,
            )
            database, schema = self._database_digests(contract, business, technical)
            return database, schema, business, technical

        async with manager.operation_lock.hold(request.workspace_id):
            source_state = manager.state_store.load(request.workspace_id)
            if (
                source_state is None
                or source_state.project_id != request.project_id
                or source_state.owner_id != request.owner_id
                or source_state.active_generation_run_id != request.generation_run_id
                or source_state.active_generation_fencing_epoch != request.fencing_epoch
                or manager.machine_runtime is None
            ):
                raise CellIdentityConflict("restoration adaptation source lease changed")
            _source_machine, source = manager.machine_runtime.parts(source_state)
            source_files = await _read_agent_workspace_files(manager, source.workspace_volume)
            source_revision = _workspace_revision(source_files)
            source_first = await observe(
                source,
                observed_on="source",
                evidence_label="source_proof_before",
            )

        async with candidate_manager.operation_lock.hold(proof.candidate_workspace_id):
            candidate_state = candidate_manager.state_store.load(proof.candidate_workspace_id)
            if (
                candidate_state is None
                or candidate_state.project_id != request.project_id
                or candidate_state.owner_id != request.owner_id
                or candidate_state.active_generation_run_id != request.generation_run_id
                or candidate_state.active_generation_fencing_epoch != proof.candidate_fencing_epoch
                or candidate_manager.machine_runtime is None
            ):
                raise CellIdentityConflict("restoration adaptation candidate lease changed")
            _candidate_machine, candidate = candidate_manager.machine_runtime.parts(candidate_state)
            if (
                source.project_postgres_password == candidate.project_postgres_password
                or source.internal_network == candidate.internal_network
                or source.project_postgres_volume == candidate.project_postgres_volume
            ):
                raise CellIdentityConflict(
                    "restoration adaptation candidate is not credential-isolated"
                )
            candidate_files = await _read_agent_workspace_files(
                candidate_manager,
                candidate.workspace_volume,
            )
            candidate_revision = _workspace_revision(candidate_files)
            candidate_artifact = self._files_digest(candidate_files)
            candidate_source_files = canonical_source_files(
                await candidate_manager.docker.read_workspace_source_files(
                    candidate.workspace_volume
                )
            )
            candidate_source_manifest = source_manifest_digest(candidate_source_files)
            if (
                candidate_revision != proof.candidate_workspace_revision
                or candidate_artifact != proof.candidate_artifact_digest
            ):
                raise CellIdentityConflict("restoration adaptation candidate source changed")
            candidate_observed = await observe(
                candidate,
                observed_on="candidate_copy",
                evidence_label="candidate_result",
            )
            candidate_contract, candidate_blockers = await machine_effect(
                catalog_contract, candidate
            )
            if candidate_blockers:
                raise CellResourceError(
                    "restoration adaptation candidate contract is incomplete"
                )
            from omnia_orchestrator.services.restoration_adaptation_probe import (
                validate_probe_contract,
            )

            rehearsal_digest: str | None = None
            rehearsal_failed = True
            # Почему именно не удалось: три разные беды, три разных разговора.
            # «Приложение не объявило годную проверку» чинит агент адаптации,
            # «проверка не прошла» — разработчик, «копия изменилась под нами» —
            # повтор операции. Один код на всё лишал оператора этой развилки.
            rehearsal_reason = "probe_rehearsal_failed"
            rehearsal_detail: str | None = None
            try:
                business_probe = validate_probe_contract(candidate_files, candidate_contract)
                probe_contract_digest = business_probe.contract_digest
            except CellIdentityConflict as exc:
                # Проверка даже не запускалась: манифест приложения не прошёл разбор.
                # Годность решают четырнадцать правил; без имени нарушенного
                # починка правит манифест вслепую.
                rehearsal_reason = "probe_manifest_invalid"
                rehearsal_detail = _rule_name(exc)
                probe_contract_digest = canonical_digest(
                    {
                        "version": 1,
                        "invalid_manifest_sha256": hashlib.sha256(
                            candidate_files.get(
                                ".omnia/restoration-probe.json", ""
                            ).encode()
                        ).hexdigest(),
                        "data_contract_digest": canonical_digest(
                            candidate_contract.model_dump(mode="json")
                        ),
                    }
                )
            else:
                rehearsal_failed = False
                try:
                    rehearsal_digest = await self._rehearser().rehearse_candidate(
                        operation_id=request.operation_id,
                        generation_run_id=request.generation_run_id,
                        project_id=request.project_id,
                        owner_id=request.owner_id,
                        activation_id=uuid5(
                            request.operation_id,
                            f"restoration-adaptation-activation:{request.generation_run_id}",
                        ),
                        candidate_workspace_id=proof.candidate_workspace_id,
                        candidate_fencing_epoch=proof.candidate_fencing_epoch,
                        business_probe=business_probe,
                        manager=candidate_manager,
                        state=candidate_state,
                        backend=candidate,
                        candidate_source_manifest_digest=candidate_source_manifest,
                    )
                except CellResourceError as exc:
                    from omnia_orchestrator.services.restoration_adaptation_health import (
                        REHEARSAL_REASON_BY_LEG,
                    )

                    rehearsal_failed = True
                    # Нога, на которой споткнулись, — единственное, что делает
                    # этот отказ пригодным для починки. Незнакомый сбой остаётся
                    # под общим кодом, а не выдумывает ногу.
                    rehearsal_reason = REHEARSAL_REASON_BY_LEG.get(
                        getattr(exc, "leg", ""), "probe_rehearsal_failed"
                    )
            candidate_after_rehearsal = await observe(
                candidate,
                observed_on="candidate_copy",
                evidence_label="candidate_rehearsal_after",
            )
            if candidate_after_rehearsal != candidate_observed:
                rehearsal_failed = True
                rehearsal_reason = "candidate_changed_during_rehearsal"
                rehearsal_digest = None

        async with manager.operation_lock.hold(request.workspace_id):
            latest_state = manager.state_store.load(request.workspace_id)
            if (
                latest_state is None
                or latest_state.active_generation_run_id != request.generation_run_id
                or latest_state.active_generation_fencing_epoch != request.fencing_epoch
                or manager.machine_runtime is None
            ):
                raise CellIdentityConflict("restoration adaptation source lease changed")
            _latest_machine, latest_source = manager.machine_runtime.parts(latest_state)
            latest_files = await _read_agent_workspace_files(
                manager,
                latest_source.workspace_volume,
            )
            latest_revision = _workspace_revision(latest_files)
            source_latest = await observe(
                latest_source,
                observed_on="source",
                evidence_label="source_proof_after",
            )

        source_database, source_schema, source_business, source_technical = source_latest
        (
            candidate_database,
            candidate_schema,
            candidate_business,
            candidate_technical,
        ) = candidate_observed
        state: Literal["proof_ready", "migration_required", "source_changed"]
        reason: str | None
        if (
            source_revision != request.source_workspace_revision
            or latest_revision != request.source_workspace_revision
        ):
            state, reason = "source_changed", "source_code_changed"
        elif source_first != source_latest or source_database != proof.source_database_digest:
            state, reason = "source_changed", "source_database_changed"
        elif candidate_schema != source_schema:
            state, reason = "migration_required", "candidate_schema_changed"
        elif candidate_business != source_business:
            state, reason = "migration_required", "candidate_business_data_changed"
        elif candidate_technical != source_technical:
            state, reason = "migration_required", "candidate_technical_data_changed"
        elif rehearsal_failed or rehearsal_digest is None:
            state, reason = "migration_required", rehearsal_reason
        else:
            state, reason = "proof_ready", None
        return AdaptationProofMaterialization(
            state=state,
            reason_code=reason,
            # Правило доезжает только вместе со своей причиной.
            reason_detail=rehearsal_detail if reason == "probe_manifest_invalid" else None,
            source_workspace_revision=latest_revision,
            candidate_workspace_revision=candidate_revision,
            candidate_proof_key=proof.candidate_proof_key,
            candidate_artifact_digest=candidate_artifact,
            candidate_source_manifest_digest=candidate_source_manifest,
            probe_contract_digest=probe_contract_digest,
            probe_rehearsal_digest=rehearsal_digest,
            probe_rehearsal_database_digest=(
                candidate_database
                if rehearsal_digest is not None and not rehearsal_failed
                else None
            ),
            source_database_digest=source_database,
            candidate_database_digest=candidate_database,
            source_schema_digest=source_schema,
            candidate_schema_digest=candidate_schema,
            source_business_digest=source_business,
            candidate_business_digest=candidate_business,
            source_technical_digest=source_technical,
            candidate_technical_digest=candidate_technical,
        )

    @staticmethod
    def _files_digest(files: dict[str, str]) -> str:
        digest = hashlib.sha256()
        for path, content in sorted(files.items()):
            path_bytes = path.encode("utf-8")
            content_bytes = content.encode("utf-8")
            digest.update(len(path_bytes).to_bytes(8, "big"))
            digest.update(path_bytes)
            digest.update(len(content_bytes).to_bytes(8, "big"))
            digest.update(content_bytes)
        return digest.hexdigest()

    async def _candidate(
        self,
        manager: Any,
        request: RestorationAdaptationPrepare,
        candidate_id: UUID,
        manifest: MachineManifest,
        files: dict[str, bytes],
    ) -> tuple[Any, int]:
        from omnia_orchestrator.core.config import get_settings
        from omnia_orchestrator.services.cell_publication_capacity import production_manager

        settings = get_settings()
        candidate_manager = production_manager(manager, settings)
        candidate_manager = replace(
            candidate_manager,
            admission_gate=CellAdmissionGate(
                candidate_manager.profile,
                workload="verification",
                verification_cpu_cores=float(settings.cell_verification_cpu_cores),
                verification_disk_bytes=int(settings.cell_verification_disk_bytes),
            ),
        )
        mutation = LifecycleMutation(
            uuid5(request.generation_run_id, "restoration-adaptation-candidate"),
            1,
            request.digest(),
        )
        spec = WorkspaceSpec(
            workspace_id=candidate_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            profile_version=candidate_manager.profile.profile_version,
            generation_run_id=request.generation_run_id,
        )
        await candidate_manager.ensure(spec, mutation)
        state = candidate_manager.state_store.load(candidate_id)
        if (
            state is None
            or state.active_generation_run_id != request.generation_run_id
            or state.active_generation_fencing_epoch != 1
            or candidate_manager.machine_runtime is None
        ):
            raise CellIdentityConflict("restoration adaptation candidate lease is incomplete")
        machine, backend = candidate_manager.machine_runtime.parts(state)
        await candidate_manager.docker.write_volume_files(
            backend.workspace_volume,
            files,
        )
        write_controller_json(
            machine.path,
            {
                "workspace_id": str(candidate_id),
                "manifest": manifest.model_dump(mode="json"),
                "epoch": 1,
                "ready_epoch": None,
                "operations": {},
            },
        )
        await machine_effect(backend.ensure, manifest, 1)
        return backend, 1

    async def owner_run_active(self, request: RestorationAdaptationPrepare) -> bool:
        manager = self._manager(request.workspace_id)
        async with manager.operation_lock.hold(request.workspace_id):
            state = manager.state_store.load(request.workspace_id)
            return bool(
                state is not None
                and state.project_id == request.project_id
                and state.owner_id == request.owner_id
                and state.active_generation_run_id == request.generation_run_id
                and state.active_generation_fencing_epoch == request.fencing_epoch
            )

    @staticmethod
    async def _reconcile_cleanup_operation(
        candidate_manager: Any,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
        state: Any,
        *,
        kind: str,
        operation_id: UUID,
    ) -> tuple[Any, UUID]:
        request_digest = request.digest()
        expected_generation = request.generation_run_id if kind == "release" else None
        current_id = operation_id
        prior_fence = -1
        prior_reconcile_id: UUID | None = None
        seen: set[UUID] = set()
        for _depth in range(_MAX_CLEANUP_RETRY_DEPTH):
            if current_id in seen:
                raise CellIdentityConflict(
                    "restoration adaptation cleanup retry chain cycles"
                )
            seen.add(current_id)
            current = state.operation(current_id)
            if current is None:
                if state.phase == "indeterminate" or (
                    prior_reconcile_id is not None
                    and state.last_operation_id != prior_reconcile_id
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation cleanup retry chain changed"
                    )
                return state, current_id
            if (
                current.kind != kind
                or current.request_digest != request_digest
                or current.generation_run_id != expected_generation
                or current.fencing_epoch <= prior_fence
            ):
                raise CellIdentityConflict(
                    "restoration adaptation cleanup retry chain changed"
                )
            if current.status == "completed":
                return state, current_id
            if current.status != "indeterminate":
                raise CellIdentityConflict(
                    "restoration adaptation cleanup retry chain changed"
                )

            reconcile_id = uuid5(
                current.operation_id,
                "restoration-adaptation-cleanup-reconcile",
            )
            reconcile = state.operation(reconcile_id)
            if reconcile is None:
                if (
                    state.phase != "indeterminate"
                    or state.last_operation_id != current.operation_id
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation cleanup reconcile chain changed"
                    )
                await candidate_manager.reconcile(
                    candidate_workspace_id,
                    LifecycleMutation(
                        reconcile_id,
                        state.fencing_epoch + 1,
                        request_digest,
                    ),
                )
                state = candidate_manager.state_store.load(candidate_workspace_id)
                if state is None:
                    raise CellResourceError(
                        "restoration adaptation cleanup reconcile was not confirmed"
                    )
                reconcile = state.operation(reconcile_id)
                if reconcile is None:
                    raise CellResourceError(
                        "restoration adaptation cleanup reconcile was not confirmed"
                    )
            if (
                reconcile.kind != "reconcile"
                or reconcile.request_digest != request_digest
                or reconcile.generation_run_id != expected_generation
                or reconcile.fencing_epoch <= current.fencing_epoch
                or reconcile.status not in {"indeterminate", "completed"}
            ):
                raise CellIdentityConflict(
                    "restoration adaptation cleanup reconcile chain changed"
                )
            if reconcile.status == "indeterminate":
                if (
                    state.phase != "indeterminate"
                    or state.last_operation_id != reconcile.operation_id
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation cleanup reconcile chain changed"
                    )
                await candidate_manager.reconcile(
                    candidate_workspace_id,
                    LifecycleMutation(
                        reconcile.operation_id,
                        reconcile.fencing_epoch,
                        request_digest,
                    ),
                )
                state = candidate_manager.state_store.load(candidate_workspace_id)
                if state is None:
                    raise CellResourceError(
                        "restoration adaptation cleanup reconcile was not confirmed"
                    )
                reconcile = state.operation(reconcile_id)
                if reconcile is None:
                    raise CellResourceError(
                        "restoration adaptation cleanup reconcile was not confirmed"
                    )
                if (
                    reconcile.kind != "reconcile"
                    or reconcile.request_digest != request_digest
                    or reconcile.generation_run_id != expected_generation
                    or reconcile.fencing_epoch <= current.fencing_epoch
                    or reconcile.status != "completed"
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation cleanup reconcile chain changed"
                    )

            prior_fence = reconcile.fencing_epoch
            prior_reconcile_id = reconcile.operation_id
            current_id = uuid5(
                current.operation_id,
                "restoration-adaptation-cleanup-retry",
            )
        raise CellIdentityConflict("restoration adaptation cleanup retry chain is too deep")

    async def cleanup(
        self,
        request: RestorationAdaptationPrepare,
        candidate_workspace_id: UUID,
        candidate_fencing_epoch: int,
    ) -> None:
        from omnia_orchestrator.core.config import get_settings
        from omnia_orchestrator.services.cell_publication_capacity import production_manager

        manager = self._manager(request.workspace_id)
        candidate_manager = production_manager(manager, get_settings())
        expected_candidate = uuid5(
            request.operation_id,
            f"restoration-adaptation:{request.generation_run_id}",
        )
        if candidate_workspace_id != expected_candidate:
            raise CellIdentityConflict("restoration adaptation cleanup candidate changed")
        state = candidate_manager.state_store.load(candidate_workspace_id)
        if state is None:
            return
        if (
            state.workspace_id != candidate_workspace_id
            or state.project_id != request.project_id
            or state.owner_id != request.owner_id
        ):
            raise CellIdentityConflict("restoration adaptation cleanup identity mismatch")
        if state.active_generation_run_id is not None:
            release_id = uuid5(request.generation_run_id, "restoration-adaptation-release")
            state, resolved_release_id = await self._reconcile_cleanup_operation(
                candidate_manager,
                request,
                candidate_workspace_id,
                state,
                kind="release",
                operation_id=release_id,
            )
            if (
                state.active_generation_run_id != request.generation_run_id
                or state.active_generation_fencing_epoch
                != (
                    state.fencing_epoch
                    if resolved_release_id != release_id
                    else candidate_fencing_epoch
                )
            ):
                raise CellIdentityConflict("restoration adaptation cleanup lease changed")
            release_id = resolved_release_id
            release_record = state.operation(release_id)
            release = LifecycleMutation(
                release_id,
                release_record.fencing_epoch
                if release_record is not None
                else state.fencing_epoch + 1,
                request.digest(),
            )
            await candidate_manager.release_generation(
                candidate_workspace_id,
                release,
                generation_run_id=request.generation_run_id,
            )
        current = candidate_manager.state_store.load(candidate_workspace_id)
        if current is None:
            return
        destroy_id = uuid5(request.generation_run_id, "restoration-adaptation-destroy")
        current, destroy_id = await self._reconcile_cleanup_operation(
            candidate_manager,
            request,
            candidate_workspace_id,
            current,
            kind="destroy",
            operation_id=destroy_id,
        )
        async with candidate_manager.operation_lock.hold(candidate_workspace_id):
            current = candidate_manager.state_store.load(candidate_workspace_id)
            if current is None or current.active_generation_run_id is not None:
                raise CellIdentityConflict("restoration adaptation candidate did not stop")
            destroy_record = current.operation(destroy_id)
            destroy = LifecycleMutation(
                destroy_id,
                destroy_record.fencing_epoch
                if destroy_record is not None
                else current.fencing_epoch + 1,
                request.digest(),
            )
            await candidate_manager.destroy_compute_without_lock(
                candidate_workspace_id,
                destroy,
                checkpoint_ref=None,
                record_operation=True,
                capture=False,
            )
            volumes = await candidate_manager.docker.list_workspace_volumes(candidate_workspace_id)
            for volume in volumes:
                if (
                    volume.labels.get("omnia.workspace_id") != str(candidate_workspace_id)
                    or volume.labels.get("omnia.project_id") != str(request.project_id)
                    or volume.labels.get("omnia.owner_id") != str(request.owner_id)
                ):
                    raise CellIdentityConflict(
                        "restoration adaptation candidate volume identity mismatch"
                    )
            for volume in volumes:
                await candidate_manager.docker.remove_volume(volume.name)


_service: RestorationAdaptationWorkspaceService | None = None


def get_restoration_adaptation_workspace_service() -> RestorationAdaptationWorkspaceService:
    global _service
    if _service is None:
        _service = RestorationAdaptationWorkspaceService()
    return _service


def start_restoration_adaptation_recovery() -> asyncio.Task[None]:
    async def run() -> None:
        while True:
            try:
                await get_restoration_adaptation_workspace_service().recover()
            except Exception as exc:
                _log.warning("recovery_failed", error_type=type(exc).__name__)
            await asyncio.sleep(30)

    return asyncio.create_task(run(), name="restoration-adaptation-recovery")
