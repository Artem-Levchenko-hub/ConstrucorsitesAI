from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services import max_finalization as finalization
from yleum_api.services.generation_metrics import GenerationPhase
from yleum_api.services.project_cell_executor import (
    ProjectCellCommandObservation,
    ProjectCellCommandRole,
)
from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity, ProofOutcome
from yleum_api.services.promotion_permit import workspace_revision_digest

FLOWER_BUILD_FAILURE = """[install] Lockfile is up to date, resolution step is skipped
Already up to date
Done in 1s
[controller:react-effect-lifetime:v1] React effect lifetime check passed
[build]
> omnia-max-miniapp@0.1.0 build /workspace
> next build
▲ Next.js 15.5.24
- Experiments (use with caution):
· cpus: 2
Creating an optimized production build ...
Failed to compile.
./src/app/page.tsx
Module not found: Can't resolve './flower.css'
[redacted]
> Build failed because of webpack errors
ELIFECYCLE Command failed with exit code 1.
"""


SOURCE_FILES = {"src/app/page.tsx": "import './flower.css';", "src/app/page.jsx": "jsx"}


@pytest.fixture
def identity():
    return ProofIdentity(
        workspace_id=uuid4(), generation_run_id=uuid4(), fencing_epoch=3,
        workspace_revision=workspace_revision_digest(SOURCE_FILES), dependency_digest="2" * 64,
        schema_data_digest="3" * 64,
        cell_manifest_digest="4" * 64, base_image_digest="5" * 64, toolchain_digest="6" * 64,
        resource_profile_version="v2", build_config_digest="7" * 64,
    )


def observation(identity, detail=FLOWER_BUILD_FAILURE, **changes):
    return replace(ProjectCellCommandObservation(
        operation_id=uuid4(), role=ProjectCellCommandRole.FULL_BUILD,
        ok=False, timed_out=False, redacted_detail=detail,
        before=identity, after=identity, invalidated_dimensions=frozenset(),
    ), **changes)


def test_finished_frozen_flower_build_is_a_repairable_source_error(identity):
    assert finalization._confirmed_build_source_error(observation(identity), identity, SOURCE_FILES)


@pytest.mark.parametrize("detail", [
    "src/app/page.tsx\nModule not found: Can't resolve '../styles/flower.css'",
    "/workspace/src/app/page.tsx\r\nModule not found: Can't resolve './catalog.ts'",
    "./src/app/page.jsx\nModule not found: Can't resolve '../components/Catalog'",
    "./src/app/page.tsx\nModule not found: Can't resolve '../images/flower.svg'",
    "src/app/page.tsx(3,2): error TS2322: Type 'number' is not assignable to type 'string'.",
    './src/app/page.tsx\nType error: Invalid Page export.',
    './src/app/page.tsx\nSyntax error: Unexpected token.',
])
def test_local_import_formats_and_existing_compiler_errors_remain_repairable(identity, detail):
    assert finalization._confirmed_build_source_error(
        observation(identity, detail), identity, SOURCE_FILES,
    )


@pytest.mark.parametrize("importer,missing", [
    ("./src/app/page.tsx", "lucide-react"),
    ("./src/app/page.tsx", "@scope/missing"),
    ("./src/app/page.tsx", "https://example.invalid/flower.css"),
    ("./src/app/page.tsx", "/etc/secret.key"),
    ("./src/app/page.tsx", "../../../outside.css"),
    ("./src/app/page.tsx", "./.env"),
    ("./src/app/page.tsx", "../../private.pem"),
    ("./src/app/page.tsx", "../node_modules/missing/index.js"),
    ("./src/app/page.tsx", "./flower.css?token=secret"),
    ("/tmp/src/app/page.tsx", "./flower.css"),
    ("./src/../../secret/page.tsx", "./flower.css"),
    ("./src/.ssh/page.tsx", "../flower.css"),
    ("./src/.env/page.tsx", "../flower.css"),
    ("./src/.git/hooks/page.tsx", "../../flower.css"),
    ("Error: ./src/app/page.tsx", "./flower.css"),
    ("./README.md", "./flower.css"),
])
def test_nonlocal_or_untrusted_missing_import_is_not_a_repair_authorization(
    identity, importer, missing,
):
    detail = f"{importer}\nModule not found: Can't resolve '{missing}'"
    assert not finalization._confirmed_build_source_error(
        observation(identity, detail), identity, SOURCE_FILES,
    )


