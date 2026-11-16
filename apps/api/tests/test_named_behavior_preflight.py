"""Named behavior readiness fails before the generation reaches any provider."""

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.services import max_behavior_proof as proof
from yleum_api.services.generation import agent_pipeline
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline

from .test_max_behavior_proof import previous_probe_contract

REQUEST = "Add a visible header Light/Dark theme control and local persistence."


@pytest.fixture
def before_provider(monkeypatch):
    async def run(request=REQUEST, *, driver=None, coordinator_enabled=True, saved=False,
                  authoritative=None, corrupt_binding=False, saved_contract=None):
        ids = GenerationIds(*(uuid4() for _ in range(5)))
        original = authoritative or request
        contract = saved_contract or proof.required_contract(original, template="max_miniapp")
        record = NS(id=ids.run_id, status="running",
                    prompt_hash=hashlib.sha256(original.encode()).hexdigest(),
                    user_message_id=ids.user_message_id, agent_state={},
                    project_id=ids.project_id, user_id=ids.user_id)
        if saved:
            record.agent_state[proof.STATE_KEY] = contract.to_json()
            from yleum_api.services.build_spec import FidelitySpec
            from yleum_api.services.generation.recovery_requirements import (
                store_frozen_requirements,
            )
            store_frozen_requirements(record, original, FidelitySpec(), contract)
        if corrupt_binding:
            record.project_id = uuid4()
        project_id = ids.project_id
        session = NS(commit=AsyncMock(), get=AsyncMock(side_effect=lambda model, *_a, **_k:
            record if model is GenerationRun else NS(owner_id=ids.user_id, discovery_spec=None)
            if model is Project else NS(
                project_id=project_id, role="user", content=original, selected_elements=None,
            )))

        @asynccontextmanager
        async def factory():
            yield session

        coordinator = NS(session_factory=factory, _locked_run=AsyncMock(return_value=record),
                         project_id=project_id, generation_run_id=ids.run_id,
                         behavior_driver=driver)
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


async def test_full_original_scope_is_frozen_before_planner_or_provider(before_provider):
    from yleum_api.services.generation import recovery_requirements as rr

    original = "task_density_v1; also export payroll to my private system"
    result = await before_provider("Synthetic repair context", authoritative=original)
    requirements = rr.frozen_requirements(result.record)
    assert any(r.required and r.check_kind == "manual" and r.text == original
               for r in requirements)
    assert any(not r.required and r.text == original for r in requirements)
    assert result.record.prompt_hash == hashlib.sha256(original.encode()).hexdigest()


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


async def test_previous_unobserved_probe_stops_before_provider(before_provider):
    legacy = previous_probe_contract(REQUEST)
    driver = proof.ControllerBehaviorDriver(
        AsyncMock(), AsyncMock(), frozenset(legacy.capabilities),
    )
    result = await before_provider(driver=driver, saved=True, saved_contract=legacy)
    assert str(result.error) == "BEHAVIOR_READINESS_UNAVAILABLE: BEHAVIOR_PROBE_UPGRADE_REQUIRED"
    result.provider.assert_not_awaited()
    result.session.commit.assert_not_awaited()
    assert result.record.agent_state[proof.STATE_KEY] == legacy.to_json()


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


async def test_operator_combined_browser_preset_admits_task_density(before_provider, monkeypatch):
    from yleum_api.services import behavior_driver_configuration as config
    from yleum_api.services.max_behavior_ui_contract import named_ui_guidance

    # Installation/pinning are exercised separately; use the real registry and driver factory.
    monkeypatch.setattr(config, "_pin", lambda *_args: None)
    driver = config.configured_behavior_driver(NS(), NS(
        max_behavior_browser_executable="/test-only-browser", env="test",
        max_behavior_browser_sha256="a" * 64,
        max_behavior_adapter_registry=json.dumps({
            "max-miniapp-nextjs": {"preset": "coffee-summary-header-theme-ui-v1"},
        }),
        max_behavior_vendor_asset_registry="",
    ))
    result = await before_provider(
        "Добавь обычную и компактную плотность для списка и доски задач", driver=driver,
    )
    assert result.admitted, str(result.error)
    assert proof.RECEIPT_KEY not in result.record.agent_state
    result.provider.assert_not_awaited()
    contract = proof.contract_from_json(result.record.agent_state[proof.STATE_KEY])
    assert contract.capabilities == ("task_density_v1",)
    adapter = config._adapter({"preset": "coffee-summary-header-theme-ui-v1"}).density
    assert adapter is not None
    guidance = named_ui_guidance(contract)
    for value in (
        adapter.normal, adapter.compact, adapter.filter, adapter.filter_value, adapter.storage_key,
        adapter.controls, adapter.list_view.control, adapter.list_view.container,
        adapter.list_view.cards, adapter.board_view.control, adapter.board_view.container,
        adapter.board_view.cards,
    ):
        assert value in guidance
    assert "UNSUPPORTED" not in guidance


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
    from yleum_api.services.generation import lifecycle, recovery_execution
    from yleum_api.services.generation.agent_runtime import require_named_behavior_readiness

    project_id, run_id, user_id, user_message_id, assistant_id = (uuid4() for _ in range(5))
    assistant = NS(content="", tokens_out=None, tokens_in=None)
    rows = {
        (Project, project_id): NS(template="max_miniapp", slug="synthetic", name="Synthetic",
                                 design_preset_id=None, image_gen_enabled=False,
                                 discovery_spec=None, language="ru", source="native"),
        (Message, assistant_id): assistant,
        (GenerationRun, run_id): NS(
            id=run_id, project_id=project_id, user_id=user_id,
            user_message_id=user_message_id, assistant_message_id=assistant_id,
            status="running", agent_state={},
        ),
    }
    for module in (lifecycle, recovery_execution):
        monkeypatch.setattr(module, "get_engine", lambda: object())
        monkeypatch.setattr(module, "async_sessionmaker", lambda *_a, **_k: lambda: Session(rows))
    assert await recovery_execution.recovery_failure_outcome(run_id) == "legacy"
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
