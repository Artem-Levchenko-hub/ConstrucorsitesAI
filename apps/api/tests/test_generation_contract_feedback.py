from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime
from yleum_api.services.integration_generation import generation_context
from yleum_api.services.max_data_evolution import build_max_agent_guide


@pytest.mark.asyncio
@pytest.mark.parametrize("portable", [False, True])
async def test_live_integration_contract_reaches_final_native_prompt(portable):
    handle = SimpleNamespace(
        capabilities={"portable_machine": portable},
        snapshot_files=AsyncMock(return_value={".omnia/cell.json": "{}"}),
    )
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: ["amocrm"])),
        scalar=AsyncMock(return_value=False),
    )
    context = await generation_context(session, uuid4())
    guide = await build_max_agent_guide(
        "LEGACY-ONLY",
        handle,
        integration_guide=context,
    )
    prompt = agent_native.native_system_prompt(guide)
    assert "CONNECTED BUSINESS INTEGRATIONS" in prompt
    assert "getYleumLeads()" in prompt
    assert "getYleumAppConfig()" in prompt
    assert "createMaxAction/getMaxActions" in prompt
    assert "personal-data consent" in prompt
    assert "Never fabricate user" in prompt
    assert "Do not turn on unrequested features" in prompt
    assert ("LEGACY-ONLY" in prompt) is not portable
    assert prompt.count("MAX DATA EVOLUTION POLICY v1") == 1


@pytest.mark.asyncio
async def test_coordinator_fast_check_uses_fixed_accepted_schema_baseline():
    from yleum_api.services.generation.agent_runtime import checked_fast_check

    baseline = {
        "src/lib/db/schema.ts": "old",
        "drizzle/0000_core.sql": "CREATE TABLE core(id int);",
    }
    candidate = {**baseline, "src/lib/db/schema.ts": "new"}
    handle = SimpleNamespace(snapshot_files=AsyncMock(side_effect=lambda: dict(candidate)))
    coordinator = SimpleNamespace(
        fast_check=AsyncMock(
            return_value=SimpleNamespace(outcome="green", redacted_detail="verified")
        )
    )
    runtime = GenerationRuntime(handle=handle, coordinator=coordinator, migration_baseline=baseline)
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    for _ in range(2):
        result = await checked_fast_check(runtime, ids)
        assert result["ok"] is False
        assert "changed without a new canonical" in result["detail"]
        assert result["environment_mutated"] is False
    coordinator.fast_check.assert_not_awaited()
    assert baseline["src/lib/db/schema.ts"] == "old"
    # The model can repair the same candidate; no new run or SQL is needed.
    candidate["drizzle/0001_extend.sql"] = "ALTER TABLE core ADD COLUMN label text;"
    assert (await checked_fast_check(runtime, ids))["ok"] is True
    coordinator.fast_check.assert_awaited_once()
    candidate["drizzle/0000_core.sql"] = "CREATE TABLE different(id int);"
    assert "immutable" in (await checked_fast_check(runtime, ids))["detail"]
    assert coordinator.fast_check.await_count == 1


