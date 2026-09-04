from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellProof, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.project_cell_proofs import (
    ProjectCellProofConflict,
    ProofDimension,
    ProofIdentity,
    ProofOutcome,
    create_proof_identity,
    find_proof_result,
    record_proof_result,
)

pytestmark = pytest.mark.asyncio


def _digest(seed: str) -> str:
    return seed * 64


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(email=f"proof-{label}-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _new_project(session: AsyncSession, owner: User) -> Project:
    project = Project(
        owner_id=owner.id,
        name="Proof test",
        slug=f"proof-{uuid.uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    return project


async def _new_run(session: AsyncSession, owner: User, project: Project) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"proof-run:{uuid.uuid4().hex}",
        prompt_hash="a" * 64,
        status="running",
        agent_state={},
    )
    session.add(run)
    await session.flush()
    return run


async def _new_workspace(
    session: AsyncSession,
    owner: User,
    project: Project,
    run: GenerationRun,
) -> ProjectCellWorkspace:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    session.add(workspace)
    await session.flush()
    return workspace


def _identity(workspace: ProjectCellWorkspace, run: GenerationRun) -> ProofIdentity:
    return ProofIdentity(
        workspace_id=workspace.id,
        generation_run_id=run.id,
        fencing_epoch=7,
        workspace_revision="1" * 64,
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64,
        toolchain_digest="6" * 64,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest="7" * 64,
    )


async def test_dimension_keys_follow_the_invalidation_matrix() -> None:
    identity = ProofIdentity(
        workspace_id=UUID(int=1),
        generation_run_id=UUID(int=2),
        fencing_epoch=7,
        workspace_revision="1" * 64,
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64,
        toolchain_digest="6" * 64,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest="7" * 64,
    )
    source_edit = replace(identity, workspace_revision="8" * 64)
    build_config_edit = replace(identity, build_config_digest="9" * 64)
    schema_edit = replace(identity, schema_data_digest="a" * 64)

    assert identity.dimension_key(ProofDimension.BOOTSTRAP) == source_edit.dimension_key(
        ProofDimension.BOOTSTRAP
    )
    assert identity.dimension_key(ProofDimension.BOOTSTRAP) == build_config_edit.dimension_key(
        ProofDimension.BOOTSTRAP
    )
    assert identity.dimension_key(ProofDimension.FAST_CHECK) != source_edit.dimension_key(
        ProofDimension.FAST_CHECK
    )
    assert identity.dimension_key(ProofDimension.FAST_CHECK) != schema_edit.dimension_key(
        ProofDimension.FAST_CHECK
    )
    assert identity.dimension_key(ProofDimension.FAST_CHECK) != build_config_edit.dimension_key(
        ProofDimension.FAST_CHECK
    )
    assert identity.dimension_key(ProofDimension.FULL_BUILD) == schema_edit.dimension_key(
        ProofDimension.FULL_BUILD
    )
    assert identity.dimension_key(ProofDimension.FULL_BUILD) != build_config_edit.dimension_key(
        ProofDimension.FULL_BUILD
    )


async def test_runtime_and_release_keys_require_referenced_artifact_digest() -> None:
    identity = ProofIdentity(
        workspace_id=UUID(int=1),
        generation_run_id=UUID(int=2),
        fencing_epoch=7,
        workspace_revision="1" * 64,
        dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64,
        base_image_digest="5" * 64,
        toolchain_digest="6" * 64,
        resource_profile_version="docker-owner-cell-resources-v2",
        build_config_digest="7" * 64,
    )

    with pytest.raises(ValueError, match="artifact_digest"):
        identity.dimension_key(ProofDimension.RUNTIME)
    assert identity.dimension_key(
        ProofDimension.RUNTIME,
        artifact_digest="a" * 64,
    ) != identity.dimension_key(
        ProofDimension.RUNTIME,
        artifact_digest="b" * 64,
    )


async def test_one_terminal_result_exists_per_dimension_key(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "owner")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    proof = await create_proof_identity(db_session, identity=_identity(workspace, run))
    await record_proof_result(
        db_session,
        proof=proof,
        dimension=ProofDimension.FULL_BUILD,
        outcome=ProofOutcome.GREEN,
        operation_id=UUID(int=7),
        artifact_ref="build/sha256/" + ("b" * 64),
        detail="green",
    )
    with pytest.raises(ProjectCellProofConflict, match="already terminal"):
        await record_proof_result(
            db_session,
            proof=proof,
            dimension=ProofDimension.FULL_BUILD,
            outcome=ProofOutcome.RED,
            operation_id=UUID(int=8),
            artifact_ref=None,
            detail="must not overwrite green",
        )


async def test_find_proof_result_returns_existing_dimension(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "reader")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    proof = await create_proof_identity(db_session, identity=_identity(workspace, run))
    result = await record_proof_result(
        db_session,
        proof=proof,
        dimension=ProofDimension.BOOTSTRAP,
        outcome=ProofOutcome.GREEN,
        operation_id=UUID(int=9),
        artifact_ref=None,
        detail="cached",
    )
    assert len(proof.proof_key) == 64
    assert await find_proof_result(
        db_session,
        proof=proof,
        dimension=ProofDimension.BOOTSTRAP,
    ) == result


async def test_proof_details_are_redacted_and_bounded(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "redacted")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    proof = await create_proof_identity(db_session, identity=_identity(workspace, run))

    result = await record_proof_result(
        db_session,
        proof=proof,
        dimension=ProofDimension.FAST_CHECK,
        outcome=ProofOutcome.RED,
        operation_id=UUID(int=10),
        artifact_ref=None,
        detail="AUTH_SECRET=do-not-persist\n" + ("x" * 10_000),
    )

    assert "do-not-persist" not in result.redacted_detail
    assert len(result.redacted_detail.encode("utf-8")) <= 4096


async def test_concurrent_terminal_result_returns_clean_conflict(test_engine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "concurrent")
        project = await _new_project(setup, owner)
        run = await _new_run(setup, owner, project)
        workspace = await _new_workspace(setup, owner, project, run)
        proof = await create_proof_identity(setup, identity=_identity(workspace, run))
        proof_id = proof.id
        await setup.commit()

    async def record(operation_id: UUID) -> str:
        async with factory() as session:
            stored = await session.get(ProjectCellProof, proof_id)
            assert stored is not None
            try:
                await record_proof_result(
                    session,
                    proof=stored,
                    dimension=ProofDimension.FULL_BUILD,
                    outcome=ProofOutcome.GREEN,
                    operation_id=operation_id,
                    artifact_ref="build/sha256/" + ("b" * 64),
                    detail="green",
                )
                await session.commit()
                return "created"
            except ProjectCellProofConflict:
                await session.rollback()
                return "conflict"

    outcomes = await asyncio.gather(record(UUID(int=21)), record(UUID(int=22)))
    assert sorted(outcomes) == ["conflict", "created"]
