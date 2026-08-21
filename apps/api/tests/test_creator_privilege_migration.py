from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest


def test_creator_grant_allows_a_truly_empty_fresh_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("migrations.versions.0044_creator_account_privileges")

    class _Connection:
        def scalar(self, _statement: object) -> int:
            return 0

        def execute(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            pytest.fail("an empty fresh install must not attempt an account grant")

    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection())

    migration.upgrade()


def test_creator_grant_still_fails_closed_on_nonempty_database_without_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("migrations.versions.0044_creator_account_privileges")

    class _Connection:
        def scalar(self, _statement: object) -> int:
            return 3

        def execute(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(rowcount=0)

    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection())

    with pytest.raises(RuntimeError, match="expected exactly one existing account"):
        migration.upgrade()


def test_creator_lifetime_grant_allows_a_truly_empty_fresh_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module("migrations.versions.0045_creator_lifetime_business")

    class _Connection:
        def scalar(self, _statement: object) -> int:
            return 0

    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Connection())
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda *_args, **_kwargs: pytest.fail(
            "an empty fresh install must not attempt a lifetime grant"
        ),
    )

    migration.upgrade()