@pytest.mark.parametrize("guard", [
    "ok", "timeout", "wrong_role", "wrong_before", "wrong_after", "infra",
])
def test_missing_import_does_not_bypass_observation_guards(identity, guard):
    changes = {
        "ok": {"ok": True},
        "timeout": {"timed_out": True},
        "wrong_role": {"role": ProjectCellCommandRole.FAST_CHECK},
        "wrong_before": {"before": replace(identity, workspace_revision="9" * 64)},
        "wrong_after": {"after": replace(identity, workspace_revision="9" * 64)},
        "infra": {"redacted_detail": FLOWER_BUILD_FAILURE + "\nworker_lost SIGKILL ENOMEM"},
    }[guard]
    assert not finalization._confirmed_build_source_error(
        observation(identity, **changes), identity, SOURCE_FILES,
    )


@pytest.mark.parametrize("change_source", [True, False])
async def test_flower_failure_enters_repair_and_rebuilds_only_after_source_change(
    monkeypatch, identity, change_source,
):
    now = datetime.now(UTC)
    run = GenerationRun(status="running", agent_state={}, created_at=now, started_at=now)

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(commit=AsyncMock())

    files = dict(SOURCE_FILES)
    coordinator = object.__new__(finalization.MaxFinalizationCoordinator)
    coordinator.session_factory = sessions
    coordinator.executor = SimpleNamespace(
        snapshot_files=AsyncMock(side_effect=lambda: dict(files)),
    )
    coordinator._raise_persisted_infrastructure_failure = AsyncMock()
    coordinator._repair_progress_receipts = AsyncMock(return_value=frozenset())
    coordinator._locked_run = AsyncMock(return_value=run)
    coordinator._checkpoint = lambda *args: None
    coordinator._log_terminal = AsyncMock()
    coordinator._verified_workspace_files = AsyncMock(side_effect=lambda *args: dict(files))
    monkeypatch.setattr(finalization, "generation_deadline", lambda run: SimpleNamespace(
        at=now + timedelta(hours=1),
    ))
    proof = coordinator._transient_proof(identity)
    builds = []
    feedback = []

    async def command(**kwargs):
        builds.append(dict(files))
        return observation(identity, ok="src/app/flower.css" in files)

    async def record(**kwargs):
        return SimpleNamespace(outcome=kwargs["outcome"].value,
                               operation_id=kwargs["operation_id"],
                               redacted_detail=kwargs["detail"])

    async def outcome(status, checkpoint, proof_bundle, detail):
        return SimpleNamespace(status=status, redacted_detail=detail)

    async def finalize(**kwargs):
        result = await coordinator._run_role_result(
            proof=proof, identity=identity, dimension=ProofDimension.FULL_BUILD,
            role=ProjectCellCommandRole.FULL_BUILD, phase=GenerationPhase.FINAL_BUILD,
        )
        if result.outcome == ProofOutcome.GREEN.value:
            return SimpleNamespace(status=finalization.MaxFinalizationStatus.COMPLETE)
        return await coordinator._failed(identity, proof, GenerationPhase.FINAL_BUILD,
                                         result, result.redacted_detail)

    async def repair(detail):
        feedback.append(detail)
        if change_source:
            files["src/app/flower.css"] = ".catalog { display: grid; gap: 1rem; }"

    coordinator._execute_role = command
    coordinator._record = record
    coordinator._outcome = outcome
    coordinator.finalize = finalize
    result = await coordinator.finalize_with_repair(prompt="Build flower catalog", repair=repair)

    assert len(feedback) == 1
    assert "Module not found: Can't resolve './flower.css'" in feedback[0]
    assert len(builds) == (2 if change_source else 1)
    assert result.status is (
        finalization.MaxFinalizationStatus.COMPLETE if change_source
        else finalization.MaxFinalizationStatus.NEEDS_EDIT
    )
