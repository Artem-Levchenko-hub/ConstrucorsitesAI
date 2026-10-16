"""Active output recovery requires server evidence; pure reuse is batch-local."""

import importlib
import importlib.util
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from tests.test_agent_native_truncation import turn
from tests.test_generation_recovery_policy import compiler_case as compiler_case
from tests.test_generation_recovery_requirements import receipt_case as receipt_case
from yleum_api.services import agent_builder, agent_native, llm_client
from yleum_api.services.generation import recovery_policy
from yleum_api.services.generation.recovery_contract import IncompleteResponseBudget


def actions():
    name = "yleum_api.services.generation.recovery_actions"
    assert importlib.util.find_spec(name), "active verified progress boundary missing"
    return importlib.import_module(name)


def test_output_boundaries_keep_total_and_reject_simultaneous_progress():
    record = getattr(recovery_policy, "record_output_boundary", None)
    assert callable(record), "shared output budget transition missing"
    budget = IncompleteResponseBudget()
    for _ in range(3):
        budget = record(budget, truncated=True, verified_progress=False)
        budget = record(budget, truncated=False, verified_progress=True)
    assert budget.total == 3 and budget.since_progress == 0
    with pytest.raises(ValueError):
        record(budget, truncated=True, verified_progress=True)


async def test_legacy_writes_without_receipts_do_not_reset_output_streak():
    files = {"src/a.ts": "initial"}
    responses = iter(["bad", "edit", "bad", "edit", "bad", "done"])
    calls = 0

    async def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        kind = next(responses)
        if kind == "bad":
            raise llm_client.LLMOutputTruncated(requested_max_tokens=None, completion_tokens=100)
        if kind == "done":
            return '<omnia:action name="done">ready</omnia:action>'
        return (
            '<omnia:action name="write_file">'
            + json.dumps({"path": "src/a.ts", "content": f"updated {calls}"})
            + "</omnia:action>"
        )

    async def execute(action):
        assert action.name == "write_file"
        files[action.path] = action.args["content"]
        return {"ok": True, "content": files[action.path]}

    result = await agent_builder.run_agent_build(
        system_prompt="build",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        snapshot_files=AsyncMock(side_effect=lambda: dict(files)),
        max_steps=8,
    )
    assert calls == 5
    assert not result.done and result.stop_reason == "output_limit"


async def test_duplicate_pure_build_same_batch_executes_once(monkeypatch):
    a = actions()
    from uuid import uuid4

    from yleum_api.services.project_cell_proofs import ProofIdentity

    turns = iter([turn(("build", {}), ("build", {})), turn(("done", {"summary": "ready"}))])
    monkeypatch.setattr(
        agent_native, "_call_messages", AsyncMock(side_effect=lambda *a, **k: next(turns))
    )
    execute = AsyncMock(return_value={"ok": True})
    capability = AsyncMock(
        return_value=a.PureCheckCapability(
            identity=ProofIdentity(uuid4(), uuid4(), 7, *(["a" * 64] * 6), "profile", "b" * 64),
            command="readonly-static-check-v1",
        )
    )
    result = await agent_native._run_native_segment(
        system="build",
        task="edit",
        execute=execute,
        max_steps=3,
        pure_check=capability,
    )
    assert result.done
    assert execute.await_count == 1
    assert any(
        isinstance(message.get("content"), list)
        and [item.get("tool_use_id") for item in message["content"]] == ["call-0", "call-1"]
        for message in result.transcript
    )


def pure_capability():
    from uuid import uuid4

    from yleum_api.services.project_cell_proofs import ProofIdentity

    return actions().PureCheckCapability(
        identity=ProofIdentity(uuid4(), uuid4(), 7, *(["a" * 64] * 6), "profile", "b" * 64),
        command="readonly-static-check-v1",
    )


