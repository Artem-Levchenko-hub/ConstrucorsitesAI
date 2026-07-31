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
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    async def fetchval(self, query: str, *args: object) -> Decimal | None:
        self.statements.append((query, args))
        return self.balance_after

    async def execute(self, query: str, *args: object) -> str:
        self.statements.append((query, args))
        return "INSERT 0 1"


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)


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
    assert "RETURNING balance_rub" in wallet_sql
    assert wallet_args == (Decimal("12.5000"), user_id)
    assert "balance_after_rub" in ledger_sql
    assert ledger_args[4] == Decimal("87.5000")
    assert ledger_args[5] == f"usage:{usage_args[0]}"


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
