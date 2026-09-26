from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_recovery, agent_verification
from yleum_api.services.orchestrator_client import OrchestratorBadRequest


def fatal():
    return OrchestratorBadRequest(
        "private controller details",
        status_code=409,
        upstream_code="protected_environment_recovery_required",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_probe", ["runtime", "build"])
async def test_candidate_probe_never_turns_fatal_infra_into_unknown_green(failed_probe):
    calls = []

    async def runtime_probe(path):
        calls.append("runtime")
        if failed_probe == "runtime":
            raise fatal()
        return {"ok": True}

    async def build_probe():
        calls.append("build")
        raise fatal()

    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await agent_verification.probe_agent_candidate(
            _agent_res=AgentResult(done=True, summary="ready", files={}, steps=1),
            _is_edit=False,
            _seg=1,
            _total_steps=1,
            files={},
            ids=SimpleNamespace(project_id=uuid4()),
            project_info=SimpleNamespace(template="max_miniapp", slug="synthetic"),
            runtime=SimpleNamespace(coordinator=None, handle=object()),
            operations=SimpleNamespace(probe_runtime=runtime_probe, probe_build=build_probe),
        )
    # Зелёная первая проверка рантайма повторяется после паузы на пересборку,
    # поэтому до сборки доходят две записи «runtime», а не одна.
    assert calls == (["runtime"] if failed_probe == "runtime" else ["runtime", "runtime", "build"])


@pytest.mark.asyncio
async def test_first_max_core_recovery_preserves_fatal_infrastructure_error(monkeypatch):
    calls = []

    async def render():
        return {}

    async def apply(**kwargs):
        calls.append("apply")

    async def build():
        calls.append("build")
        raise fatal()

    monkeypatch.setattr(agent_recovery, "_apply_project_cell_preview_files", apply)
    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await agent_recovery.recover_stopped_candidate(
            _agent_res=AgentResult(
                done=False, summary="stopped", files={}, steps=1, stop_reason="max_steps_red"
            ),
            _max_has_generated_snapshot=False,
            _max_seed_files={},
            _provider_failure=None,
            _render_current_max_starter_files=render,
            baseline=SimpleNamespace(sha=None),
            ids=SimpleNamespace(project_id=uuid4()),
            project_info=SimpleNamespace(template="max_miniapp", slug="synthetic"),
            runtime=SimpleNamespace(handle=object()),
            operations=SimpleNamespace(probe_build=build),
        )
    assert calls == ["apply", "apply", "build"]


@pytest.mark.asyncio
async def test_max_seed_keeps_primary_fatal_code_instead_of_repairable_agent_result():
    from yleum_api.services.generation import agent_seed

    async def render():
        return {}

    async def stage(*args):
        raise fatal()

    async def emit(*args):
        pass

    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await agent_seed.stage_max_starter(
            _agent_emit=emit,
            _agent_res=None,
            _design_contract=None,
            _max_has_generated_snapshot=False,
            _render_current_max_starter_files=render,
            bindings=SimpleNamespace(),
            ids=SimpleNamespace(project_id=uuid4()),
            orchestrate=True,
            project_info=SimpleNamespace(template="max_miniapp", slug="synthetic"),
            runtime=SimpleNamespace(
                coordinator=object(), handle=SimpleNamespace(stage_patch=stage)
            ),
        )


@pytest.mark.asyncio
async def test_finalization_fatal_skips_source_repair_and_preserves_code(monkeypatch):
    from yleum_api.services.generation import agent_finalization

    calls = []

    async def finalize(**kwargs):
        raise fatal()

    def read_files(*args):
        calls.append("restore source")
        return {}

    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", read_files)
    with pytest.raises(RuntimeError, match="protected_environment_recovery_required"):
        await agent_finalization.finalize_max_candidate(
            _is_edit=True,
            _max_has_generated_snapshot=True,
            _max_shell_enabled=False,
            accumulated="candidate",
            baseline=SimpleNamespace(sha="a" * 40),
            files={"src/app/page.tsx": "candidate"},
            ids=SimpleNamespace(project_id=uuid4()),
            is_free=True,
            prompt_text="synthetic",
            plan=SimpleNamespace(),
            operations=SimpleNamespace(),
            runtime=SimpleNamespace(
                handle=object(), coordinator=SimpleNamespace(finalize_with_repair=finalize)
            ),
        )
    assert calls == []


@pytest.mark.asyncio
async def test_sealed_adaptation_failure_preserves_candidate_for_forward_recovery(
    monkeypatch,
):
    from yleum_api.services.generation import agent_finalization
    from yleum_api.services.max_finalization import (
        AdaptationActivationRecoveryRequired,
    )

    calls = []

    async def finalize(**kwargs):
        raise AdaptationActivationRecoveryRequired("planned Git upload uncertain")

    def read_files(*args):
        calls.append("restore source")
        return {}

    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", read_files)
    with pytest.raises(RuntimeError, match="planned Git upload uncertain"):
        await agent_finalization.finalize_max_candidate(
            _is_edit=True,
            _max_has_generated_snapshot=True,
            _max_shell_enabled=False,
            accumulated="sealed candidate",
            baseline=SimpleNamespace(sha="a" * 40),
            files={"src/app/page.tsx": "sealed candidate"},
            ids=SimpleNamespace(project_id=uuid4()),
            is_free=False,
            prompt_text="adapt",
            plan=SimpleNamespace(),
            operations=SimpleNamespace(),
            runtime=SimpleNamespace(
                handle=object(), coordinator=SimpleNamespace(finalize_with_repair=finalize)
            ),
        )
    assert calls == []


@pytest.mark.asyncio
async def test_terminal_adaptation_cancel_stops_pipeline_without_source_rollback(
    monkeypatch,
):
    import asyncio

    from yleum_api.services.generation import agent_finalization
    from yleum_api.services.max_finalization import MaxFinalizationStatus

    calls = []

    async def finalize(**kwargs):
        return SimpleNamespace(
            status=MaxFinalizationStatus.CANCELLED,
            redacted_detail="adaptation activation cancelled",
        )

    def read_files(*args):
        calls.append("restore source")
        return {}

    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", read_files)
    with pytest.raises(asyncio.CancelledError):
        await agent_finalization.finalize_max_candidate(
            _is_edit=True,
            _max_has_generated_snapshot=True,
            _max_shell_enabled=False,
            accumulated="sealed candidate",
            baseline=SimpleNamespace(sha="a" * 40),
            files={"src/app/page.tsx": "sealed candidate"},
            ids=SimpleNamespace(project_id=uuid4()),
            is_free=False,
            prompt_text="adapt",
            plan=SimpleNamespace(),
            operations=SimpleNamespace(),
            runtime=SimpleNamespace(
                handle=object(), coordinator=SimpleNamespace(finalize_with_repair=finalize)
            ),
        )
    assert calls == []
