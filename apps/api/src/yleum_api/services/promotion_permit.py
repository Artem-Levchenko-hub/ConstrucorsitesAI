"""Fail-closed, content-bound permit for MAX candidate promotion/publication."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FULL_BUILD_CONTRACT_VERSION = "max-full-build-project-migrations-v2"
MAX_RELEASE_PROOF_CONTRACT_VERSION = "max-release-v2"
MAX_EMBEDDED_SECURITY_POLICY_VERSION = "owner-preview-csp-v1"


class PromotionPermitError(RuntimeError):
    """Structured rejection before a candidate can become user-visible."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class PromotionPermit:
    version: int
    proof_key: str
    workspace_id: UUID
    generation_run_id: UUID
    fencing_epoch: int
    workspace_revision: str
    build_ref: str
    artifact_digest: str
    published_files_digest: str
    build_contract_version: str
    proof_contract_version: str
    security_policy_version: str
    behavior_receipt_digest: str
    release_receipt_digest: str
    verification_ref: str
    permit_digest: str


def _canonical_payload(permit: PromotionPermit) -> dict[str, object]:
    payload = asdict(permit)
    payload["workspace_id"] = str(permit.workspace_id)
    payload["generation_run_id"] = str(permit.generation_run_id)
    payload.pop("permit_digest")
    return payload


def _digest_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _files_digest(files: Mapping[str, str], *, source_revision: bool) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        if type(path) is not str or type(content) is not str:
            raise PromotionPermitError(
                "PROMOTION_EVIDENCE_MISSING",
                "published files must be a string mapping",
            )
        if source_revision and (
            PurePosixPath(path).name == "next-env.d.ts" or path.endswith(".tsbuildinfo")
        ):
            continue
        path_bytes = path.encode("utf-8")
        content_bytes = content.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content_bytes).to_bytes(8, "big"))
        digest.update(content_bytes)
    return digest.hexdigest()


def canonical_files_digest(files: Mapping[str, str]) -> str:
    """Digest the exact file mapping that Git will publish."""

    return _files_digest(files, source_revision=False)


def workspace_revision_digest(files: Mapping[str, str]) -> str:
    """Mirror the Project Cell source identity, excluding compiler bookkeeping."""

    return _files_digest(files, source_revision=True)


def release_receipt_ref(*, artifact_digest: str, receipt_digest: str) -> str:
    if not _SHA256_RE.fullmatch(artifact_digest) or not _SHA256_RE.fullmatch(receipt_digest):
        raise ValueError("release receipt digests must be lowercase sha256")
    return f"verification/sha256/{receipt_digest}"


