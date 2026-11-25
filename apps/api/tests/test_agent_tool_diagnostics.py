import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder, agent_native, agent_tool_diagnostics
from yleum_api.services.agent_tool_diagnostics import (
    missing_shell_executable,
    record_shell_diagnostics,
    safe_tool_diagnostic,
    trace_tool,
)
from yleum_api.services.generation import recovery_execution, recovery_tool_receipts
from yleum_api.services.generation.recovery_actions import PureCheckCapability
from yleum_api.services.generation.recovery_contract import (
    ContinuationClaim,
    ExecutionCursor,
    RecoveryIdentity,
    RecoveryStateV2,
)
from yleum_api.services.generation.recovery_reconciliation import (
    ToolEffectCursor,
    tool_effect_scope,
)
from yleum_api.services.project_cell_proofs import ProofIdentity


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("JWT_SECRET", "unit-test-settings-only" * 2)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _text_action(name, args):
    return f'<omnia:action name="{name}">{json.dumps(args)}</omnia:action>'


async def _run(engine, monkeypatch, turns, execute, emit, **options):
    if engine == "text":
        actions = iter(action for turn in turns for action in turn)

        async def complete(*_args, **_kwargs):
            return _text_action(*next(actions))

        return await agent_builder._run_agent_segment(
            system_prompt="s", user_prompt="u", model="m", execute=execute, emit=emit,
            complete=complete, require_green_before_done=True, max_steps=6, **options,
        )

    responses = iter(turns)

    async def call(*_args, **_kwargs):
        return {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": f"tu_{uuid4().hex}", "name": name, "input": args}
            for name, args in next(responses)
        ]}

    monkeypatch.setattr(agent_native, "_call_messages", call)
    return await agent_native._run_native_segment(
        system="s", task="u", execute=execute, emit=emit, **options,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["text", "native"])
async def test_attempt_intent_precedes_each_dispatch_and_diagnostics_keep_no_shell_text(
    engine, monkeypatch,
):
    events = []
    sent = []

    async def emit(event, data):
        events.append((event, data))

    secret = "PRIVATE-SHELL-ARGUMENT"

    async def execute(action):
        if action.name == "bash":
            sent.append(events[-1])
            record_shell_diagnostics({
                "exit_code": 127, "timed_out": False, "shell_outcome": "terminal",
                "timings_ms": {"dispatch": 9, "workspace_sync": 12, "other": 42},
                "stdout": secret,
            })
            return {"ok": False, "detail": secret}
        return {"ok": True}

    turns = [
        [("done", {"summary": "early"})],
        [("bash", {"cmd": f"/usr/bin/node {secret}"})] * 2,
        [("build", {})],
    ]
    if engine == "text":
        turns.append([("runtime_check", {"path": "/"})])
    turns.append([("done", {"summary": "verified"})])
    result = await _run(engine, monkeypatch, turns, execute, emit)

    assert result.done
    assert [event for event, _ in sent] == ["agent.tool_started"] * 2
    starts = [data["tool_diagnostic"] for _, data in sent]
    assert len({start["call_id"] for start in starts}) == 2
    assert all(UUID(start["call_id"]).version == 4 for start in starts)
    shell_steps = [data for event, data in events
                   if event == "agent.step" and data["action"] == "bash"]
    assert [data["tool_diagnostic"]["call_id"] for data in shell_steps] == [
        start["call_id"] for start in starts
    ]
    for data in shell_steps:
        diagnostic = data["tool_diagnostic"]
        assert diagnostic["state"] == "failed"
        assert diagnostic["reason_code"] == "nonzero_exit"
        assert diagnostic["exit_code"] == 127
        assert diagnostic["command_family"] == "node"
        assert diagnostic["timings_ms"] == {"dispatch": 9, "workspace_sync": 12}
        assert diagnostic["elapsed_ms"] >= 0
    assert secret not in repr(events)
    blocked = [data["tool_diagnostic"] for _, data in events if data.get("action") == "done"
               and "tool_diagnostic" in data]
    assert [diagnostic["reason_code"] for diagnostic in blocked] == ["done_blocked"]
    assert blocked[0]["state"] == "skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["text", "native"])
