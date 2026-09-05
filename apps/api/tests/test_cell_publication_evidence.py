from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import (
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from omnia_api.models.snapshot import Snapshot
from omnia_api.services.project_cell_proofs import ProofDimension, ProofIdentity


def evidence():
    project_id, owner_id, workspace_id, run_id, snapshot_id = [uuid4() for _ in range(5)]
    project = Project(
        id=project_id,
        owner_id=owner_id,
        current_snapshot_id=snapshot_id,
        name="Release",
        slug="release-test",
        template="max_miniapp",
    )
    workspace = ProjectCellWorkspace(
        id=workspace_id,
        project_id=project_id,
        owner_id=owner_id,
        state="ready",
        provider="docker_owner_canary",
        generation_run_id=None,
        fencing_epoch=8,
    )
    snapshot = Snapshot(id=snapshot_id, project_id=project_id, commit_sha="a" * 40)
    identity = ProofIdentity(
        workspace_id=workspace_id,
        generation_run_id=run_id,
        fencing_epoch=7,
        workspace_revision="b" * 64,
        dependency_digest="c" * 64,
        schema_data_digest="d" * 64,
        cell_manifest_digest="e" * 64,
        base_image_digest="f" * 64,
        toolchain_digest="1" * 64,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest="2" * 64,
    )
    proof = ProjectCellProof(
        id=uuid4(),
        proof_key=identity.proof_key,
        **{name: getattr(identity, name) for name in identity.__dataclass_fields__},
    )
    candidate = ProjectCellCandidate(
        id=uuid4(),
        workspace_id=workspace_id,
        generation_run_id=run_id,
        fencing_epoch=7,
        source_revision="b" * 64,
        migration_digest="d" * 64,
        database_backup_ref="database-backup/sha256/" + "3" * 64,
        build_ref="build/sha256/" + "4" * 64,
        verification_ref="verification/sha256/" + "5" * 64,
        status="accepted",
        cancelled=False,
    )
    run = GenerationRun(
        id=run_id,
        project_id=project_id,
        user_id=owner_id,
        status="completed",
        agent_state={
            "snapshot_id": str(snapshot_id),
            "commit_sha": "a" * 40,
            "max_finalization": {
                "outcome": "complete",
                "checkpoint": {
                    "phase": "complete",
                    "candidate_id": str(candidate.id),
                    "proof_key": proof.proof_key,
                },
            },
        },
    )
    results = []
    for dimension in (
        ProofDimension.BOOTSTRAP,
        ProofDimension.FULL_BUILD,
        ProofDimension.RUNTIME,
        ProofDimension.RELEASE,
    ):
        results.append(
            ProjectCellProofResult(
                id=uuid4(),
                proof_id=proof.id,
                workspace_id=workspace_id,
                dimension=dimension.value,
                dimension_key=identity.dimension_key(
                    dimension,
                    artifact_digest=(
                        "4" * 64
                        if dimension in {ProofDimension.RUNTIME, ProofDimension.RELEASE}
                        else None
                    ),
                ),
                outcome="green",
                operation_id=uuid4(),
                redacted_detail="passed",
                detail_digest=hashlib.sha256(b"passed").hexdigest(),
                artifact_ref=(
                    candidate.build_ref
                    if dimension is ProofDimension.FULL_BUILD
                    else candidate.verification_ref
                    if dimension is ProofDimension.RELEASE
                    else None
                ),
            )
        )
    return dict(
        project=project,
        workspace=workspace,
        snapshot=snapshot,
        run=run,
        candidate=candidate,
        proof=proof,
        results=results,
    )


def test_released_generation_keeps_its_exact_proven_candidate_publishable():
    from omnia_api.services.cell_publication import validate_publication_evidence

    data = evidence()
    value = validate_publication_evidence(**data)
    assert value["source_revision"] == "b" * 64
    assert value["snapshot_id"] == str(data["snapshot"].id)
    # Controller compares its current fence, not the completed generation lease.
    assert value["fencing_epoch"] == 8
    assert value["build_ref"] == "build/sha256/" + "4" * 64


def test_public_deploy_status_keeps_exact_snapshot_binding():
    from omnia_api.routers.runtime import _to_deploy_status

    snapshot_id = uuid4()
    value = _to_deploy_status({
        "phase": "done", "snapshot_id": str(snapshot_id), "commit_sha": "a" * 40,
    })
    assert value.snapshot_id == snapshot_id
    assert value.commit_sha == "a" * 40


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("workspace", "owner_id", uuid4()),
        ("workspace", "generation_run_id", uuid4()),
        ("workspace", "state", "failed"),
        ("snapshot", "project_id", uuid4()),
        ("snapshot", "commit_sha", "9" * 40),
        ("run", "status", "running"),
        ("run", "project_id", uuid4()),
        ("run", "user_id", uuid4()),
        ("candidate", "status", "rejected"),
        ("candidate", "cancelled", True),
        ("candidate", "workspace_id", uuid4()),
        ("candidate", "source_revision", "9" * 64),
        ("candidate", "verification_ref", "verification/sha256/" + "9" * 64),
        ("proof", "proof_key", "9" * 64),
        ("proof", "generation_run_id", uuid4()),
    ],
)
def test_unrelated_or_unfinished_evidence_never_authorizes_publication(target, field, value):
    from omnia_api.services.cell_publication import validate_publication_evidence

    data = evidence()
    setattr(data[target], field, value)
    with pytest.raises(ApiError) as error:
        validate_publication_evidence(**data)
    assert error.value.status_code == 409


@pytest.mark.parametrize("change", ["red", "missing", "wrong_build", "wrong_workspace"])
def test_release_proof_must_cover_this_build_and_workspace(change):
    from omnia_api.services.cell_publication import validate_publication_evidence

    data = evidence()
    result = data["results"][-1]
    if change == "missing":
        data["results"].pop()
    elif change == "red":
        result.outcome = "red"
    elif change == "wrong_build":
        result.dimension_key = "0" * 64
    else:
        result.workspace_id = uuid4()
    with pytest.raises(ApiError):
        validate_publication_evidence(**data)