@pytest.mark.asyncio
async def test_native_build_and_done_get_contract_feedback_and_repair_in_same_run(monkeypatch):
    from yleum_api.core.config import get_settings
    from yleum_api.services.generation.agent_runtime import (
        checked_fast_check,
        guard_native_source_contract,
    )

    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    get_settings.cache_clear()
    baseline = {
        "src/lib/db/schema.ts": "old",
        "drizzle/0000_core.sql": "CREATE TABLE core(id int);",
    }
    candidate = {**baseline, "src/lib/db/schema.ts": "new", "src/app/page.tsx": "product"}
    handle = SimpleNamespace(snapshot_files=AsyncMock(side_effect=lambda: dict(candidate)))
    runtime = GenerationRuntime(
        handle=handle,
        migration_baseline=baseline,
        coordinator=SimpleNamespace(
            fast_check=AsyncMock(
                return_value=SimpleNamespace(outcome="green", redacted_detail="ok")
            ),
        ),
    )
    ids = GenerationIds(*(uuid4() for _ in range(5)))

    async def execute(action):
        if action.name == "write_file":
            candidate[action.path] = action.args["content"]
            return {"ok": True}
        return await checked_fast_check(runtime, ids)

    checked_execute, completion = await guard_native_source_contract(
        runtime, ids, execute, lambda *_: None
    )
    assert "changed without a new canonical" in completion({}, {})
    turns = iter(
        [
            {"content": [{"type": "tool_use", "id": "premature", "name": "build", "input": {}}]},
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "repair",
                        "name": "write_file",
                        "input": {
                            "path": "drizzle/0001_extend.sql",
                            "content": "ALTER TABLE core ADD COLUMN label text;",
                        },
                    }
                ]
            },
            {"content": [{"type": "tool_use", "id": "done", "name": "done", "input": {}}]},
        ]
    )
    transcripts = []

    async def provider(*args, **kwargs):
        transcripts.append(str(args[2]))
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="finish product",
        portable_cell=True,
        execute=checked_execute,
        completion_check=completion,
        initial_files=candidate,
        max_steps=3,
    )
    assert "changed without a new canonical" in transcripts[1]
    assert result.done
    assert runtime.coordinator.fast_check.await_count == 1
    assert runtime.migration_baseline == baseline
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_prepare_stack_keeps_ready_contract_after_portable_replacement(monkeypatch):
    from yleum_api.services.generation import agent_preparation

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return None

        async def scalars(self, *args):
            return SimpleNamespace(all=lambda: ["amocrm"])

        async def scalar(self, *args):
            return False

    monkeypatch.setattr(
        agent_preparation,
        "get_settings",
        lambda: SimpleNamespace(
            use_design_mood=False,
            use_skill_injection=False,
        ),
    )
    monkeypatch.setattr(agent_preparation, "_build_agent_seed_parts", AsyncMock(return_value=[]))
    runtime = GenerationRuntime(
        handle=SimpleNamespace(
            capabilities={"portable_machine": True},
            snapshot_files=AsyncMock(return_value={".omnia/cell.json": "{}"}),
        )
    )
    stack = await agent_preparation.prepare_stack_prompt(
        _design_contract=None,
        factory=Session,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        orchestrate=True,
        project_info=SimpleNamespace(
            template="max_miniapp", memory_context="", restoration_context=""
        ),
        prompt_text="UNTRUSTED_USER_INSTRUCTION",
        runtime=runtime,
    )
    prompt = agent_native.native_system_prompt(stack.guide, stack.skills)
    assert "getYleumLeads()" in prompt and "getYleumAppConfig()" in prompt
    assert "EXTENSIBLE MAIN STACK" in prompt
    assert "UNTRUSTED_USER_INSTRUCTION" not in prompt


@pytest.mark.asyncio
async def test_attested_adaptation_preserves_schema_exemption_but_not_migration_rewrites(
    monkeypatch,
):
    from yleum_api.services.generation import agent_verification
    from yleum_api.services.generation.agent_runtime import migration_feedback

    proof = AsyncMock(return_value=True)
    monkeypatch.setattr(agent_verification, "_preserves_adaptation_database", proof)
    baseline = {
        "src/lib/db/schema.ts": "old",
        "drizzle/0000_core.sql": "CREATE TABLE core(id int);",
    }
    candidate = {**baseline, "src/lib/db/schema.ts": "aligned existing DB"}
    runtime = GenerationRuntime(
        migration_baseline=baseline,
        handle=SimpleNamespace(
            snapshot_files=AsyncMock(side_effect=lambda: dict(candidate)),
        ),
    )
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    assert await migration_feedback(runtime, ids) is None
    proof.assert_awaited_once_with(runtime, ids)
    candidate["drizzle/0000_core.sql"] = "changed"
    assert "immutable" in await migration_feedback(runtime, ids)


