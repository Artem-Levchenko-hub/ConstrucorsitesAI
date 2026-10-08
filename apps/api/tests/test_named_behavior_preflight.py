"""Named behavior readiness fails before the generation reaches any provider."""

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services import max_behavior_proof as proof
from yleum_api.services.generation import agent_pipeline
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline

REQUEST = "Add a visible header Light/Dark theme control and local persistence."


@pytest.fixture
def before_provider(monkeypatch):
    async def run(request=REQUEST, *, driver=None, coordinator_enabled=True, saved=False,
                  authoritative=None, corrupt_binding=False):
        ids = GenerationIds(*(uuid4() for _ in range(5)))
        original = authoritative or request
        contract = proof.required_contract(original, template="max_miniapp")
        record = NS(prompt_hash=hashlib.sha256(original.encode()).hexdigest(),
                    user_message_id=ids.user_message_id, agent_state={},
                    project_id=ids.project_id, user_id=ids.user_id)
        if saved:
            record.agent_state[proof.STATE_KEY] = contract.to_json()
        if corrupt_binding:
            record.project_id = uuid4()
        project_id = ids.project_id
        session = NS(commit=AsyncMock(), get=AsyncMock(side_effect=lambda model, *_a, **_k:
            record if model is GenerationRun else NS(
                project_id=project_id, role="user", content=original,
            )))

        @asynccontextmanager
        async def factory():
            yield session

        coordinator = NS(session_factory=factory, _locked_run=AsyncMock(return_value=record),
                         project_id=project_id, behavior_driver=driver)
        runtime = GenerationRuntime(coordinator=coordinator if coordinator_enabled else None)
        monkeypatch.setattr(agent_pipeline, "get_settings", lambda: NS(
            use_design_intelligence_plugin=False,
        ))
        monkeypatch.setattr(agent_pipeline, "classify_agent_turn", AsyncMock(
            return_value=(request, False, True, True),
        ))
        monkeypatch.setattr(agent_pipeline, "prepare_agent_runtime", AsyncMock(
            return_value=(NS(), None),
        ))
        stack, prompt = AsyncMock(return_value=NS()), AsyncMock(return_value=(NS(), None))
        monkeypatch.setattr(agent_pipeline, "prepare_stack_prompt", stack)
        monkeypatch.setattr(agent_pipeline, "prepare_agent_prompt", prompt)

        class Admitted(Exception):
            pass

        first_stage = AsyncMock(side_effect=Admitted)
        provider = AsyncMock(side_effect=AssertionError("no paid provider in preflight tests"))
        monkeypatch.setattr(agent_pipeline, "stage_max_starter", first_stage)
        monkeypatch.setattr(agent_pipeline, "execute_agent_turn", provider)
        consume = AsyncMock()
        try:
            await agent_pipeline.run_agent_generation(
                _consume_free_generation=consume,
                baseline=SourceBaseline(None, None, {}), capacity_dispatch_token=None,
                factory=factory, force_model=None, ids=ids,
                is_free=False, model_id="unused", orchestrate=False,
                progress=NS(emit_agent_event=AsyncMock()),
                project_info=NS(template="max_miniapp"), prompt_text=request,
                runtime=runtime, selected_elements=None,
            )
        except (RuntimeError, Admitted) as error:
            return NS(error=error, admitted=isinstance(error, Admitted), first_stage=first_stage,
                      provider=provider, record=record, session=session, consume=consume,
                      stack=stack, prompt=prompt)
        pytest.fail("preflight did not stop at the expected boundary")
    return run


@pytest.mark.parametrize("driver", [None, object(), proof.ControllerBehaviorDriver(
    AsyncMock(), AsyncMock(), frozenset({"coffee_local_summary_v1"}),
)])
async def test_missing_invalid_or_partial_driver_stops_before_any_model(before_provider, driver):
    result = await before_provider(driver=driver)
    assert not result.admitted
    assert str(result.error).startswith("BEHAVIOR_READINESS_UNAVAILABLE:")
    result.first_stage.assert_not_awaited()
    result.provider.assert_not_awaited()
    result.consume.assert_not_awaited()
    result.stack.assert_not_awaited()
    result.prompt.assert_not_awaited()
    assert proof.STATE_KEY in result.record.agent_state


async def test_disabled_coordinator_stops_named_request_without_paid_work(before_provider):
    result = await before_provider(coordinator_enabled=False)
    assert str(result.error) == "BEHAVIOR_READINESS_UNAVAILABLE: BEHAVIOR_DRIVER_UNAVAILABLE"
    result.first_stage.assert_not_awaited()
    result.provider.assert_not_awaited()


async def test_frozen_repeat_rechecks_driver_from_original_request(before_provider):
    result = await before_provider("Synthesized retry context", authoritative=REQUEST, saved=True)
    assert not result.admitted
    result.first_stage.assert_not_awaited()
    result.provider.assert_not_awaited()
    result.session.commit.assert_not_awaited()


