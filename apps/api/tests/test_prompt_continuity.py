"""Owned storage -> cache -> real assembly -> actual mocked coder boundaries.

SQLite adapts JSONB storage only. Preparation claims/writes and both model
implementations are denied; source resolution and all prompt assembly stay real.
"""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_agent_retry_context import history as _history_fixture
from yleum_api.core import config
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import agent_builder, agent_native, llm_client
from yleum_api.services import prompt_preparation as prep
from yleum_api.services.generation import agent_generation
from yleum_api.services.generation.agent_preparation import classify_agent_turn
from yleum_api.services.generation.agent_prompt import prepare_agent_prompt
from yleum_api.services.generation.contracts import (
    AgentTurnClassification,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)
from yleum_api.services.generation.prompt_entry_evidence import coder_entry_evidence
from yleum_api.services.generation.retry_request import resolve_request_source

history = _history_fixture

TASK = "Собери приложение питания. Мой MAX заголовок. Цвет бордовый. " + "цель " * 520
PLAN = "Начало плана. " + "а" * 6100 + "ВИЗУАЛЬНЫЙ СМЫСЛ В СЕРЕДИНЕ" + "б" * 7350 + "КОНЕЦ Ё"
FACTS = ProjectGenerationFacts(
    "max_miniapp", "fixture", "Fixture", None, None, False, "ru", False, "", "",
)


def ready(history, run, *, preferences=None, provenance=False, result=PLAN):
    preferences = preferences or {}
    message = history.session.get(Message, run.user_message_id)
    value = {
        "version": 1, "state": "ready", "claim_id": str(uuid4()),
        "input_sha256": hashlib.sha256(json.dumps(
            {"description": message.content, "preferences": preferences},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(), "result": result,
    }
    if provenance:
        value["source"] = {
            "message_id": str(message.id),
            "description_sha256": hashlib.sha256(message.content.encode()).hexdigest(),
            "preferences": preferences,
        }
    run.agent_state = {**(run.agent_state or {}), prep.KEY: value}
    history.session.commit()


@pytest.fixture
def no_preparation_expense(monkeypatch):
    denied = AsyncMock(side_effect=AssertionError("reference reuse cannot claim/write/call"))
    monkeypatch.setattr(prep, "_claim", denied)
    monkeypatch.setattr(prep, "_write", denied)
    monkeypatch.setattr(prep, "prepare_prompt_brief", denied)
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        prompt_preparation_enabled=True,
    ))
    return denied


async def assembled(
    history, current, *, turn_override=None, selected=None, reference_factory=None,
):
    ids = history.ids(current)
    message = history.session.get(Message, current.user_message_id)
    turn = turn_override or await classify_agent_turn(
        baseline=history.baseline, factory=history.factory, ids=ids, orchestrate=False,
        project_info=FACTS, prompt_text=message.content,
    )
    brief = await prep.prepare_initial_prompt_brief(
        reference_factory or history.factory, ids, FACTS, turn, selected,
        baseline=history.baseline,
    )
    plan, _ = await prepare_agent_prompt(
        stack=StackPrompt("", None, None, None, "fixture", False),
        factory=history.factory, ids=ids, project_info=FACTS, prompt_text=turn.prompt,
        runtime=GenerationRuntime(), orchestrate=False, selected_elements=selected,
        _is_edit=turn.is_edit, _is_continue=turn.is_continue,
        force_model="fixture-model", prepared_brief=brief,
    )
    return brief, plan, turn


class Boundary(BaseException):
    pass