@pytest.mark.parametrize("middle", ["unknown_tool", "write_file"])
async def test_native_unknown_or_mutating_action_invalidates_batch(monkeypatch, middle):
    calls = [("build", {}), (middle, {"path": "src/a.ts", "content": "changed"}), ("build", {})]
    turns = iter([turn(*calls), turn(("done", {"summary": "ready"}))])
    monkeypatch.setattr(
        agent_native, "_call_messages", AsyncMock(side_effect=lambda *a, **k: next(turns))
    )
    capability = pure_capability()
    reader = AsyncMock(side_effect=lambda action: capability if action.name == "build" else None)
    execute = AsyncMock(return_value={"ok": True})
    await agent_native._run_native_segment(
        system="build", task="edit", execute=execute, max_steps=2, pure_check=reader
    )
    assert sum(call.args[0].name == "build" for call in execute.await_args_list) == 2


async def test_capability_failure_clears_existing_batch_cache():
    capability = pure_capability()
    reader = AsyncMock(
        side_effect=[
            capability,
            capability,
            RuntimeError("identity unavailable"),
            capability,
            capability,
        ]
    )
    batch = actions().PureCheckBatch(reader)
    execute = AsyncMock(return_value={"ok": True})
    action = agent_builder.Action("build", {})
    await batch.execute(action, execute)
    with pytest.raises(RuntimeError):
        await batch.execute(action, execute)
    await batch.execute(action, execute)
    assert execute.await_count == 2


@pytest.mark.parametrize(
    "field",
    [
        "workspace_id",
        "generation_run_id",
        "fencing_epoch",
        "workspace_revision",
        "dependency_digest",
        "schema_data_digest",
        "cell_manifest_digest",
        "base_image_digest",
        "toolchain_digest",
        "resource_profile_version",
        "build_config_digest",
        "command",
        "args",
    ],
)
async def test_full_physical_identity_command_and_args_invalidate_pure_check(field):
    from uuid import uuid4

    before = pure_capability()
    after = before
    args = {}
    if field == "command":
        after = replace(before, command="different-approved-command")
    elif field == "args":
        args = {"scope": "different"}
    else:
        value = uuid4() if field.endswith("_id") else 8 if field == "fencing_epoch" else "c" * 64
        after = replace(before, identity=replace(before.identity, **{field: value}))
    reader = AsyncMock(side_effect=[before, before, after, after])
    batch = actions().PureCheckBatch(reader)
    execute = AsyncMock(return_value={"ok": True})
    await batch.execute(agent_builder.Action("build", {}), execute)
    await batch.execute(agent_builder.Action("build", args), execute)
    assert execute.await_count == 2


@pytest.mark.parametrize("response", [None, {"pure": True, "kind": "fast_check"}])
async def test_full_build_is_not_assumed_pure(response):
    batch = actions().PureCheckBatch(AsyncMock(return_value=response))
    execute = AsyncMock(return_value={"ok": True, "pure": True, "kind": "fast_check"})
    for _ in range(2):
        await batch.execute(agent_builder.Action("build", {"pure": True}), execute)
    assert execute.await_count == 2


async def test_pure_cache_never_crosses_provider_response(monkeypatch):
    responses = iter([turn(("build", {})), turn(("build", {})), turn(("done", {}))])
    monkeypatch.setattr(
        agent_native, "_call_messages", AsyncMock(side_effect=lambda *a, **k: next(responses))
    )
    execute = AsyncMock(return_value={"ok": True})
    await agent_native.run_native_build(
        system="build",
        task="edit",
        execute=execute,
        max_steps=3,
        pure_check=AsyncMock(return_value=pure_capability()),
    )
    assert execute.await_count == 2


@pytest.mark.parametrize(
    "outcome",
    [
        {"ok": True, "environment_mutated": True},
        {"ok": True, "indeterminate": True},
        {"error": "unknown"},
        {"ok": "true"},
    ],
)
async def test_uncertain_or_mutating_outcomes_never_enter_cache(outcome):
    batch = actions().PureCheckBatch(AsyncMock(return_value=pure_capability()))
    execute = AsyncMock(return_value=outcome)
    for _ in range(2):
        await batch.execute(agent_builder.Action("build", {}), execute)
    assert execute.await_count == 2


