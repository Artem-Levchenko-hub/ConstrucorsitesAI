"""Pure boundary tests; SQL behavior is verified separately on PostgreSQL 16."""

import importlib

import pytest


def roles():
    spec = importlib.util.find_spec("yleum_orchestrator.services.project_database_roles")
    assert spec is not None, "project runtime/admin role separation is missing"
    return importlib.import_module(spec.name)


@pytest.mark.parametrize("password", ["", "short", "x" * 23, "x" * 24 + "\x00"])
def test_invalid_password_is_rejected_without_echoing_it(password):
    with pytest.raises(ValueError) as caught:
        roles().bootstrap_project_roles_sql(password, "m" * 32)
    assert password not in str(caught.value) if password else str(caught.value)


def test_admin_rotation_validates_password_too():
    with pytest.raises(ValueError):
        roles().bootstrap_project_roles_sql("r" * 32, "m" * 32, admin_password="weak")


def test_hba_has_no_blanket_trust_and_orders_role_rules_before_reject():
    lines = [line.split() for line in roles().project_role_hba().splitlines() if line]
    assert lines[0] == ["local", "all", "postgres", "peer"]
    assert all("trust" not in line for line in lines)
    runtime = next(i for i, line in enumerate(lines) if "omnia_project_runtime" in line)
    migrator = next(i for i, line in enumerate(lines) if "omnia_project_migrator" in line)
    assert lines[migrator][-2:] == ["127.0.0.1/32", "scram-sha-256"]
    reject = next(i for i, line in enumerate(lines) if "0.0.0.0/0" in line)
    assert runtime < reject and migrator < reject