async def coder_boundary(monkeypatch, history, current, plan, turn, *, native, restored=False):
    monkeypatch.setattr(agent_generation, "get_settings", lambda: SimpleNamespace(
        use_native_agent=native, agent_max_segments=1,
        agent_require_green_before_done=True, agent_ship_green_on_abort=False,
    ))
    runner = AsyncMock(side_effect=Boundary)
    monkeypatch.setattr(agent_native if native else agent_builder,
                        "run_native_build" if native else "run_agent_build", runner)
    files = {"src/app/page.tsx": "export default function Page(){return <p>draft</p>}"}
    snapshot = AsyncMock(return_value=files)
    recorder = AsyncMock()
    with pytest.raises(Boundary):
        await agent_generation.execute_agent_turn(
            _agent_res=None, _is_edit=turn.is_edit,
            _max_has_generated_snapshot=turn.has_generated_snapshot,
            _max_seed_files={}, _max_shell_enabled=False, baseline=history.baseline,
            ids=history.ids(current), is_free=False, project_info=FACTS,
            prompt_text=turn.prompt, plan=plan,
            runtime=GenerationRuntime(handle=SimpleNamespace(
                is_portable=lambda: True, capabilities={"portable_machine": True},
                export_files=snapshot, snapshot_files=snapshot,
            )),
            operations=SimpleNamespace(
                execute=AsyncMock(), emit=AsyncMock(), snapshot_files=snapshot,
            ),
            workspace_context_files=files if restored else None,
            record_prompt_entry=recorder,
        )
    actual = runner.call_args.kwargs["task" if native else "user_prompt"]
    recorder.assert_awaited_once_with(actual, "native" if native else "legacy")
    runner.assert_awaited_once()
    return actual


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("retry_text", ["Продолжи", "попробуй ещё раз"])
@pytest.mark.parametrize("classification", ["actual", "edit", "first-build"])
async def test_owned_legacy_ready_plan_survives_generated_metadata_and_reaches_coder(
    monkeypatch, history, no_preparation_expense, native, retry_text, classification,
):
    project = history.ensure_project()
    original = history.add(TASK)
    ready(history, original)  # Actual observed legacy shape: original preferences {}.
    project.discovery_spec = {"primary_family": "Generated Onest", "dark_mode": True}
    history.session.commit()
    current = history.add(retry_text, status="running", mode="edit")
    current.agent_state = {**current.agent_state, prep.POLICY_KEY: {
        "version": 1, "prompt_hash": current.prompt_hash, "allow_new": False,
    }}
    history.session.commit()
    override = None if classification == "actual" else AgentTurnClassification(
        TASK, False, classification == "edit", classification == "edit",
    )
    brief, plan, turn = await assembled(history, current, turn_override=override)
    assert brief == PLAN
    actual = await coder_boundary(
        monkeypatch, history, current, plan, turn, native=native, restored=True,
    )
    fragment = prep.render_prepared_brief(PLAN)
    assert TASK in actual and fragment in actual
    assert actual.count(fragment) == 1
    assert "ВИЗУАЛЬНЫЙ СМЫСЛ В СЕРЕДИНЕ" in actual and "КОНЕЦ Ё" in actual
    evidence = coder_entry_evidence(
        history.ids(current), history.baseline, input_prompt=turn.prompt,
        coder_user=actual, entry_kind="native" if native else "legacy", rendered_brief=fragment,
    )
    assert evidence["prepared_reference_available"] and evidence["rendered_brief_included"]
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("native", [False, True])
async def test_same_run_cold_entry_reuses_saved_task_exactly_once(
    monkeypatch, history, no_preparation_expense, native,
):
    project = history.ensure_project()
    current = history.add(TASK, status="running")
    preferences = {"primary_family": "Original font", "tone": "calm", "dark_mode": False}
    ready(history, current, preferences=preferences, provenance=True)
    project.discovery_spec = {"primary_family": "Later generated", "dark_mode": True}
    history.session.commit()
    brief, plan, turn = await assembled(history, current, turn_override=AgentTurnClassification(
        TASK, False, False, False,
    ))
    from yleum_api.services.generation.recovery_pipeline import PreparedAgentEntry

    saved = PreparedAgentEntry(
        (turn.prompt, turn.is_continue, turn.is_edit, turn.has_generated_snapshot),
        None, StackPrompt("", None, None, None, "fixture", False), plan, {}, None, None,
    )
    restored = PreparedAgentEntry.from_json(saved.to_json())
    assert restored.prompt_plan.user == plan.user
    reference = prep.render_prepared_brief(await prep.reuse_ready_prompt_brief(
        history.factory, history.ids(current),
    ))
    assert brief == PLAN and reference == prep.render_prepared_brief(PLAN)
    actual = await coder_boundary(
        monkeypatch, history, current, restored.prompt_plan, turn, native=native, restored=True,
    )
    assert actual.count(reference) == 1
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("case", [
    "new-edit", "amended-continue", "api", "foreign-owner", "wrong-source",
    "wrong-fingerprint", "new-font", "new-palette", "selected", "unknown-old-preferences",
])
async def test_nonmatching_owner_intent_cannot_adopt_old_plan(
    history, no_preparation_expense, case,
):
    project = history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    if case == "foreign-owner":
        original.user_id = uuid4()
    elif case in {"wrong-source", "wrong-fingerprint"}:
        value = dict(original.agent_state[prep.KEY])
        if case == "wrong-source":
            value["source"] = {"message_id": str(uuid4()), "description_sha256": "a" * 64,
                               "preferences": {}}
        else:
            value["input_sha256"] = "a" * 64
        original.agent_state = {**original.agent_state, prep.KEY: value}
    elif case == "unknown-old-preferences":
        ready(history, original, preferences={"tone": "lost historical choice"})
        project.discovery_spec = {"tone": "later value"}
    request = {"new-edit": "Use a new blue one-screen design",
               "amended-continue": "Продолжи и полностью поменяй дизайн",
               "api": "API-only: build an API without UI"}.get(case, "Продолжи")
    current = history.add(request, status="running", mode="edit")
    if case == "new-font":
        message = history.session.get(Message, current.user_message_id)
        message.resolved_fonts = [{"family": "Explicit new font", "role": "body"}]
    if case == "new-palette":
        from yleum_api.services.design_presets import PRESETS
        current.agent_state = {**current.agent_state, "owner_palette_v1": {
            "version": 1, "prompt_hash": current.prompt_hash, "preset_id": next(iter(PRESETS)),
        }}
    history.session.commit()
    brief = await prep.prepare_initial_prompt_brief(
        history.factory, history.ids(current), FACTS,
        AgentTurnClassification(request, True, True, True),
        [{"selector": "#explicit"}] if case == "selected" else None,
    )
    assert brief == ""
    no_preparation_expense.assert_not_awaited()