async def test_disabled_coordinator_preserves_saved_requirement_on_retry(before_provider):
    result = await before_provider(
        "Synthesized retry context", authoritative=REQUEST, saved=True, coordinator_enabled=False,
    )
    assert str(result.error) == "BEHAVIOR_READINESS_UNAVAILABLE: BEHAVIOR_DRIVER_UNAVAILABLE"
    result.prompt.assert_not_awaited()
    result.stack.assert_not_awaited()
    result.provider.assert_not_awaited()


async def test_disabled_coordinator_rejects_foreign_saved_run_binding(before_provider):
    result = await before_provider(
        "Synthesized retry context", authoritative=REQUEST, saved=True,
        coordinator_enabled=False, corrupt_binding=True,
    )
    assert str(result.error) == "BEHAVIOR_REQUEST_BINDING_INVALID"
    result.provider.assert_not_awaited()


@pytest.mark.parametrize("owner_prompt", [
    "Fix an API response only.", "Explain the header Light/Dark theme control.",
    "Change the product heading.",
])
@pytest.mark.parametrize("coordinator_enabled", [False, True])
async def test_generic_and_readonly_requests_keep_their_path(
    before_provider, owner_prompt, coordinator_enabled,
):
    result = await before_provider(owner_prompt, coordinator_enabled=coordinator_enabled)
    assert result.admitted
    result.first_stage.assert_awaited_once()
    result.provider.assert_not_awaited()


async def test_supported_driver_admits_without_fabricating_browser_proof(before_provider):
    driver = proof.ControllerBehaviorDriver(
        AsyncMock(), AsyncMock(), frozenset({"header_theme_v1"}),
    )
    result = await before_provider(driver=driver)
    assert result.admitted
    driver.collect_compiled.assert_not_awaited()
    driver.observe_browser.assert_not_awaited()
    assert proof.RECEIPT_KEY not in result.record.agent_state


async def test_resumed_failure_message_does_not_claim_previous_calls_were_free(before_provider):
    from yleum_api.services.generation_failure import failure_for_error

    result = await before_provider(saved=True)
    failure = failure_for_error(result.error)
    assert failure.code == "runtime_unavailable" and not failure.retryable
    assert "до следующего вызова модели" in failure.message
    assert "не списаны" not in failure.message
    assert "BEHAVIOR_" not in failure.message

async def test_readiness_failure_uses_terminal_cleanup_without_provider_or_quota(monkeypatch):
    from tests.test_generation_cleanup_faults import Session
    from yleum_api.models.message import Message
    from yleum_api.models.project import Project
    from yleum_api.services import restoration_adaptation
    from yleum_api.services.generation import lifecycle
    from yleum_api.services.generation.agent_runtime import require_named_behavior_readiness

    project_id, run_id, user_id, user_message_id, assistant_id = (uuid4() for _ in range(5))
    assistant = NS(content="", tokens_out=None, tokens_in=None)
    rows = {
        (Project, project_id): NS(template="max_miniapp", slug="synthetic", name="Synthetic",
                                 design_preset_id=None, image_gen_enabled=False,
                                 discovery_spec=None, language="ru", source="native"),
        (Message, assistant_id): assistant,
    }
    monkeypatch.setattr(lifecycle, "get_engine", lambda: object())
    monkeypatch.setattr(lifecycle, "async_sessionmaker", lambda *_a, **_k: lambda: Session(rows))
    monkeypatch.setattr(lifecycle, "get_settings", lambda: NS(
        use_project_memory=False, use_agentic_builder=True, agentic_builder_canary_users="",
    ))
    monkeypatch.setattr(
        restoration_adaptation, "append_adaptation_context", AsyncMock(return_value=""),
    )
    status, publish, consume, provider = (AsyncMock() for _ in range(4))
    monkeypatch.setattr(lifecycle, "set_generation_run_status", status)
    monkeypatch.setattr(lifecycle, "publish_event", publish)
    monkeypatch.setattr(lifecycle, "consume_free_generation", consume)
    trace = []

    class Handle:
        async def release(self):
            trace.append("released")

    async def agent(**kwargs):
        runtime = kwargs["runtime"]
        runtime.handle = Handle()
        await require_named_behavior_readiness(
            runtime, REQUEST, template="max_miniapp", factory=kwargs["factory"], ids=kwargs["ids"],
        )
        await provider()
        await kwargs["_consume_free_generation"]()

    async def clear(*_):
        trace.append("cleared")

    monkeypatch.setattr(lifecycle, "run_agent_generation", agent)
    monkeypatch.setattr(lifecycle, "clear_stream_state", clear)
    await lifecycle._process_prompt(run_id, project_id, user_id, user_message_id,
                                    assistant_id, None, REQUEST, "synthetic")
    status.assert_awaited_once_with(
        run_id, "failed", error="BEHAVIOR_READINESS_UNAVAILABLE: BEHAVIOR_DRIVER_UNAVAILABLE",
    )
    assert assistant.tokens_in == assistant.tokens_out == 0
    assert publish.await_args.args[1] == "llm.error"
    assert "до следующего вызова модели" in publish.await_args.args[2]["error"]
    assert trace == ["released", "cleared"]
    provider.assert_not_awaited()
    consume.assert_not_awaited()