@pytest.mark.parametrize("finish_available", [False, True])
async def test_cancelled_tool_finishes_with_the_original_cancelled_error(
    engine, finish_available, monkeypatch,
):
    events = []
    original = asyncio.CancelledError("private cancellation context")

    async def emit(event, data):
        events.append((event, data))
        if event == "agent.tool_finished" and not finish_available:
            raise RuntimeError("recorder unavailable")

    async def execute(_action):
        raise original

    with pytest.raises(asyncio.CancelledError) as error:
        await _run(engine, monkeypatch, [[("bash", {"cmd": "node"})]], execute, emit)

    assert error.value is original
    assert [event for event, _ in events] == ["agent.tool_started", "agent.tool_finished"]
    started, finished = (data["tool_diagnostic"] for _, data in events)
    assert finished["call_id"] == started["call_id"]
    assert finished["state"] == "cancelled"
    assert "private cancellation context" not in repr(events)


@pytest.mark.asyncio
async def test_stalled_intent_recorder_cannot_block_or_retry_a_tool(monkeypatch):
    events = []
    actions = []

    async def emit(event, data):
        events.append((event, data))
        if event == "agent.tool_started" and data["action"] == "bash":
            await asyncio.Event().wait()

    async def execute(action):
        actions.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_tool_diagnostics, "_LIFECYCLE_WRITE_TIMEOUT_SECONDS", 0.01)
    async with asyncio.timeout(1):
        result = await _run("text", monkeypatch, [
            [("bash", {"cmd": "node"})], [("build", {})],
            [("runtime_check", {"path": "/"})], [("done", {})],
        ], execute, emit)

    assert result.done and actions == ["bash", "build", "runtime_check"]
    started, finished = [data["tool_diagnostic"] for _, data in events if data["action"] == "bash"]
    assert started["call_id"] == finished["call_id"]
    assert finished["state"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(("shell_outcome", "hint", "state", "reason"), [
    ("not_started", "reader_unavailable", "skipped", "reader_unavailable"),
    ("not_started", None, "skipped", "dispatch_rejected"),
    ("unknown", None, "unknown", "outcome_unknown"),
])
async def test_shell_unavailable_distinguishes_rejected_and_uncertain_dispatch(
    shell_outcome, hint, state, reason, monkeypatch,
):
    events = []

    async def emit(event, data):
        events.append((event, data))

    async def execute(action):
        if action.name == "bash":
            record_shell_diagnostics({
                "shell_outcome": shell_outcome, "reason_code": hint, "exit_code": 126,
            })
            return {"ok": False, "error": "local transport details"}
        return {"ok": True}

    result = await _run("native", monkeypatch, [
        [("bash", {"cmd": "node"})], [("build", {}), ("done", {})],
    ], execute, emit)

    assert result.done
    [(before_event, before), (after_event, after)] = [
        (event, data["tool_diagnostic"]) for event, data in events if data.get("action") == "bash"
    ]
    assert (before_event, after_event) == ("agent.tool_started", "agent.step")
    assert before["call_id"] == after["call_id"]
    assert (after["state"], after["reason_code"]) == (state, reason)
    assert "exit_code" not in after
    assert "local transport details" not in repr(events)


def test_diagnostic_whitelist_drops_unknown_values_and_nested_keys():
    call_id = str(uuid4())
    assert safe_tool_diagnostic({
        "call_id": call_id, "state": "failed", "reason_code": "private value",
        "elapsed_ms": True, "exit_code": -9, "command_family": "/home/private/node",
        "timings_ms": {"dispatch": 10, "workspace_sync": -1, "stderr": "private"},
        "output": {"token": "private"},
    }) == {"call_id": call_id, "state": "failed", "exit_code": -9,
           "timings_ms": {"dispatch": 10}}
    assert safe_tool_diagnostic({"call_id": "private", "state": "started"}) == {}


@pytest.mark.asyncio
async def test_text_discovery_pause_reports_the_guard_without_a_shell_intent(monkeypatch):
    events = []
    actions = []

    async def emit(event, data):
        events.append((event, data))

    async def execute(action):
        actions.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_builder, "_NO_WRITE_NUDGE_AT", 1)
    result = await _run("text", monkeypatch, [
        [("list_dir", {})], [("bash", {"cmd": "node"})], [("build", {})],
        [("runtime_check", {"path": "/"})], [("done", {})],
    ], execute, emit)

    assert result.done and result.steps == 5
    assert actions == ["list_dir", "build", "runtime_check"]
    [(event, data)] = [(event, data) for event, data in events if data.get("action") == "bash"]
    assert event == "agent.step" and data["tool_diagnostic"]["state"] == "skipped"
    assert data["tool_diagnostic"]["reason_code"] == "discovery_pause"