@pytest.mark.parametrize("native", [False, True])
async def test_total_and_streak_survive_public_repair_calls(monkeypatch, native):
    recovery = actions().ResponseRecovery()
    execute = AsyncMock(return_value={"ok": True})

    async def bad(*args, **kwargs):
        if native:
            return turn(("bash", {"cmd": "FORBIDDEN"}), stop="max_tokens")
        raise llm_client.LLMOutputTruncated(requested_max_tokens=None, completion_tokens=100)

    for _ in range(2):
        if native:
            monkeypatch.setattr(agent_native, "_call_messages", bad)
            await agent_native.run_native_build(
                system="build",
                task="repair",
                execute=execute,
                max_steps=1,
                response_recovery=recovery,
            )
        else:
            await agent_builder.run_agent_build(
                system_prompt="build",
                user_prompt="repair",
                model="test",
                complete=bad,
                execute=execute,
                max_steps=1,
                response_recovery=recovery,
            )
    assert recovery.budget.total == recovery.budget.since_progress == 2
    execute.assert_not_awaited()


async def test_checked_fast_check_records_receipt_before_dict_collapse():
    from uuid import uuid4

    from yleum_api.services.generation.agent_runtime import checked_fast_check

    result = NS(id=uuid4(), outcome="red", redacted_detail="compiler error")
    collector = NS(record=AsyncMock())
    runtime = NS(
        migration_baseline=None,
        coordinator=NS(fast_check=AsyncMock(return_value=result)),
        verified_progress=collector,
    )
    assert await checked_fast_check(runtime, NS()) == {"ok": False, "detail": "compiler error"}
    collector.record.assert_awaited_once_with(result.id)


async def test_collector_one_shot_loses_authority_on_copy_and_rejects_stale(compiler_case):
    import pickle
    from copy import copy

    from yleum_api.services.project_cell_proofs import proof_identity_from_model

    a, c = actions(), compiler_case
    c.run.agent_state = {
        k: v for k, v in c.run.agent_state.items() if k != "max_named_behavior_receipt"
    }
    c.sync.commit()

    @asynccontextmanager
    async def factory():
        yield c.session

    async def physical():
        return replace(
            proof_identity_from_model(c.proof), workspace_revision=c.physical.workspace_revision
        )

    collector = a.VerifiedProgressCollector(
        factory=factory,
        run_id=c.run.id,
        project_id=c.project.id,
        user_id=c.run.user_id,
        current_identity=physical,
    )
    recovery = a.ResponseRecovery()
    recovery.truncated()
    red, _, _ = await c.compiler_receipt("1", False)
    await collector.record(red.id)
    assert await collector.take() is None
    green, _, _ = await c.compiler_receipt("2", True)
    await collector.record(green.id)
    receipt = await collector.take()
    assert receipt is not None
    await recovery.consume(AsyncMock(return_value=receipt))
    assert recovery.budget.since_progress == 1  # Genuine, but this receiver has no owner.
    other = a.VerifiedProgressCollector(
        factory=factory,
        run_id=c.run.id,
        project_id=c.project.id,
        user_id=c.run.user_id,
        current_identity=physical,
    )
    other.response_recovery.truncated()
    await other.response_recovery.consume(AsyncMock(return_value=receipt))
    assert other.response_recovery.budget.since_progress == 1
    recovery = collector.response_recovery
    recovery.truncated()
    await recovery.consume(AsyncMock(return_value=copy(receipt)))
    assert recovery.budget.since_progress == 1
    await recovery.consume(AsyncMock(return_value=pickle.loads(pickle.dumps(receipt))))
    assert recovery.budget.since_progress == 1
    await recovery.consume(AsyncMock(return_value={"verified_progress": True}))
    assert recovery.budget.since_progress == 1
    await recovery.consume(AsyncMock(return_value=receipt))
    assert recovery.budget.total == 1 and recovery.budget.since_progress == 0
    recovery.truncated()
    await recovery.consume(AsyncMock(return_value=receipt))
    assert recovery.budget.since_progress == 1
    await collector.record(green.id)
    assert await collector.take() is None
    # A once-green historical source does not become progress by ABA.
    red2, _, _ = await c.compiler_receipt("3", False)
    await collector.record(red2.id)
    await collector.record(green.id)
    assert await collector.take() is None


