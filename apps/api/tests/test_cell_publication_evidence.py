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


def restored_evidence():
    from omnia_api.models.project_version import ProjectVersion
    from omnia_api.models.restoration import Restoration

    data = evidence()
    project, workspace, snapshot = (data[key] for key in ("project", "workspace", "snapshot"))
    operation_id, candidate_id, base_id, source_id, version_id = [uuid4() for _ in range(5)]
    snapshot.parent_id = base_id
    request = {
        "operation_id": str(operation_id),
        "project_id": str(project.id),
        "owner_id": str(project.owner_id),
        "workspace_id": str(workspace.id),
        "candidate_id": str(candidate_id),
        "planned_commit_sha": snapshot.commit_sha,
        "target_commit_sha": "c" * 40,
        "expected_source_head": "d" * 40,
        "fencing_epoch": 7,
    }
    report = {
        "revision": 1,
        "mode": "exact",
        "changes": [],
        "retained_data": [],
        "unavailable_features": [],
        "warnings": [],
        "blockers": [],
        "next_actions": [],
    }
    runtime = {
        key: request[key] for key in ("operation_id", "project_id", "owner_id", "workspace_id")
    }
    runtime.update(
        state="completed",
        phase="complete",
        revision=3,
        candidate_id=str(candidate_id),
        report=report,
        can_apply=False,
        can_cancel=False,
        observed={
            "candidate_id": str(candidate_id),
            "source_commit_sha": snapshot.commit_sha,
            "fencing_epoch": 7,
            "applied": True,
            "source_revision": "b" * 64,
        },
    )
    operation = Restoration(
        id=operation_id,
        project_id=project.id,
        owner_id=project.owner_id,
        workspace_id=workspace.id,
        state="completed",
        applied_snapshot_id=snapshot.id,
        applied_version_id=version_id,
        source_snapshot_id=source_id,
        base_draft_snapshot_id=base_id,
        candidate_id=candidate_id,
        planned_commit_sha=snapshot.commit_sha,
        target_commit_sha="c" * 40,
        base_commit_sha="d" * 40,
        fencing_epoch=7,
        apply_digest="claim",
        request_payload=request,
        runtime_result=runtime,
        report=report,
    )
    version = ProjectVersion(
        id=version_id,
        project_id=project.id,
        snapshot_id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        restored_from_snapshot_id=source_id,
        base_snapshot_id=base_id,
        status="ready",
    )
    return dict(
        project=project,
        workspace=workspace,
        snapshot=snapshot,
        restoration=operation,
        version=version,
    )


def test_completed_restoration_publication_has_distinct_provenance():
    from omnia_api.services.cell_publication import validate_restoration_publication_evidence

    data = restored_evidence()
    result = validate_restoration_publication_evidence(**data)
    assert result["restoration_operation_id"] == str(data["restoration"].id)
    assert result["commit_sha"] == data["snapshot"].commit_sha
    assert result["source_revision"] == "b" * 64
    assert result["accepted_fencing_epoch"] == 7 and result["fencing_epoch"] == 8
    assert not set(result) & {"proof_key", "build_ref", "schema_data_digest", "verification_ref"}


@pytest.mark.parametrize(
    "runtime_state,persisted_state",
    [
        (None, None),
        (None, "unknown"),
        ("unknown", None),
        ("unknown", "unknown"),
        ("empty", "empty"),
        ("present", "present"),
    ],
)
def test_publication_normalizes_legacy_report_defaults(runtime_state, persisted_state):
    from copy import deepcopy

    from omnia_api.services.cell_publication import validate_restoration_publication_evidence

    data = restored_evidence()
    operation = data["restoration"]
    operation.report = deepcopy(operation.report)
    if runtime_state is not None:
        operation.runtime_result["report"]["database_state"] = runtime_state
    if persisted_state is not None:
        operation.report["database_state"] = persisted_state
    result = validate_restoration_publication_evidence(**data)
    assert result["restoration_operation_id"] == str(operation.id)


@pytest.mark.parametrize(
    "runtime_state,persisted_state",
    [
        ("empty", "present"),
        ("present", "empty"),
        ("unknown", "present"),
        ("present", None),
        (None, "empty"),
    ],
)
def test_publication_rejects_different_explicit_database_observations(
    runtime_state, persisted_state
):
    from copy import deepcopy

    from omnia_api.services.cell_publication import validate_restoration_publication_evidence

    data = restored_evidence()
    operation = data["restoration"]
    operation.report = deepcopy(operation.report)
    if runtime_state is not None:
        operation.runtime_result["report"]["database_state"] = runtime_state
    if persisted_state is not None:
        operation.report["database_state"] = persisted_state
    with pytest.raises(ApiError) as error:
        validate_restoration_publication_evidence(**data)
    assert error.value.details == {"reason": "restoration_activation_unproven"}


@pytest.mark.parametrize(
    "change",
    [
        {"database_state": "unverified"},
        {"database_state": None},
        {"revision": "1"},
        {"blockers": None},
        {"unrecognized": True},
        {"warnings": ["different"]},
    ],
)
def test_publication_rejects_invalid_or_changed_persisted_report(change):
    data = restored_evidence()
    data["restoration"].report = {**data["restoration"].report, **change}
    from omnia_api.services.cell_publication import validate_restoration_publication_evidence

    with pytest.raises(ApiError) as error:
        validate_restoration_publication_evidence(**data)
    assert error.value.details == {"reason": "restoration_activation_unproven"}


@pytest.mark.parametrize(
    "tamper",
    [
        "state",
        "head",
        "owner",
        "workspace",
        "candidate",
        "sha",
        "epoch",
        "receipt",
        "revision",
        "report",
        "version",
        "parent",
    ],
)
def test_restoration_publication_rejects_unbound_evidence(tamper):
    from copy import deepcopy

    from omnia_api.services.cell_publication import validate_restoration_publication_evidence

    data = restored_evidence()
    operation = data["restoration"]
    if tamper == "state":
        operation.state = "reconciling"
    elif tamper == "head":
        data["project"].current_snapshot_id = uuid4()
    elif tamper == "owner":
        operation.owner_id = uuid4()
    elif tamper == "workspace":
        operation.workspace_id = uuid4()
    elif tamper == "candidate":
        operation.candidate_id = uuid4()
    elif tamper == "sha":
        operation.planned_commit_sha = "e" * 40
    elif tamper == "epoch":
        data["workspace"].fencing_epoch = 6
    elif tamper == "receipt":
        operation.runtime_result = None
    elif tamper == "revision":
        operation.runtime_result["observed"].pop("source_revision")
    elif tamper == "report":
        operation.report = deepcopy(operation.report)
        operation.report["blockers"] = ["unsafe"]
    elif tamper == "version":
        data["version"].restored_from_snapshot_id = uuid4()
    elif tamper == "parent":
        data["snapshot"].parent_id = uuid4()
    with pytest.raises(ApiError):
        validate_restoration_publication_evidence(**data)


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
    value = _to_deploy_status(
        {
            "phase": "done",
            "snapshot_id": str(snapshot_id),
            "commit_sha": "a" * 40,
        }
    )
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
