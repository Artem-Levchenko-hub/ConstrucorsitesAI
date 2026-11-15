"""A confirmed provider filter can only hand off source for fresh proof."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from yleum_api.services import (
    agent_builder,
    agent_native,
    llm_client,
    max_behavior_proof,
    restorations,
)
from yleum_api.services.generation import (
    agent_finalization,
    agent_generation,
    agent_recovery,
    agent_verification,
)
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.generation.recovery_contract import RecoveryStateV3
from yleum_api.services.max_finalization import MaxFinalizationStatus

_PAGE = "src/app/page.tsx"


@pytest.mark.parametrize(
    "v3,ambiguous,allow_handoff,expected",
    [
        (True, False, True, True),
        (True, None, True, False),
        (True, True, True, False),
        (False, False, True, False),
        (True, False, False, False),
    ],
)
async def test_legacy_filter_hands_off_only_confirmed_ordinary_max(
    monkeypatch, v3, ambiguous, allow_handoff, expected,
):
    filtered = llm_client.LLMError(
        "provider output filtered",
        code="provider_output_filtered",
        provider_charge_ambiguous=ambiguous,
        finish_reason="content_filter",
    )
    complete = AsyncMock(side_effect=[
        '<omnia:action name="write_file">'
        '{"path":"src/app/page.tsx","content":"candidate"}</omnia:action>',
        filtered,
    ])
    monkeypatch.setattr(llm_client, "complete_chat", complete)
    execute = AsyncMock(return_value={"ok": True, "content": "candidate"})

    async def invoke_legacy(_frame, invoke):
        return await invoke()

    async def tool(_key, _action, invoke):
        return await invoke()

    execution = SimpleNamespace(budget=SimpleNamespace(absolute_deadline=None))
    driver = SimpleNamespace(
        state=RecoveryStateV3.model_construct(execution=execution) if v3 else None,
        context={}, restore_frame=lambda kind: None,
        provider=invoke_legacy, tool=tool,
    )
    result = await agent_builder.run_agent_build(
        system_prompt="system", user_prompt="build", model="claude-sonnet-5.5",
        execute=execute, max_steps=4, coordinator_handoff=True, continuation=driver,
        allow_filtered_source_handoff=allow_handoff,
    )

    assert complete.await_count == 2
    assert execute.await_count == 1
    assert result.files == {_PAGE: "candidate"}
    assert result.done is False and result.proof_checkpoint is None
    if expected:
        assert result.needs_finalization
        assert result.stop_reason == agent_builder.SOURCE_FINALIZATION_HANDOFF
        assert result.continuation_reason == "provider_output_filtered"
    else:
        assert not result.needs_finalization
        assert result.stop_reason == "provider_error"


@pytest.mark.parametrize("enabled", [True, False])
async def test_native_frame_uses_the_same_filtered_source_proof_gate(monkeypatch, enabled):
    monkeypatch.setattr(max_behavior_proof, "freeze_for_turn", AsyncMock())
    monkeypatch.setattr(agent_generation, "get_settings", lambda: SimpleNamespace(
        use_native_agent=False, agent_progressive_continuation=True,
    ))
    candidate = {_PAGE: "saved product"}

    async def run_native(**kwargs):
        if not kwargs.get("allow_filtered_source_handoff"):
            return agent_builder.AgentResult(
                done=False, summary="PROVIDER_OUTPUT_FILTERED: filtered", files=candidate,
                steps=1, stop_reason="provider_error",
            )
        return agent_builder.AgentResult(
            done=False, summary="", files=candidate, steps=1,
            stop_reason=agent_builder.SOURCE_FINALIZATION_HANDOFF, needs_finalization=True,
            continuation_reason=agent_builder.FILTERED_PROVIDER_OUTPUT,
        )

    monkeypatch.setattr(agent_native, "run_native_build", run_native)
    runtime = GenerationRuntime(
        handle=SimpleNamespace(
            is_portable=lambda: True, export_files=AsyncMock(return_value=candidate),
        ),
        coordinator=SimpleNamespace(source_edit_deadline=AsyncMock(return_value=None)),
    )
    operations = SimpleNamespace(
        execute=AsyncMock(), emit=AsyncMock(),
        continuation=SimpleNamespace(context={"active_frame": {"kind": "native"}}),
    )
    turn = agent_generation.execute_agent_turn(
        _agent_res=None, _is_edit=False, _max_has_generated_snapshot=False,
        _max_seed_files={}, _max_shell_enabled=False, baseline=SourceBaseline(None, None, {}),
        ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
        project_info=SimpleNamespace(template="max_miniapp"), prompt_text="Build a product",
        runtime=runtime, plan=SimpleNamespace(
            model="claude-sonnet-5.5", stack_guide="", skills=None, user="build", steps=4,
        ),
        operations=operations, allow_autonomous_source_repair=enabled,
    )
    if enabled:
        result, failure = await turn
        assert failure is None and result.requires_verification_only
        assert result.needs_finalization and not result.done and result.files == candidate
    else:
        with pytest.raises(RuntimeError, match="PROVIDER_OUTPUT_FILTERED"):
            await turn
    operations.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "outcome,existing",
    [("verified", True), ("candidate failed", True), ("candidate failed", False),
     ("provider output filtered", True)],
)
async def test_confirmed_filter_runs_full_proof_with_zero_repairs(monkeypatch, outcome, existing):
    baseline = {_PAGE: "accepted"}
    candidate = {_PAGE: "candidate", "src/lib/new-draft.ts": "rejected helper"}
    restore = AsyncMock()
    restore_core = AsyncMock(return_value={"ok": True})
    accepted_read = Mock(return_value=baseline)
    saved = {}

    async def save_checkpoint(_result):
        saved.update(candidate)

    checkpoint = AsyncMock(side_effect=save_checkpoint)
    paid_repair = AsyncMock(side_effect=AssertionError("filtered output cannot cause another call"))
    monkeypatch.setattr(agent_finalization, "run_native_source_repair", paid_repair)
    monkeypatch.setattr(agent_finalization, "_restore_project_cell_source", restore)
    monkeypatch.setattr(agent_finalization, "_restore_max_core", restore_core)
    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", accepted_read)
    monkeypatch.setattr(restorations, "adaptation_activation_holds_generation_lease",
                        AsyncMock(return_value=False))
    proof = object()

    async def finalize_with_repair(**kwargs):
        assert kwargs["max_source_repairs"] == 0
        assert await kwargs["source_preflight"]() is None
        if outcome == "verified":
            return SimpleNamespace(status=MaxFinalizationStatus.COMPLETE, proof=proof)
        if outcome == "provider output filtered":
            await kwargs["repair_source"]("unexpected saved repair")
        return SimpleNamespace(
            status=MaxFinalizationStatus.NEEDS_EDIT, redacted_detail=outcome,
        )

    runtime = GenerationRuntime(
        handle=SimpleNamespace(
            snapshot_files=AsyncMock(return_value=candidate), is_portable=lambda: True,
            prove_restoration_adaptation=None, export_files=AsyncMock(return_value={}),
        ),
        coordinator=SimpleNamespace(
            finalize_with_repair=AsyncMock(side_effect=finalize_with_repair),
            session_factory=object(),
        ),
    )
    args = dict(
        _is_edit=existing, _max_has_generated_snapshot=existing, _max_shell_enabled=False,
        accumulated="", baseline=SourceBaseline(uuid4(), "accepted-sha", baseline),
        files=candidate, ids=GenerationIds(*(uuid4() for _ in range(5))),
        is_free=False, prompt_text="Add priority filters", runtime=runtime,
        plan=SimpleNamespace(),
        operations=SimpleNamespace(probe_build=AsyncMock(), checkpoint=checkpoint),
        source_handoff=True, verification_only=True,
        render_core_on_rejection=AsyncMock(),
    )

    if outcome == "verified":
        final_proof, files, _ = await agent_finalization.finalize_max_candidate(**args)
        assert final_proof is proof and files == candidate
        checkpoint.assert_not_awaited()
    else:
        with pytest.raises(RuntimeError, match=outcome):
            await agent_finalization.finalize_max_candidate(**args)
        checkpoint.assert_awaited_once()
        assert saved == candidate
    assert baseline == {_PAGE: "accepted"}
    accepted_read.assert_not_called()
    restore.assert_not_awaited()
    restore_core.assert_not_awaited()
    runtime.coordinator.finalize_with_repair.assert_awaited_once()
    paid_repair.assert_not_awaited()


@pytest.mark.parametrize(
    "source,recorded,attributed",
    [("starter page", "starter page", False), ("candidate", "candidate", True),
     ("unrelated tree", "stale write", False)],
)
async def test_confirmed_filter_attribution_survives_cold_executor_baseline(
    monkeypatch, source, recorded, attributed,
):
    monkeypatch.setattr(max_behavior_proof, "freeze_for_turn", AsyncMock())
    restore_core = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(agent_recovery, "_restore_max_core", restore_core)
    checkpoint = AsyncMock()
    paid_repair = AsyncMock()
    monkeypatch.setattr(agent_builder, "run_agent_build", paid_repair)
    proof_gate = AsyncMock()
    runtime = GenerationRuntime(
        handle=SimpleNamespace(export_files=AsyncMock(return_value={})),
        coordinator=SimpleNamespace(finalize_with_repair=proof_gate),
    )
    result = agent_builder.AgentResult(
        done=False, summary="candidate", files={_PAGE: recorded}, steps=1,
        stop_reason=agent_builder.SOURCE_FINALIZATION_HANDOFF, needs_finalization=True,
        continuation_reason="provider_output_filtered",
    )
    seeds = {_PAGE: "starter page"}
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    baseline = SourceBaseline(None, None, {})
    project = SimpleNamespace(template="max_miniapp", slug="test")
    operations = SimpleNamespace(
        probe_build=AsyncMock(), emit=AsyncMock(), checkpoint=checkpoint,
        snapshot_files=AsyncMock(return_value={_PAGE: source, "src/lib/old-draft.ts": "helper"}),
    )
    checked, provider_failure = await agent_generation.execute_agent_turn(
        _agent_res=result, _is_edit=False, _max_has_generated_snapshot=False,
        _max_seed_files=seeds, _max_shell_enabled=False,
        baseline=baseline, ids=ids, is_free=False, project_info=project,
        prompt_text="Build a product", runtime=runtime, plan=SimpleNamespace(),
        operations=operations, allow_autonomous_source_repair=True,
    )

    if attributed:
        assert checked.requires_verification_only and checked.files[_PAGE] == source
        checkpoint.assert_not_awaited()
    else:
        with pytest.raises(RuntimeError, match=r"provider_output_filtered.*не подтверждены") as err:
            await agent_recovery.recover_stopped_candidate(
                _agent_res=checked, _max_has_generated_snapshot=False, _max_seed_files=seeds,
                _provider_failure=provider_failure, _render_current_max_starter_files=AsyncMock(),
                baseline=baseline, ids=ids, project_info=project, runtime=runtime,
                operations=operations, allow_source_repair=True,
            )
        assert not checked.done and not checked.needs_finalization
        assert "rolled_back" not in str(err.value)
        checkpoint.assert_awaited_once()
        operations.emit.assert_not_awaited()
    restore_core.assert_not_awaited()
    proof_gate.assert_not_awaited()
    paid_repair.assert_not_awaited()


async def test_confirmed_filter_keeps_legacy_prechecks_read_only(monkeypatch):
    from yleum_api.services import max_data_evolution, max_generation_contract, sast_gate

    candidate = {
        _PAGE: "const unsafe = eval(request.body);",
        "src/app/globals.css": "source CSS",
    }
    checked = []
    unsafe_rollback = AsyncMock()
    source_patch = AsyncMock()

    def check_sast(files):
        checked.append(dict(files))
        return sast_gate.SastVerdict(
            safe=False, findings=[sast_gate.Finding(_PAGE, "CWE-95", "eval")],
        )

    paid_repair = AsyncMock(side_effect=AssertionError("legacy SAST must not request a repair"))
    monkeypatch.setattr(agent_builder, "run_agent_build", paid_repair)
    monkeypatch.setattr(sast_gate, "check_sast", check_sast)
    monkeypatch.setattr(max_data_evolution, "max_migration_contract_errors",
                        lambda *args, **kwargs: ["missing migration"])
    monkeypatch.setattr(max_generation_contract, "normalize_max_globals_css",
                        lambda source: "normalized CSS")
    monkeypatch.setattr(agent_verification, "_abort_unsafe_max_backend", unsafe_rollback)
    monkeypatch.setattr(agent_verification, "_apply_project_cell_preview_files", source_patch)
    monkeypatch.setattr(agent_verification, "get_settings", lambda: SimpleNamespace(
        use_agent_gate_feedback=True, use_native_agent=False, agent_gate_max_attempts=2,
        use_sast_gate=True, sast_gate_blocking=True,
    ))
    result = agent_builder.AgentResult(
        done=False, summary="candidate", files=candidate, steps=1,
        stop_reason=agent_builder.SOURCE_FINALIZATION_HANDOFF, needs_finalization=True,
        continuation_reason="provider_output_filtered",
    )
    runtime = GenerationRuntime(
        handle=SimpleNamespace(is_portable=lambda: True),
        continuation=object(),
        coordinator=object(),
    )
    await agent_verification.check_backend_and_normalize_css(
        _active_max_locked_files=frozenset(), _agent_res=result,
        _is_edit=False, _max_seed_files={}, baseline=SourceBaseline(None, None, {}),
        files=candidate, ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=SimpleNamespace(template="max_miniapp", slug="test"),
        runtime=runtime, plan=SimpleNamespace(), operations=SimpleNamespace(),
        verification_only=result.requires_verification_only,
    )

    assert checked == [candidate]
    assert candidate["src/app/globals.css"] == "source CSS"
    unsafe_rollback.assert_not_awaited()
    source_patch.assert_not_awaited()
    paid_repair.assert_not_awaited()
