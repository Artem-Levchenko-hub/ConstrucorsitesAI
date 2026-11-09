"""Fresh MAX source diagnostics are feedback, never a shipping permission."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from yleum_api.services import agent_builder as ab
from yleum_api.services.max_management_generation import ManagedSourceGap, ManagedSourceObligation

from .test_agent_builder import _ok_executor, _repair_action

PATH = "src/app/page.tsx"


def managed_gap():
    return ManagedSourceGap(
        "Connect the mounted lead form and rendered history.",
        obligation=ManagedSourceObligation.LEAD_HISTORY,
        contract_hash="a" * 64,
    )


async def run_actions(actions, *, gap="Connect the stylesheet to the page.", tree=None,
                      report=True, coordinator=True, ship=False, deadline=None,
                      snapshot_enabled=True, build_ok=True, source_changed=None,
                      repair_state=None):
    state = {PATH: "existing candidate"} if tree is None else dict(tree)
    calls, checks, emitted = [], [], []
    executor = _ok_executor([], state)

    async def complete(messages, *_args, **_kwargs):
        calls.append([dict(message) for message in messages])
        assert len(calls) <= len(actions), "No extra model call is allowed"
        return actions[len(calls) - 1]

    async def execute(action):
        if action.name == "build":
            return {"ok": build_ok, "detail": "Compiler receipt only"}
        return await executor(action)

    async def snapshot():
        return dict(state)

    def check(files, evidence):
        checks.append((dict(files), dict(evidence)))
        return gap(files) if callable(gap) else gap

    async def emit(name, payload):
        if name == "agent.step":
            emitted.append(payload)

    result = await ab._run_agent_segment(
        system_prompt="source contract fixture", user_prompt="create MAX", model="fixture",
        complete=complete, execute=execute, snapshot_files=snapshot if snapshot_enabled else None,
        completion_check=check, report_managed_build_gap=report,
        coordinator_handoff=coordinator, ship_green_on_abort=ship,
        edit_deadline=deadline, edit_source_changed=source_changed,
        max_steps=len(actions), emit=emit, source_repair=repair_state,
    )
    return result, calls, checks, emitted


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("ship", [False, True])
@pytest.mark.parametrize("wrote", [False, True])
@pytest.mark.parametrize("has_deadline", [False, True])
async def test_fresh_build_feedback_does_not_require_shipping_deadline_or_local_write(
    typed, ship, wrote, has_deadline,
):
    gap = managed_gap() if typed else "Connect the stylesheet to the page."
    actions = [_repair_action("build"), _repair_action("read_file", path=PATH)]
    if wrote:
        actions.insert(0, _repair_action("write_file", path=PATH, content="new candidate"))
    result, calls, checks, emitted = await run_actions(
        actions, gap=gap, ship=ship,
        deadline=datetime.now(UTC) + timedelta(minutes=10) if has_deadline else None,
    )
    feedback = calls[-1][-1]["content"]
    assert "[observation: build OK]" in feedback
    assert "SOURCE CHECK: " + gap in feedback
    assert checks and all(evidence == {} for _files, evidence in checks)
    assert all(files[PATH] == ("new candidate" if wrote else "existing candidate")
               for files, _evidence in checks)
    assert next(event for event in emitted if event["action"] == "build")["ok"] is True
    assert not result.done and not result.needs_finalization


@pytest.mark.parametrize("gap", ["Unknown plain source gap.", ManagedSourceGap("Unknown gap.")])
async def test_repeated_untrusted_feedback_is_visible_without_inventing_early_stop_identity(gap):
    repair = ab._SourceRepairState()
    result, calls, _checks, _events = await run_actions(
        [_repair_action("build"), _repair_action("build"),
         _repair_action("read_file", path=PATH)], gap=gap, repair_state=repair,
    )
    assert len(calls) == 3
    assert all("SOURCE CHECK: " + gap in call[-1]["content"] for call in calls[1:])
    assert repair.last_checked_rejection is None
    assert result.stop_reason != "managed_source_contract_unresolved"
    assert not result.done and not result.needs_finalization


async def test_only_trusted_repeated_current_gap_stops_without_new_shipping_authority():
    result, calls, _checks, _events = await run_actions(
        [_repair_action("build"), _repair_action("build"),
         _repair_action("read_file", path=PATH)], gap=managed_gap(),
    )
    assert len(calls) == 2
    assert result.stop_reason == "managed_source_contract_unresolved"
    assert not result.done and not result.needs_finalization


async def test_current_repair_clears_typed_rejection_and_hands_off_without_runtime_proof():
    repair = ab._SourceRepairState()
    result, calls, _checks, _events = await run_actions([
        _repair_action("build"),
        _repair_action("write_file", path=PATH, content="repaired"),
        _repair_action("build"), _repair_action("done", summary="candidate only"),
    ], gap=lambda files: None if files[PATH] == "repaired" else managed_gap(), repair_state=repair)
    assert "SOURCE CHECK:" in calls[1][-1]["content"]
    assert "SOURCE CHECK:" not in calls[3][-1]["content"]
    assert repair.last_checked_rejection is None
    assert result.done and result.needs_finalization
    assert result.proof_checkpoint is None


@pytest.mark.parametrize("options", [
    {"report": False}, {"coordinator": False}, {"snapshot_enabled": False},
    {"tree": {}}, {"tree": {"../outside": "invalid"}},
    {"tree": {PATH: None}}, {"build_ok": False}, {"build_ok": "true"},
    {"source_changed": lambda: False},
])
async def test_feedback_requires_opted_in_current_authoritative_clean_source(options):
    result, calls, _checks, _events = await run_actions([
        _repair_action("build"), _repair_action("read_file", path=PATH),
    ], **options)
    assert "SOURCE CHECK:" not in calls[-1][-1]["content"]
    assert not result.done and not result.needs_finalization


async def test_source_change_invalidates_the_check_until_the_next_build():
    result, calls, checks, _events = await run_actions([
        _repair_action("build"), _repair_action("write_file", path=PATH, content="changed"),
        _repair_action("read_file", path=PATH),
    ])
    assert "SOURCE CHECK:" in calls[1][-1]["content"]
    assert "SOURCE CHECK:" not in calls[2][-1]["content"]
    assert len(checks) == 1 and checks[0][0][PATH] == "existing candidate"
    assert not result.done and not result.needs_finalization


@pytest.mark.parametrize("typed", [False, True])
async def test_build_feedback_is_redacted_and_bounded_before_model_visibility(typed):
    diagnostic = "Source obligation\n" + "quoted source\n" * 1000
    diagnostic += "DATABASE_URL=postgresql://user:DO_NOT_QUOTE@db:5432/app\nUNRESOLVED"
    gap = managed_gap().with_diagnostic(diagnostic) if typed else diagnostic
    repair = ab._SourceRepairState()
    result, calls, _checks, _events = await run_actions([
        _repair_action("build"), _repair_action("read_file", path=PATH),
    ], gap=gap, repair_state=repair)
    feedback = calls[-1][-1]["content"].split("SOURCE CHECK: ", 1)[1]
    assert feedback.startswith("Source obligation") and feedback.endswith("UNRESOLVED")
    assert len(feedback.encode()) <= ab._MAX_OBS_CHARS and "DO_NOT_QUOTE" not in feedback
    assert repair.last_checked_rejection == (gap.rejection_key() if typed else None)
    assert not result.done and not result.needs_finalization


@pytest.mark.parametrize("invalid", [False, "", "   ", {}])
async def test_invalid_completion_feedback_cannot_mint_a_typed_identity(invalid):
    repair = ab._SourceRepairState()
    result, calls, _checks, _events = await run_actions([
        _repair_action("build"), _repair_action("read_file", path=PATH),
    ], gap=invalid, repair_state=repair)
    assert "SOURCE CHECK: Product acceptance check returned invalid feedback." in (
        calls[-1][-1]["content"]
    )
    assert repair.last_checked_rejection is None
    assert not result.done and not result.needs_finalization


async def test_callback_failure_is_safe_feedback_without_shipping_or_trusted_identity():
    def failed_check(_files):
        raise RuntimeError("PRIVATE_CHECKER_FAILURE")

    result, calls, _checks, _events = await run_actions([
        _repair_action("build"), _repair_action("read_file", path=PATH),
    ], gap=failed_check)
    assert "SOURCE CHECK: Product acceptance check failed: RuntimeError." in (
        calls[-1][-1]["content"]
    )
    assert "PRIVATE_CHECKER_FAILURE" not in str(calls) + str(result.transcript)
    assert not result.done and not result.needs_finalization


async def test_typed_gap_survives_the_ordinary_fourteen_step_segment_boundary():
    state, calls = {}, []
    actions = [action for attempt in range(7) for action in (
        _repair_action("write_file", path=PATH, content=f"attempt {attempt}"),
        _repair_action("build"),
    )]
    actions.append(_repair_action("build"))

    async def complete(messages, *_args, **_kwargs):
        calls.append([dict(message) for message in messages])
        assert len(calls) <= len(actions)
        return actions[len(calls) - 1]

    async def snapshot():
        return dict(state)

    result = await ab.run_agent_build(
        system_prompt="s", user_prompt="create MAX", model="fixture", complete=complete,
        execute=_ok_executor([], state), snapshot_files=snapshot,
        completion_check=lambda files, _: managed_gap() if files[PATH] == "attempt 6" else None,
        report_managed_build_gap=True, coordinator_handoff=True, ship_green_on_abort=False,
        max_steps=14, max_segments=2,
    )
    assert len(calls) == 15 and result.segments == 2
    assert result.stop_reason == "managed_source_contract_unresolved"
    assert not result.done and not result.needs_finalization


def fixed_clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 11, 9, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(ab, "datetime", Clock)
    return Clock


async def run_provider_window(monkeypatch, *, remaining, gap=None, report=True,
                              coordinator=True, driver=None, ship=True, invalid=False,
                              written=True, source_changed=True, model="claude-sonnet-5"):
    clock = fixed_clock(monkeypatch)
    calls = []
    state = {"../outside" if invalid else PATH: "candidate"}

    async def complete(*_args, **kwargs):
        calls.append(kwargs)
        return _repair_action("read_file", path=PATH)

    monkeypatch.setattr(ab.llm_client, "complete_chat", complete)
    result = await ab._run_agent_segment(
        system_prompt="s", user_prompt="create MAX", model=model,
        execute=_ok_executor([]), snapshot_files=lambda: asyncio.sleep(0, result=dict(state)),
        initial_written={PATH: "candidate"} if written else None,
        completion_check=lambda *_: gap, report_managed_build_gap=report,
        coordinator_handoff=coordinator, ship_green_on_abort=ship, continuation=driver,
        edit_source_changed=lambda: source_changed,
        edit_deadline=clock.current + timedelta(seconds=remaining), max_steps=1,
    )
    return result, calls


@pytest.mark.parametrize("remaining", [144, 315])
@pytest.mark.parametrize("gap", [None, managed_gap()])
async def test_ordinary_max_reserves_response_and_receipt_before_another_post(
    monkeypatch, remaining, gap,
):
    result, calls = await run_provider_window(monkeypatch, remaining=remaining, gap=gap)
    assert calls == []
    assert not result.done and result.needs_finalization
    assert result.stop_reason == ab.SOURCE_FINALIZATION_HANDOFF
    assert result.continuation_reason == "source_deadline"
    assert result.proof_checkpoint is None  # Coordinator must still verify/repair this candidate.


@pytest.mark.parametrize(("model", "expected_timeout"), [
    ("claude-sonnet-5", 300), ("claude-sonnet-5.5", 316),
])
async def test_above_runway_boundary_keeps_existing_model_response_budget(
    monkeypatch, model, expected_timeout,
):
    result, calls = await run_provider_window(monkeypatch, remaining=316, model=model)
    assert len(calls) == 1 and calls[0]["timeout_seconds"] == expected_timeout
    assert not result.done and not result.needs_finalization


@pytest.mark.parametrize("options", [
    {"gap": "Unknown source completion requirement."}, {"invalid": True},
    {"ship": False}, {"written": False}, {"source_changed": False},
])
async def test_reserved_window_does_not_bypass_existing_handoff_ownership(monkeypatch, options):
    result, calls = await run_provider_window(monkeypatch, remaining=144, **options)
    assert calls == []
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "source_deadline"


@pytest.mark.parametrize("mode", ["standalone", "non_opted_in", "durable"])
async def test_other_provider_admission_paths_keep_the_existing_window(monkeypatch, mode):
    driver = None
    if mode == "durable":
        async def provider(_frame, invoke):
            return await invoke()

        async def tool(_key, _action, invoke):
            return await invoke()

        driver = SimpleNamespace(context={}, restore_frame=lambda _: None,
                                 provider=provider, tool=tool)
    result, calls = await run_provider_window(
        monkeypatch, remaining=144, coordinator=mode != "standalone",
        report=mode != "non_opted_in", driver=driver,
    )
    assert len(calls) == 1 and calls[0]["timeout_seconds"] == 144
    assert not result.done and not result.needs_finalization


async def test_saved_provider_frame_reconciles_without_new_post_in_reserved_window(monkeypatch):
    clock = fixed_clock(monkeypatch)
    started, frames, posts = clock.current, [], []
    saved = None

    async def forbidden(*_args, **_kwargs):
        posts.append(None)
        pytest.fail("A committed response must be reconciled, never redispatched")

    async def provider(frame, _invoke):
        frames.append(frame)
        if saved is None:
            raise asyncio.CancelledError
        assert frame == saved
        return _repair_action("read_file", path=PATH)

    async def tool(_key, _action, invoke):
        return await invoke()

    driver = SimpleNamespace(context={}, restore_frame=lambda _: saved["value"] if saved else None,
                             provider=provider, tool=tool)
    monkeypatch.setattr(ab.llm_client, "complete_chat", forbidden)

    async def run():
        return await ab._run_agent_segment(
            system_prompt="s", user_prompt="create MAX", model="claude-sonnet-5",
            execute=_ok_executor([]),
            snapshot_files=lambda: asyncio.sleep(0, result={PATH: "source"}),
            completion_check=lambda *_: managed_gap(), report_managed_build_gap=True,
            coordinator_handoff=True, continuation=driver,
            edit_deadline=started + timedelta(seconds=144), max_steps=1,
        )

    with pytest.raises(asyncio.CancelledError):
        await run()
    saved = frames[0]
    clock.current += timedelta(seconds=130)
    result = await run()
    assert len(frames) == 2 and posts == []
    assert not result.done and not result.needs_finalization
