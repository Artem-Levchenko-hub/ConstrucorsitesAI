from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from omnia_gateway.core.errors import WalletEmptyError
from omnia_gateway.services import billing

pytestmark = pytest.mark.asyncio


class _AsyncContext:
    def __init__(self, value: object | None = None) -> None:
        self.value = value

    async def __aenter__(self) -> object | None:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeConnection:
    def __init__(self, balance_after: Decimal | None) -> None:
        self.balance_after = balance_after
        self.billing_account_id = uuid4()
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    async def fetchrow(
        self, query: str, *args: object
    ) -> dict[str, object] | None:
        self.statements.append((query, args))
        if self.balance_after is None:
            return None
        return {
            "balance_rub": self.balance_after,
            "billing_account_id": self.billing_account_id,
        }

    async def execute(self, query: str, *args: object) -> str:
        self.statements.append((query, args))
        return "INSERT 0 1"


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)


class _ReservationConnection:
    def __init__(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        requests: int = 2,
        cost_rub: Decimal = Decimal("20"),
        provider_cost_usd: Decimal = Decimal("0.10"),
        status: str = "running",
    ) -> None:
        self.user_id = user_id
        self.project_id = project_id
        self.requests = requests
        self.cost_rub = cost_rub
        self.provider_cost_usd = provider_cost_usd
        self.status = status
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.statements.append((query, args))
        if "FROM generation_runs" in query:
            return {
                "user_id": self.user_id,
                "project_id": self.project_id,
                "status": self.status,
            }
        if "FROM usage" in query:
            return {
                "requests": self.requests,
                "cost_rub": self.cost_rub,
                "provider_cost_usd": self.provider_cost_usd,
            }
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: object) -> str:
        self.statements.append((query, args))
        if "UPDATE usage" in query:
            return "UPDATE 1"
        if "INSERT INTO usage" in query:
            return "INSERT 0 1"
        if "pg_advisory_xact_lock" in query:
            return "SELECT 1"
        raise AssertionError(f"unexpected execute: {query}")


async def test_charge_records_balance_and_usage_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(Decimal("87.5000"))
    monkeypatch.setattr(billing, "get_pool", lambda: _FakePool(connection))
    user_id = uuid4()

    charge_id = await billing.charge(
        user_id=user_id,
        project_id=None,
        message_id=None,
        model_id="test-model",
        tokens_in=10,
        tokens_out=5,
        cost_rub=Decimal("12.5000"),
        description="test usage",
    )

    assert isinstance(charge_id, UUID)
    assert len(connection.statements) == 3
    wallet_sql, wallet_args = connection.statements[0]
    ledger_sql, ledger_args = connection.statements[1]
    usage_sql, usage_args = connection.statements[2]
    assert "RETURNING w.balance_rub, w.billing_account_id" in wallet_sql
    assert wallet_args == (user_id, Decimal("12.5000"))
    assert "balance_after_rub" in ledger_sql
    assert ledger_args[1] == connection.billing_account_id
    assert ledger_args[5] == Decimal("87.5000")
    assert ledger_args[6] == f"usage:{usage_args[0]}"


async def test_charge_does_not_write_when_conditional_debit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(None)
    monkeypatch.setattr(billing, "get_pool", lambda: _FakePool(connection))

    with pytest.raises(WalletEmptyError):
        await billing.charge(
            user_id=uuid4(),
            project_id=None,
            message_id=None,
            model_id="test-model",
            tokens_in=10,
            tokens_out=5,
            cost_rub=Decimal("12.5000"),
            description="test usage",
        )

    assert len(connection.statements) == 1


async def test_native_reservation_is_atomic_and_uses_run_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    project_id = uuid4()
    run_id = uuid4()
    connection = _ReservationConnection(user_id=user_id, project_id=project_id)
    monkeypatch.setattr(billing, "get_pool", lambda: _FakePool(connection))

    reservation = await billing.reserve_native_run_request(
        run_id=run_id,
        user_id=user_id,
        project_id=None,
        message_id=None,
        model_id="test-model",
        stage="native_agent",
        reserved_cost_rub=Decimal("100"),
        reserved_provider_cost_usd=Decimal("0.35"),
        max_requests=10,
        max_cost_rub=Decimal("200"),
        max_provider_cost_usd=Decimal("1.75"),
    )

    assert reservation.requests_before == 2
    assert reservation.cost_rub_before == Decimal("20")
    assert "pg_advisory_xact_lock" in connection.statements[0][0]
    assert "$1::uuid::text" in connection.statements[0][0]
    insert_sql, insert_args = connection.statements[-1]
    assert "native-budget-reservation" in insert_sql
    assert insert_args[2] == project_id
    assert insert_args[4] == run_id


async def test_native_reservation_rejects_budget_before_usage_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    project_id = uuid4()
    connection = _ReservationConnection(
        user_id=user_id,
        project_id=project_id,
        provider_cost_usd=Decimal("1.50"),
    )
    monkeypatch.setattr(billing, "get_pool", lambda: _FakePool(connection))

    with pytest.raises(billing.RunBudgetExceededError):
        await billing.reserve_native_run_request(
            run_id=uuid4(),
            user_id=user_id,
            project_id=project_id,
            message_id=None,
            model_id="test-model",
            stage="verification",
            reserved_cost_rub=Decimal("100"),
            reserved_provider_cost_usd=Decimal("0.35"),
            max_requests=10,
            max_cost_rub=Decimal("200"),
            max_provider_cost_usd=Decimal("1.75"),
        )

    assert not any("INSERT INTO usage" in query for query, _ in connection.statements)


async def test_charge_reconciles_existing_native_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    connection = _ReservationConnection(user_id=user_id, project_id=uuid4())
    monkeypatch.setattr(billing, "get_pool", lambda: _FakePool(connection))
    reservation_id = uuid4()

    await billing.charge(
        user_id=user_id,
        project_id=connection.project_id,
        message_id=None,
        run_id=uuid4(),
        model_id="test-model",
        tokens_in=100,
        tokens_out=10,
        cost_rub=Decimal("1.5"),
        description="settle reservation",
        free=True,
        provider_request_id="provider-1",
        provider_cost_usd=Decimal("0.01"),
        reserved_usage_id=reservation_id,
    )

    assert len(connection.statements) == 1
    update_sql, update_args = connection.statements[0]
    assert "UPDATE usage" in update_sql
    assert "native-budget-reservation" in update_sql
    assert update_args[0] == reservation_id
    assert update_args[9] == "provider-1"
