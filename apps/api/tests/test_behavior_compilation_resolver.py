"""Retained transport is controller data, not generated DOM/model state."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from yleum_api.services import max_behavior_proof as b
from yleum_api.services.behavior_compilation_resolver import (
    COLLECTOR_SOURCE_SHA256,
    compilation_operation_id,
    resolve_retained_compilation,
)
from yleum_api.services.orchestrator_client import (
    ProjectCellAgentExecResponse,
    ProjectCellWorkspaceIdentity,
)
from yleum_api.services.project_cell_proofs import ProofIdentity

from .test_max_behavior_proof import binding, contract


def setup():
    initial = binding()
    identity = ProofIdentity(
        initial.workspace_id,
        initial.generation_run_id,
        initial.fencing_epoch,
        initial.source_revision,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "f" * 64,
        "profile-v1",
        "1" * 64,
    )
    bound = replace(initial, proof_key=identity.proof_key)
    request = b.BehaviorDriverInput(bound, contract(), b.PrivatePreviewCapability(None))
    operation = compilation_operation_id(bound)
    wi = ProjectCellWorkspaceIdentity(
        identity.workspace_revision,
        identity.dependency_digest,
        identity.schema_data_digest,
        identity.cell_manifest_digest,
        identity.base_image_digest,
        identity.build_config_digest,
    )
    receipt = dict(
        collector_version="next-fixed-data-v1",
        declared_next_version="15.5.24",
        build_id_sha256="2" * 64,
        metadata_sha256={
            k: "3" * 64
            for k in ["package.json", ".next/build-manifest.json", ".next/app-build-manifest.json"]
        },
        assets=[{"path": "/_next/static/chunks/app/page.js", "sha256": "4" * 64, "bytes": 12}],
        workspace_id=str(bound.workspace_id),
        project_id=str(bound.project_id),
        generation_run_id=str(bound.generation_run_id),
        fencing_epoch=bound.fencing_epoch,
        source_revision=bound.source_revision,
        operation_id=str(operation),
        manifest_digest=identity.cell_manifest_digest,
        collector_source_sha256=COLLECTOR_SOURCE_SHA256,
    )
    response = ProjectCellAgentExecResponse(
        True,
        0,
        "",
        False,
        bound.source_revision,
        operation,
        wi,
        wi,
        False,
        compiled_asset_receipt=receipt,
    )
    status = NS(operation_id=operation, state="completed", terminal_response=response)
    handle = NS(
        operation_status=AsyncMock(return_value=status),
        current_identity=AsyncMock(return_value=identity),
    )
    return request, handle, response, status, identity


async def test_exact_retained_operation_with_candidate_and_request_binding():
    req, handle, response, _, _ = setup()
    witness = await resolve_retained_compilation(handle, req)
    assert witness.binding == req.binding
    assert witness.assets[0].path.endswith("/app/page.js")
    assert witness.compilation_receipt_sha256 != response.compiled_asset_receipt["source_revision"]
    assert handle.current_identity.await_count == 2
    handle.operation_status.assert_awaited_once_with(compilation_operation_id(req.binding))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "foreign"),
        ("project_id", "foreign"),
        ("generation_run_id", "foreign"),
        ("fencing_epoch", 8),
        ("source_revision", "0" * 64),
        ("operation_id", "foreign"),
        ("manifest_digest", "0" * 64),
        ("collector_version", "model"),
        ("declared_next_version", "16.0.0"),
    ],
)
async def test_foreign_stale_unsupported_compilation_denied(field, value):
    req, handle, response, _, _ = setup()
    response.compiled_asset_receipt[field] = value
    with pytest.raises(b.BehaviorProofError):
        await resolve_retained_compilation(handle, req)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "running",
        "failed",
        "timed-out",
        "before-source",
        "after-environment",
        "metadata-extra",
        "source-path",
    ],
)
async def test_no_receipt_or_incompatible_transport_never_proves_compilation(mutation):
    req, handle, response, status, _ = setup()
    if mutation == "missing":
        status.terminal_response = replace(response, compiled_asset_receipt=None)
    if mutation == "running":
        status.state = "running"
    if mutation == "failed":
        status.terminal_response = replace(response, ok=False, exit_code=1)
    if mutation == "timed-out":
        status.terminal_response = replace(response, timed_out=True)
    if mutation == "before-source":
        status.terminal_response = replace(
            response,
            before_identity=replace(response.before_identity, workspace_revision="0" * 64),
            environment_mutated=True,
        )
    if mutation == "after-environment":
        status.terminal_response = replace(
            response,
            after_identity=replace(response.after_identity, environment_digest="0" * 64),
            environment_mutated=True,
        )
    if mutation == "metadata-extra":
        response.compiled_asset_receipt["metadata_sha256"][".env"] = "0" * 64
    if mutation == "source-path":
        response.compiled_asset_receipt["assets"][0]["path"] = "/src/app/page.tsx"
    with pytest.raises(b.BehaviorProofError):
        await resolve_retained_compilation(handle, req)


async def test_identity_race_after_operation_read_denied():
    req, handle, _, _, identity = setup()
    handle.current_identity.side_effect = [identity, replace(identity, fencing_epoch=8)]
    with pytest.raises(b.BehaviorProofError):
        await resolve_retained_compilation(handle, req)


async def test_replayed_compilation_cannot_cross_request_or_candidate():
    req, handle, _, _, _ = setup()
    one = await resolve_retained_compilation(handle, req)
    altered = replace(req, binding=replace(req.binding, request_sha256="9" * 64))
    with pytest.raises(b.BehaviorProofError):
        await resolve_retained_compilation(handle, altered)
    assert one.binding.request_sha256 == req.contract.request_sha256


def test_strict_exec_transport_round_trip_preserves_receipt():
    from dataclasses import asdict

    req, _, response, _, _ = setup()
    payload = asdict(response)
    payload["operation_id"] = str(response.operation_id)
    recovered = ProjectCellAgentExecResponse.from_json(payload)
    assert recovered.compiled_asset_receipt == response.compiled_asset_receipt
    assert recovered.operation_id == compilation_operation_id(req.binding)
    payload["compiled_asset_receipt"] = "model self-report"
    with pytest.raises(Exception):
        ProjectCellAgentExecResponse.from_json(payload)


@pytest.mark.parametrize(
    "path",
    [
        "/_next/static/exports/app.zip",
        "/_next/static/media/source.tsx",
        "/src/app/page.tsx",
        "/_next/static/chunks/../env.js",
    ],
)
async def test_archive_source_and_traversal_can_never_be_compiled_asset(path):
    req, handle, response, _, _ = setup()
    response.compiled_asset_receipt["assets"][0]["path"] = path
    with pytest.raises(b.BehaviorProofError):
        await resolve_retained_compilation(handle, req)


@pytest.mark.parametrize(
    "source_sha256",
    ["0" * 64, "4fa8d8d7a4f6d5266c2e85585eba02e66429d262c3f5d6c360d2c2f7fc34008e"],
)
async def test_different_collector_source_is_unsupported_even_with_same_version(source_sha256):
    req, handle, response, _, _ = setup()
    response.compiled_asset_receipt["collector_source_sha256"] = source_sha256
    with pytest.raises(b.BehaviorProofError):
        await resolve_retained_compilation(handle, req)


def test_collector_executable_source_pin_matches_this_frozen_orchestrator_slice():
    import hashlib
    from pathlib import Path

    collector = (
        Path(__file__).parents[2]
        / "orchestrator/src/yleum_orchestrator/services/next_compilation_receipt.py"
    )
    assert hashlib.sha256(collector.read_bytes()).hexdigest() == COLLECTOR_SOURCE_SHA256


async def test_transport_before_environment_cannot_cross_current_proof_identity():
    req, handle, response, status, _ = setup()
    status.terminal_response = replace(
        response,
        before_identity=replace(response.before_identity, environment_digest="0" * 64),
        environment_mutated=True,
    )
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_COMPILATION_IDENTITY_CHANGED"):
        await resolve_retained_compilation(handle, req)