async def test_progress_pending_receipt_rechecks_source_before_consumption(coordinator_case):
    c = coordinator_case
    await c.execute(agent_builder.Action("build", {}))
    c.files["src/a.py"] = "value = 1"
    await c.execute(agent_builder.Action("build", {}))
    recovery = c.collector.response_recovery
    recovery.truncated()
    c.files["src/a.py"] = "value = 2"
    await recovery.consume(c.collector.take)
    assert recovery.budget.since_progress == 1


@pytest.mark.parametrize("native", [False, True])
async def test_outer_segments_share_one_budget_owner(monkeypatch, native):
    recovery = actions().ResponseRecovery()
    owners = []

    async def segment(**kwargs):
        owner = kwargs["response_recovery"]
        owners.append(owner)
        owner.truncated()
        result = agent_builder.AgentResult(
            done=False,
            summary="continue",
            steps=1,
            files={"src/a.ts": str(len(owners))},
            stop_reason="max_steps",
        )
        if kwargs.get("on_progress"):
            await kwargs["on_progress"](result)
        return result

    if native:
        monkeypatch.setattr(agent_native, "_run_native_segment", segment)
        await agent_native.run_native_build(
            system="build",
            task="edit",
            execute=AsyncMock(),
            max_segments=2,
            response_recovery=recovery,
        )
    else:
        monkeypatch.setattr(agent_builder, "_run_agent_segment", segment)
        await agent_builder.run_agent_build(
            system_prompt="build",
            user_prompt="edit",
            model="test",
            execute=AsyncMock(),
            max_segments=2,
            response_recovery=recovery,
        )
    assert len(owners) == 2 and all(owner is recovery for owner in owners)
    assert recovery.budget.total == recovery.budget.since_progress == 2


async def test_observer_failure_does_not_change_executor_outcome():
    recovery = actions().ResponseRecovery()
    recovery.truncated()
    await recovery.consume(AsyncMock(side_effect=RuntimeError("unavailable observer")))
    assert recovery.budget.total == recovery.budget.since_progress == 1


@pytest.mark.parametrize(
    "mismatch", ["run", "project", "user", "source", "serialized", "corrupt_recovery"]
)
async def test_collector_refuses_foreign_stale_or_serialized_evidence(
    compiler_case, monkeypatch, mismatch
):
    from uuid import uuid4

    from yleum_api.services.generation.recovery_contract import CompilerWitness
    from yleum_api.services.project_cell_proofs import proof_identity_from_model

    a, c = actions(), compiler_case
    c.run.agent_state = {
        k: v for k, v in c.run.agent_state.items() if k != "max_named_behavior_receipt"
    }
    if mismatch == "corrupt_recovery":
        c.run.agent_state = {**c.run.agent_state, "recovery_v1": None}
    c.sync.commit()

    @asynccontextmanager
    async def factory():
        yield c.session

    async def physical():
        return replace(
            proof_identity_from_model(c.proof),
            workspace_revision="f" * 64 if mismatch == "source" else c.physical.workspace_revision,
        )

    if mismatch == "serialized":
        original_loader = a.load_compiler_witness

        async def serialized(*args, **kwargs):
            witness = await original_loader(*args, **kwargs)
            return CompilerWitness.model_validate_json(witness.model_dump_json())

        monkeypatch.setattr(a, "load_compiler_witness", serialized)
    collector = a.VerifiedProgressCollector(
        factory=factory,
        run_id=uuid4() if mismatch == "run" else c.run.id,
        project_id=uuid4() if mismatch == "project" else c.project.id,
        user_id=uuid4() if mismatch == "user" else c.run.user_id,
        current_identity=physical,
    )
    for source, passed in [("1", False), ("2", True)]:
        receipt, _, _ = await c.compiler_receipt(source, passed)
        await collector.record(receipt.id)
    assert await collector.take() is None


async def test_accepted_functional_receipt_cannot_be_ignored_after_source_change(compiler_case):
    from yleum_api.services.project_cell_proofs import proof_identity_from_model

    c, a = compiler_case, actions()

    @asynccontextmanager
    async def factory():
        yield c.session

    async def physical():
        return replace(
            proof_identity_from_model(c.proof), workspace_revision=c.physical.workspace_revision
        )

    collector = a.VerifiedProgressCollector(
        factory=factory,
        run_id=c.run.id,
        project_id=c.project.id,
        user_id=c.run.user_id,
        current_identity=physical,
    )
    for source, passed in [("1", False), ("2", True)]:
        result, _, _ = await c.compiler_receipt(source, passed)
        await collector.record(result.id)
    assert await collector.take() is None


