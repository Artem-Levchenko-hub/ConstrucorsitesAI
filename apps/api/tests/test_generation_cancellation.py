"""Task9 characterization; full real helper, no generation/model execution.

DB cases require repository conftest fixtures on disposable PostgreSQL;
exclude with -k 'not disposable_db' locally.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core import config
from yleum_api.models.generation_billing import (
    GenerationBillingIntent,
    GenerationBillingOutbox,
    GenerationBillingPolicy,
)
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.usage import Usage
from yleum_api.models.user import User
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.services import generation_runs, project_memory
from yleum_api.services.generation_billing import admit_policy

MARKER = "[Отменено пользователем]"


def target():
    # Change this one import seam only after extraction; expected behavior stays frozen.
    return generation_runs.apply_cancelled_generation_locked


def run_and_message(content="Partial answer  ", tokens_out=None, tokens_in=13):
    project_id, message_id = uuid4(), uuid4()
    run = GenerationRun(
        id=uuid4(),
        project_id=project_id,
        user_id=uuid4(),
        assistant_message_id=message_id,
        status="cancel_requested",
        error="preserve diagnostic",
        agent_state={"keep": "yes"},
    )
    message = Message(
        id=message_id,
        project_id=project_id,
        role="assistant",
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    return run, message


class Session:
    def __init__(self, run, message, operations):
        self.run, self.message, self.operations = run, message, operations
        self.trace = []

    async def get(self, model, key):
        # These characterization rows predate prospective billing admission.
        assert model is GenerationBillingPolicy and key == self.run.id
        self.trace.append("billing-policy")
        return None

    async def scalar(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "messages.id =" in sql and "messages.project_id =" in sql
        assert sql.endswith("FOR UPDATE")
        assert set(compiled.params.values()) == {self.run.assistant_message_id, self.run.project_id}
        self.trace.append("message-lock")
        if self.message and self.message.project_id == self.run.project_id:
            return self.message
        return None

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "project_cell_operations.generation_run_id =" in sql
        assert "project_cell_operations.status IN" in sql and sql.endswith("FOR UPDATE")
        values = list(compiled.params.values())
        assert self.run.id in values and ["pending", "waiting_capacity"] in values
        self.trace.append("operation-locks")
        selected = [
            op
            for op in self.operations
            if op.generation_run_id == self.run.id and op.status in {"pending", "waiting_capacity"}
        ]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: selected))

    @asynccontextmanager
    async def begin_nested(self):
        self.trace.append("savepoint")
        try:
            yield
        except Exception:
            self.trace.append("savepoint-rollback")
            raise

    async def commit(self):
        pytest.fail("The helper must not own the caller transaction")

    async def rollback(self):
        pytest.fail("The helper must not roll back the caller transaction")


@pytest.fixture
def memory_spy(monkeypatch):
    calls = []

    async def compile_memory(session, run):
        session.trace.append("memory")
        calls.append((run.status, run.finished_at))

    monkeypatch.setattr(generation_runs, "compile_terminal_run_memory", compile_memory)
    return calls


@pytest.mark.parametrize(
    "content,tokens_in,tokens_out,expected,in_expected,out_expected",
    [
        ("Partial answer  ", 13, None, f"Partial answer\n\n{MARKER}", 13, 0),
        ("", None, None, MARKER, 0, 0),
        (f"Partial\n\n{MARKER}", 7, None, f"Partial\n\n{MARKER}", 7, 0),
        ("Already completed", 11, 25, "Already completed", 11, 25),
        ("Completed empty usage", None, 0, "Completed empty usage", None, 0),
    ],
)
async def test_full_helper_message_contract(
    content,
    tokens_in,
    tokens_out,
    expected,
    in_expected,
    out_expected,
    memory_spy,
):
    run, message = run_and_message(content, tokens_out, tokens_in)
    session = Session(run, message, [])
    await target()(session, run)
    assert (message.content, message.tokens_in, message.tokens_out) == (
        expected,
        in_expected,
        out_expected,
    )
    assert run.status == "cancelled" and run.finished_at.tzinfo is UTC
    assert run.error == "preserve diagnostic" and run.agent_state == {"keep": "yes"}
    assert session.trace == ["message-lock", "operation-locks", "billing-policy", "memory"]
    assert memory_spy == [("cancelled", run.finished_at)]


async def test_full_helper_only_pending_operations_same_run_share_timestamp(memory_spy):
    run, message = run_and_message()
    statuses = [
        "pending",
        "waiting_capacity",
        "running",
        "completed",
        "failed",
        "cancelled",
        "indeterminate",
    ]
    old = datetime(2020, 1, 1, tzinfo=UTC)
    operations = [
        ProjectCellOperation(
            generation_run_id=run.id,
            status=status,
            capacity_reason="budget",
            next_attempt_at=old,
            finished_at=old,
        )
        for status in statuses
    ]
    operations.append(
        ProjectCellOperation(
            generation_run_id=uuid4(),
            status="pending",
            capacity_reason="other",
            next_attempt_at=old,
            finished_at=old,
        )
    )
    await target()(Session(run, message, operations), run)
    for op in operations[:2]:
        assert op.status == "cancelled"
        assert op.capacity_reason is None and op.next_attempt_at is None
        assert op.finished_at is run.finished_at
    assert [op.status for op in operations[2:]] == [*statuses[2:], "pending"]
    assert all(op.finished_at == old for op in operations[2:])
    assert operations[-1].capacity_reason == "other"


@pytest.mark.parametrize("kind", ["no-id", "absent-row", "foreign-project"])
async def test_full_helper_missing_or_foreign_assistant(kind, memory_spy):
    run, message = run_and_message()
    if kind == "no-id":
        run.assistant_message_id = None
    elif kind == "foreign-project":
        message.project_id = uuid4()
    session = Session(run, None if kind == "absent-row" else message, [])
    await target()(session, run)
    assert message.content == "Partial answer  " and message.tokens_out is None
    assert run.status == "cancelled"
    assert ("message-lock" in session.trace) == (kind != "no-id")


async def test_real_memory_compiler_failure_is_savepoint_local(monkeypatch):
    run, message = run_and_message()
    session = Session(run, message, [])
    settings = config.get_settings().model_copy(update={"use_project_memory": True})
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        generation_runs, "compile_terminal_run_memory", generation_runs.compile_terminal_run_memory
    )

    async def fail(_session, _run):
        raise RuntimeError("fixture memory compilation failure")

    monkeypatch.setattr(project_memory, "compile_project_memory_revision", fail)
    await target()(session, run)
    assert run.status == "cancelled" and message.tokens_out == 0
    assert session.trace[-2:] == ["savepoint", "savepoint-rollback"]


@pytest.mark.parametrize("operation_status", ["pending", "waiting_capacity"])
@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("foreign_message", [True, False])
async def test_disposable_db_full_helper_caller_transaction(
    operation_status,
    commit,
    foreign_message,
    test_engine,
    monkeypatch,
):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    settings = config.get_settings().model_copy(update={"use_project_memory": False})
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    owner = User(id=uuid4(), email=f"cancel-{uuid4().hex}@example.test", password_hash="fixture")
    project = Project(
        id=uuid4(),
        owner_id=owner.id,
        name="Cancel fixture",
        slug=f"cancel-{uuid4().hex}",
        template="blank",
    )
    foreign = Project(
        id=uuid4(),
        owner_id=owner.id,
        name="Other fixture",
        slug=f"other-{uuid4().hex}",
        template="blank",
    )
    message = Message(
        id=uuid4(),
        project_id=foreign.id if foreign_message else project.id,
        role="assistant",
        content="Partial",
        tokens_in=7,
    )
    run = GenerationRun(
        id=uuid4(),
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=message.id,
        status="cancel_requested",
        idempotency_key="cancel-fixture",
        prompt_hash="a" * 64,
    )
    workspace = ProjectCellWorkspace(
        id=uuid4(),
        project_id=project.id,
        owner_id=owner.id,
        provider="fixture",
        state="provisioning",
    )
    op = ProjectCellOperation(
        id=uuid4(),
        workspace_id=workspace.id,
        generation_run_id=run.id,
        status=operation_status,
        kind="ensure",
        idempotency_key="cancel-op",
        request_digest="b" * 64,
        capacity_reason="budget",
        next_attempt_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    usage = Usage(
        id=uuid4(),
        user_id=owner.id,
        project_id=project.id,
        message_id=message.id,
        run_id=run.id,
        model_id="fixture",
        tokens_in=7,
        tokens_out=2,
        cost_rub=Decimal("12.3456"),
    )
    async with factory() as session:
        session.add(owner)
        await session.flush()
        session.add_all([project, foreign])
        await session.flush()
        session.add(message)
        await session.flush()
        run.status = "running"
        session.add_all([run, workspace])
        await session.flush()
        await admit_policy(session, run.id, is_free=False)
        run.status = "cancel_requested"
        session.add_all([op, usage])
        await session.commit()
    async with factory() as session:
        locked = await session.scalar(
            select(GenerationRun).where(GenerationRun.id == run.id).with_for_update()
        )
        await target()(session, locked)
        if commit:
            await session.commit()
        else:
            await session.rollback()
    async with factory() as session:
        saved_run = await session.get(GenerationRun, run.id)
        saved_op = await session.get(ProjectCellOperation, op.id)
        saved_message = await session.get(Message, message.id)
        intent = await session.get(GenerationBillingIntent, run.id)
        assert (await session.get(GenerationBillingPolicy, run.id)).is_free is False
        assert await session.get(GenerationBillingOutbox, run.id) is None
        assert (await session.scalars(select(WalletCharge))).all() == []
        assert (await session.get(Usage, usage.id)).cost_rub == Decimal("12.3456")
        if commit:
            assert saved_run.status == saved_op.status == "cancelled"
            assert saved_run.finished_at == saved_op.finished_at
            assert saved_op.capacity_reason is None and saved_op.next_attempt_at is None
            assert saved_message.content == (
                "Partial" if foreign_message else f"Partial\n\n{MARKER}"
            )
            assert saved_message.tokens_out == (None if foreign_message else 0)
            assert saved_run.agent_state == {
                "generation_billing": {"outcome": "waived", "amount_rub": "0", "payment": "waived"}
            }
            assert intent.outcome == "waived" and intent.amount_rub == 0
            assert intent.user_id == owner.id and intent.billing_account_id is None
            assert intent.accepted_snapshot_id is None
            assert intent.usage_ids == [str(usage.id)]
            assert intent.receipt == {"reason": "cancelled"}
        else:
            assert saved_run.status == "cancel_requested" and saved_run.finished_at is None
            assert saved_op.status == operation_status and saved_op.capacity_reason == "budget"
            assert saved_message.content == "Partial" and saved_message.tokens_out is None
            assert saved_run.agent_state == {} and intent is None
