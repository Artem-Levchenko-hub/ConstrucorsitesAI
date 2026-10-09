"""Pipeline terminal events agree with the committed product outcome.

External generation phases are fixed inputs; source validation, failure recording
and empty-candidate publication run unchanged. The session double exposes only
committed state at the event boundary, without requiring a PostgreSQL server.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.services import restorations
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_generation, agent_pipeline, agent_publication
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)


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
        baseline=SimpleNamespace(sha=None),
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
            baseline=SimpleNamespace(sha=None), ids=None, is_free=False,
            project_info=None, prompt_text="", runtime=GenerationRuntime(),
            plan=None, operations=None,
        )


@pytest.fixture
def pipeline_turn(monkeypatch):
    async def run(*, candidate, done=True, native=True, verification_failed=False):
        ids = GenerationIds(*(uuid4() for _ in range(5)))
        run_row = GenerationRun(id=ids.run_id, status="running", agent_state={})
        message = Message(id=ids.assistant_message_id, content="", tokens_out=None)
        committed = {}
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
                if model is GenerationRun:
                    assert key == ids.run_id
                    return run_row
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
            "classify_agent_turn": ("Change title", False, True, True),
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

        await agent_pipeline.run_agent_generation(
            _consume_free_generation=forbidden,
            baseline=SourceBaseline(uuid4(), "baseline-sha", {"src/page.tsx": "original"}),
            capacity_dispatch_token=None,
            factory=Session,
            force_model=None,
            ids=ids,
            is_free=False,
            model_id="fixture-model",
            orchestrate=False,
            progress=SimpleNamespace(emit_agent_event=forbidden, steps=[]),
            project_info=ProjectGenerationFacts(
                "max_miniapp", "fixture", "Fixture", None, None, False, "ru", False, "", ""
            ),
            prompt_text="Change title",
            runtime=GenerationRuntime(),
            selected_elements=None,
        )
        return SimpleNamespace(
            ids=ids, committed=committed, trace=trace, events=events, cards=cards
        )

    return run


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
    assert turn.trace == ["commit", "commit", "llm.error", "clear_stream"]
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
    assert saved["outcome"] == {}
    assert saved["tokens_out"] == 0
    assert turn.trace[-2:] == ["llm.done", "clear_stream"]
    assert bool(turn.cards) is (not done)
