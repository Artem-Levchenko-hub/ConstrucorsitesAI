"""Pre-model core readiness and its exact controller migration evidence."""

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from tests.test_project_cell_executor import business_config_rpc  # noqa: F401
from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.services import project_cell_executor
from yleum_api.services.generation import agent_pipeline, agent_seed
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.generation_metrics import GenerationPhase
from yleum_api.services.max_finalization import MaxFinalizationCoordinator
from yleum_api.services.project_cell_executor import (
    ProjectCellCommandRole,
    valid_platform_bootstrap_receipt,
)
from yleum_api.services.project_cell_proofs import (
    MAX_BOOTSTRAP_CONTRACT_VERSION,
    ProofDimension,
    ProofIdentity,
)


def identity():
    return ProofIdentity(
        workspace_id=uuid4(), generation_run_id=uuid4(), fencing_epoch=7,
        workspace_revision="a" * 64, dependency_digest="b" * 64,
        schema_data_digest="c" * 64, cell_manifest_digest="d" * 64,
        base_image_digest="e" * 64, toolchain_digest="e" * 64,
        resource_profile_version="test", build_config_digest="f" * 64,
    )


def test_bootstrap_proof_is_schema_bound_and_cannot_reuse_install_only_contract():
    current = identity()
    original_install = {
        "dimension": "bootstrap", "fencing_epoch": current.fencing_epoch,
        "dependency_digest": current.dependency_digest,
        "cell_manifest_digest": current.cell_manifest_digest,
        "base_image_digest": current.base_image_digest,
        "toolchain_digest": current.toolchain_digest,
        "resource_profile_version": current.resource_profile_version,
    }
    old_key = hashlib.sha256(json.dumps(
        original_install, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    key = current.dimension_key(ProofDimension.BOOTSTRAP)
    assert key != old_key
    assert key != replace(current, schema_data_digest="7" * 64).dimension_key(
        ProofDimension.BOOTSTRAP,
    )
    assert key == replace(current, workspace_revision="7" * 64).dimension_key(
        ProofDimension.BOOTSTRAP,
    )


async def test_bootstrap_operation_id_cannot_replay_old_install_only_command():
    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.generation_run_id = uuid4()
    coordinator._execute_role_activity = AsyncMock(return_value=NS(ok=True))
    current = identity()
    await coordinator._execute_role(
        identity=current, dimension=ProofDimension.BOOTSTRAP,
        role=ProjectCellCommandRole.BOOTSTRAP, phase=GenerationPhase.PREPARE,
    )
    operation_id = coordinator._execute_role_activity.await_args.kwargs["operation_id"]
    prefix = (
        f"command:{current.workspace_id}:{current.fencing_epoch}:"
        f"{current.proof_key}:bootstrap:"
    )
    assert operation_id != uuid5(coordinator.generation_run_id, prefix + "v1")
    assert operation_id == uuid5(
        coordinator.generation_run_id, prefix + MAX_BOOTSTRAP_CONTRACT_VERSION,
    )


@pytest.mark.parametrize("fault", [
    None, "missing", "contract", "sql", "operation", "schema", "source", "fence", "count",
])
def test_platform_bootstrap_requires_current_bound_controller_receipt(fault):
    before, operation_id, source = identity(), uuid4(), "trusted platform SQL"
    after = replace(before, base_image_digest="1" * 64, toolchain_digest="1" * 64)
    receipt = {
        "contract": "project-migrations-v1", "mode": "apply",
        "workspace_id": str(before.workspace_id),
        "generation_run_id": str(before.generation_run_id), "fencing_epoch": 7,
        "source_revision": before.workspace_revision, "migration_count": 1,
        "source_digest": "2" * 64, "database_identity": "3" * 64,
        "catalog_digest": "4" * 64,
        "platform_core_bootstrap": {
            "contract": MAX_BOOTSTRAP_CONTRACT_VERSION,
            "migration_path": "drizzle/0003_owner_management.sql",
            "migration_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "operation_id": str(operation_id), "schema_data_digest": after.schema_data_digest,
        },
    }
    core = receipt["platform_core_bootstrap"]
    if fault == "missing":
        receipt.pop("platform_core_bootstrap")
    elif fault == "contract":
        core["contract"] = "old-install-only-v1"
    elif fault == "sql":
        core["migration_sha256"] = "5" * 64
    elif fault == "operation":
        core["operation_id"] = str(uuid4())
    elif fault == "schema":
        core["schema_data_digest"] = "5" * 64
    elif fault == "source":
        after = replace(after, workspace_revision="5" * 64)
    elif fault == "fence":
        receipt["fencing_epoch"] = 8
    elif fault == "count":
        receipt["migration_count"] = 0
    assert valid_platform_bootstrap_receipt(
        receipt, before=before, after=after, operation_id=operation_id, source=source,
    ) is (fault is None)


@pytest.mark.parametrize("valid", [False, True])
async def test_executor_bootstrap_cannot_accept_unwitnessed_success(
    monkeypatch, db_session, test_engine, valid,
):
    from tests.test_project_cell_executor import _prepare_executor

    source = "trusted platform SQL"
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine,
        snapshot_files={
            ".omnia/cell.json": '{"version":1,"app_config_protocol":1}',
            "drizzle/0003_owner_management.sql": source,
        }, capabilities={"portable_machine": True},
    )
    original = project_cell_executor.project_cell_agent_exec

    async def execute(workspace_id, *args, **kwargs):
        result = await original(workspace_id, *args, **kwargs)
        assert kwargs["task_role"] == "bootstrap"
        if not valid:
            return result  # This is the old install-only success response.
        return replace(result, project_migration_receipt={
            "contract": "project-migrations-v1", "mode": "apply",
            "workspace_id": str(workspace_id),
            "generation_run_id": str(kwargs["generation_run_id"]),
            "fencing_epoch": kwargs["fencing_epoch"],
            "source_revision": kwargs["expected_revision"], "migration_count": 1,
            "source_digest": "1" * 64, "database_identity": "2" * 64,
            "catalog_digest": "3" * 64,
            "platform_core_bootstrap": {
                "contract": MAX_BOOTSTRAP_CONTRACT_VERSION,
                "migration_path": "drizzle/0003_owner_management.sql",
                "migration_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "operation_id": str(result.operation_id),
                "schema_data_digest": result.after_identity.schema_data_digest,
            },
        })

    monkeypatch.setattr(project_cell_executor, "project_cell_agent_exec", execute)
    observation = await harness.handle.run_role(ProjectCellCommandRole.BOOTSTRAP, uuid4())
    assert observation.ok is valid
    if not valid:
        assert "model launch rejected" in observation.redacted_detail


@pytest.mark.parametrize("outcome", ["green", "red", "error"])
async def test_coordinator_seed_runs_real_core_check_after_staging(outcome):
    trace = []
    workspace = {".omnia/cell.json": '{"version":1,"tasks":[]}',
                 "src/app/page.tsx": "old product"}

    async def stage(writes, deletes):
        trace.append("stage")
        workspace.update(writes)
        for path in deletes:
            workspace.pop(path, None)

    async def check():
        trace.append("controller_bootstrap_and_fast_check")
        assert "src/app/page.tsx" not in workspace
        assert workspace["package.json"] == "trusted package"
        if outcome == "error":
            raise RuntimeError("controller unavailable")
        return NS(outcome=outcome, redacted_detail="actual controller result")

    runtime = GenerationRuntime(
        handle=NS(is_portable=lambda: True,
                  snapshot_files=AsyncMock(side_effect=lambda: dict(workspace)),
                  stage_patch=stage),
        coordinator=NS(fast_check=check),
    )
    legacy_build, emit = AsyncMock(), AsyncMock()
    call = agent_seed.stage_max_starter(
        _agent_emit=emit, _agent_res=None, _design_contract=None,
        _max_has_generated_snapshot=False,
        _render_current_max_starter_files=AsyncMock(
            return_value={"package.json": "trusted package"},
        ), bindings=NS(probe_build=legacy_build), ids=NS(project_id=uuid4()),
        orchestrate=True, project_info=NS(), runtime=runtime,
    )
    if outcome == "green":
        await call
        assert emit.await_args_list[-1].args[1]["ok"] is True
    else:
        with pytest.raises(ApiError, match="MAX_CORE_PREPARATION_FAILED"):
            await call
        assert emit.await_args_list[-1].args[1]["ok"] is False
    assert trace == ["stage", "controller_bootstrap_and_fast_check"]
    legacy_build.assert_not_awaited()


async def test_failed_real_core_check_stops_actual_pipeline_before_paid_agent(monkeypatch):
    from yleum_api.services.generation import recovery_execution

    # This fixture exercises the ordinary pipeline, not durable recovery. Do
    # not poison the process-wide cached settings with a temporary database URL.
    monkeypatch.setattr(recovery_execution, "execution_enabled", lambda _project: False)
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    request = "Создай пустой каталог"
    record = NS(id=ids.run_id, project_id=ids.project_id, user_id=ids.user_id,
                status="running", prompt_hash=hashlib.sha256(request.encode()).hexdigest(),
                user_message_id=ids.user_message_id, agent_state={})
    session = NS(commit=AsyncMock(), get=AsyncMock(side_effect=lambda model, *_a, **_k:
        record if model is GenerationRun else NS(owner_id=ids.user_id, discovery_spec=None)
        if model is Project else NS(project_id=ids.project_id, role="user", content=request)))

    @asynccontextmanager
    async def factory():
        yield session

    check = AsyncMock(return_value=NS(outcome="red", redacted_detail="migration verification red"))
    runtime = GenerationRuntime(
        handle=NS(is_portable=lambda: True, stage_patch=AsyncMock(),
                  snapshot_files=AsyncMock(return_value={".omnia/cell.json": '{"version":1}'})),
        coordinator=NS(session_factory=factory, _locked_run=AsyncMock(return_value=record),
                       project_id=ids.project_id, generation_run_id=ids.run_id,
                       fast_check=check),
    )
    monkeypatch.setattr(agent_pipeline, "get_settings", lambda: NS(
        use_design_intelligence_plugin=False,
    ))
    monkeypatch.setattr(agent_pipeline, "adjacent_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_pipeline, "classify_agent_turn", AsyncMock(
        return_value=(request, False, False, False),
    ))
    monkeypatch.setattr(agent_pipeline, "prepare_agent_runtime", AsyncMock(
        return_value=(NS(shell_enabled=False, probe_build=AsyncMock()), None),
    ))
    monkeypatch.setattr(agent_pipeline, "prepare_stack_prompt", AsyncMock(return_value=NS()))
    monkeypatch.setattr(agent_pipeline, "prepare_agent_prompt", AsyncMock(
        return_value=(NS(), None),
    ))
    monkeypatch.setattr(agent_pipeline, "render_current_max_starter", AsyncMock(
        return_value={"package.json": "trusted"},
    ))
    provider, consume = AsyncMock(), AsyncMock()
    monkeypatch.setattr(agent_pipeline, "execute_agent_turn", provider)
    with pytest.raises(ApiError, match="MAX_CORE_PREPARATION_FAILED"):
        await agent_pipeline.run_agent_generation(
            _consume_free_generation=consume, baseline=SourceBaseline(None, None, {}),
            capacity_dispatch_token=None, factory=factory, force_model=None, ids=ids,
            is_free=False, model_id="unused", orchestrate=True,
            progress=NS(emit_agent_event=AsyncMock()),
            project_info=NS(template="max_miniapp", name="Test", slug="test"),
            prompt_text=request, runtime=runtime, selected_elements=None,
        )
    check.assert_awaited_once()
    provider.assert_not_awaited()
    consume.assert_not_awaited()
