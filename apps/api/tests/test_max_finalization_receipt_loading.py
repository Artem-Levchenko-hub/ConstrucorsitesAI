"""Exercise receipt SQL and bundle assembly without an external database.

SQLite deliberately omits PostgreSQL-only constraints; PostgreSQL integration
tests own those. These tests execute the real SELECT predicates and count cursor
reads, rather than replacing _find or find_proof_result with canned responses.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, MetaData, create_engine, event
from sqlalchemy.orm import Session

from yleum_api.models.project_cell import ProjectCellProof, ProjectCellProofResult
from yleum_api.services import max_finalization as finalization
from yleum_api.services.max_finalization import MaxFinalizationCoordinator
from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity
from yleum_api.services.promotion_permit import (
    canonical_files_digest,
    release_receipt_digest,
    release_receipt_ref,
    workspace_revision_digest,
)


@pytest.fixture
def receipt_store():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (ProjectCellProof, ProjectCellProofResult):
        table = model.__table__.to_metadata(metadata)
        for constraint in list(table.constraints):
            if isinstance(constraint, (CheckConstraint, ForeignKeyConstraint)):
                table.constraints.remove(constraint)
        table.foreign_keys.clear()
        for column in table.columns:
            column.foreign_keys.clear()
    metadata.create_all(engine)
    files = {"src/app.py": "value = 1"}
    identity = ProofIdentity(
        workspace_id=uuid4(), generation_run_id=uuid4(), fencing_epoch=1,
        workspace_revision=workspace_revision_digest(files),
        dependency_digest="1" * 64, schema_data_digest="2" * 64,
        cell_manifest_digest="3" * 64, base_image_digest="4" * 64,
        toolchain_digest="5" * 64, resource_profile_version="test-v1",
        build_config_digest="6" * 64,
    )
    proof = MaxFinalizationCoordinator._transient_proof(identity)
    digest = canonical_files_digest(files)
    results = {}
    for dimension in ProofDimension:
        detail = (
            finalization._versioned_build_detail("actual compile")
            if dimension is ProofDimension.FULL_BUILD else "verified gate"
        )
        artifact_ref = None
        if dimension is ProofDimension.FULL_BUILD:
            artifact_ref = f"build/sha256/{digest}"
        elif dimension is ProofDimension.RUNTIME:
            artifact_ref = "verification/sha256/" + "7" * 64
        elif dimension is ProofDimension.RELEASE:
            artifact_ref = release_receipt_ref(
                artifact_digest=digest,
                receipt_digest=release_receipt_digest(
                    proof_key=identity.proof_key, artifact_digest=digest, detail=detail,
                ),
            )
        results[dimension.value] = ProjectCellProofResult(
            id=uuid4(), proof_id=proof.id, workspace_id=proof.workspace_id,
            dimension=dimension.value,
            dimension_key=identity.dimension_key(
                dimension,
                artifact_digest=digest if dimension in {
                    ProofDimension.RUNTIME, ProofDimension.RELEASE,
                } else None,
            ),
            outcome="green", operation_id=uuid4(), artifact_ref=artifact_ref,
            detail_digest="8" * 64, redacted_detail=detail,
        )

    reads = []

    @event.listens_for(engine, "before_cursor_execute")
    def count_reads(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append((statement, parameters))

    @asynccontextmanager
    async def sessions():
        with Session(engine) as session:
            async def scalar(statement):
                return session.scalar(statement)

            yield SimpleNamespace(scalar=scalar, expunge=session.expunge)

    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.session_factory = sessions
    coordinator._identity = AsyncMock(return_value=identity)

    def save(*, missing=None, missing_proof=False, previous_proofs=()):
        with Session(engine, expire_on_commit=False) as session:
            if not missing_proof:
                session.add(proof)
            session.add_all(previous_proofs)
            session.add_all([row for name, row in results.items() if name != missing])
            session.commit()

    yield SimpleNamespace(
        coordinator=coordinator, files=files, proof=proof, results=results,
        reads=reads, save=save, digest=digest, identity=identity,
    )
    engine.dispose()


# Removing source/artifact validation or accepting a forged release must break
# these literal expectations. Freeze the existing permit decisions as well.
@pytest.mark.parametrize(
    "case,expected,permit",
    [
        ("current", {"bootstrap", "fast_check", "full_build", "runtime", "release"}, True),
        ("missing_proof", set(), True),
        ("stale_source", set(), True),
        ("stale_artifact", {"bootstrap", "fast_check"}, False),
        ("legacy_build", {"bootstrap", "fast_check"}, True),
        ("forged_release", {"bootstrap", "fast_check", "full_build", "runtime"}, False),
        ("release_other_source", {"bootstrap", "fast_check", "full_build", "runtime"}, False),
        ("release_other_artifact", {"bootstrap", "fast_check", "full_build", "runtime"}, False),
        ("reused_bootstrap", {"bootstrap", "fast_check", "full_build", "runtime", "release"}, True),
        ("reused_build", {"bootstrap", "fast_check", "full_build", "runtime", "release"}, False),
        ("missing_bootstrap", {"fast_check", "full_build", "runtime", "release"}, True),
        ("missing_fast_check", {"bootstrap", "full_build", "runtime", "release"}, True),
        ("missing_full_build", {"bootstrap", "fast_check"}, False),
        ("missing_runtime", {"bootstrap", "fast_check", "full_build", "release"}, False),
        ("missing_release", {"bootstrap", "fast_check", "full_build", "runtime"}, False),
        ("red_bootstrap", {"fast_check", "full_build", "runtime", "release"}, True),
        ("red_fast_check", {"bootstrap", "full_build", "runtime", "release"}, True),
        ("red_full_build", {"bootstrap", "fast_check"}, False),
        ("red_runtime", {"bootstrap", "fast_check", "full_build", "release"}, False),
        ("red_release", {"bootstrap", "fast_check", "full_build", "runtime"}, False),
    ],
)
async def test_receipt_and_permit_decisions_are_preserved(receipt_store, case, expected, permit):
    store = receipt_store
    previous_proofs = []
    if case == "stale_source":
        store.files["src/app.py"] = "changed = 2"
    elif case == "stale_artifact":
        store.results["full_build"].artifact_ref = "build/sha256/" + "9" * 64
    elif case == "legacy_build":
        store.results["full_build"].redacted_detail = "obsolete compile contract"
    elif case == "forged_release":
        store.results["release"].artifact_ref = "verification/sha256/" + "9" * 64
    elif case in {"release_other_source", "release_other_artifact"}:
        store.results["release"].artifact_ref = release_receipt_ref(
            artifact_digest=store.digest,
            receipt_digest=release_receipt_digest(
                proof_key="9" * 64 if case == "release_other_source" else store.proof.proof_key,
                artifact_digest="9" * 64 if case == "release_other_artifact" else store.digest,
                detail="verified gate",
            ),
        )
    elif case.startswith("reused_"):
        dimension = "bootstrap" if case == "reused_bootstrap" else "full_build"
        # A dimension-compatible row may belong to a previous proof; lookup must
        # not require proof_id. Promotion still rejects foreign build identity.
        previous_identity = replace(
            store.identity,
            generation_run_id=uuid4(),
            **({"workspace_revision": "a" * 64} if dimension == "bootstrap"
               else {"schema_data_digest": "a" * 64}),
        )
        previous = MaxFinalizationCoordinator._transient_proof(previous_identity)
        assert previous.proof_key != store.proof.proof_key
        previous_proofs.append(previous)
        store.results[dimension].proof_id = previous.id
    elif case.startswith("red_"):
        store.results[case.removeprefix("red_")].outcome = "red"
    missing = case.removeprefix("missing_") if case.startswith("missing_") else None
    store.save(
        missing=missing, missing_proof=case == "missing_proof", previous_proofs=previous_proofs,
    )

    assert await store.coordinator._repair_progress_receipts(store.files) == frozenset(expected)
    if case == "stale_source":
        assert store.reads == []
    # Load the bundle separately to freeze its permit contract independently
    # from progress receipts, which deliberately do not require a permit.
    bundle = await store.coordinator._load_bundle(store.proof)
    assert (bundle.permit is not None) is permit


@pytest.mark.parametrize("dimension", list(ProofDimension))
@pytest.mark.parametrize("mismatch", ["workspace", "dimension", "dimension_key"])
async def test_receipts_require_the_complete_dimension_identity(receipt_store, dimension, mismatch):
    store = receipt_store
    row = store.results[dimension.value]
    if mismatch == "workspace":
        row.workspace_id = uuid4()
    elif mismatch == "dimension":
        row.dimension = "unknown"
    else:
        row.dimension_key = "f" * 64
    store.save()
    expected = {"bootstrap", "fast_check", "full_build", "runtime", "release"} - {dimension.value}
    if dimension is ProofDimension.FULL_BUILD:
        expected -= {"runtime", "release"}
    assert await store.coordinator._repair_progress_receipts(store.files) == frozenset(expected)


@pytest.mark.parametrize("bootstrap_state", ["green", "red", "missing"])
async def test_current_progress_receipts_need_only_six_database_reads(
    receipt_store, bootstrap_state,
):
    # Reintroducing the bootstrap read in bundle assembly must fail this test.
    store = receipt_store
    store.results["bootstrap"].outcome = "red" if bootstrap_state == "red" else "green"
    store.save(missing="bootstrap" if bootstrap_state == "missing" else None)
    expected = {"fast_check", "full_build", "runtime", "release"}
    if bootstrap_state == "green":
        expected.add("bootstrap")
    assert await store.coordinator._repair_progress_receipts(store.files) == frozenset(
        expected
    )
    assert len(store.reads) == 6
    assert [parameters[1] for _statement, parameters in store.reads[1:]] == [
        "bootstrap", "fast_check", "full_build", "runtime", "release",
    ]