@pytest.fixture
async def coordinator_case(compiler_case, monkeypatch):
    """Real coordinator/proof SQL/loader; SQLite replaces PostgreSQL lease transport."""
    from datetime import UTC, datetime

    from yleum_api.models.project_cell import ProjectCellActivityLease
    from yleum_api.services import max_finalization as mf
    from yleum_api.services.generation.agent_runtime import checked_fast_check
    from yleum_api.services.project_cell_executor import ProjectCellCommandObservation
    from yleum_api.services.project_cell_proofs import proof_identity_from_model

    a, c = actions(), compiler_case
    c.run.agent_state = {
        k: v for k, v in c.run.agent_state.items() if k != "max_named_behavior_receipt"
    }
    c.sync.commit()
    files = {"src/a.py": "value ="}
    baseline = proof_identity_from_model(c.proof)

    class SessionAdapter:
        # Owning SQL functions stay real; only async transport is adapted.
        def add(self, row):
            c.sync.add(row)

        def expunge(self, row):
            c.sync.expunge(row)

        @asynccontextmanager
        async def begin_nested(self):
            with c.sync.begin_nested():
                yield

        async def get(self, *args, **kwargs):
            return c.sync.get(*args, **kwargs)

        async def scalar(self, *args, **kwargs):
            return c.sync.scalar(*args, **kwargs)

        async def flush(self):
            c.sync.flush()

        async def commit(self):
            c.sync.commit()

        async def rollback(self):
            c.sync.rollback()

        async def refresh(self, row):
            c.sync.refresh(row)

    @asynccontextmanager
    async def factory():
        yield SessionAdapter()

    async def physical():
        import hashlib

        digest = hashlib.sha256(files["src/a.py"].encode()).hexdigest()
        c.physical = replace(c.physical, workspace_revision=digest)
        return replace(baseline, workspace_revision=digest)

    commands = []

    async def run_role(role, operation_id):
        identity = await physical()
        passed = True
        if role.value == "fast_check":
            try:
                compile(files["src/a.py"], "src/a.py", "exec")
            except SyntaxError:
                passed = False
        commands.append((role.value, passed))
        return ProjectCellCommandObservation(
            operation_id,
            role,
            passed,
            False,
            "checked" if passed else "SyntaxError src/a.py",
            identity,
            identity,
            frozenset(),
        )

    async def activity(*, lease, work, **kwargs):
        result = await work()
        now = datetime.now(UTC)
        c.sync.add(
            ProjectCellActivityLease(
                operation_id=lease.operation_id,
                workspace_id=lease.workspace_id,
                generation_run_id=lease.generation_run_id,
                kind="command",
                state="completed" if result.ok else "failed",
                fencing_epoch=lease.fencing_epoch,
                proof_key=lease.proof_key,
                phase=lease.phase,
                started_at=now,
                heartbeat_at=now,
                finished_at=now,
                deadline_at=lease.deadline_at,
                log_bytes=0,
            )
        )
        c.sync.commit()
        return result

    monkeypatch.setattr(mf, "run_with_activity_lease", activity)
    coordinator = object.__new__(mf.MaxFinalizationCoordinator)
    coordinator.session_factory = factory
    coordinator.generation_run_id = c.run.id
    coordinator.emit = None
    coordinator.executor = NS(
        current_identity=physical,
        workspace_id=c.workspace.id,
        prove_restoration_adaptation=None,
        run_role=run_role,
        operation_status=AsyncMock(),
    )
    for method in (
        "_raise_persisted_infrastructure_failure",
        "_phase_started",
        "_phase_finished",
        "_counter",
    ):
        setattr(coordinator, method, AsyncMock())
    collector = a.VerifiedProgressCollector(
        factory=factory,
        run_id=c.run.id,
        project_id=c.project.id,
        user_id=c.run.user_id,
        current_identity=physical,
    )
    runtime = NS(migration_baseline=None, coordinator=coordinator, verified_progress=collector)
    executed = []

    async def execute(action):
        executed.append(action.name)
        if action.name == "build":
            return await checked_fast_check(runtime, NS())
        assert action.name == "write_file"
        files[action.path] = action.args["content"]
        return {"ok": True, "content": files[action.path]}

    return NS(
        collector=collector, execute=execute, files=files, commands=commands, executed=executed
    )