async def test_plain_continue_source_resolution_is_owned_not_unbound_history(history):
    history.ensure_project()
    original = history.add(TASK)
    current = history.add("Продолжи", status="running")
    async with history.factory() as session:
        source = await resolve_request_source(
            session, ids=history.ids(current), baseline=history.baseline,
        )
    assert source.message_id == original.user_message_id and source.prompt == TASK



@pytest.mark.parametrize("native", [False, True])
async def test_verified_checkpoint_plan_reaches_both_coder_protocols(
    monkeypatch, history, no_preparation_expense, native,
):
    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    digest = "c" * 64
    original.agent_state = {**original.agent_state, "source_checkpoint": {
        "digest": digest, "baseline_sha": history.baseline.sha,
    }}
    current = history.add("Продолжи", status="running", mode="edit")
    current.agent_state = {**current.agent_state, "source_checkpoint_resume": {
        "previous_run_id": str(original.id), "source_run_id": str(original.id),
        "digest": digest, "baseline_sha": history.baseline.sha,
    }}
    history.session.commit()
    brief, plan, turn = await assembled(history, current)
    assert brief == PLAN and TASK in plan.user
    actual = await coder_boundary(
        monkeypatch, history, current, plan, turn, native=native, restored=True,
    )
    assert actual.count(prep.render_prepared_brief(PLAN)) == 1
    no_preparation_expense.assert_not_awaited()


async def test_reference_lookup_is_select_only_and_does_not_emit_progress(
    history, no_preparation_expense,
):
    from sqlalchemy import event

    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    current = history.add("Продолжи", status="running")
    observed = []

    def only_select(_conn, _cursor, statement, _params, _context, _many):
        observed.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement

    engine = history.session.get_bind()
    event.listen(engine, "before_cursor_execute", only_select)
    progress = AsyncMock(side_effect=AssertionError("cache reuse is not a preparation request"))
    try:
        result = await prep.prepare_initial_prompt_brief(
            history.factory, history.ids(current), FACTS,
            AgentTurnClassification(TASK, True, False, True), None, emit_progress=progress,
        )
    finally:
        event.remove(engine, "before_cursor_execute", only_select)
    assert result == PLAN and observed
    progress.assert_not_awaited()
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("case", [
    "project-owner", "assistant", "original-message-hash", "baseline", "not-ready", "empty",
])
async def test_reference_binding_or_cache_state_failure_cannot_adopt_plan(
    history, no_preparation_expense, case,
):
    project = history.ensure_project()
    original = history.add(TASK)
    ready(history, original, result="" if case == "empty" else PLAN)
    current = history.add("Продолжи", status="running")
    ids = history.ids(current)
    if case == "project-owner":
        project.owner_id = uuid4()
    elif case == "assistant":
        current.assistant_message_id = uuid4()
    elif case == "original-message-hash":
        history.session.get(Message, original.user_message_id).content += "amended"
    elif case == "baseline":
        history.snapshot.commit_sha = "b" * 40
        # Dispatch pins the snapshot identity; actual source request still belongs
        # to that same snapshot. A changed current snapshot is the hard barrier.
        project.current_snapshot_id = uuid4()
    elif case == "not-ready":
        original.agent_state = {**original.agent_state, prep.KEY: {
            **original.agent_state[prep.KEY], "state": "claimed",
        }}
    history.session.commit()
    assert await prep.reuse_ready_prompt_brief(history.factory, ids) == ""
    no_preparation_expense.assert_not_awaited()


async def test_immutable_explicit_fonts_palette_survive_generated_discovery_metadata(
    history, no_preparation_expense,
):
    from yleum_api.services.design_presets import PRESETS

    project = history.ensure_project()
    original = history.add(TASK)
    message = history.session.get(Message, original.user_message_id)
    message.resolved_fonts = [{"family": "Owner Font", "role": "body"}]
    palette = next(iter(PRESETS))
    original.agent_state = {**original.agent_state, "owner_palette_v1": {
        "version": 1, "prompt_hash": original.prompt_hash, "preset_id": palette,
    }}
    ready(history, original, preferences={
        "fonts": [{"family": "Owner Font", "role": "body"}], "palette": PRESETS[palette].palette,
    })
    project.discovery_spec = {"primary_family": "Generated Other", "dark_mode": False}
    current = history.add("Продолжи", status="running")
    history.session.commit()
    assert await prep.reuse_ready_prompt_brief(history.factory, history.ids(current)) == PLAN
    no_preparation_expense.assert_not_awaited()