@pytest.mark.asyncio
async def test_final_candidate_keeps_checked_retry_tree_including_preexisting_migration():
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation.agent_generation import complete_empty_legacy_build
    from yleum_api.services.generation.agent_runtime import migration_feedback
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    baseline = {"src/lib/db/schema.ts": "accepted", "src/obsolete.ts": "remove me"}
    checked = {
        "src/lib/db/schema.ts": "new schema",
        "drizzle/0002_retry.sql": "CREATE TABLE coffee_requests(id int);",
        "src/app/page.tsx": "new product",
    }
    # The retained failed workspace already had 0002 at executor bootstrap.
    # Its export diff omits that SQL even though the checked tree contains it.
    result = AgentResult(
        done=True,
        summary="done",
        steps=3,
        files={
            "src/lib/db/schema.ts": "new schema",
            "src/app/page.tsx": "new product",
        },
    )
    runtime = GenerationRuntime(
        migration_baseline=dict(baseline),
        handle=SimpleNamespace(
            snapshot_files=AsyncMock(return_value=checked),
        ),
    )
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    assert await migration_feedback(runtime, ids) is None
    _, files, _ = await complete_empty_legacy_build(
        _agent_res=result,
        _is_edit=False,
        _max_seed_files={"src/lib/db/schema.ts": "accepted"},
        ids=ids,
        runtime=runtime,
        plan=SimpleNamespace(),
        operations=SimpleNamespace(),
    )
    candidate = dict(baseline)
    for path, content in files.items():
        if content == "":
            candidate.pop(path, None)
        else:
            candidate[path] = content
    assert candidate == checked
    assert not max_migration_contract_errors(baseline, candidate)
    assert "drizzle/0002_retry.sql" not in result.files  # Executor diff semantics unchanged.
    candidate["drizzle/0002_retry.sql"] = "altered"
    assert "immutable" in ";".join(max_migration_contract_errors(checked, candidate))


@pytest.mark.asyncio
@pytest.mark.parametrize("readonly", [False, True])
async def test_snapshot_candidate_preserves_managed_only_noop_semantics(readonly):
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation.agent_finalization import (
        unchanged_candidate_before_finalization,
    )
    from yleum_api.services.generation.agent_generation import complete_empty_legacy_build

    baseline = {"src/app/page.tsx": "same", "src/lib/omnia/integration-client.ts": "old"}
    snapshot = {**baseline, "src/lib/omnia/integration-client.ts": "new managed SDK"}
    runtime = GenerationRuntime(
        migration_baseline=dict(baseline),
        handle=SimpleNamespace(
            snapshot_files=AsyncMock(return_value=snapshot),
        ),
    )
    _, files, _ = await complete_empty_legacy_build(
        _agent_res=AgentResult(done=True, summary="inspection", steps=2, files={}),
        _is_edit=True,
        _max_seed_files={},
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        runtime=runtime,
        plan=SimpleNamespace(),
        operations=SimpleNamespace(),
    )
    assert files == snapshot
    verdict = unchanged_candidate_before_finalization(
        baseline_files=baseline,
        workspace_files=files,
        requires_source_change=not readonly,
        message="inspection",
    )
    assert verdict is not None
    assert (verdict.failure is None) is readonly
    assert verdict.files == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rewrite", "delete"])
async def test_snapshot_candidate_does_not_legalize_accepted_migration_changes(operation):
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation.agent_generation import complete_empty_legacy_build
    from yleum_api.services.generation.agent_runtime import migration_feedback
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    baseline = {"drizzle/0000_core.sql": "CREATE TABLE core(id int);"}
    snapshot = {} if operation == "delete" else {"drizzle/0000_core.sql": "rewritten"}
    runtime = GenerationRuntime(
        migration_baseline=dict(baseline),
        handle=SimpleNamespace(
            snapshot_files=AsyncMock(return_value=snapshot),
        ),
    )
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    assert await migration_feedback(runtime, ids)
    _, files, _ = await complete_empty_legacy_build(
        _agent_res=AgentResult(done=True, summary="done", steps=2, files={}),
        _is_edit=True,
        _max_seed_files={},
        ids=ids,
        runtime=runtime,
        plan=SimpleNamespace(),
        operations=SimpleNamespace(),
    )
    candidate = {k: v for k, v in {**baseline, **files}.items() if v != ""}
    assert candidate == snapshot
    assert max_migration_contract_errors(baseline, candidate)
    assert runtime.migration_baseline == baseline