def release_receipt_digest(*, proof_key: str, artifact_digest: str, detail: str) -> str:
    for value, label in ((proof_key, "proof_key"), (artifact_digest, "artifact_digest")):
        if not _SHA256_RE.fullmatch(value):
            raise ValueError(f"{label} must be lowercase sha256")
    payload = "\0".join(
        (
            MAX_RELEASE_PROOF_CONTRACT_VERSION,
            MAX_EMBEDDED_SECURITY_POLICY_VERSION,
            proof_key,
            artifact_digest,
            detail,
        )
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def release_receipt_matches(result: object, artifact_digest: str, proof_key: str) -> bool:
    detail = str(getattr(result, "redacted_detail", ""))
    expected_digest = release_receipt_digest(
        proof_key=proof_key,
        artifact_digest=artifact_digest,
        detail=detail,
    )
    return getattr(result, "artifact_ref", None) == release_receipt_ref(
        artifact_digest=artifact_digest,
        receipt_digest=expected_digest,
    )


def _receipt_digest(ref: object, *, label: str, prefix: str) -> str:
    value = str(ref or "")
    expected = prefix + "/sha256/"
    digest = value.removeprefix(expected) if value.startswith(expected) else ""
    if not _SHA256_RE.fullmatch(digest):
        raise PromotionPermitError(
            "PROMOTION_EVIDENCE_MISSING",
            f"{label} is not a content-addressed receipt",
        )
    return digest


def _green_result(bundle: Any, name: str) -> Any:
    result = getattr(bundle, name, None)
    if result is None:
        raise PromotionPermitError("PROMOTION_EVIDENCE_MISSING", f"{name} proof is missing")
    if getattr(result, "outcome", None) != "green":
        raise PromotionPermitError("PROMOTION_EVIDENCE_RED", f"{name} proof is not green")
    return result


def issue_promotion_permit(bundle: Any) -> PromotionPermit:
    """Issue a deterministic permit only for one complete green proof bundle."""

    identity = getattr(bundle, "identity", None)
    if identity is None:
        raise PromotionPermitError("PROMOTION_EVIDENCE_MISSING", "proof identity is missing")
    proof_key = str(getattr(identity, "proof_key", ""))
    workspace_revision = str(getattr(identity, "workspace_revision", ""))
    if not _SHA256_RE.fullmatch(proof_key) or not _SHA256_RE.fullmatch(workspace_revision):
        raise PromotionPermitError(
            "PROMOTION_EVIDENCE_MISSING",
            "proof identity is not content-addressed",
        )
    try:
        workspace_id = UUID(str(identity.workspace_id))
        generation_run_id = UUID(str(identity.generation_run_id))
        fencing_epoch = int(identity.fencing_epoch)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PromotionPermitError(
            "PROMOTION_EVIDENCE_MISSING",
            "proof identity is incomplete",
        ) from exc
    if fencing_epoch <= 0:
        raise PromotionPermitError(
            "PROMOTION_EVIDENCE_MISSING",
            "proof fencing epoch is invalid",
        )

    build = _green_result(bundle, "full_build")
    behavior = _green_result(bundle, "runtime")
    release = _green_result(bundle, "release")
    proof_id = getattr(identity, "id", None)
    for label, result in (("full_build", build), ("runtime", behavior), ("release", release)):
        result_workspace = UUID(str(getattr(result, "workspace_id", workspace_id)))
        if (
            proof_id is not None and getattr(result, "proof_id", proof_id) != proof_id
        ) or result_workspace != workspace_id:
            raise PromotionPermitError(
                "PROMOTION_EVIDENCE_STALE",
                f"{label} proof belongs to another identity",
            )

    build_ref = str(getattr(build, "artifact_ref", "") or "")
    artifact_digest = _receipt_digest(build_ref, label="build", prefix="build")
    behavior_digest = _receipt_digest(
        getattr(behavior, "artifact_ref", None),
        label="behavior",
        prefix="verification",
    )
    verification_ref = str(getattr(release, "artifact_ref", "") or "")
    expected_release_digest = release_receipt_digest(
        proof_key=proof_key,
        artifact_digest=artifact_digest,
        detail=str(getattr(release, "redacted_detail", "")),
    )
    if verification_ref != release_receipt_ref(
        artifact_digest=artifact_digest,
        receipt_digest=expected_release_digest,
    ):
        raise PromotionPermitError(
            "PROMOTION_EVIDENCE_STALE",
            "release receipt uses an obsolete contract, policy, or artifact",
        )
    release_digest = expected_release_digest
    unsigned = PromotionPermit(
        version=3,
        proof_key=proof_key,
        workspace_id=workspace_id,
        generation_run_id=generation_run_id,
        fencing_epoch=fencing_epoch,
        workspace_revision=workspace_revision,
        build_ref=build_ref,
        artifact_digest=artifact_digest,
        published_files_digest=artifact_digest,
        build_contract_version=MAX_FULL_BUILD_CONTRACT_VERSION,
        proof_contract_version=MAX_RELEASE_PROOF_CONTRACT_VERSION,
        security_policy_version=MAX_EMBEDDED_SECURITY_POLICY_VERSION,
        behavior_receipt_digest=behavior_digest,
        release_receipt_digest=release_digest,
        verification_ref=verification_ref,
        permit_digest="",
    )
    return PromotionPermit(
        **{
            **asdict(unsigned),
            "permit_digest": _digest_payload(_canonical_payload(unsigned)),
        }
    )


def require_promotion_permit(
    permit: PromotionPermit | None,
    *,
    proof: Any | None = None,
    current_identity: Any | None = None,
    expected_generation_run_id: UUID | None = None,
    expected_workspace_id: UUID | None = None,
    published_files: Mapping[str, str] | None = None,
) -> PromotionPermit:
    """Validate integrity and bind the permit to supplied proof/current identity."""

    if permit is None:
        raise PromotionPermitError(
            "PROMOTION_PERMIT_MISSING",
            "MAX promotion requires a green immutable permit",
        )
    if permit.permit_digest != _digest_payload(_canonical_payload(permit)):
        raise PromotionPermitError(
            "PROMOTION_PERMIT_TAMPERED",
            "promotion permit digest does not match its payload",
        )
    if proof is not None and permit != issue_promotion_permit(proof):
        raise PromotionPermitError(
            "PROMOTION_PERMIT_STALE",
            "promotion permit does not match the proof bundle",
        )
    if expected_generation_run_id is not None and permit.generation_run_id != UUID(
        str(expected_generation_run_id)
    ):
        raise PromotionPermitError(
            "PROMOTION_PERMIT_STALE",
            "promotion permit belongs to another generation run",
        )
    if expected_workspace_id is not None and permit.workspace_id != UUID(
        str(expected_workspace_id)
    ):
        raise PromotionPermitError(
            "PROMOTION_PERMIT_STALE",
            "promotion permit belongs to another workspace",
        )
    if published_files is not None:
        published_digest = canonical_files_digest(published_files)
        if (
            published_digest != permit.published_files_digest
            or published_digest != permit.artifact_digest
        ):
            raise PromotionPermitError(
                "PROMOTION_PERMIT_STALE",
                "published files changed after artifact verification",
            )
    if current_identity is not None:
        current = (
            str(getattr(current_identity, "proof_key", "")),
            UUID(str(getattr(current_identity, "workspace_id", UUID(int=0)))),
            UUID(str(getattr(current_identity, "generation_run_id", UUID(int=0)))),
            int(getattr(current_identity, "fencing_epoch", 0)),
            str(getattr(current_identity, "workspace_revision", "")),
        )
        expected = (
            permit.proof_key,
            permit.workspace_id,
            permit.generation_run_id,
            permit.fencing_epoch,
            permit.workspace_revision,
        )
        if current != expected:
            raise PromotionPermitError(
                "PROMOTION_PERMIT_STALE",
                "workspace identity changed after verification",
            )
    return permit


__all__ = [
    "MAX_EMBEDDED_SECURITY_POLICY_VERSION",
    "MAX_FULL_BUILD_CONTRACT_VERSION",
    "MAX_RELEASE_PROOF_CONTRACT_VERSION",
    "PromotionPermit",
    "PromotionPermitError",
    "canonical_files_digest",
    "issue_promotion_permit",
    "release_receipt_digest",
    "release_receipt_matches",
    "release_receipt_ref",
    "require_promotion_permit",
    "workspace_revision_digest",
]
