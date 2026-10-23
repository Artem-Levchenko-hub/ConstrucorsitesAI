"""Scope expansion preserves evidence and refuses lossy downgrades."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


def migration():
    path = (Path(__file__).resolve().parents[1]
            / "migrations/versions/0090_aitunnel_text_fallback.py")
    spec = importlib.util.spec_from_file_location("aitunnel_scope_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_expands_only_provider_and_terminal_checks(monkeypatch):
    module = migration()
    operations = Mock()
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    assert module.down_revision == "0089_shared_max_bots"
    assert len(module.revision) <= 32
    assert operations.drop_constraint.call_count == 2
    assert operations.create_check_constraint.call_count == 2
    assert "aitunnel" in operations.create_check_constraint.call_args_list[0].args[2]
    assert "upstream_refused" in operations.create_check_constraint.call_args_list[1].args[2]
    operations.execute.assert_not_called()


def test_downgrade_locks_and_refuses_new_evidence_without_mutation(monkeypatch):
    module = migration()
    operations = Mock()
    operations.get_bind.return_value.scalar.return_value = True
    monkeypatch.setattr(module, "op", operations)
    with pytest.raises(RuntimeError, match="aitunnel_fallback_evidence_present"):
        module.downgrade()
    lock_sql = operations.execute.call_args.args[0]
    assert "LOCK TABLE provider_calls, generation_provider_attempts" in lock_sql
    operations.drop_constraint.assert_not_called()
    operations.create_check_constraint.assert_not_called()


def test_safe_downgrade_restores_previous_constraints(monkeypatch):
    module = migration()
    operations = Mock()
    operations.get_bind.return_value.scalar.return_value = False
    monkeypatch.setattr(module, "op", operations)
    module.downgrade()
    calls = operations.create_check_constraint.call_args_list
    assert calls[0].args[2] == "provider_scope = 'llmgw'"
    assert "upstream_refused" not in calls[1].args[2]