@pytest.mark.parametrize("native", [False, True])
async def test_three_truncations_with_verified_progress_continue(
    coordinator_case, monkeypatch, native
):
    c = coordinator_case
    recovery = c.collector.response_recovery
    sequence = iter(
        [
            ("bad", {}),
            ("build", {}),
            ("write_file", {"path": "src/a.py", "content": "value = 1"}),
            ("build", {}),
            ("bad", {}),
            ("write_file", {"path": "src/a.py", "content": "other ="}),
            ("build", {}),
            ("write_file", {"path": "src/a.py", "content": "other = 2"}),
            ("build", {}),
            ("bad", {}),
            ("build", {}),
            ("done", {"summary": "ready"}),
        ]
    )

    async def provider(*args, **kwargs):
        name, values = next(sequence)
        if name == "bad":
            if native:
                return turn(("bash", {"cmd": "FORBIDDEN_PARTIAL_ACTION"}), stop="max_tokens")
            raise llm_client.LLMOutputTruncated(requested_max_tokens=None, completion_tokens=100)
        if native:
            return turn((name, values))
        return f'<omnia:action name="{name}">{json.dumps(values)}</omnia:action>'

    if native:
        monkeypatch.setattr(agent_native, "_call_messages", provider)
        result = await agent_native.run_native_build(
            system="build",
            task="repair syntax",
            execute=c.execute,
            max_steps=16,
            initial_files=dict(c.files),
            response_recovery=recovery,
            take_verified_progress=c.collector.take,
        )
    else:
        result = await agent_builder.run_agent_build(
            system_prompt="build",
            user_prompt="repair syntax",
            model="test",
            complete=provider,
            execute=c.execute,
            max_steps=16,
            snapshot_files=AsyncMock(side_effect=lambda: dict(c.files)),
            response_recovery=recovery,
            take_verified_progress=c.collector.take,
        )
    assert result.done
    assert c.files["src/a.py"] == "other = 2"
    assert recovery.budget.total == 3 and recovery.budget.since_progress == 1
    assert "bash" not in c.executed
    assert [passed for role, passed in c.commands if role == "fast_check"] == [
        False,
        True,
        False,
        True,
    ]
    assert len(c.collector.history) == 4  # Repeated last GREEN is not new authority.


async def test_cancel_requested_by_observer_cannot_consume_receipt(coordinator_case):
    import asyncio

    c = coordinator_case
    await c.execute(agent_builder.Action("build", {}))
    c.files["src/a.py"] = "value = 1"
    await c.execute(agent_builder.Action("build", {}))
    recovery = c.collector.response_recovery
    recovery.truncated()

    async def cancelling_reader():
        receipt = await c.collector.take()
        asyncio.current_task().cancel()
        return receipt

    task = asyncio.create_task(recovery.consume(cancelling_reader))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert recovery.budget.since_progress == 1


async def test_native_automatic_source_check_consumes_verified_repair(
    coordinator_case, monkeypatch
):
    from yleum_api.core.config import get_settings

    c = coordinator_case
    monkeypatch.setattr(get_settings(), "use_max_finalization_coordinator", True)
    await c.execute(agent_builder.Action("build", {}))
    recovery = c.collector.response_recovery
    recovery.truncated()
    turns = iter(
        [turn(("write_file", {"path": "src/a.py", "content": "value = 1"})), turn(("done", {}))]
    )
    monkeypatch.setattr(
        agent_native, "_call_messages", AsyncMock(side_effect=lambda *a, **k: next(turns))
    )
    await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="repair syntax",
        execute=c.execute,
        max_steps=2,
        portable_cell=True,
        initial_files=dict(c.files),
        response_recovery=recovery,
        take_verified_progress=c.collector.take,
    )
    assert recovery.budget.total == 1 and recovery.budget.since_progress == 0
