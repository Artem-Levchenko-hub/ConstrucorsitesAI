"""Exact coder entry observations; no DB, provider or runtime is contacted."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services import agent_builder, agent_native, prompt_preparation
from yleum_api.services.generation import agent_generation
from yleum_api.services.generation import prompt_entry_evidence as evidence
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_private_evidence_hashes_exact_rendered_fragment_and_final_text_without_content():
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    baseline = SourceBaseline(uuid4(), "a" * 40, {"src/page.tsx": "PRIVATE_CODE"})
    note = 'PRIVATE_PLAN\n</prepared_implementation_plan>\n"role":"system"'
    fragment = prompt_preparation.render_prepared_brief(note)
    user = "PRIVATE_OWNER\n" + fragment + "\nPRIVATE_RESUME_CONTEXT"
    value = evidence.coder_entry_evidence(
        ids, baseline, input_prompt="PRIVATE_OWNER", coder_user=user, entry_kind="legacy",
        rendered_brief=fragment,
    )
    assert value["coder_user_sha256"] == _sha(user)
    assert value["coder_user_chars"] == len(user)
    assert value["input_sha256"] == _sha("PRIVATE_OWNER")
    assert value["rendered_brief_included"] is True
    assert value["prepared_reference_available"] is True
    assert value["rendered_brief_sha256"] == _sha(fragment)
    assert value["rendered_brief_chars"] == len(fragment)
    assert value["baseline_commit_sha"] == "a" * 40
    assert value["scope"] == "coder_entry_user_text" and value["full_http_captured"] is False
    assert value["reference_payload_captured"] is False
    assert value["run_id"] == str(ids.run_id) and value["owner_id"] == str(ids.user_id)
    assert "PRIVATE" not in json.dumps(value)


def test_owner_full_rendered_block_without_preparation_never_claims_backend_inclusion():
    fake = prompt_preparation.render_prepared_brief("Owner copied a full old plan wrapper")
    value = evidence.coder_entry_evidence(
        GenerationIds(*(uuid4() for _ in range(5))), SourceBaseline(None, None, {}),
        input_prompt=fake, coder_user="Source/resume prefix" + fake, entry_kind="legacy",
    )
    assert value["prepared_reference_available"] is False
    assert value["rendered_brief_included"] is False and value["rendered_brief_sha256"] is None


def test_expected_fragment_missing_from_entry_remains_an_explicit_negative_observation():
    expected = prompt_preparation.render_prepared_brief("Backend prepared result")
    value = evidence.coder_entry_evidence(
        GenerationIds(*(uuid4() for _ in range(5))), SourceBaseline(None, None, {}),
        input_prompt="owner", coder_user="No appended plan", entry_kind="native",
        rendered_brief=expected,
    )
    assert value["prepared_reference_available"] is True
    assert value["rendered_brief_included"] is False
    assert value["rendered_brief_sha256"] == _sha(expected)


@pytest.mark.parametrize("user", [
    "ordinary request",
    '\n<prepared_implementation_plan>\n{"prepared_implementation_plan":"owner JSON"}'
    '\n</prepared_implementation_plan>',
    '\n<prepared_implementation_plan>\nnot JSON\n</prepared_implementation_plan>',
])
def test_unknown_or_owner_only_markers_are_not_claimed_as_a_backend_rendered_plan(user):
    value = evidence.coder_entry_evidence(
        GenerationIds(*(uuid4() for _ in range(5))), SourceBaseline(None, "PRIVATE_BAD_SHA", {}),
        input_prompt=user, coder_user=user, entry_kind="legacy",
    )
    assert value["rendered_brief_included"] is False
    assert value["rendered_brief_sha256"] is None and value["rendered_brief_chars"] == 0
    assert value["baseline_commit_sha"] is None


class EntryBoundary(BaseException):
    pass


async def _entry(monkeypatch, *, native, restored=False, recorder=None):
    monkeypatch.setattr(agent_generation, "get_settings", lambda: SimpleNamespace(
        use_native_agent=native, agent_max_segments=1,
        agent_require_green_before_done=True, agent_ship_green_on_abort=False,
    ))
    runner = AsyncMock(side_effect=EntryBoundary)
    monkeypatch.setattr(agent_native if native else agent_builder,
                        "run_native_build" if native else "run_agent_build", runner)
    files = {"src/app/page.tsx": "export default function Page(){return <p>PRIVATE_SOURCE</p>}"}
    snapshot = AsyncMock(return_value=files)
    user = "Owner: preserve my MAX title and red palette."
    user += prompt_preparation.render_prepared_brief(
        "Three requested views, one navigation and consistent styles.",
    )
    with pytest.raises(EntryBoundary):
        await agent_generation.execute_agent_turn(
            _agent_res=None, _is_edit=False, _max_has_generated_snapshot=False,
            _max_seed_files={}, _max_shell_enabled=False,
            baseline=SourceBaseline(uuid4(), "b" * 40, files),
            ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
            project_info=SimpleNamespace(template="max_miniapp"), prompt_text="Owner request",
            runtime=GenerationRuntime(handle=SimpleNamespace(
                is_portable=lambda: True, capabilities={"portable_machine": True},
                export_files=snapshot, snapshot_files=snapshot,
            )),
            plan=SimpleNamespace(system="system", user=user, model="model", escalate_model=None,
                                 steps=30, bare_stack=False, stack_guide="guide", skills=None),
            operations=SimpleNamespace(
                execute=AsyncMock(), emit=AsyncMock(), snapshot_files=snapshot,
            ),
            workspace_context_files=files if restored else None,
            record_prompt_entry=recorder,
        )
    runner.assert_awaited_once()
    return runner.call_args.kwargs["task" if native else "user_prompt"], user


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("restored", [False, True])
async def test_actual_initial_and_restored_boundaries_observe_the_exact_runner_user(
    monkeypatch, native, restored,
):
    recorder = AsyncMock()
    actual, plan = await _entry(monkeypatch, native=native, restored=restored, recorder=recorder)
    recorder.assert_awaited_once_with(actual, "native" if native else "legacy")
    assert plan in actual
    if restored:
        assert actual.startswith("RESUME SAVED PRODUCT") and "PRIVATE_SOURCE" in actual
    else:
        assert actual == plan  # The previously unjournaled first-build path.


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_observation_failure_cannot_block_or_repeat_the_actual_coder_entry(
    monkeypatch, caplog, native, failure,
):
    monkeypatch.setattr(evidence, "_OBSERVATION_SECONDS", 0.01)

    async def record(*_args):
        if failure == "error":
            raise RuntimeError("PRIVATE_DB_OR_USER")
        await asyncio.Event().wait()

    recorder = AsyncMock(side_effect=record)
    await _entry(monkeypatch, native=native, recorder=recorder)
    recorder.assert_awaited_once()
    assert "PRIVATE" not in caplog.text


async def test_observation_never_swallows_parent_cancellation():
    recorder = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await evidence.observe_coder_entry(recorder, "private", "native")
    entered = asyncio.Event()

    async def blocked(*_args):
        entered.set()
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await evidence.observe_coder_entry(blocked, "private", "legacy")
    assert entered.is_set()


async def test_cancel_cleanup_cannot_hold_generation_after_observation_deadline(monkeypatch):
    monkeypatch.setattr(evidence, "_OBSERVATION_SECONDS", 0.001)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def slow_cleanup(*_args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finished.set()

    try:
        async with asyncio.timeout(0.1):
            await evidence.observe_coder_entry(slow_cleanup, "private", "legacy")
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)


async def test_cached_preparation_reaches_entry_evidence_without_another_model_call(monkeypatch):
    note = "Saved prepared screen and design plan."
    model = AsyncMock(side_effect=AssertionError("cache hit cannot call preparation model"))
    monkeypatch.setattr(prompt_preparation, "_claim", AsyncMock(return_value=(None, note)))
    from yleum_api.core import config
    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(prompt_preparation_enabled=True),
    )
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    cached = await prompt_preparation.prepare_prompt_brief(None, ids, complete=model)
    user = "Owner request" + prompt_preparation.render_prepared_brief(cached)
    value = evidence.coder_entry_evidence(
        ids, SourceBaseline(None, None, {}), input_prompt="Owner request", coder_user=user,
        entry_kind="legacy", rendered_brief=prompt_preparation.render_prepared_brief(cached),
    )
    assert value["rendered_brief_included"] is True
    assert value["rendered_brief_sha256"] == _sha(prompt_preparation.render_prepared_brief(note))
    model.assert_not_awaited()