@pytest.mark.asyncio
async def test_native_pure_check_cache_reuses_proof_without_second_intent(monkeypatch):
    events = []
    calls = []
    identity = ProofIdentity(uuid4(), uuid4(), 1, *(["a" * 64] * 6), "fixture-v1", "b" * 64)

    async def pure_check(_action):
        return PureCheckCapability(identity, "tsc --noEmit")

    async def emit(event, data):
        events.append((event, data))

    async def execute(action):
        calls.append(action.name)
        return {"ok": True}

    result = await _run("native", monkeypatch, [[("build", {}), ("build", {}), ("done", {})]],
                        execute, emit, pure_check=pure_check)

    assert result.done and result.steps == 1
    assert calls == ["build"]
    build_trace = [(event, data["tool_diagnostic"]) for event, data in events
                   if data.get("action") == "build"]
    assert [(event, data["state"]) for event, data in build_trace] == [
        ("agent.tool_started", "started"), ("agent.step", "succeeded"), ("agent.step", "skipped"),
    ]
    assert build_trace[2][1]["reason_code"] == "cached_result"


@pytest.mark.asyncio
async def test_cold_effect_receipt_replay_has_no_start_and_preserves_observation():
    cursor = ToolEffectCursor(ContinuationClaim(
        run_id=uuid4(), token=uuid4(), attempt_seq=1, worker_id="test-worker",
        expires_at=datetime.now(UTC),
    ), state_revision=1, logical_step_seq=1, tool_call_id="cold", reconcile_only=True)
    events = []
    receipt = {"ok": True, "content": "known source"}

    async def emit(event, data):
        events.append((event, data))

    async def reconcile(_action):
        record_shell_diagnostics({"exit_code": 0, "timings_ms": {"workspace_sync": 12}})
        return receipt

    action = agent_builder.Action("write_file", {"path": "src/page.ts", "content": "known source"})
    with tool_effect_scope(cursor):
        async with trace_tool(emit, 3, action) as invocation:
            result = await invocation.execute(reconcile)

    assert result is receipt and set(result) == {"ok", "content"}
    [(event, data)] = events
    assert event == "agent.tool_finished"
    diagnostic = data["tool_diagnostic"]
    assert diagnostic["state"] == "skipped" and diagnostic["reason_code"] == "replayed_result"
    assert "elapsed_ms" not in diagnostic and "timings_ms" not in diagnostic


@pytest.fixture
def _tool_cursor(monkeypatch):
    identity = RecoveryIdentity.model_construct(run_id=uuid4(), project_id=uuid4(), user_id=uuid4())
    state = RecoveryStateV2.model_construct(
        identity=identity, state_revision=3,
        continuation_claim=ContinuationClaim(
            run_id=identity.run_id, token=uuid4(), attempt_seq=1, worker_id="test-worker",
            expires_at=datetime.now(UTC),
        ),
        execution=ExecutionCursor.model_construct(
            substep="response_validated", logical_step_seq=8, next_tool_index=0,
            provider_effect_id=uuid4(),
        ),
    )
    committed = []
    receipt = {}

    async def commit(_factory, *, previous, execution, context):
        next_state = previous.model_copy(update={"execution": execution})
        committed.append((next_state, context))
        return next_state

    async def save(driver, execution, *, context):
        driver.state = driver.state.model_copy(update={"execution": execution})
        driver.context = context

    async def load_result(_factory, **_key):
        return receipt.get("observation")

    async def save_result(_factory, *, observation, **_key):
        receipt["observation"] = observation

    async def require_reconciliation(_factory, **_key):
        assert receipt["observation"] is not None

    async def wait(driver):
        raise recovery_execution.GenerationYielded(
            run_id=driver.state.identity.run_id, checkpoint=None, attempt_seq=1,
            phase="waiting_dependency",
        )

    monkeypatch.setattr(recovery_execution, "commit_execution_cursor", commit)
    monkeypatch.setattr(recovery_execution.ExecutionDriver, "save", save)
    monkeypatch.setattr(recovery_execution.ExecutionDriver, "wait", wait)
    monkeypatch.setattr(recovery_tool_receipts, "load_tool_result", load_result)
    monkeypatch.setattr(recovery_tool_receipts, "save_tool_result", save_result)
    monkeypatch.setattr(
        recovery_tool_receipts, "require_command_reconciliation", require_reconciliation,
    )
    return state, committed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cold", "consumed", "indeterminate"])