async def test_reference_unavailability_is_not_a_generation_failure_or_secret_log(caplog):
    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("PRIVATE_OWNER_PROMPT_OR_DATABASE_URL")

        async def __aexit__(self, *_args):
            return None

    assert await prep.reuse_ready_prompt_brief(BrokenSession, None) == ""
    assert "PRIVATE" not in caplog.text


async def test_reference_lookup_preserves_parent_cancellation():
    import asyncio

    class CancelledSession:
        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *_args):
            return None

    with pytest.raises(asyncio.CancelledError):
        await prep.reuse_ready_prompt_brief(CancelledSession, None)


async def cache_reference(history, monkeypatch, original, recipient):
    async def persist(_factory, ids, value):
        assert ids == history.ids(recipient)
        recipient.agent_state = {**recipient.agent_state, prep.REFERENCE_KEY: value}
        history.session.commit()

    writer = AsyncMock(side_effect=persist)
    monkeypatch.setattr(prep, "_write_cache_reference", writer)
    async with history.factory() as session:
        context = await prep._load_input(session, history.ids(recipient))
        await prep._remember_cache_hit(
            session, history.factory, history.ids(recipient), context, original, PLAN,
        )
    writer.assert_awaited_once()
    return recipient.agent_state[prep.REFERENCE_KEY]


async def claim_cache_hit(history, monkeypatch, origin, recipient):
    recipient.agent_state = {**recipient.agent_state, prep.POLICY_KEY: {
        "version": 1, "prompt_hash": recipient.prompt_hash, "allow_new": True,
    }}
    history.session.commit()
    class ClaimSession(history.factory):
        async def execute(self, statement, parameters=None):
            assert str(statement).startswith("SELECT pg_advisory_xact_lock")
            assert parameters == {"key": "prompt-preparation:" + str(recipient.project_id)}

        async def scalar(self, statement):
            if "input_sha256" in str(statement):
                # This one PostgreSQL JSONB expression is not portable to SQLite.
                # Origin ownership/content validation and all later reference SQL
                # lookups remain real; Root runs the native claim tests separately.
                return origin
            return await super().scalar(statement)

    monkeypatch.setattr(prep, "_write", AsyncMock(side_effect=AssertionError("no new claim")))
    return await prep._claim(ClaimSession, history.ids(recipient))


@pytest.mark.parametrize("native", [False, True])
async def test_global_cache_hit_without_own_result_survives_failure_and_continue(
    monkeypatch, history, native,
):
    project = history.ensure_project()
    project.discovery_spec = {"tone": "original-context"}
    origin = history.add(TASK)
    ready(history, origin, preferences={"tone": "original-context"})
    recipient = history.add(TASK, status="running")
    recipient.agent_state = {**recipient.agent_state, prep.POLICY_KEY: {
        "version": 1, "prompt_hash": recipient.prompt_hash, "allow_new": True,
    }}
    history.session.commit()

    async def persist(_factory, ids, value):
        assert ids == history.ids(recipient)
        recipient.agent_state = {**recipient.agent_state, prep.REFERENCE_KEY: value}
        history.session.commit()

    writer = AsyncMock(side_effect=persist)
    monkeypatch.setattr(prep, "_write_cache_reference", writer)
    monkeypatch.setattr(prep, "_write", AsyncMock(side_effect=AssertionError("no new claim")))
    claim, cached = await claim_cache_hit(history, monkeypatch, origin, recipient)
    assert claim is None and cached == PLAN
    assert prep.KEY not in recipient.agent_state
    writer.assert_awaited_once()
    reference = recipient.agent_state[prep.REFERENCE_KEY]
    assert not any(text in json.dumps(reference) for text in (TASK, PLAN, "original-context"))
    recipient.status = "failed"
    project.discovery_spec = {"primary_family": "Later generated", "dark_mode": True}
    history.session.commit()
    current = history.add("Продолжи", status="running", mode="edit")
    monkeypatch.setattr(prep, "prepare_prompt_brief", AsyncMock(
        side_effect=AssertionError("no new preparation expense"),
    ))
    brief, plan, turn = await assembled(history, current)
    assert brief == PLAN
    actual = await coder_boundary(
        monkeypatch, history, current, plan, turn, native=native, restored=True,
    )
    assert actual.count(prep.render_prepared_brief(PLAN)) == 1


