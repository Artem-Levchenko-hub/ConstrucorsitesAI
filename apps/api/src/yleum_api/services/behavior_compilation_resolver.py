"""Trusted retained command -> candidate-bound compiler witness, without commands.

Only controller-owned handle callbacks read an exact durable operation. Generated
state, DOM, source digests and model tool responses cannot supply this receipt.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, cast
from uuid import UUID, uuid5

from yleum_api.services.max_behavior_proof import (
    BehaviorDriverInput,
    BehaviorProofError,
    CandidateBehaviorBinding,
    CompiledAssetWitness,
    ObservedAsset,
    _binding,
    _compiled,
    digest,
    need,
)
from yleum_api.services.orchestrator_client import ProjectCellAgentExecResponse
from yleum_api.services.project_cell_proofs import ProofIdentity
from yleum_api.services.promotion_permit import MAX_FULL_BUILD_CONTRACT_VERSION

COLLECTOR_SOURCE_SHA256 = "c7fb31a2f7fd70e984a931c770e3f2d5753d07b875361ac8171136c7e23a1bd6"

_HEX = re.compile(r"[0-9a-f]{64}")


def compilation_operation_id(bound: CandidateBehaviorBinding) -> UUID:
    return uuid5(
        bound.generation_run_id,
        "command:"
        f"{bound.workspace_id}:{bound.fencing_epoch}:{bound.proof_key}:"
        f"full_build:{MAX_FULL_BUILD_CONTRACT_VERSION}",
    )


def _identity(current: object, bound: CandidateBehaviorBinding) -> ProofIdentity:
    need(type(current) is ProofIdentity, "BEHAVIOR_COMPILATION_IDENTITY_CHANGED")
    proved = cast(ProofIdentity, current)
    need(
        proved.workspace_id == bound.workspace_id
        and proved.generation_run_id == bound.generation_run_id
        and proved.fencing_epoch == bound.fencing_epoch
        and proved.workspace_revision == bound.source_revision
        and proved.proof_key == bound.proof_key,
        "BEHAVIOR_COMPILATION_IDENTITY_CHANGED",
    )
    return proved


async def resolve_retained_compilation(
    handle: Any, request: BehaviorDriverInput
) -> CompiledAssetWitness:
    _binding(request.binding, request.contract)
    need(
        callable(handle.current_identity) and callable(handle.operation_status),
        "BEHAVIOR_COMPILATION_UNAVAILABLE",
    )
    bound = request.binding
    before = _identity(await handle.current_identity(), bound)
    operation = compilation_operation_id(bound)
    status = await handle.operation_status(operation)
    response = status.terminal_response
    need(
        status.operation_id == operation
        and status.state == "completed"
        and type(response) is ProjectCellAgentExecResponse
        and response.operation_id == operation
        and response.ok
        and response.exit_code == 0
        and not response.timed_out
        and response.workspace_revision == bound.source_revision,
        "BEHAVIOR_COMPILATION_UNAVAILABLE",
    )
    need(
        response.before_identity is not None
        and response.after_identity is not None
        and response.before_identity.workspace_revision == bound.source_revision,
        "BEHAVIOR_COMPILATION_IDENTITY_CHANGED",
    )
    expected = dict(
        workspace_revision=before.workspace_revision,
        dependency_digest=before.dependency_digest,
        schema_data_digest=before.schema_data_digest,
        cell_manifest_digest=before.cell_manifest_digest,
        environment_digest=before.base_image_digest,
        build_config_digest=before.build_config_digest,
    )
    need(
        asdict(response.before_identity) == expected
        and asdict(response.after_identity) == expected,
        "BEHAVIOR_COMPILATION_IDENTITY_CHANGED",
    )
    data = response.compiled_asset_receipt
    keys = {
        "collector_version",
        "declared_next_version",
        "build_id_sha256",
        "metadata_sha256",
        "assets",
        "workspace_id",
        "project_id",
        "generation_run_id",
        "fencing_epoch",
        "source_revision",
        "operation_id",
        "manifest_digest",
        "collector_source_sha256",
    }
    need(type(data) is dict and set(data) == keys, "BEHAVIOR_COMPILATION_UNAVAILABLE")
    try:
        need(
            data["collector_version"] == "next-fixed-data-v1"
            and data["collector_source_sha256"] == COLLECTOR_SOURCE_SHA256
            and data["declared_next_version"] == "15.5.24"
            and data["workspace_id"] == str(bound.workspace_id)
            and data["project_id"] == str(bound.project_id)
            and data["generation_run_id"] == str(bound.generation_run_id)
            and type(data["fencing_epoch"]) is int
            and data["fencing_epoch"] == bound.fencing_epoch
            and data["source_revision"] == bound.source_revision
            and data["operation_id"] == str(operation)
            and data["manifest_digest"] == before.cell_manifest_digest,
            "BEHAVIOR_COMPILATION_IDENTITY_CHANGED",
        )
        need(
            all(
                type(data[k]) is str and _HEX.fullmatch(data[k])
                for k in ("build_id_sha256", "collector_source_sha256")
            ),
            "BEHAVIOR_COMPILATION_UNAVAILABLE",
        )
        metadata = data["metadata_sha256"]
        need(
            type(metadata) is dict
            and set(metadata)
            == {"package.json", ".next/build-manifest.json", ".next/app-build-manifest.json"}
            and all(type(v) is str and _HEX.fullmatch(v) for v in metadata.values()),
            "BEHAVIOR_COMPILATION_UNAVAILABLE",
        )
        assets = data["assets"]
        need(type(assets) is list and 1 <= len(assets) <= 128, "BEHAVIOR_COMPILATION_UNAVAILABLE")
        need(
            all(type(a) is dict and set(a) == {"path", "sha256", "bytes"} for a in assets),
            "BEHAVIOR_COMPILATION_UNAVAILABLE",
        )
        witness = _compiled(
            CompiledAssetWitness(
                bound,
                tuple(ObservedAsset(**asset) for asset in assets),
                digest(
                    {
                        "binding": bound.to_json(),
                        "operation": str(operation),
                        "transport_after_identity": expected,
                        "compiler_receipt": data,
                    }
                ),
                "served_candidate_compilation_v1",
            ),
            bound,
        )
    except BehaviorProofError:
        raise
    except Exception:
        raise BehaviorProofError("BEHAVIOR_COMPILATION_UNAVAILABLE") from None
    after = _identity(await handle.current_identity(), bound)
    need(before == after, "BEHAVIOR_COMPILATION_IDENTITY_CHANGED")
    return witness
