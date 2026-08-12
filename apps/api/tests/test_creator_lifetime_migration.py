from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "0045_creator_lifetime_business.py"
    )
    spec = importlib.util.spec_from_file_location("creator_lifetime_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_creator_lifetime_grant_is_exact_audited_and_non_renewing(monkeypatch) -> None:
    migration = _load_migration()
    executed: list[str] = []
    constraints: list[tuple[object, ...]] = []
    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *args, **_kwargs: constraints.append(args),
    )
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))

    migration.upgrade()

    assert migration.CREATOR_EMAIL == "undj00x03@gmail.com"
    assert len(executed) == 1
    grant_sql = executed[0]
    assert "email = 'undj00x03@gmail.com' AND is_anon = false" in grant_sql
    assert "expected exactly one existing account" in grant_sql
    assert "expected exactly one billing account" in grant_sql
    assert "expected exactly one live subscription" in grant_sql
    assert "refuses an in-flight subscription checkout" in grant_sql
    assert "code = 'business' AND is_active = true" in grant_sql
    assert "'active',\n                    true,\n                    false" in grant_sql
    assert "creator.subscription.lifetime_business.bootstrap" in grant_sql
    assert constraints[0][0] == "ck_subscriptions_lifetime_shape"
    assert "current_period_end IS NULL" in constraints[0][2]

