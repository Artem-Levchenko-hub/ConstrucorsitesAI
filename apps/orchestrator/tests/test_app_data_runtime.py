from types import SimpleNamespace
from uuid import uuid4

import pytest


def test_disabled_provider_has_no_key_mount_or_database_probe():
    from omnia_orchestrator.services.app_data_runtime import prepare_core_keys

    assert prepare_core_keys(SimpleNamespace(), uuid4(), None, None) is None


def test_missing_key_history_cannot_be_recreated_over_ciphertext(tmp_path, monkeypatch):
    from omnia_orchestrator.services import app_data_runtime as runtime

    allowed = []

    class Keys:
        def prepare(self, project_id, *, allow_create):
            allowed.append(allow_create)
            return tmp_path / "ring-test.json"

    monkeypatch.setattr(runtime, "key_manager", lambda _: Keys())
    pg = SimpleNamespace(exec_run=lambda *a, **kw: SimpleNamespace(exit_code=0, output=b"1\n"))
    assert runtime.prepare_core_keys(
        SimpleNamespace(cell_data_vault_address="configured"), uuid4(), pg, None
    )
    assert allowed == [False]


def test_bound_project_never_recreates_missing_key_history(tmp_path, monkeypatch):
    from omnia_orchestrator.services import app_data_runtime as runtime

    allowed = []

    class Keys:
        def prepare(self, project_id, *, allow_create):
            allowed.append(allow_create)
            return tmp_path / "ring-test.json"

    monkeypatch.setattr(runtime, "key_manager", lambda _: Keys())
    marker = tmp_path / "bound.json"
    project = uuid4()
    marker.write_text('{"project_id":"' + str(project) + '"}')
    marker.chmod(0o600)
    assert runtime.prepare_core_keys(
        SimpleNamespace(cell_data_vault_address="configured"), project, None, marker
    )
    assert allowed == [False]


def test_failed_database_inventory_does_not_generate_replacement_keys(monkeypatch):
    from omnia_orchestrator.services import app_data_runtime as runtime

    pg = SimpleNamespace(
        exec_run=lambda *a, **kw: SimpleNamespace(exit_code=1, output=b"private error")
    )
    with pytest.raises(runtime.AppDataKeyError, match="inventory"):
        runtime.prepare_core_keys(
            SimpleNamespace(cell_data_vault_address="configured"), uuid4(), pg, None
        )


def test_new_database_can_provision_and_marks_identity(tmp_path, monkeypatch):
    from omnia_orchestrator.services import app_data_runtime as runtime

    allowed = []

    class Keys:
        def prepare(self, project_id, *, allow_create):
            allowed.append(allow_create)
            return tmp_path / "ring-test.json"

    monkeypatch.setattr(runtime, "key_manager", lambda _: Keys())
    pg = SimpleNamespace(exec_run=lambda *a, **kw: SimpleNamespace(exit_code=0, output=b"0\n"))
    marker = tmp_path / "bound.json"
    assert runtime.prepare_core_keys(
        SimpleNamespace(cell_data_vault_address="configured"), uuid4(), pg, marker
    )
    assert allowed == [True]
    assert marker.exists()