async def test_durable_restart_closes_the_original_intent_without_dispatch(kind, _tool_cursor):
    cold = kind == "cold"
    state, committed = _tool_cursor
    action = agent_builder.Action("bash", {"cmd": "rg needle"})
    receipt = {"ok": False} if cold else {"ok": False, "exit_code": 126}
    if kind == "indeterminate":
        receipt["indeterminate"] = True
    started = []
    finished = []
    calls = []

    async def emit_before_crash(event, data):
        if event == "agent.tool_started":
            assert committed == []
            started.append(data["tool_diagnostic"])

    async def execute(_action):
        assert len(committed) == 1
        calls.append("dispatch")
        return receipt

    driver = recovery_execution.ExecutionDriver(None, state, {}, None)
    async with trace_tool(emit_before_crash, 0, action) as invocation:
        await driver.tool("bash-one", action, lambda: invocation.execute(execute))

    saved_state, saved_context = committed[0] if cold else (driver.state, driver.context)

    async def emit_after_restart(event, data):
        finished.append((event, data["tool_diagnostic"]))

    async def reconcile(_action):
        assert cold
        calls.append("reconcile")
        record_shell_diagnostics({
            "exit_code": 127, "replayed_result": True, "shell_outcome": "terminal",
            "timings_ms": {"workspace_sync": 9},
        })
        return receipt

    async def resume(executor):
        resumed = recovery_execution.ExecutionDriver(None, saved_state, saved_context, None)
        async with trace_tool(emit_after_restart, 0, action) as invocation:
            result = await resumed.tool("bash-one", action, lambda: invocation.execute(executor))
            invocation.finish_continuation(result)
            return result

    if cold:
        async def invalid_receipt(_action):
            calls.append("rejected reconciliation")
            record_shell_diagnostics({
                "exit_code": 128, "replayed_result": True, "shell_outcome": "terminal",
            })
            return {"ok": True}

        with pytest.raises(recovery_execution.GenerationYielded):
            await resume(invalid_receipt)
        assert finished == [("agent.tool_finished", {
            "call_id": started[0]["call_id"], "state": "unknown", "reason_code": "outcome_unknown",
            "command_family": "rg",
        })]
        finished.clear()

    result = await resume(reconcile)

    assert result is receipt
    assert calls == (["dispatch", "rejected reconciliation", "reconcile"] if cold else ["dispatch"])
    assert len(committed) == len(started) == len(finished) == 1
    event, diagnostic = finished[0]
    assert event == "agent.tool_finished"
    assert UUID(diagnostic["call_id"]).version == 5
    assert diagnostic["call_id"] == started[0]["call_id"] and diagnostic["command_family"] == "rg"
    if cold:
        assert diagnostic["state"] == "failed" and diagnostic["reason_code"] == "nonzero_exit"
        assert diagnostic["exit_code"] == 127 and diagnostic["reconciled"] is True
        assert "timings_ms" not in diagnostic
    else:
        assert "exit_code" not in diagnostic
        if kind == "consumed":
            assert diagnostic["state"] == "failed" and diagnostic["reconciled"] is True
        else:
            assert diagnostic["state"] == "unknown" and "reconciled" not in diagnostic


