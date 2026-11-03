from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace

import pytest


def script():
    return importlib.import_module("scripts.cleanup_builds")


def setup_collectors(module, monkeypatch, *, archive_status="ok"):
    calls = []

    def archives(root, *, min_age_seconds, apply):
        calls.append(("archives", apply, min_age_seconds))
        return {"status": archive_status, "deleted_files": 0}

    async def images(root, loader, *, apply, min_age_seconds, registry_host):
        roots = loader()
        assert roots.image_ids == frozenset({"sha256:" + "a" * 64})
        calls.append(("images", apply, min_age_seconds, registry_host))
        return {"removed": 0, "candidates": 1}

    monkeypatch.setattr(module, "collect_archives", archives)
    monkeypatch.setattr(module, "collect_local_images", images)
    monkeypatch.setattr(
        module,
        "collect_roots",
        lambda root: SimpleNamespace(
            image_ids=frozenset({"sha256:" + "a" * 64}),
            image_refs=frozenset(),
            busy_workspaces=frozenset(),
        ),
    )
    return calls


def test_cli_defaults_to_non_destructive_full_sweep(tmp_path, monkeypatch, capsys):
    module = script()
    calls = setup_collectors(module, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cleanup", "--state-root", str(tmp_path)])
    assert module.main() == 0
    assert calls == [("archives", False, 7200), ("images", False, 7200, "registry.yleum.ru")]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_archive_abort_prevents_image_deletion(tmp_path, monkeypatch, capsys):
    module = script()
    calls = setup_collectors(module, monkeypatch, archive_status="aborted")
    monkeypatch.setattr(sys, "argv", ["cleanup", "--state-root", str(tmp_path), "--apply"])
    assert module.main() == 1
    assert calls == [("archives", True, 7200)]
    assert json.loads(capsys.readouterr().out)["status"] == "aborted"


def test_inventory_error_is_not_an_empty_protected_set(tmp_path, monkeypatch, capsys):
    module = script()
    setup_collectors(module, monkeypatch)

    def broken(root):
        raise RuntimeError("do-not-print-private-token")

    monkeypatch.setattr(module, "collect_roots", broken)
    monkeypatch.setattr(sys, "argv", ["cleanup", "--state-root", str(tmp_path), "--apply"])
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "do-not-print-private-token" not in output
    assert json.loads(output)["status"] == "aborted"


def test_minimum_grace_cannot_be_disabled(tmp_path, monkeypatch):
    module = script()
    calls = setup_collectors(module, monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cleanup", "--state-root", str(tmp_path), "--min-age-seconds", "7199", "--apply"],
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2 and not calls


def test_report_is_separate_from_application_state(tmp_path, monkeypatch, capsys):
    module = script()
    setup_collectors(module, monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    destination = tmp_path / "status.json"
    monkeypatch.setattr(
        sys, "argv", ["cleanup", "--state-root", str(state), "--report", str(destination)]
    )
    assert module.main() == 0
    assert json.loads(destination.read_text(encoding="utf-8"))["status"] == "ok"
    assert not list(state.iterdir())
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_report_cannot_change_protected_state(tmp_path, monkeypatch, capsys):
    module = script()
    calls = setup_collectors(module, monkeypatch)
    destination = tmp_path / "controller.json"
    monkeypatch.setattr(
        sys, "argv", ["cleanup", "--state-root", str(tmp_path), "--report", str(destination)]
    )
    assert module.main() == 1
    assert not destination.exists() and not calls
    assert json.loads(capsys.readouterr().out)["status"] == "aborted"


def test_image_phase_errors_fail_service_instead_of_hiding_failure(tmp_path, monkeypatch, capsys):
    module = script()
    setup_collectors(module, monkeypatch)

    async def images(*args, **kwargs):
        return {"removed": 0, "candidates": 1, "errors": 1}

    monkeypatch.setattr(module, "collect_local_images", images)
    monkeypatch.setattr(sys, "argv", ["cleanup", "--state-root", str(tmp_path), "--apply"])
    assert module.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "aborted" and report["images"]["errors"] == 1
