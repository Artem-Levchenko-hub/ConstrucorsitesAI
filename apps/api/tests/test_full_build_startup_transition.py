"""A full build may attest a new process only with exact controller evidence."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services import project_cell_executor as executor
from yleum_api.services.project_cell_proofs import ProofIdentity


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def environment(content, incarnation):
    return digest(
        {
            "protocol": "project-machine-environment-incarnation/v1",
            "content_digest": content,
            "startup_incarnation": incarnation,
        }
    )


def evidence():
    project, owner, operation = uuid4(), uuid4(), uuid4()
    content = "a" * 64
    before_env, after_env = environment(content, "b" * 64), environment(content, "c" * 64)
    before = ProofIdentity(
        workspace_id=uuid4(),
        generation_run_id=uuid4(),
        fencing_epoch=3,
        workspace_revision="1" * 64,
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest=before_env,
        toolchain_digest=before_env,
        resource_profile_version="v1",
        build_config_digest="5" * 64,
    )
    after = replace(before, base_image_digest=after_env, toolchain_digest=after_env)
    binding = dict(
        workspace_id=str(before.workspace_id),
        project_id=str(project),
        owner_id=str(owner),
        generation_run_id=str(before.generation_run_id),
        fencing_epoch=3,
        operation_id=str(operation),
        source_revision=before.workspace_revision,
        manifest_digest=before.cell_manifest_digest,
    )
    migration = dict(
        contract="project-migrations-v1",
        mode="apply",
        migration_count=0,
        source_digest="6" * 64,
        database_identity="7" * 64,
        catalog_digest="8" * 64,
        **{
            key: value
            for key, value in binding.items()
            if key in {"workspace_id", "generation_run_id", "fencing_epoch", "source_revision"}
        },
    )
    compilation = dict(
        collector_version="next-fixed-data-v1",
        assets=[],
        **{key: value for key, value in binding.items() if key != "owner_id"},
    )
    compilation["full_build_startup_transition"] = dict(
        protocol="full-build-startup-transition/v1",
        version=1,
        role="full_build",
        **binding,
        content_digest=content,
        before_incarnation="b" * 64,
        after_incarnation="c" * 64,
        compilation_sha256=digest(compilation),
        migration_sha256=digest(migration),
    )
    return before, after, compilation, migration, project, owner, operation


def validate(values):
    before, after, compilation, migration, project, owner, operation = values
    return executor.full_build_startup_transition(
        before,
        after,
        compilation,
        migration,
        project_id=project,
        owner_id=owner,
        operation_id=operation,
        ok=True,
        timed_out=False,
        exit_code=0,
    )


@pytest.mark.parametrize("migration_count", [0, 1])
def test_exact_same_content_full_build_restart_is_authorized(migration_count):
    values = evidence()
    values[3]["migration_count"] = migration_count
    values[2]["full_build_startup_transition"]["migration_sha256"] = digest(values[3])
    transition = validate(values)
    assert transition is not None
    assert transition.before == values[0] and transition.after == values[1]
    assert transition.operation_id == values[-1]


@pytest.mark.parametrize(
    "field",
    [
        "workspace_id",
        "generation_run_id",
        "fencing_epoch",
        "workspace_revision",
        "dependency_digest",
        "schema_data_digest",
        "cell_manifest_digest",
        "resource_profile_version",
        "build_config_digest",
        "base_image_digest",
        "toolchain_digest",
    ],
)
def test_any_unproven_identity_change_is_rejected(field):
    values = list(evidence())
    old = getattr(values[1], field)
    other = uuid4() if field.endswith("_id") else 4 if field == "fencing_epoch" else "9" * 64
    assert other != old
    values[1] = replace(values[1], **{field: other})
    assert validate(values) is None


@pytest.mark.parametrize(
    "field",
    [
        "protocol",
        "version",
        "role",
        "workspace_id",
        "project_id",
        "owner_id",
        "generation_run_id",
        "fencing_epoch",
        "operation_id",
        "source_revision",
        "manifest_digest",
        "content_digest",
        "before_incarnation",
        "after_incarnation",
        "compilation_sha256",
        "migration_sha256",
    ],
)
def test_missing_malformed_or_foreign_transition_fails_closed(field):
    values = evidence()
    transition = values[2]["full_build_startup_transition"]
    transition[field] = True if field in {"version", "fencing_epoch"} else "foreign"
    assert validate(values) is None


def test_missing_old_opaque_receipt_and_post_receipt_mutation_are_rejected():
    values = evidence()
    values[2].pop("full_build_startup_transition")
    assert validate(values) is None
    values = evidence()
    values[2]["assets"].append({"path": "forged"})
    assert validate(values) is None
    values = evidence()
    values[3]["catalog_digest"] = "f" * 64
    assert validate(values) is None


def test_ordinary_restoration_transition_contract_remains_distinct():
    before, after, compilation, *_ = evidence()
    assert not executor.restored_environment_transition_matches(before, after, compilation)
    restored = {
        "environment_transition": dict(
            protocol="restored-environment-transition/v1",
            version=1,
            content_digest="a" * 64,
            before_incarnation="b" * 64,
            after_incarnation="c" * 64,
        )
    }
    assert executor.restored_environment_transition_matches(before, after, restored)
    values = list(evidence())
    values[2] = deepcopy(restored)
    assert validate(values) is None


@pytest.mark.parametrize(
    "ok,timed_out,exit_code",
    [(False, False, 0), (True, True, 0), (True, False, 1), (True, False, None)],
)
def test_non_successful_command_cannot_authorize_transition(ok, timed_out, exit_code):
    before, after, compilation, migration, project, owner, operation = evidence()
    assert (
        executor.full_build_startup_transition(
            before,
            after,
            compilation,
            migration,
            project_id=project,
            owner_id=owner,
            operation_id=operation,
            ok=ok,
            timed_out=timed_out,
            exit_code=exit_code,
        )
        is None
    )


def coordinator_fixture(*, with_transition=True, reject=None, change_artifact=False):
    from yleum_api.services.max_finalization import MaxFinalizationCoordinator
    from yleum_api.services.project_cell_proofs import ProofDimension, ProofOutcome
    from yleum_api.services.promotion_permit import (
        canonical_files_digest,
        workspace_revision_digest,
    )

    values = list(evidence())
    files = {"src/app/page.tsx": "export default function Page(){return <main>ok</main>}"}
    source = workspace_revision_digest(files)
    values[0] = replace(values[0], workspace_revision=source)
    values[1] = replace(values[1], workspace_revision=source)
    values[2]["source_revision"] = source
    values[3]["source_revision"] = source
    raw = values[2]["full_build_startup_transition"]
    raw["source_revision"] = source
    raw["compilation_sha256"] = digest(
        {key: value for key, value in values[2].items() if key != "full_build_startup_transition"}
    )
    raw["migration_sha256"] = digest(values[3])
    before, after, *_ = values
    transition = validate(values) if with_transition else None
    calls, stored = [], {}
    coordinator = object.__new__(MaxFinalizationCoordinator)

    async def proof(identity):
        return MaxFinalizationCoordinator._transient_proof(identity)

    async def execute(**kwargs):
        role = kwargs["role"]
        calls.append((role, kwargs["identity"]))
        if role is executor.ProjectCellCommandRole.FULL_BUILD:
            return executor.ProjectCellCommandObservation(
                operation_id=values[-1],
                role=role,
                ok=True,
                timed_out=False,
                redacted_detail="compiled, types, tests, migration all passed",
                before=before,
                after=after,
                invalidated_dimensions=executor.invalidated_dimensions(before, after),
                full_build_startup_transition=transition,
            )
        return executor.ProjectCellCommandObservation(
            operation_id=uuid4(),
            role=role,
            ok=role != reject,
            timed_out=False,
            redacted_detail="revalidated",
            before=after,
            after=after,
            invalidated_dimensions=frozenset(),
        )

    async def record(**kwargs):
        result = SimpleNamespace(
            proof_id=kwargs["proof"].id,
            outcome=kwargs["outcome"].value,
            dimension=kwargs["dimension"].value,
            artifact_ref=kwargs["artifact_ref"],
            redacted_detail=kwargs["detail"],
            operation_id=kwargs["operation_id"],
        )
        stored[(kwargs["proof"].proof_key, kwargs["dimension"])] = result
        return result

    async def find(proof, dimension):
        return stored.get((proof.proof_key, dimension))

    async def verified(identity):
        assert identity == after
        return {**files, **({"extra.txt": "changed"} if change_artifact and len(calls) > 1 else {})}

    coordinator._execute_role, coordinator._proof = execute, proof
    coordinator._record, coordinator._find = record, find
    coordinator._verified_workspace_files = verified
    return (
        coordinator,
        before,
        after,
        calls,
        stored,
        canonical_files_digest(files),
        ProofDimension,
        ProofOutcome,
    )


@pytest.mark.asyncio
async def test_coordinator_adopts_after_and_refreshes_only_cheap_dimensions_once():
    c, before, after, calls, stored, artifact, dimension, _outcome = coordinator_fixture()
    old_proof = await c._proof(before)
    build, identity, proof = await c._run_full_build_result(old_proof, before)
    assert identity == after and proof.proof_key == after.proof_key
    assert build.outcome == "green" and build.artifact_ref == f"build/sha256/{artifact}"
    assert (before.proof_key, dimension.FULL_BUILD) not in stored
    bootstrap, rejected = await c._revalidate_full_build_startup(proof, after, build)
    assert rejected is None and bootstrap.proof_id == proof.id
    assert [role for role, _ in calls] == [
        executor.ProjectCellCommandRole.FULL_BUILD,
        executor.ProjectCellCommandRole.BOOTSTRAP,
        executor.ProjectCellCommandRole.FAST_CHECK,
    ]
    assert all(identity == after for _role, identity in calls[1:])
    # Cold pass finds actual AFTER receipts, not old proofs or another full build.
    assert await c._revalidate_full_build_startup(proof, after, build) == (bootstrap, None)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_actual_old_opaque_environment_only_success_stays_red():
    c, before, _after, calls, _stored, _artifact, _dim, _outcome = coordinator_fixture(
        with_transition=False
    )
    build, identity, proof = await c._run_full_build_result(await c._proof(before), before)
    assert build.outcome == "red" and build.artifact_ref is None
    assert identity == before and proof.proof_key == before.proof_key
    assert "command changed the frozen proof identity" in build.redacted_detail
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role", [executor.ProjectCellCommandRole.BOOTSTRAP, executor.ProjectCellCommandRole.FAST_CHECK]
)
async def test_failed_after_revalidation_stops_without_rebuilding(role):
    c, before, after, calls, *_ = coordinator_fixture(reject=role)
    build, _identity, proof = await c._run_full_build_result(await c._proof(before), before)
    _bootstrap, rejected = await c._revalidate_full_build_startup(proof, after, build)
    assert rejected.outcome == "red"
    assert sum(item is executor.ProjectCellCommandRole.FULL_BUILD for item, _ in calls) == 1


@pytest.mark.asyncio
async def test_after_revalidation_cannot_change_built_artifact():
    from yleum_api.services.max_finalization import MaxFinalizationConflict

    c, before, after, _calls, *_ = coordinator_fixture(change_artifact=True)
    build, _identity, proof = await c._run_full_build_result(await c._proof(before), before)
    with pytest.raises(MaxFinalizationConflict, match="artifact changed"):
        await c._revalidate_full_build_startup(proof, after, build)


@pytest.mark.asyncio
async def test_after_bundle_issues_only_after_bound_release_permit():
    from yleum_api.services.max_finalization import ProofBundle
    from yleum_api.services.project_cell_proofs import ProofDimension, ProofOutcome
    from yleum_api.services.promotion_permit import (
        PromotionPermitError,
        issue_promotion_permit,
        release_receipt_digest,
        release_receipt_ref,
    )

    c, before, after, _calls, _stored, artifact, *_ = coordinator_fixture()
    build, _identity, proof = await c._run_full_build_result(await c._proof(before), before)
    bootstrap, rejected = await c._revalidate_full_build_startup(proof, after, build)
    assert rejected is None
    runtime = c._transient_result(
        identity=after,
        proof=proof,
        dimension=ProofDimension.RUNTIME,
        outcome=ProofOutcome.GREEN,
        operation_id=uuid4(),
        detail="fresh AFTER runtime",
        artifact_digest=artifact,
        artifact_ref="verification/sha256/" + "d" * 64,
    )
    detail = "fresh AFTER release"
    release = c._transient_result(
        identity=after,
        proof=proof,
        dimension=ProofDimension.RELEASE,
        outcome=ProofOutcome.GREEN,
        operation_id=uuid4(),
        detail=detail,
        artifact_digest=artifact,
        artifact_ref=release_receipt_ref(
            artifact_digest=artifact,
            receipt_digest=release_receipt_digest(
                proof_key=after.proof_key, artifact_digest=artifact, detail=detail
            ),
        ),
    )
    bundle = ProofBundle(
        identity=proof, bootstrap=bootstrap, full_build=build, runtime=runtime, release=release
    )
    assert issue_promotion_permit(bundle).proof_key == after.proof_key
    runtime.proof_id = (await c._proof(before)).id
    with pytest.raises(PromotionPermitError):
        issue_promotion_permit(bundle)


def test_observed_opaque_environment_hash_pair_does_not_license_a_transition():
    values = list(evidence())
    values[0] = replace(
        values[0],
        base_image_digest="96900548e6ff2ab3bc06955ba06f61c1a1c46e32eed3305e9b822c1b3a7f7817",
        toolchain_digest="96900548e6ff2ab3bc06955ba06f61c1a1c46e32eed3305e9b822c1b3a7f7817",
    )
    values[1] = replace(
        values[1],
        base_image_digest="b0876b0c9a7d196f23113276b37013a3b56abe95189a4af270a711a5244be053",
        toolchain_digest="b0876b0c9a7d196f23113276b37013a3b56abe95189a4af270a711a5244be053",
    )
    values[2].pop("full_build_startup_transition")
    assert validate(values) is None


def behavior_transition_fixture():
    from unittest.mock import AsyncMock

    from tests.test_behavior_compilation_resolver import setup
    from yleum_api.services.behavior_compilation_resolver import compilation_operation_id
    from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity

    request, handle, response, status, current = setup()
    owner = uuid4()
    before = replace(
        current,
        base_image_digest=environment("a" * 64, "b" * 64),
        toolchain_digest=environment("a" * 64, "b" * 64),
    )
    after = replace(
        current,
        base_image_digest=environment("a" * 64, "c" * 64),
        toolchain_digest=environment("a" * 64, "c" * 64),
    )
    request = replace(request, binding=replace(request.binding, proof_key=after.proof_key))
    operation = compilation_operation_id(replace(request.binding, proof_key=before.proof_key))

    def wire(identity):
        return ProjectCellWorkspaceIdentity(
            identity.workspace_revision,
            identity.dependency_digest,
            identity.schema_data_digest,
            identity.cell_manifest_digest,
            identity.base_image_digest,
            identity.build_config_digest,
        )

    compilation = deepcopy(response.compiled_asset_receipt)
    compilation["operation_id"] = str(operation)
    migration = dict(
        contract="project-migrations-v1",
        mode="apply",
        migration_count=1,
        source_digest="6" * 64,
        database_identity="7" * 64,
        catalog_digest="8" * 64,
        workspace_id=str(before.workspace_id),
        generation_run_id=str(before.generation_run_id),
        fencing_epoch=before.fencing_epoch,
        source_revision=before.workspace_revision,
    )
    compilation["full_build_startup_transition"] = dict(
        protocol="full-build-startup-transition/v1",
        version=1,
        role="full_build",
        content_digest="a" * 64,
        before_incarnation="b" * 64,
        after_incarnation="c" * 64,
        workspace_id=str(before.workspace_id),
        project_id=str(request.binding.project_id),
        owner_id=str(owner),
        generation_run_id=str(before.generation_run_id),
        fencing_epoch=before.fencing_epoch,
        operation_id=str(operation),
        source_revision=before.workspace_revision,
        manifest_digest=before.cell_manifest_digest,
        compilation_sha256=digest(compilation),
        migration_sha256=digest(migration),
    )
    response = replace(
        response,
        operation_id=operation,
        before_identity=wire(before),
        after_identity=wire(after),
        compiled_asset_receipt=compilation,
        project_migration_receipt=migration,
        environment_mutated=True,
    )
    status.operation_id, status.terminal_response = operation, response
    handle.operation_status = AsyncMock(return_value=status)
    handle.current_identity = AsyncMock(return_value=after)
    handle.resolve_full_build_operation = AsyncMock(return_value=(operation, owner))
    return request, handle, response, status, before, after, owner


@pytest.mark.asyncio
async def test_behavior_uses_actual_before_operation_with_exact_after_compilation():
    from yleum_api.services.behavior_compilation_resolver import (
        compilation_operation_id,
        resolve_retained_compilation,
    )

    request, handle, response, _status, _before, _after, _owner = behavior_transition_fixture()
    assert response.operation_id != compilation_operation_id(request.binding)
    witness = await resolve_retained_compilation(handle, request)
    assert witness.binding == request.binding and witness.assets
    handle.operation_status.assert_awaited_once_with(response.operation_id)
    handle.resolve_full_build_operation.assert_awaited_once_with(
        request.binding.proof_key,
        request.binding.build_ref,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["no_lookup", "missing_result", "foreign_owner", "old_receipt", "wrong_after", "running"],
)
async def test_behavior_transition_requires_owning_result_and_terminal_response(mutation):
    from yleum_api.services.behavior_compilation_resolver import resolve_retained_compilation
    from yleum_api.services.max_behavior_proof import BehaviorProofError

    request, handle, response, status, _before, _after, _owner = behavior_transition_fixture()
    if mutation == "no_lookup":
        handle.resolve_full_build_operation = None
    elif mutation == "missing_result":
        handle.resolve_full_build_operation.return_value = None
    elif mutation == "foreign_owner":
        handle.resolve_full_build_operation.return_value = (response.operation_id, uuid4())
    elif mutation == "old_receipt":
        response.compiled_asset_receipt.pop("full_build_startup_transition")
    elif mutation == "wrong_after":
        status.terminal_response = replace(
            response, after_identity=response.before_identity, environment_mutated=False
        )
    else:
        status.state = "running"
    with pytest.raises(BehaviorProofError):
        await resolve_retained_compilation(handle, request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "old_receipt",
        "foreign_owner",
        "foreign_lease",
        "stale_fence",
        "fast_check",
        "red",
        "running",
        "timeout",
    ],
)
async def test_compiler_witness_reloads_exact_original_activity_transition(monkeypatch, mutation):
    from dataclasses import asdict
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.models.project_cell import (
        ProjectCellActivityLease,
        ProjectCellProof,
        ProjectCellProofResult,
        ProjectCellWorkspace,
    )
    from yleum_api.services.generation import recovery_requirements as requirements
    from yleum_api.services.generation.recovery_actions import recovery_binding
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.project_cell_proofs import ProofDimension

    request, _handle, response, status, before, after, owner = behavior_transition_fixture()
    run = SimpleNamespace(
        id=after.generation_run_id,
        project_id=request.binding.project_id,
        user_id=owner,
        prompt_hash="1" * 64,
    )
    proof = SimpleNamespace(id=uuid4(), proof_key=after.proof_key, **asdict(after))
    result = SimpleNamespace(
        id=uuid4(),
        proof_id=proof.id,
        workspace_id=after.workspace_id,
        dimension="full_build",
        outcome="green",
        operation_id=response.operation_id,
        dimension_key=after.dimension_key(ProofDimension.FULL_BUILD),
        redacted_detail="exact compiled result",
        detail_digest="",
    )
    result.detail_digest = hashlib.sha256(result.redacted_detail.encode()).hexdigest()
    lease = SimpleNamespace(
        workspace_id=after.workspace_id,
        generation_run_id=run.id,
        kind="command",
        fencing_epoch=after.fencing_epoch,
        proof_key=before.proof_key,
        phase="final_build",
        state="completed",
        finished_at=datetime.now(UTC),
    )
    workspace = SimpleNamespace(
        project_id=run.project_id,
        owner_id=owner,
        generation_run_id=run.id,
        fencing_epoch=after.fencing_epoch,
        state="ready",
        deleted_at=None,
    )
    records = {
        GenerationRun: run,
        Project: SimpleNamespace(id=run.project_id, owner_id=owner),
        ProjectCellWorkspace: workspace,
        ProjectCellProofResult: result,
        ProjectCellProof: proof,
        ProjectCellActivityLease: lease,
    }

    async def get(kind, _id, **_kwargs):
        return records[kind]

    if mutation == "old_receipt":
        response.compiled_asset_receipt.pop("full_build_startup_transition")
    elif mutation == "foreign_owner":
        response.compiled_asset_receipt["full_build_startup_transition"]["owner_id"] = str(uuid4())
    elif mutation == "foreign_lease":
        lease.proof_key = "f" * 64
    elif mutation == "stale_fence":
        lease.fencing_epoch += 1
    elif mutation == "fast_check":
        result.dimension = "fast_check"
        result.dimension_key = after.dimension_key(ProofDimension.FAST_CHECK)
    elif mutation == "red":
        result.outcome = "red"
        lease.state = "failed"
    elif mutation == "running":
        status.state = "running"
    elif mutation == "timeout":
        status.terminal_response = replace(response, timed_out=True)
    monkeypatch.setattr(
        requirements, "project_cell_agent_operation_status", AsyncMock(return_value=status)
    )
    monkeypatch.setattr(
        requirements, "project_cell_agent_identity", AsyncMock(return_value=response.after_identity)
    )
    if mutation is None:
        witness = await requirements.load_compiler_witness(
            SimpleNamespace(get=get),
            run_id=run.id,
            receipt_id=result.id,
            binding=recovery_binding(after),
        )
        assert witness.passed and witness.binding.environment_sha256 == after.base_image_digest
        assert lease.proof_key == before.proof_key  # Original activity is never rewritten.
    else:
        with pytest.raises(RecoveryContractError):
            await requirements.load_compiler_witness(
                SimpleNamespace(get=get),
                run_id=run.id,
                receipt_id=result.id,
                binding=recovery_binding(after),
            )
    if mutation in {"stale_fence", "fast_check", "red"}:
        requirements.project_cell_agent_operation_status.assert_not_awaited()
    else:
        requirements.project_cell_agent_operation_status.assert_awaited_once_with(
            after.workspace_id, response.operation_id,
        )