@pytest.mark.parametrize("case", [
    "owner", "project", "origin-id", "source-id", "request-id", "input-hash", "result-hash",
    "result-count", "bool-count", "owner-preferences", "changed-result", "extra-field",
])
async def test_wrong_cache_reference_does_not_adopt_any_other_ready_result(
    monkeypatch, history, no_preparation_expense, case,
):
    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    recipient = history.add(TASK, status="running")
    reference = dict(await cache_reference(history, monkeypatch, original, recipient))
    if case == "owner":
        original.user_id = uuid4()
    elif case == "project":
        original.project_id = uuid4()
    elif case == "changed-result":
        original.agent_state = {**original.agent_state, prep.KEY: {
            **original.agent_state[prep.KEY], "result": PLAN + "modified",
        }}
    else:
        key, value = {
            "origin-id": ("origin_run_id", str(uuid4())),
            "source-id": ("origin_source_message_id", str(uuid4())),
            "request-id": ("request_message_id", str(uuid4())),
            "input-hash": ("input_sha256", "f" * 64),
            "result-hash": ("result_sha256", "f" * 64),
            "result-count": ("result_chars", len(PLAN) + 1),
            "bool-count": ("result_chars", True),
            "owner-preferences": ("owner_preferences_sha256", "f" * 64),
            "extra-field": ("unexpected", "refuse"),
        }[case]
        reference[key] = value
    recipient.agent_state = {**recipient.agent_state, prep.REFERENCE_KEY: reference}
    recipient.status = "failed"
    history.session.commit()
    current = history.add("Продолжи", status="running", mode="edit")
    assert await prep.reuse_ready_prompt_brief(history.factory, history.ids(current)) == ""
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_reference_write_failure_does_not_withhold_an_existing_cached_plan(
    monkeypatch, history, failure,
):
    import asyncio

    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    recipient = history.add(TASK, status="running")
    monkeypatch.setattr(prep, "_REFERENCE_WRITE_SECONDS", 0.001)

    async def unavailable(*_args):
        if failure == "error":
            raise RuntimeError("PRIVATE_DATABASE_OR_PLAN")
        await asyncio.Event().wait()

    monkeypatch.setattr(prep, "_write_cache_reference", unavailable)
    claim, cached = await claim_cache_hit(history, monkeypatch, original, recipient)
    assert claim is None and cached == PLAN
    assert prep.REFERENCE_KEY not in recipient.agent_state


