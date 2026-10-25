"""The real reader observer permits isolated font jobs without any exemption."""

import importlib.util
import sys
from pathlib import Path

import pytest

from yleum_orchestrator.services.font_validation import LIFETIME, WORKER


@pytest.fixture
def observer(monkeypatch):
    path = Path(__file__).resolve().parents[3] / "infra/release/generation-reader-inventory.py"
    name = "font_validation_reader_boundary_observer"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def job_command():
    return ["/usr/bin/timeout", "-s", "KILL", str(LIFETIME),
            "/app/.venv/bin/python", "-I", WORKER]


def worker_process(group):
    return {
        "host_pid": 200,
        "parent": 100,
        "role": None,
        "argv": ["/app/.venv/bin/python", "-I", WORKER],
        "cgroup": "0::" + group,
    }


def registered_readers():
    commands = {
        "api": ["uvicorn", "yleum_api.main:app"],
        "worker": ["python3", "-m", "yleum_api.workers.run"],
        "generation-worker": ["python3", "-m", "yleum_api.workers.generation"],
    }
    return [{
        "name": "yleum-prod-" + role, "service": role, "command": command,
        "configured_image": "omnia-api:" + "b" * 40, "image": "sha256:" + "c" * 64,
        "id": str(index) * 64, "pid": index, "status": "running", "running": True,
    } for index, (role, command) in enumerate(commands.items(), 1)]


@pytest.mark.parametrize("group", ["/docker/api-reader", "/docker/api-reader/child"])
def test_python_font_worker_inside_api_reader_cgroup_still_fails_closed(observer, group):
    process = worker_process(group)
    assert observer.process_role(process["argv"]) is None
    with pytest.raises(ValueError):
        observer.require_no_hidden_readers({200: process}, ["/docker/api-reader"])


def test_api_image_tag_makes_standalone_job_an_unregistered_reader(observer):
    row = {
        "name": "yleum-font-validation-" + "a" * 32,
        "command": job_command(),
        "configured_image": "omnia-api:" + "b" * 40,
        "status": "running",
    }
    assert observer.process_role(row["command"]) is None
    assert observer.is_reader_container(row) is True
    readers = registered_readers()
    assert len(observer.classify_containers(readers, "core")) == 3
    with pytest.raises(ValueError):
        observer.classify_containers([*readers, row], "core")


def test_immutable_isolated_job_and_worker_do_not_enter_reader_inventory(observer):
    row = {
        "name": "yleum-font-validation-" + "a" * 32,
        "command": job_command(),
        "configured_image": "sha256:" + "c" * 64,
        "status": "running",
    }
    assert observer.process_role(row["command"]) is None
    assert observer.is_reader_container(row) is False
    readers = registered_readers()
    assert observer.classify_containers([*readers, row], "core") == {
        reader["service"]: reader for reader in readers
    }
    worker = worker_process("/docker/isolated-font-job")
    supervisor = {**worker, "host_pid": 100, "parent": 1, "argv": job_command()}
    observer.require_no_hidden_readers(
        {100: supervisor, 200: worker}, ["/docker/api-reader", "/system.slice/orchestrator"],
    )


def test_immutable_image_does_not_exempt_a_real_reader_command(observer):
    row = {
        "name": "yleum-font-validation-" + "a" * 32,
        "command": ["/app/.venv/bin/python", "-m", "yleum_api.workers.generation"],
        "configured_image": "sha256:" + "c" * 64,
        "status": "running",
        "service": "font-validation",
    }
    assert observer.process_role(row["command"]) == "generation-worker"
    assert observer.is_reader_container(row) is True
    with pytest.raises(ValueError):
        observer.classify_containers([*registered_readers(), row], "core")
