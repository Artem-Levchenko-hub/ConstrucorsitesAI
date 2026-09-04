from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.project_cell import ProjectCellProof, ProjectCellProofResult
from omnia_api.services.agent_progress import bounded_redacted_text

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_DETAIL_BYTES = 4096
_MAX_ARTIFACT_REF_BYTES = 512


class ProjectCellProofConflict(RuntimeError):
    pass


class ProofDimension(StrEnum):
    BOOTSTRAP = "bootstrap"
    FAST_CHECK = "fast_check"
    FULL_BUILD = "full_build"
    RUNTIME = "runtime"
    RELEASE = "release"


class ProofOutcome(StrEnum):
    GREEN = "green"
    RED = "red"


def require_sha256_digest(value: str, label: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a 64-character lowercase sha256 digest")
    return value


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _bounded_redacted_text(value: str, *, max_bytes: int) -> str:
    return bounded_redacted_text(value.strip(), max_bytes=max_bytes)


@dataclass(frozen=True, slots=True)
class ProofIdentity:
    workspace_id: UUID
    generation_run_id: UUID
    fencing_epoch: int
    workspace_revision: str
    dependency_digest: str
    schema_data_digest: str
    cell_manifest_digest: str
    base_image_digest: str
    toolchain_digest: str
    resource_profile_version: str
    build_config_digest: str

    def __post_init__(self) -> None:
        if self.fencing_epoch <= 0:
            raise ValueError("fencing_epoch must be positive")
        for label, value in (
            ("workspace_revision", self.workspace_revision),
            ("dependency_digest", self.dependency_digest),
            ("schema_data_digest", self.schema_data_digest),
            ("cell_manifest_digest", self.cell_manifest_digest),
            ("base_image_digest", self.base_image_digest),
            ("toolchain_digest", self.toolchain_digest),
            ("build_config_digest", self.build_config_digest),
        ):
            require_sha256_digest(value, label)
        if not self.resource_profile_version.strip():
            raise ValueError("resource_profile_version must not be blank")

    @property
    def proof_key(self) -> str:
        return _digest(
            {
                "fencing_epoch": self.fencing_epoch,
                "workspace_revision": self.workspace_revision,
                "dependency_digest": self.dependency_digest,
                "schema_data_digest": self.schema_data_digest,
                "cell_manifest_digest": self.cell_manifest_digest,
                "base_image_digest": self.base_image_digest,
                "toolchain_digest": self.toolchain_digest,
                "resource_profile_version": self.resource_profile_version,
                "build_config_digest": self.build_config_digest,
            }
        )

    def dimension_key(
        self,
        dimension: ProofDimension,
        *,
        artifact_digest: str | None = None,
    ) -> str:
        common: dict[str, object] = {
            "fencing_epoch": self.fencing_epoch,
            "dependency_digest": self.dependency_digest,
            "cell_manifest_digest": self.cell_manifest_digest,
            "base_image_digest": self.base_image_digest,
            "toolchain_digest": self.toolchain_digest,
            "resource_profile_version": self.resource_profile_version,
        }
        if dimension is ProofDimension.BOOTSTRAP:
            payload = common
        elif dimension is ProofDimension.FAST_CHECK:
            payload = {
                **common,
                "workspace_revision": self.workspace_revision,
                "schema_data_digest": self.schema_data_digest,
                "build_config_digest": self.build_config_digest,
            }
        elif dimension is ProofDimension.FULL_BUILD:
            payload = {
                **common,
                "workspace_revision": self.workspace_revision,
                "build_config_digest": self.build_config_digest,
            }
        elif dimension in {ProofDimension.RUNTIME, ProofDimension.RELEASE}:
            if artifact_digest is None:
                raise ValueError(f"artifact_digest is required for {dimension.value}")
            payload = {
                **common,
                "workspace_revision": self.workspace_revision,
                "schema_data_digest": self.schema_data_digest,
                "build_config_digest": self.build_config_digest,
                "artifact_digest": require_sha256_digest(
                    artifact_digest,
                    "artifact_digest",
                ),
            }
        else:
            raise ValueError(f"unsupported proof dimension: {dimension}")
        if artifact_digest is not None and dimension not in {
            ProofDimension.RUNTIME,
            ProofDimension.RELEASE,
        }:
            raise ValueError(f"artifact_digest is not valid for {dimension.value}")
        return _digest({"dimension": dimension.value, **payload})


def proof_identity_from_model(proof: ProjectCellProof) -> ProofIdentity:
    return ProofIdentity(
        workspace_id=proof.workspace_id,
        generation_run_id=proof.generation_run_id,
        fencing_epoch=proof.fencing_epoch,
        workspace_revision=proof.workspace_revision,
        dependency_digest=proof.dependency_digest,
        schema_data_digest=proof.schema_data_digest,
        cell_manifest_digest=proof.cell_manifest_digest,
        base_image_digest=proof.base_image_digest,
        toolchain_digest=proof.toolchain_digest,
        resource_profile_version=proof.resource_profile_version,
        build_config_digest=proof.build_config_digest,
    )


async def create_proof_identity(
    session: AsyncSession,
    *,
    identity: ProofIdentity,
) -> ProjectCellProof:
    existing = await session.scalar(
        select(ProjectCellProof).where(
            ProjectCellProof.workspace_id == identity.workspace_id,
            ProjectCellProof.fencing_epoch == identity.fencing_epoch,
            ProjectCellProof.proof_key == identity.proof_key,
        )
    )
    if existing is not None:
        return existing
    proof = ProjectCellProof(
        id=uuid4(),
        workspace_id=identity.workspace_id,
        generation_run_id=identity.generation_run_id,
        fencing_epoch=identity.fencing_epoch,
        proof_key=identity.proof_key,
        workspace_revision=identity.workspace_revision,
        dependency_digest=identity.dependency_digest,
        schema_data_digest=identity.schema_data_digest,
        cell_manifest_digest=identity.cell_manifest_digest,
        base_image_digest=identity.base_image_digest,
        toolchain_digest=identity.toolchain_digest,
        resource_profile_version=identity.resource_profile_version,
        build_config_digest=identity.build_config_digest,
    )
    try:
        async with session.begin_nested():
            session.add(proof)
            await session.flush()
    except IntegrityError:
        raced = await session.scalar(
            select(ProjectCellProof).where(
                ProjectCellProof.workspace_id == identity.workspace_id,
                ProjectCellProof.fencing_epoch == identity.fencing_epoch,
                ProjectCellProof.proof_key == identity.proof_key,
            )
        )
        if raced is None:
            raise
        return raced
    return proof


async def find_proof_result(
    session: AsyncSession,
    *,
    proof: ProjectCellProof,
    dimension: ProofDimension,
    artifact_digest: str | None = None,
) -> ProjectCellProofResult | None:
    dimension_key = proof_identity_from_model(proof).dimension_key(
        dimension,
        artifact_digest=artifact_digest,
    )
    return cast(
        ProjectCellProofResult | None,
        await session.scalar(
            select(ProjectCellProofResult).where(
                ProjectCellProofResult.workspace_id == proof.workspace_id,
                ProjectCellProofResult.dimension == dimension.value,
                ProjectCellProofResult.dimension_key == dimension_key,
            )
        )
    )


async def record_proof_result(
    session: AsyncSession,
    *,
    proof: ProjectCellProof,
    dimension: ProofDimension,
    outcome: ProofOutcome,
    operation_id: UUID,
    artifact_ref: str | None,
    detail: str,
    artifact_digest: str | None = None,
) -> ProjectCellProofResult:
    existing = await find_proof_result(
        session,
        proof=proof,
        dimension=dimension,
        artifact_digest=artifact_digest,
    )
    if existing is not None:
        raise ProjectCellProofConflict(
            f"{dimension.value} proof result already terminal for this dimension key"
        )
    detail_text = _bounded_redacted_text(detail, max_bytes=_MAX_DETAIL_BYTES)
    safe_artifact_ref = (
        None
        if artifact_ref is None
        else _bounded_redacted_text(artifact_ref, max_bytes=_MAX_ARTIFACT_REF_BYTES)
    )
    result = ProjectCellProofResult(
        id=uuid4(),
        proof_id=proof.id,
        workspace_id=proof.workspace_id,
        dimension=dimension.value,
        dimension_key=proof_identity_from_model(proof).dimension_key(
            dimension,
            artifact_digest=artifact_digest,
        ),
        outcome=outcome.value,
        operation_id=operation_id,
        artifact_ref=safe_artifact_ref,
        detail_digest=hashlib.sha256(detail_text.encode("utf-8")).hexdigest(),
        redacted_detail=detail_text,
    )
    try:
        async with session.begin_nested():
            session.add(result)
            await session.flush()
    except IntegrityError as exc:
        raise ProjectCellProofConflict(
            f"{dimension.value} proof result already terminal for this dimension key"
        ) from exc
    return result


__all__ = [
    "ProjectCellProofConflict",
    "ProofDimension",
    "ProofIdentity",
    "ProofOutcome",
    "create_proof_identity",
    "find_proof_result",
    "proof_identity_from_model",
    "record_proof_result",
    "require_sha256_digest",
]