async def test_cache_reference_observation_preserves_cancellation_and_bounds_slow_cleanup(
    monkeypatch,
):
    import asyncio

    entered, release = asyncio.Event(), asyncio.Event()

    async def cancelled(*_args):
        raise asyncio.CancelledError

    monkeypatch.setattr(prep, "_write_cache_reference", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await prep._persist_cache_reference(None, None, {})

    async def slow_cleanup(*_args):
        try:
            entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr(prep, "_write_cache_reference", slow_cleanup)
    monkeypatch.setattr(prep, "_REFERENCE_WRITE_SECONDS", 0.001)
    try:
        async with asyncio.timeout(0.1):
            await prep._persist_cache_reference(None, None, {})
        assert entered.is_set()
    finally:
        release.set()
        await asyncio.sleep(0)


def published_partial(history):
    from yleum_api.services.app_errors import render_block

    project = history.ensure_project()
    original = history.add(TASK, status="completed")
    message = history.session.get(Message, original.user_message_id)
    message.resolved_fonts = [{"family": "Owner Font", "role": "body"}]
    ready(history, original, preferences={"fonts": message.resolved_fonts})
    partial = Snapshot(id=uuid4(), project_id=project.id, parent_id=history.snapshot.id,
                       prompt_text=TASK, commit_sha="b" * 40)
    assistant = history.session.get(Message, original.assistant_message_id)
    assistant.snapshot_id = partial.id
    assistant.content = render_block(
        category="incomplete", title="Сборка не завершена",
        detail=("Агент исчерпал лимит шагов, не закончив. Часть уже сохранена "
                "— нажми «Продолжить», доделаю с того же места, не начиная заново."),
        file=None, fixable=True,
    )
    history.session.add(partial)
    project.current_snapshot_id = partial.id
    history.baseline = SourceBaseline(partial.id, partial.commit_sha, {"src/page.tsx": "draft B"})
    history.session.commit()
    current = history.add("Продолжи", status="running", mode="edit")
    return original, assistant, partial, current


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("manual", ["continue", "failed-continue-then-retry"])
async def test_completed_partial_a_to_b_link_keeps_owned_task_plan_and_font_source(
    monkeypatch, history, no_preparation_expense, native, manual,
):
    original, _assistant, _partial, current = published_partial(history)
    if manual == "failed-continue-then-retry":
        current.status = "failed"
        history.session.commit()
        current = history.add("попробуй ещё раз", status="running", mode="edit")
    async with history.factory() as session:
        source = await resolve_request_source(
            session, ids=history.ids(current), baseline=history.baseline,
        )
    assert source.message_id == original.user_message_id
    assert history.session.get(Message, source.message_id).resolved_fonts == [
        {"family": "Owner Font", "role": "body"},
    ]
    brief, plan, turn = await assembled(history, current)
    assert turn.is_continue is (manual == "continue")
    assert brief == PLAN and turn.prompt == TASK
    actual = await coder_boundary(
        monkeypatch, history, current, plan, turn, native=native, restored=True,
    )
    assert TASK in actual and actual.count(prep.render_prepared_brief(PLAN)) == 1
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("damage", [
    "owner", "assistant-project", "assistant-role", "assistant-link", "card", "parent",
    "foreign-parent", "foreign-snapshot", "changed-commit", "changed-current", "message-hash",
])
async def test_partial_snapshot_link_cannot_cross_a_changed_or_foreign_boundary(
    history, no_preparation_expense, damage,
):
    original, assistant, partial, current = published_partial(history)
    if damage == "owner":
        original.user_id = uuid4()
    elif damage == "assistant-project":
        assistant.project_id = uuid4()
    elif damage == "assistant-role":
        assistant.role = "user"
    elif damage == "assistant-link":
        assistant.snapshot_id = history.snapshot.id
    elif damage == "card":
        assistant.content = "Finished product."
    elif damage == "parent":
        partial.parent_id = uuid4()
    elif damage == "foreign-parent":
        history.snapshot.project_id = uuid4()
    elif damage == "foreign-snapshot":
        partial.project_id = uuid4()
    elif damage == "changed-commit":
        partial.commit_sha = "c" * 40
    elif damage == "changed-current":
        history.ensure_project().current_snapshot_id = history.snapshot.id
    elif damage == "message-hash":
        history.session.get(Message, original.user_message_id).content += "new owner condition"
    history.session.commit()
    async with history.factory() as session:
        source = await resolve_request_source(
            session, ids=history.ids(current), baseline=history.baseline,
        )
    assert source.message_id == current.user_message_id
    brief, _plan, _turn = await assembled(history, current)
    assert brief == ""
    no_preparation_expense.assert_not_awaited()


# Failure injection is below the real lookup. No resolution/cache/assembly function
# is replaced; fail only the selected storage read before it returns evidence.
def failing_reads(history, current, *, at="cache", failures=2, error=OSError):
    status = {"remaining": failures, "failed_reads": 0}

    def fail():
        if status["remaining"]:
            status["remaining"] -= 1
            status["failed_reads"] += 1
            raise error("PRIVATE_TRANSIENT_DATABASE_DETAIL")

    class Session(history.factory):
        async def get(self, model, key, **kwargs):
            if ((at == "identity" and model is GenerationRun and key == current.id)
                or (at == "message" and model is Message and key == current.user_message_id)
                or (at == "project" and model is Project)):
                fail()
            return await super().get(model, key, **kwargs)

        async def scalars(self, statement):
            if at == "cache":
                fail()
            return await super().scalars(statement)

    return Session, status


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("at", ["identity", "message", "cache"])
async def test_transient_reference_read_retries_without_expense_then_sends_full_plan(
    monkeypatch, history, no_preparation_expense, native, at,
):
    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    current = history.add("Продолжи", status="running", mode="edit")
    factory, status = failing_reads(history, current, at=at, failures=1)
    brief, plan, turn = await assembled(history, current, reference_factory=factory)
    assert status["failed_reads"] == 1 and brief == PLAN
    actual = await coder_boundary(monkeypatch, history, current, plan, turn, native=native)
    assert actual.count(prep.render_prepared_brief(PLAN)) == 1
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("at", ["identity", "message", "cache"])
@pytest.mark.parametrize("error", [OSError, ValueError])
async def test_persistent_required_read_failure_without_driver_stops_before_provider(
    history, no_preparation_expense, at, error, caplog,
):
    from yleum_api.services.generation.recovery_pipeline import run_bootstrap_stage

    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    current = history.add("Продолжи", status="running", mode="edit")
    factory, status = failing_reads(history, current, at=at, error=error)
    coder = AsyncMock(side_effect=AssertionError("no provider before resolved reference"))

    async def invoke():
        _brief, plan, _turn = await assembled(history, current, reference_factory=factory)
        await coder(plan)

    with pytest.raises(prep.PromptReferenceUnavailable, match=r"^prompt_reference_unavailable$"):
        await run_bootstrap_stage(None, "prompt_prepare", invoke, handle=None)
    assert status["failed_reads"] == 2
    coder.assert_not_awaited()
    assert original.agent_state[prep.KEY]["result"] == PLAN
    assert "PRIVATE" not in caplog.text
    # Legacy lifecycle terminalizes the interrupted turn without provider work.
    # A later explicitly admitted retry can still reach that same original plan.
    current.status = "failed"
    history.session.commit()
    retry = history.add("попробуй ещё раз", status="running", mode="edit")
    async with history.factory() as session:
        source = await resolve_request_source(
            session, ids=history.ids(retry), baseline=history.baseline,
        )
    assert source.message_id == original.user_message_id
    brief, _plan, _turn = await assembled(history, retry)
    assert brief == PLAN
    no_preparation_expense.assert_not_awaited()


async def test_known_fresh_optional_input_keeps_fallback_but_saved_ready_input_waits(
    monkeypatch, history,
):
    history.ensure_project()
    current = history.add(TASK, status="running")
    factory, status = failing_reads(history, current, at="project")
    optional = AsyncMock(return_value="")
    monkeypatch.setattr(prep, "prepare_prompt_brief", optional)
    assert await prep.prepare_initial_prompt_brief(
        factory, history.ids(current), FACTS, AgentTurnClassification(TASK, False, False, False),
        None, baseline=history.baseline,
    ) == ""
    assert status["failed_reads"] == 1
    optional.assert_awaited_once()
    optional.reset_mock()
    ready(history, current)
    factory, status = failing_reads(history, current, at="project")
    with pytest.raises(prep.PromptReferenceUnavailable):
        await prep.prepare_initial_prompt_brief(
            factory, history.ids(current), FACTS,
            AgentTurnClassification(TASK, False, False, False),
            None, baseline=history.baseline,
        )
    assert status["failed_reads"] == 2
    optional.assert_not_awaited()


@pytest.mark.parametrize("native", [False, True])
async def test_secondary_reference_read_failure_never_changes_or_denies_saved_user_content(
    monkeypatch, history, no_preparation_expense, native,
):
    history.ensure_project()
    current = history.add(TASK, status="running")
    ready(history, current)
    _brief, plan, turn = await assembled(history, current)
    saved_user = plan.user
    saved_hash = hashlib.sha256(saved_user.encode()).hexdigest()
    factory, status = failing_reads(history, current, at="identity")
    reference = await prep.reuse_ready_prompt_brief(factory, history.ids(current))
    assert reference == "" and status["failed_reads"] == 1
    actual = await coder_boundary(monkeypatch, history, current, plan, turn, native=native)
    assert actual == saved_user and hashlib.sha256(actual.encode()).hexdigest() == saved_hash
    assert actual.count(prep.render_prepared_brief(PLAN)) == 1
    observed = coder_entry_evidence(
        history.ids(current), history.baseline, input_prompt=turn.prompt, coder_user=actual,
        entry_kind="native" if native else "legacy", rendered_brief=None,
    )
    assert not observed["prepared_reference_available"]
    # No trusted reference means inclusion is unproved, not that USER lacks it.
    assert not observed["rendered_brief_included"] and prep.render_prepared_brief(PLAN) in actual
    no_preparation_expense.assert_not_awaited()


async def test_required_read_wait_retains_and_resumes_exact_bootstrap_stage(
    monkeypatch, history, no_preparation_expense,
):
    from copy import deepcopy

    from yleum_api.services.generation import recovery_tool_receipts
    from yleum_api.services.generation.recovery_contract import CheckpointRef, ExecutionCursor
    from yleum_api.services.generation.recovery_execution import GenerationYielded
    from yleum_api.services.generation.recovery_pipeline import run_bootstrap_stage

    history.ensure_project()
    original = history.add(TASK)
    ready(history, original)
    current = history.add("Продолжи", status="running", mode="edit")
    factory, status = failing_reads(history, current)
    # Real bootstrap scheduling/serialization; only durable storage/lease receipts
    # are replaced. Native PG coverage proves their separate commit/CAS contract.
    reconcile = AsyncMock()
    require = AsyncMock()
    monkeypatch.setattr(recovery_tool_receipts, "reconcile_preparation_receipts", reconcile)
    monkeypatch.setattr(recovery_tool_receipts, "require_preparation_receipts", require)

    class Driver:
        def __init__(self):
            self.factory = history.factory
            self.context = {"completed": {"existing_work": {"source": "retained"}}}
            self.state = SimpleNamespace(
                execution=ExecutionCursor(stage="bootstrap", substep="prompt_prepare",
                                          logical_step_seq=1, physical_state="active",
                                          execution_context_ref=CheckpointRef(
                                              object_key="synthetic-context", sha256="a" * 64,
                                          )),
                continuation_claim=SimpleNamespace(run_id=current.id),
                state_revision=0, identity=SimpleNamespace(run_id=current.id),
            )
            self.waits = 0

        async def save(self, execution, *, context):
            self.state.execution = execution
            self.state.state_revision += 1
            self.context = deepcopy(context)

        async def wait(self):
            self.waits += 1
            raise GenerationYielded(run_id=current.id, checkpoint=None, attempt_seq=0,
                                    phase="waiting_dependency")

    driver = Driver()
    provider = AsyncMock()
    invocations = 0

    async def invoke():
        nonlocal invocations
        invocations += 1
        _brief, plan, _turn = await assembled(history, current, reference_factory=factory)
        return plan, None

    async def enter_coder():
        plan, _ = await run_bootstrap_stage(
            driver, "prompt_prepare", invoke, handle=SimpleNamespace(),
        )
        await provider(plan.user)
        return plan

    with pytest.raises(GenerationYielded):
        await enter_coder()
    provider.assert_not_awaited()
    assert status["failed_reads"] == 2 and driver.waits == 1
    assert driver.context["completed"]["bootstrap_pending"] == {
        "version": 1, "name": "prompt_prepare",
    }
    assert "bootstrap_results" not in driver.context["completed"]
    pending = driver.state.execution
    assert pending.substep == "prompt_prepare" and pending.pending_effect_id is not None
    assert original.agent_state[prep.KEY]["result"] == PLAN

    plan = await enter_coder()
    assert prep.render_prepared_brief(PLAN) in plan.user
    provider.assert_awaited_once_with(plan.user)
    assert driver.state.execution.logical_step_seq == pending.logical_step_seq
    assert driver.state.execution.pending_effect_id is None
    assert driver.context["completed"]["existing_work"] == {"source": "retained"}
    assert driver.context["completed"]["bootstrap_pending"] is None
    assert invocations == 2 and driver.waits == 1
    reconcile.assert_awaited_once()
    require.assert_awaited_once()
    # A second cold loader consumes the serialized result and does not replay
    # lookup, preparation or assembly. The trusted plan stays present once.
    cached, _ = await run_bootstrap_stage(
        driver, "prompt_prepare", invoke, handle=SimpleNamespace(),
    )
    assert cached.user == plan.user and invocations == 2
    assert cached.user.count(prep.render_prepared_brief(PLAN)) == 1
    no_preparation_expense.assert_not_awaited()


@pytest.mark.parametrize("cached", [False, True])
async def test_full_prepare_initial_does_not_reenter_after_frozen_baseline_mismatch(
    monkeypatch, history, cached,
):
    project = history.ensure_project()
    origin = history.add(TASK)
    if cached:
        ready(history, origin)
    current = history.add(TASK, status="running")
    current.agent_state = {**current.agent_state, prep.POLICY_KEY: {
        "version": 1, "prompt_hash": current.prompt_hash, "allow_new": True,
    }}
    changed = Snapshot(id=uuid4(), project_id=project.id, parent_id=history.snapshot.id,
                       prompt_text=None, commit_sha="b" * 40)
    history.session.add(changed)
    project.current_snapshot_id = changed.id
    history.session.commit()

    class Session(history.factory):
        async def execute(self, statement, parameters=None):
            assert str(statement).startswith("SELECT pg_advisory_xact_lock")

        async def scalar(self, statement):
            if "input_sha256" in str(statement):
                return origin if cached else None
            return await super().scalar(statement)

    completion = AsyncMock(return_value="NEW OPTIONAL PLAN AFTER BASELINE REJECTION")
    writes = AsyncMock(return_value=True)
    monkeypatch.setattr(config, "get_settings", lambda: type("Settings", (), {
        "prompt_preparation_enabled": True,
    })())
    monkeypatch.setattr(llm_client, "complete_prompt_brief", completion)
    monkeypatch.setattr(prep, "_write", writes)
    result = await prep.prepare_initial_prompt_brief(
        Session, history.ids(current), FACTS, AgentTurnClassification(TASK, False, False, False),
        None, baseline=history.baseline,
    )
    assert result == "", {"cached": cached, "model_calls": completion.await_count,
                           "claim_or_result_writes": writes.await_count}
    completion.assert_not_awaited()
    writes.assert_not_awaited()


async def test_full_prepare_initial_same_baseline_prepares_once_then_reuses_exact_cache(
    monkeypatch, history,
):
    history.ensure_project()
    current = history.add(TASK, status="running")
    current.agent_state = {**current.agent_state, prep.POLICY_KEY: {
        "version": 1, "prompt_hash": current.prompt_hash, "allow_new": True,
    }}
    history.session.commit()

    class Session(history.factory):
        async def execute(self, statement, parameters=None):
            assert str(statement).startswith("SELECT pg_advisory_xact_lock")

        async def scalar(self, statement):
            if "input_sha256" in str(statement):
                return None
            return await super().scalar(statement)

    async def store(_session, ids, value, **_kwargs):
        assert ids == history.ids(current)
        # Adapt only PostgreSQL JSONB writes to this real SQLite ORM row.
        current.agent_state = {**current.agent_state, prep.KEY: value}
        history.session.commit()
        return True

    completion = AsyncMock(return_value=PLAN)
    writes = AsyncMock(side_effect=store)
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        prompt_preparation_enabled=True,
    ))
    monkeypatch.setattr(llm_client, "complete_prompt_brief", completion)
    monkeypatch.setattr(prep, "_write", writes)
    turn = AgentTurnClassification(TASK, False, False, False)
    assert await prep.prepare_initial_prompt_brief(
        Session, history.ids(current), FACTS, turn, None, baseline=history.baseline,
    ) == PLAN
    assert await prep.prepare_initial_prompt_brief(
        Session, history.ids(current), FACTS, turn, None, baseline=history.baseline,
    ) == PLAN
    completion.assert_awaited_once()
    assert writes.await_count == 2  # One claim + ready, no write/model on reuse.
