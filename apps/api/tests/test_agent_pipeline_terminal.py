"""Pipeline terminal events agree with the committed product outcome.

External generation phases are fixed inputs; source validation, failure recording
and empty-candidate publication run unchanged. The session double exposes only
committed state at the event boundary, without requiring a PostgreSQL server.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.services import restorations
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import (
    agent_generation,
    agent_pipeline,
    agent_publication,
    agent_recovery,
)
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.recovery_requirements import STATE_KEY, frozen_requirements


def test_output_limit_failure_survives_rollback_but_not_successful_finalization():
    failed = AgentResult(
        done=False,
        summary="Partial response rejected",
        files={"src/app/page.tsx": "changed"},
        steps=10,
        stop_reason="output_limit",
    )
    cause = agent_generation._primary_provider_failure(failed)
    assert cause is not None and "output_limit" in cause
    failed.needs_finalization = True
    assert agent_generation._primary_provider_failure(failed) is None


@pytest.mark.asyncio
async def test_first_max_output_limit_reaches_safe_core_restoration():
    stopped = AgentResult(
        done=False,
        summary="Incomplete provider response",
        files={"src/app/page.tsx": "partial"},
        steps=10,
        stop_reason="output_limit",
    )
    result, cause = await agent_generation.execute_agent_turn(
        _agent_res=stopped,
        _is_edit=False,
        _max_has_generated_snapshot=False,
        _max_seed_files={},
        _max_shell_enabled=False,
        baseline=SourceBaseline(None, None, {}),
        ids=None,
        is_free=False,
        project_info=None,
        prompt_text="",
        runtime=GenerationRuntime(),
        plan=None,
        operations=None,
    )
    assert result is stopped
    assert cause is not None and "output_limit" in cause


async def test_first_provider_rejection_exits_before_extra_repair_or_finalization():
    stopped = AgentResult(
        done=False, summary="provider_http_401: Автоматический повтор остановлен.",
        files={}, steps=1, stop_reason="provider_error",
    )
    with pytest.raises(RuntimeError, match="provider_http_401"):
        await agent_generation.execute_agent_turn(
            _agent_res=stopped, _is_edit=False, _max_has_generated_snapshot=False,
            _max_seed_files={}, _max_shell_enabled=False,
            baseline=SourceBaseline(None, None, {}), ids=None, is_free=False,
            project_info=None, prompt_text="", runtime=GenerationRuntime(),
            plan=None, operations=None,
        )


async def test_first_ordinary_max_output_limit_without_writes_still_enters_repair(monkeypatch):
    monkeypatch.setattr("yleum_api.services.max_behavior_proof.freeze_for_turn", AsyncMock())
    runtime = GenerationRuntime(
        coordinator=SimpleNamespace(),
        handle=SimpleNamespace(
            is_portable=lambda: True, snapshot_files=AsyncMock(),
            export_files=AsyncMock(return_value={}),
        ),
    )
    operations = SimpleNamespace(emit=AsyncMock(), snapshot_files=None, continuation=None)
    starter = {"src/app/layout.tsx": "platform layout"}
    baseline = SourceBaseline(None, None, {})
    project = SimpleNamespace(template="max_miniapp", slug="qa")
    candidate = AgentResult(
        done=False, summary="Settled truncation", files={}, steps=10,
        stop_reason="output_limit",
    )
    candidate, cause = await agent_generation.execute_agent_turn(
        _agent_res=candidate, _is_edit=False, _max_has_generated_snapshot=False,
        _max_seed_files=starter, _max_shell_enabled=False, baseline=baseline,
        ids=None, is_free=False, project_info=project, prompt_text="Рацион на месяц",
        runtime=runtime, plan=None, operations=operations,
        allow_autonomous_source_repair=True,
    )
    handoff, first, _, _ = await agent_recovery.recover_stopped_candidate(
        _agent_res=candidate, _max_has_generated_snapshot=False, _max_seed_files=starter,
        _provider_failure=cause, _render_current_max_starter_files=AsyncMock(),
        baseline=baseline, ids=None, project_info=project, runtime=runtime,
        operations=operations, allow_source_repair=True,
    )
    assert first and handoff.needs_finalization and not handoff.done
    assert handoff.continuation_reason == "output_limit"
    assert handoff.files == {}


@pytest.fixture
def pipeline_turn(monkeypatch):
    async def run(*, candidate, done=True, native=True, verification_failed=False,
                  checkpoint_failure_phase=None, source_deadline=None,
                  multi_path_checkpoint_failure=False, checkpoint_snapshot_failure=False,
                  ordinary_max=False):
        ids = GenerationIds(*(uuid4() for _ in range(5)))
        original_prompt = "Change title"
        project = Project(id=ids.project_id, owner_id=ids.user_id, template="max_miniapp",
                          name="Fixture", slug="fixture", discovery_spec=None)
        original_message = Message(id=ids.user_message_id, project_id=ids.project_id,
                                   role="user", content=original_prompt)
        run_row = GenerationRun(id=ids.run_id, project_id=ids.project_id, user_id=ids.user_id,
                                status="running", agent_state={}, idempotency_key="terminal-turn",
                                prompt_hash=sha256(original_prompt.encode()).hexdigest(),
                                user_message_id=original_message.id,
                                assistant_message_id=ids.assistant_message_id)
        message = Message(id=ids.assistant_message_id, project_id=ids.project_id,
                          role="assistant", content="", tokens_out=None)
        committed = {}
        scope_commit = {}
        trace = []
        events = []
        cards = []

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def scalar(self, statement):
                assert statement.column_descriptions[0]["entity"] is GenerationRun
                return run_row

            async def get(self, model, key, **_kwargs):
                if model is Project:
                    assert key == ids.project_id
                    return project
                if model is GenerationRun:
                    assert key == ids.run_id
                    return run_row
                if model is Message and key == ids.user_message_id:
                    return original_message
                assert model is Message and key == ids.assistant_message_id
                return message

            async def commit(self):
                committed.update(
                    outcome=deepcopy(run_row.agent_state),
                    status=run_row.status,
                    content=message.content,
                    tokens_out=message.tokens_out,
                    snapshot_id=message.snapshot_id,
                )
                if not scope_commit:
                    assert set(run_row.agent_state) == {STATE_KEY}
                    scope_commit.update(deepcopy(committed))
                    trace.append("freeze_requirements")
                else:
                    trace.append("commit")

        async def publish(project_id, kind, payload):
            assert project_id == ids.project_id
            events.append((kind, payload, deepcopy(committed)))
            trace.append(kind)

        async def clear(project_id, message_id):
            assert (project_id, message_id) == (ids.project_id, ids.assistant_message_id)
            trace.append("clear_stream")

        async def card(*_args, **kwargs):
            cards.append(kwargs)
            trace.append("continue_card")

        async def forbidden(*_args, **_kwargs):
            pytest.fail("empty or rejected candidate must not publish a snapshot or consume quota")

        monkeypatch.setattr(agent_pipeline, "publish_event", publish)
        monkeypatch.setattr(agent_publication, "publish_event", publish)
        monkeypatch.setattr(agent_pipeline, "clear_stream_state", clear)
        monkeypatch.setattr(agent_pipeline.app_errors, "publish", card)
        monkeypatch.setattr(agent_publication, "create_generation_snapshot", forbidden)
        monkeypatch.setattr(
            restorations, "adaptation_activation_consumed", AsyncMock(return_value=False)
        )
        monkeypatch.setattr(
            agent_pipeline,
            "get_settings",
            lambda: SimpleNamespace(use_native_agent=native, use_build_attestation=False),
        )
        result = AgentResult(
            done=done,
            summary="provider internal diagnostic fixture-secret",
            files=candidate,
            steps=2,
            stop_reason="done" if done else "max_steps",
        )
        bindings = SimpleNamespace(
            execute=forbidden,
            probe_runtime=forbidden,
            probe_build=forbidden,
            preview_url=forbidden,
            shell_enabled=False,
            locked_files=frozenset(),
        )
        # Treat expensive provider/runtime stages as already completed. Preserve
        # the real final source guard, product verdict and database publication.
        phase_results = {
            "require_named_behavior_readiness": None,
            "classify_agent_turn": ("Change title", False, not ordinary_max, not ordinary_max),
            "prepare_agent_runtime": (bindings, None),
            "prepare_stack_prompt": SimpleNamespace(orchestrator_template=None),
            "prepare_agent_prompt": (None, None),
            "stage_max_starter": ({}, None),
            "execute_agent_turn": (result, None),
            "recover_stopped_candidate": (result, False, {}, 1),
            "complete_empty_legacy_build": (result, candidate, 2),
            "check_backend_and_normalize_css": None,
            "probe_agent_candidate": (result.summary, True, True, "", ""),
            "repair_legacy_edit": (result.summary, True, True, "", ""),
            "recover_rejected_candidate": (
                verification_failed, result, candidate, True, True, result.summary
            ),
            "apply_legacy_design_and_result_text": (result.summary,),
            "check_runtime_security_gates": (None, "", result.summary),
            "check_coverage_gate": (None, result.summary),
            "finalize_max_candidate": (None, candidate, result.summary),
        }
        for name, value in phase_results.items():
            monkeypatch.setattr(agent_pipeline, name, AsyncMock(return_value=value))

        runtime = GenerationRuntime()
        if source_deadline is not None or ordinary_max:
            runtime.coordinator = SimpleNamespace(
                source_edit_deadline=AsyncMock(return_value=source_deadline),
                ensure_bootstrap=AsyncMock(return_value=SimpleNamespace(outcome="green")),
            )
        baseline_files = {"src/page.tsx": "original"}
        tree = {"src/page.tsx": "unsaved source"}
        if ordinary_max:
            runtime.handle = SimpleNamespace(
                snapshot_files=AsyncMock(return_value=dict(tree)),
                prove_restoration_adaptation=None,
            )
        if checkpoint_failure_phase is not None:
            snapshot_unavailable = False

            async def snapshot():
                if snapshot_unavailable:
                    raise RuntimeError("private snapshot unavailable")
                return dict(tree)

            runtime.handle = SimpleNamespace(
                snapshot_files=AsyncMock(side_effect=snapshot),
            )
            bindings.probe_build = AsyncMock(return_value={"ok": True})
            stopped = AgentResult(
                done=False, summary="checkpoint storage failed", files=dict(tree), steps=1,
                stop_reason="checkpoint_error",
            )
            getattr(agent_pipeline, checkpoint_failure_phase).side_effect = (
                agent_generation.SourceCheckpointError(stopped)
            )

            def read_baseline(project_id, sha):
                assert project_id == ids.project_id and sha == "baseline-sha"
                return dict(baseline_files)

            async def apply_restored_files(*, files, empty_files=(), **_kwargs):
                for path, content in files.items():
                    if content or path in empty_files:
                        tree[path] = content
                    else:
                        tree.pop(path, None)
                trace.append("restored_accepted_source")

            monkeypatch.setattr(agent_recovery.repo_svc, "read_files", read_baseline)
            monkeypatch.setattr(
                agent_recovery, "_apply_project_cell_preview_files", apply_restored_files,
            )

            if multi_path_checkpoint_failure:
                baseline_files = {
                    "src/main.tsx": "accepted main",
                    "src/heal.tsx": "accepted heal",
                    "src/deleted.tsx": "accepted deleted",
                }
                tree.clear()
                tree.update({**baseline_files, **candidate})
                monkeypatch.setattr(agent_pipeline, "persist_checkpoint", AsyncMock(
                    side_effect=[None, RuntimeError("private storage unavailable")],
                ))

                async def fail_heal(*, operations, **_kwargs):
                    nonlocal snapshot_unavailable
                    tree["src/prior-new.tsx"] = "earlier heal source"
                    await operations.checkpoint(AgentResult(
                        done=False, summary="earlier heal", steps=1,
                        files={"src/prior-new.tsx": "earlier heal source"},
                    ))
                    tree.update({
                        "src/heal.tsx": "failed heal source",
                        "src/heal-new.tsx": "failed heal new source",
                    })
                    tree.pop("src/deleted.tsx")
                    stopped.files = {
                        "src/heal.tsx": "failed heal source",
                        "src/heal-new.tsx": "failed heal new source",
                        "src/deleted.tsx": "",
                    }
                    snapshot_unavailable = checkpoint_snapshot_failure
                    try:
                        await operations.checkpoint(stopped)
                    except RuntimeError:
                        snapshot_unavailable = True
                        agent_generation.raise_if_source_checkpoint_failed(stopped)
                    pytest.fail("failed private checkpoint must stop the heal")

                getattr(agent_pipeline, checkpoint_failure_phase).side_effect = fail_heal

            async def recover(**kwargs):
                if kwargs["_agent_res"].stop_reason == "checkpoint_error":
                    return await agent_recovery.recover_stopped_candidate(**kwargs)
                return phase_results["recover_stopped_candidate"]

            agent_pipeline.recover_stopped_candidate.side_effect = recover

        invocation = agent_pipeline.run_agent_generation(
            _consume_free_generation=forbidden,
            baseline=SourceBaseline(uuid4(), "baseline-sha", baseline_files),
            capacity_dispatch_token=None,
            factory=Session,
            force_model=None,
            ids=ids,
            is_free=False,
            model_id="fixture-model",
            orchestrate=False,
            progress=SimpleNamespace(
                emit_agent_event=AsyncMock() if checkpoint_failure_phase is not None else forbidden,
                steps=[],
            ),
            project_info=ProjectGenerationFacts(
                "max_miniapp", "fixture", "Fixture", None, None, False, "ru", False, "", ""
            ),
            prompt_text="Change title",
            runtime=runtime,
            selected_elements=None,
        )
        failure = None
        try:
            await invocation
        except Exception as exc:
            if checkpoint_failure_phase is None:
                raise
            failure = exc
        assert scope_commit and scope_commit["status"] == "running"
        assert scope_commit["content"] == "" and scope_commit["tokens_out"] is None
        assert scope_commit["snapshot_id"] is None
        requirements = frozen_requirements(run_row)
        assert any(not requirement.required and requirement.text == original_prompt
                   for requirement in requirements)
        assert any(requirement.required and requirement.check_kind == "manual"
                   and requirement.text == original_prompt for requirement in requirements)
        return SimpleNamespace(
            ids=ids, committed=committed, trace=trace, events=events, cards=cards,
            tree=tree, failure=failure, scope_commit=scope_commit,
            phases={name: getattr(agent_pipeline, name) for name in phase_results},
        )

    return run


async def test_pipeline_carries_one_source_deadline_to_main_and_legacy_fallbacks(pipeline_turn):
    deadline = datetime.now(UTC) + timedelta(seconds=20)
    turn = await pipeline_turn(candidate={}, done=False, native=False, source_deadline=deadline)
    operations = turn.phases["execute_agent_turn"].await_args.kwargs["operations"]
    assert operations.edit_deadline is deadline
    assert operations.checkpoint is not None
    for name in (
        "complete_empty_legacy_build", "check_backend_and_normalize_css", "repair_legacy_edit",
        "check_runtime_security_gates", "check_coverage_gate",
    ):
        assert turn.phases[name].await_args.kwargs["operations"] is operations


@pytest.mark.parametrize("phase", [
    "complete_empty_legacy_build", "check_backend_and_normalize_css", "repair_legacy_edit",
    "check_runtime_security_gates", "check_coverage_gate",
])
async def test_pipeline_checkpoint_failure_restores_accepted_source_before_stopping(
    pipeline_turn, phase,
):
    turn = await pipeline_turn(
        candidate={"src/page.tsx": "changed"}, native=False, checkpoint_failure_phase=phase,
    )
    assert turn.failure is not None
    assert turn.tree == {"src/page.tsx": "original"}
    assert turn.trace == ["freeze_requirements", "restored_accepted_source"]
    assert turn.phases["recover_stopped_candidate"].await_count == 2
    turn.phases["finalize_max_candidate"].assert_not_awaited()
    assert turn.events == [] and turn.cards == [] and turn.committed == turn.scope_commit


@pytest.mark.parametrize("phase", [
    "check_backend_and_normalize_css", "repair_legacy_edit",
    "check_runtime_security_gates", "check_coverage_gate",
])
@pytest.mark.parametrize("checkpoint_snapshot_failure", [False, True])
async def test_heal_checkpoint_failure_restores_every_run_change_to_exact_accepted_tree(
    pipeline_turn, phase, checkpoint_snapshot_failure,
):
    turn = await pipeline_turn(
        candidate={"src/main.tsx": "main candidate", "src/main-new.tsx": "main new source"},
        native=False, checkpoint_failure_phase=phase, multi_path_checkpoint_failure=True,
        checkpoint_snapshot_failure=checkpoint_snapshot_failure,
    )
    assert turn.failure is not None and "private" not in str(turn.failure)
    assert turn.tree == {
        "src/main.tsx": "accepted main",
        "src/heal.tsx": "accepted heal",
        "src/deleted.tsx": "accepted deleted",
    }
    assert turn.trace == ["freeze_requirements", "restored_accepted_source"]
    heal_phases = (
        "check_backend_and_normalize_css", "repair_legacy_edit",
        "check_runtime_security_gates", "check_coverage_gate",
    )
    for later_phase in heal_phases[heal_phases.index(phase) + 1:]:
        turn.phases[later_phase].assert_not_awaited()
    turn.phases["finalize_max_candidate"].assert_not_awaited()
    assert turn.events == [] and turn.cards == [] and turn.committed == turn.scope_commit


@pytest.mark.parametrize("native,done", [(True, True), (True, False), (False, False)])
async def test_identical_edit_emits_error_after_failure_commit_without_snapshot_or_partial_claim(
    pipeline_turn, native, done
):
    turn = await pipeline_turn(candidate={"src/page.tsx": "original"}, native=native, done=done)

    assert [kind for kind, _, _ in turn.events] == ["llm.error"]
    _, payload, saved = turn.events[0]
    assert payload == {
        "message_id": str(turn.ids.assistant_message_id),
        "error": (
            "Не удалось применить правку: итоговый код не изменился. "
            "Повтори запрос или уточни, что именно нужно изменить."
        ),
    }
    assert saved["outcome"]["product_outcome"] == {
        "status": "failed", "error": "edit produced no source changes"
    }
    assert saved["content"] == payload["error"]
    assert saved["tokens_out"] == 0
    assert saved["snapshot_id"] is None
    assert saved["status"] == "running"  # Executor cleanup still owns finalization.
    assert turn.trace == ["freeze_requirements", "commit", "commit", "llm.error", "clear_stream"]
    assert turn.cards == []


@pytest.mark.parametrize("done,verification_failed", [(False, False), (True, True)])
async def test_native_failed_outcome_emits_safe_error_without_internal_summary(
    pipeline_turn, done, verification_failed
):
    turn = await pipeline_turn(candidate={}, done=done, verification_failed=verification_failed)

    assert [kind for kind, _, _ in turn.events] == ["llm.error"]
    _, payload, saved = turn.events[0]
    assert payload["message_id"] == str(turn.ids.assistant_message_id)
    assert payload["error"] and "Не удалось" in payload["error"]
    assert "provider" not in payload["error"] and "fixture-secret" not in payload["error"]
    assert saved["outcome"]["product_outcome"]["status"] == "failed"
    assert saved["status"] == "running"
    assert turn.trace[-2:] == ["llm.error", "clear_stream"]


async def test_rejected_first_ordinary_max_repair_emits_only_the_final_failure(pipeline_turn):
    turn = await pipeline_turn(
        candidate={}, native=False, verification_failed=True, ordinary_max=True,
    )

    assert [kind for kind, _, _ in turn.events] == ["llm.error"]
    _, payload, saved = turn.events[0]
    assert "fixture-secret" not in payload["error"]
    assert saved["outcome"]["product_outcome"]["status"] == "failed"
    assert saved["snapshot_id"] is None
    assert turn.cards == []
    turn.phases["repair_legacy_edit"].assert_not_awaited()


@pytest.mark.parametrize("native,done", [(True, True), (False, False)])
async def test_successful_noop_and_legacy_partial_keep_done_event(pipeline_turn, native, done):
    turn = await pipeline_turn(candidate={}, native=native, done=done)

    assert [kind for kind, _, _ in turn.events] == ["llm.done"]
    _, payload, saved = turn.events[0]
    assert payload == {
        "message_id": str(turn.ids.assistant_message_id),
        "tokens_in": None,
        "tokens_out": None,
        "cost_rub": None,
    }
    assert saved["outcome"] == turn.scope_commit["outcome"]
    assert saved["tokens_out"] == 0
    assert turn.trace[-2:] == ["llm.done", "clear_stream"]
    assert bool(turn.cards) is (not done)


@pytest.mark.parametrize("native", [False, True])
async def test_actual_agent_turn_forwards_owned_continuation_and_yields(monkeypatch, native):
    from yleum_api.core.config import get_settings
    from yleum_api.services import agent_native
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    settings = get_settings().model_copy(update={"use_native_agent": native})
    monkeypatch.setattr(agent_generation, "get_settings", lambda: settings)
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    marker = object()
    seen = []
    async def run(**kwargs):
        seen.append(kwargs)
        assert kwargs.get("continuation") is marker
        raise GenerationYielded(run_id=ids.run_id, checkpoint=None,
                               attempt_seq=1, phase="waiting_dependency")
    monkeypatch.setattr(agent_native, "run_native_build", run)
    monkeypatch.setattr(agent_generation.agent_builder, "run_agent_build", run)
    operations = SimpleNamespace(
        execute=AsyncMock(), emit=AsyncMock(), snapshot_files=None, continuation=marker,
    )
    plan = SimpleNamespace(system="s", user="u", model="synthetic", escalate_model=None,
        steps=3, stack_guide=None, skills=None, bare_stack=False)
    with pytest.raises(GenerationYielded):
        await agent_generation.execute_agent_turn(
            _agent_res=None, _is_edit=False, _max_has_generated_snapshot=False,
            _max_seed_files={}, _max_shell_enabled=False, baseline=SourceBaseline(None, None, {}),
            ids=ids, is_free=False, project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text="synthetic", runtime=GenerationRuntime(), plan=plan, operations=operations,
        )
    assert len(seen) == 1
    operations.execute.assert_not_awaited()


@pytest.mark.parametrize(
    ("bootstrap_gate", "prepared"),
    [
        (gate, prepared)
        for gate in (None, "green", "red", "missing", "adaptation", "completed", "replayed")
        for prepared in (False, True)
    ] + [("isolated_retry", False)],
)
async def test_v2_pipeline_enters_actual_turn_with_saved_preparation_and_driver(
    monkeypatch, prepared, bootstrap_gate
):
    from tests.test_generation_recovery_reconciliation import bootstrap
    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation.contracts import AgentPromptPlan, StackPrompt
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ContinuationClaim,
        decode_recovery,
    )
    from yleum_api.services.generation.recovery_pipeline import (
        BootstrapPreparation,
        CompletedAgentTurn,
    )
    from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError

    raw = bootstrap()
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    raw["identity"].update(
        run_id=str(ids.run_id), project_id=str(ids.project_id), user_id=str(ids.user_id)
    )
    claim = ContinuationClaim(
        run_id=ids.run_id,
        token=uuid4(),
        attempt_seq=0,
        worker_id="synthetic",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    raw["continuation_claim"] = claim.model_dump(mode="json")
    ref = CheckpointRef(object_key="synthetic/context", sha256="a" * 64)
    raw["context_ref"] = ref.model_dump(mode="json")
    raw["execution"]["execution_context_ref"] = ref.model_dump(mode="json")
    state = decode_recovery(raw)
    plan = AgentPromptPlan(
        "system", "Исходная цель", "synthetic", None, 5, "seed", None, "system", None, False
    )
    stack = StackPrompt("seed", None, None, None, "system", False)
    entry = {
        "version": 1,
        "classification": ["Исходная цель", False, False, False],
        "design_contract": None,
        "stack": stack._asdict(),
        "prompt_plan": plan.__dict__,
        "build_plan": None,
        "seed_files": {"src/core.ts": "trusted"},
        "initial_result": None,
        "edit_deadline": None,
    }
    context = {
        "schema_version": 1,
        "entry": "pipeline",
        "pipeline": {"baseline": {"files": {}}},
        "completed": {"agent_entry": entry} if prepared else {},
        "active_frame": None,
    }
    if bootstrap_gate == "completed":
        prepared = True
        state = state.model_copy(update={"execution": state.execution.model_copy(update={
            "stage": "finalization", "substep": "checkpoint_ready",
        })})
        context["completed"] = {"agent_entry": entry, "agent_turn": CompletedAgentTurn(
            AgentResult(done=True, summary="saved", files={}, steps=1), None,
            state.incomplete_response_budget,
        ).to_json()}
    elif bootstrap_gate == "replayed" and not prepared:
        # An old install-only completed starter stage must not grant readiness.
        context["completed"]["bootstrap_results"] = {
            "starter_apply": {"files": {"src/core.ts": "trusted"}, "result": None},
        }
    elif bootstrap_gate == "isolated_retry":
        draft_test = "tests/draft.test.mjs"
        saved_preparation = BootstrapPreparation(
            classification=("Исходная цель", False, False, False),
            adjacent_files={draft_test: "saved test"},
            isolated_test_paths=(draft_test,),
        ).model_dump(mode="json")
        context["completed"]["bootstrap_preparation"] = saved_preparation
        context["completed"]["bootstrap_results"] = {
            "starter_apply": {"files": {"src/core.ts": "trusted"}, "result": None},
            "starter_isolate": True,
            "adjacent_apply": True,
        }
    monkeypatch.setattr(ex, "load_execution_context", AsyncMock(return_value=deepcopy(context)))
    saves = []

    async def save(self, execution, *, context, phase="working"):
        saves.append(deepcopy(context))
        self.context = context
        self.state = self.state.model_copy(
            update={"execution": execution, "state_revision": self.state.state_revision + 1}
        )

    monkeypatch.setattr(ex.ExecutionDriver, "save", save)

    async def commit_prefix(factory, *, previous, execution, context):
        saves.append(deepcopy(context))
        return previous.model_copy(
            update={"execution": execution, "state_revision": previous.state_revision + 1}
        )

    monkeypatch.setattr(ex, "commit_execution_cursor", commit_prefix)
    from yleum_api.services.generation import recovery_tool_receipts

    monkeypatch.setattr(recovery_tool_receipts, "require_preparation_receipts", AsyncMock())
    handle = SimpleNamespace(
        snapshot_files=AsyncMock(return_value={"src/core.ts": "trusted"}),
        refresh_snapshot_files=AsyncMock(return_value={"src/core.ts": "trusted"}),
        current_identity=AsyncMock(),
        is_portable=lambda: True,
        control_workspace_id=uuid4(),
        control_fencing_epoch=1,
    )
    runtime = GenerationRuntime(handle=handle)
    check = AsyncMock(return_value=(None if bootstrap_gate == "missing" else
                                   SimpleNamespace(outcome=bootstrap_gate)))
    if bootstrap_gate is not None:
        runtime.coordinator = SimpleNamespace(
            ensure_bootstrap=check, source_edit_deadline=AsyncMock(return_value=None),
        )
        if bootstrap_gate == "adaptation":
            handle.prove_restoration_adaptation = AsyncMock()
    monkeypatch.setattr(agent_pipeline, "adjacent_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_pipeline, "_seed_saved_max_config", AsyncMock())
    bindings = SimpleNamespace(
        execute=AsyncMock(),
        probe_runtime=AsyncMock(),
        probe_build=AsyncMock(),
        preview_url=AsyncMock(),
        shell_enabled=False,
    )
    phases = {
        "classify_agent_turn": ("Исходная цель", False, False, False),
        "prepare_agent_runtime": (bindings, None),
        "require_named_behavior_readiness": None,
        "prepare_stack_prompt": stack,
        "prepare_agent_prompt": (plan, None),
        "stage_max_starter": ({"src/core.ts": "trusted"}, None),
        "stage_owner_fonts": {},
        "_load_turn_reference_images": [],
    }
    for name, value in phases.items():
        monkeypatch.setattr(agent_pipeline, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        agent_pipeline,
        "get_settings",
        lambda: SimpleNamespace(use_design_intelligence_plugin=False),
    )

    class ObservedTurn(BaseException):
        pass

    provider = AsyncMock()

    async def turn(**kwargs):
        driver = kwargs["operations"].continuation
        assert isinstance(driver, ex.ExecutionDriver)
        assert kwargs["plan"] == plan
        assert kwargs["_max_seed_files"] == {"src/core.ts": "trusted"}
        assert kwargs["reference_images"] == []
        if bootstrap_gate not in {None, "adaptation", "completed"}:
            check.assert_not_awaited()  # No SQL before ordinary reconciliation.
            await driver.provider({"version": 1, "kind": "native", "value": {"step": 0}}, provider)
        else:
            assert driver.before_fresh_provider is None
        raise ObservedTurn()

    monkeypatch.setattr(agent_pipeline, "execute_agent_turn", turn)
    if bootstrap_gate == "completed":
        monkeypatch.setattr(agent_pipeline, "recover_stopped_candidate",
                            AsyncMock(side_effect=ObservedTurn()))
    blocked_gate = bootstrap_gate in {"red", "missing", "replayed", "isolated_retry"}
    expected_error = ProjectCellInfrastructureError if blocked_gate else ObservedTurn
    with pytest.raises(expected_error):
        await agent_pipeline.run_agent_generation(
            _consume_free_generation=AsyncMock(),
            baseline=SourceBaseline(None, None, {}),
            capacity_dispatch_token=None,
            factory=None,
            force_model=None,
            ids=ids,
            is_free=False,
            model_id="synthetic",
            orchestrate=bootstrap_gate == "isolated_retry",
            progress=SimpleNamespace(emit_agent_event=AsyncMock()),
            project_info=ProjectGenerationFacts(
                "max_miniapp" if bootstrap_gate else "blank", "synthetic", "Synthetic", None,
                None, False, "ru", False, "", ""
            ),
            prompt_text="Исходная цель",
            runtime=runtime,
            selected_elements=None,
            _resume_state=state,
        )
    if blocked_gate:
        check.assert_awaited_once()
        provider.assert_not_awaited()
    elif bootstrap_gate == "green":
        check.assert_awaited_once()
        provider.assert_awaited_once()
    else:
        check.assert_not_awaited()
    for name in (
        "classify_agent_turn",
        "prepare_stack_prompt",
        "prepare_agent_prompt",
        "stage_max_starter",
        "stage_owner_fonts",
    ):
        expected = int(not prepared)
        if bootstrap_gate in {"replayed", "isolated_retry"} and name == "stage_max_starter":
            expected = 0
        if bootstrap_gate == "isolated_retry" and name == "classify_agent_turn":
            expected = 0
        assert getattr(agent_pipeline, name).await_count == expected
    assert agent_pipeline.prepare_agent_runtime.await_count == 1  # Rebind live owned handle.
    assert agent_pipeline._load_turn_reference_images.await_count == int(
        bootstrap_gate != "completed",
    )
    if not prepared:
        expected_entry = (
            {**entry, "restored_files": {"src/core.ts": "trusted"}}
            if bootstrap_gate == "isolated_retry" else entry
        )
        assert saves[-1]["completed"]["agent_entry"] == expected_entry
        if bootstrap_gate == "isolated_retry":
            assert saves[0]["completed"]["bootstrap_preparation"] == saved_preparation
            assert "agent_entry" not in saves[0]["completed"]
            assert saves[-1]["completed"]["bootstrap_preparation"] == {
                key: value
                for key, value in saved_preparation.items()
                if key != "isolated_test_paths"
            }