@pytest.mark.asyncio
async def test_cancelled_durable_intent_recording_leaves_no_tool_wait(_tool_cursor):
    state, committed = _tool_cursor
    action = agent_builder.Action("bash", {"cmd": "rg needle"})
    intent_waiting = asyncio.Event()

    async def emit(event, _data):
        if event == "agent.tool_started":
            intent_waiting.set()
            await asyncio.Event().wait()

    async def execute():
        pytest.fail("Cancelling before tool_wait must leave the physical action untouched")

    driver = recovery_execution.ExecutionDriver(None, state, {}, None)

    async def attempt():
        async with trace_tool(emit, 0, action):
            await driver.tool("bash-one", action, execute)

    task = asyncio.create_task(attempt())
    await asyncio.wait_for(intent_waiting.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert committed == [] and driver.state.execution.substep == "response_validated"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_unconfirmed_fresh_shell_result_waits_for_validated_recovery(
    cancelled, monkeypatch, _tool_cursor,
):
    state, committed = _tool_cursor
    action = agent_builder.Action("bash", {"cmd": "node"})
    events = []
    calls = []
    original_save = recovery_tool_receipts.save_tool_result
    failure = asyncio.CancelledError() if cancelled else RuntimeError("physical proof unavailable")

    async def save_result(factory, *, observation, **key):
        if calls == ["dispatch"]:
            raise failure
        await original_save(factory, observation=observation, **key)

    async def require_physical_receipt(_factory, **_key):
        assert calls == ["dispatch"]

    monkeypatch.setattr(recovery_tool_receipts, "save_tool_result", save_result)
    monkeypatch.setattr(
        recovery_tool_receipts, "require_command_reconciliation", require_physical_receipt,
    )

    async def emit(event, data):
        events.append((event, data["tool_diagnostic"]))

    async def execute(_action):
        calls.append("dispatch")
        record_shell_diagnostics({
            "shell_outcome": "terminal", "exit_code": 0, "timings_ms": {"dispatch": 3},
        })
        return {"ok": False}

    async def reconcile(_action):
        calls.append("reconcile")
        record_shell_diagnostics({
            "shell_outcome": "terminal", "exit_code": 0, "replayed_result": True,
        })
        return {"ok": True}

    async def attempt(driver, executor):
        async with trace_tool(emit, 0, action) as invocation:
            observation = await driver.tool("shell", action, lambda: invocation.execute(executor))
            invocation.finish_continuation(observation)

    driver = recovery_execution.ExecutionDriver(None, state, {}, None)
    error_type = asyncio.CancelledError if cancelled else recovery_execution.GenerationYielded
    with pytest.raises(error_type) as failed:
        await attempt(driver, execute)
    if cancelled:
        assert failed.value is failure

    [(pending_state, context)] = committed
    restarted = recovery_execution.ExecutionDriver(None, pending_state, context, None)
    await attempt(restarted, reconcile)

    assert calls == ["dispatch", "reconcile"]
    assert [event for event, _ in events] == [
        "agent.tool_started", "agent.tool_finished", "agent.tool_finished",
    ]
    started, uncertain, verified = [diagnostic for _, diagnostic in events]
    assert started["call_id"] == uncertain["call_id"] == verified["call_id"]
    assert uncertain["state"] == "unknown" and "exit_code" not in uncertain
    assert uncertain["reason_code"] == ("cancelled" if cancelled else "outcome_unknown")
    assert "reconciled" not in uncertain
    assert verified["state"] == "succeeded" and verified["exit_code"] == 0
    assert verified["reconciled"] is True


@pytest.mark.asyncio
async def test_durable_predispatch_refusal_remains_proven_on_yield(monkeypatch, _tool_cursor):
    state, _ = _tool_cursor
    action = agent_builder.Action("bash", {"cmd": "node"})
    events = []

    async def save_result(*_args, **_kwargs):
        raise RuntimeError("logical checkpoint unavailable")

    monkeypatch.setattr(recovery_tool_receipts, "save_tool_result", save_result)

    async def emit(event, data):
        events.append((event, data["tool_diagnostic"]))

    async def execute(_action):
        record_shell_diagnostics({
            "shell_outcome": "not_started", "reason_code": "context_mismatch", "exit_code": 126,
        })
        return {"ok": False}

    driver = recovery_execution.ExecutionDriver(None, state, {}, None)
    with pytest.raises(recovery_execution.GenerationYielded):
        async with trace_tool(emit, 0, action) as invocation:
            await driver.tool("shell", action, lambda: invocation.execute(execute))

    [(start, intent), (finish, outcome)] = events
    assert (start, finish) == ("agent.tool_started", "agent.tool_finished")
    assert intent["call_id"] == outcome["call_id"]
    assert outcome["state"] == "skipped" and outcome["reason_code"] == "context_mismatch"
    assert "exit_code" not in outcome and "reconciled" not in outcome


@pytest.mark.asyncio
async def test_confirmed_fresh_durable_result_preserves_executor_timing(monkeypatch, _tool_cursor):
    state, _ = _tool_cursor
    action = agent_builder.Action("bash", {"cmd": "node"})
    clock = 1.0
    events = []
    original_save = recovery_tool_receipts.save_tool_result

    async def save_result(factory, *, observation, **key):
        nonlocal clock
        clock += 5
        await original_save(factory, observation=observation, **key)

    monkeypatch.setattr(agent_tool_diagnostics, "monotonic", lambda: clock)
    monkeypatch.setattr(recovery_tool_receipts, "save_tool_result", save_result)

    async def emit(event, data):
        events.append((event, data["tool_diagnostic"]))

    async def execute(_action):
        nonlocal clock
        clock += 0.018
        record_shell_diagnostics({
            "shell_outcome": "terminal", "exit_code": 0, "timings_ms": {"dispatch": 4, "total": 16},
        })
        return {"ok": True}

    driver = recovery_execution.ExecutionDriver(None, state, {}, None)
    async with trace_tool(emit, 0, action) as invocation:
        observation = await driver.tool("shell", action, lambda: invocation.execute(execute))
        invocation.finish_continuation(observation)

    assert [event for event, _ in events] == ["agent.tool_started", "agent.tool_finished"]
    result = events[-1][1]
    assert result["state"] == "succeeded" and result["exit_code"] == 0
    assert result["elapsed_ms"] == 18 and result["timings_ms"] == {"dispatch": 4, "total": 16}
    assert "reconciled" not in result


@pytest.mark.parametrize(("detail", "expected"), [
    ("/bin/sh: 1: rg: not found\n", "rg"),
    ("[shell] sh: 1: rg: not found", "rg"),
    ("[foreign] sh: 1: rg: not found", None),
    ("bash: line 1: git: command not found", "git"),
    ("bash: curl: command not found", "curl"),
    ("git: command not found", None),
    ("bash: line 1: private_tool: command not found", None),
    ("bash: line 1: curl: command not found\nmore output", None),
])
def test_missing_executable_needs_an_exact_known_shell_diagnostic(detail, expected):
    assert missing_shell_executable(detail) == expected


@pytest.mark.asyncio
async def test_nested_shell_names_the_allowlisted_missing_executable():
    assert missing_shell_executable("[shell] sh: 1: private_executable: not found") is None
    events = []

    async def emit(event, data):
        events.append((event, data["tool_diagnostic"]))

    async def execute(_action):
        record_shell_diagnostics({
            "reason_code": "executable_not_found", "exit_code": 127, "shell_outcome": "terminal",
            "missing_executable": missing_shell_executable("[shell] sh: 1: rg: not found"),
        })
        return {"ok": False}

    action = agent_builder.Action("bash", {"cmd": "sh -c 'rg PRIVATE_PATH'"})
    async with trace_tool(emit, 0, action) as invocation:
        await invocation.execute(execute)

    assert [event for event, _ in events] == ["agent.tool_started", "agent.tool_finished"]
    diagnostic = events[-1][1]
    assert diagnostic["command_family"] == "sh" and diagnostic["missing_executable"] == "rg"
    assert diagnostic["reason_code"] == "executable_not_found"
    assert "PRIVATE_PATH" not in repr(events)
    for invalid in ({"reason_code": "timeout"}, {"missing_executable": "x"}):
        assert "missing_executable" not in safe_tool_diagnostic({**diagnostic, **invalid})


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "reason"), [
    ("", "empty_response"),
    ("PRIVATE_PAYLOAD", "missing_action"),
    ('<omnia:action name="PRIVATE_PAYLOAD">{}</omnia:action>', "invalid_tool"),
    ('<omnia:action name="edit_file">PRIVATE_PAYLOAD</omnia:action>', "invalid_tool_arguments"),
])
async def test_unparseable_text_reply_publishes_only_the_reason(reply, reason, capsys):
    replies = iter([reply, _text_action("done", {})])
    events = []

    async def complete(*_args, **_kwargs):
        return next(replies)

    async def execute(_action):
        pytest.fail("A rejected response and the done gate may not dispatch tools")

    async def emit(event, data):
        events.append((event, data))

    result = await agent_builder._run_agent_segment(
        system_prompt="s", user_prompt="u", model="m", execute=execute, emit=emit,
        complete=complete, max_steps=2,
    )

    assert result.done and result.steps == 2
    assert [event for event, _ in events] == ["agent.stalled", "agent.step"]
    diagnostic = events[0][1]["tool_diagnostic"]
    assert diagnostic["state"] == "skipped" and diagnostic["reason_code"] == reason
    captured = capsys.readouterr()
    assert f"reason={reason}" in captured.out
    assert "PRIVATE_PAYLOAD" not in captured.out + captured.err + repr(events)
