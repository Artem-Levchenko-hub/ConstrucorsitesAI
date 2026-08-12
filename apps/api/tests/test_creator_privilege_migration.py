from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Connection:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.executed: list[tuple[object, dict[str, str]]] = []

    def execute(self, statement: object, params: dict[str, str]) -> _Result:
        self.executed.append((statement, params))
        return _Result(self.rowcount)


def _load_migration():
    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "0044_creator_account_privileges.py"
    )
    spec = importlib.util.spec_from_file_location("creator_privilege_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_creator_grant_targets_one_explicit_email_and_writes_audit(monkeypatch) -> None:
    migration = _load_migration()
    connection = _Connection()
    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.upgrade()

    assert len(connection.executed) == 2
    grant_sql, audit_sql = (str(statement) for statement, _params in connection.executed)
    assert "SET role = 'admin', unlimited_generations = true" in grant_sql
    assert "WHERE email = :email AND is_anon = false" in grant_sql
    assert "creator.privileges.bootstrap" in audit_sql
    assert "admin_audit_events" in audit_sql
    assert connection.executed[0][1] == {"email": "undj00x03@gmail.com"}
    assert connection.executed[1][1] == {"email": "undj00x03@gmail.com"}


@pytest.mark.parametrize("rowcount", [0, 2])
def test_creator_grant_fails_closed_without_exact_account(monkeypatch, rowcount: int) -> None:
    migration = _load_migration()
    connection = _Connection(rowcount=rowcount)
    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    with pytest.raises(RuntimeError, match="expected exactly one existing account"):
        migration.upgrade()

    assert len(connection.executed) == 1
