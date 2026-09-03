"""Portable manifest must validate isolation, not choose a product stack."""

import copy
import importlib
import importlib.util

import pytest
from pydantic import ValidationError


def manifest_type():
    name = "omnia_orchestrator.core.project_machine"
    assert importlib.util.find_spec(name) is not None, "portable manifest is missing"
    return importlib.import_module(name).MachineManifest


def payload():
    return {
        "version": 1,
        "tasks": [
            {"name": "install", "role": "bootstrap", "argv": ["sh", "install.sh"]},
            {"name": "test", "role": "test", "argv": ["python", "-m", "unittest"]},
        ],
        "services": [
            {
                "name": "api",
                "argv": ["python", "server.py"],
                "readiness": {"port": 8080, "path": "/health"},
                "resources": {
                    "cpu_cores": 0.25,
                    "memory_bytes": 67108864,
                    "disk_bytes": 1048576,
                    "pids": 32,
                },
            },
            {
                "name": "worker",
                "argv": ["ruby", "worker.rb"],
                "depends_on": ["api"],
                "resources": {
                    "cpu_cores": 0.25,
                    "memory_bytes": 67108864,
                    "disk_bytes": 1048576,
                    "pids": 32,
                },
            },
        ],
        "routes": [{"path": "/", "service": "api", "port": 8080}],
    }


def test_unrelated_stacks_and_install_commands_are_not_filtered():
    cls = manifest_type()
    value = payload()
    value["tasks"][0]["argv"] = ["sh", "-c", "apt-get update && pip install flask"]
    manifest = cls.model_validate(value)
    assert manifest.service_order() == ("api", "worker")
    assert manifest.services[1].argv == ["ruby", "worker.rb"]
    assert manifest.resource_request().memory_bytes == 134217728
    assert manifest.resource_request().pids == 64


def test_digest_is_canonical_and_changes_with_commands():
    cls = manifest_type()
    first = cls.model_validate(payload())
    value = dict(reversed(list(payload().items())))
    assert cls.model_validate(value).digest() == first.digest()
    value["tasks"][0]["argv"].append("--different")
    assert cls.model_validate(value).digest() != first.digest()


@pytest.mark.parametrize(
    "change",
    [
        {"cwd": "../../host"},
        {"cwd": "/etc"},
        {"cwd": "x/../y"},
        {"cwd": "C:\\host"},
        {"argv": []},
        {"argv": ["a\x00b"]},
        {"privileged": True},
        {"devices": ["/dev/sda"]},
        {"mounts": [{"volume": "../../host", "target": "/data"}]},
        {"mounts": [{"volume": "data", "target": "/run/secrets"}]},
        {"mounts": [{"volume": "data", "target": "/workspace"}]},
        {"mounts": [{"volume": "data", "target": "/proc"}]},
        {"resources": {"cpu_cores": float("inf")}},
    ],
)
def test_unsafe_commands_or_host_control_fields_fail_closed(change):
    cls = manifest_type()
    value = payload()
    value["services"][0].update(change)
    with pytest.raises(ValidationError):
        cls.model_validate(value)


@pytest.mark.parametrize("case", ["cycle", "missing", "duplicate", "route", "reserved"])
def test_graph_and_routes_are_validated(case):
    cls = manifest_type()
    value = payload()
    if case == "cycle":
        value["services"][0]["depends_on"] = ["worker"]
    elif case == "missing":
        value["services"][1]["depends_on"] = ["absent"]
    elif case == "duplicate":
        value["services"].append(copy.deepcopy(value["services"][0]))
    elif case == "route":
        value["routes"].append(copy.deepcopy(value["routes"][0]))
    else:
        value["routes"].append({"path": "/api/omnia/auth", "service": "api", "port": 8080})
    with pytest.raises(ValidationError):
        cls.model_validate(value)


def recovery_payload():
    value = payload()
    value["services"][0]["mounts"] = [{"volume": "data", "target": "/data"}]
    value["tasks"].extend(
        [
            {"name": "freeze", "role": "quiesce", "argv": ["sh", "freeze.sh"]},
            {"name": "restore", "role": "restore_check", "argv": ["sh", "restore.sh"]},
        ]
    )
    value["data_stores"] = [
        {
            "name": "facts",
            "volumes": ["data"],
            "quiesce_task": "freeze",
            "restore_check_task": "restore",
        }
    ]
    return value


def test_mounted_volume_with_declared_recovery_tasks_is_accepted():
    manifest = manifest_type().model_validate(recovery_payload())
    assert manifest.data_stores[0].restore_check_task == "restore"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"quiesce_task": "test"}, "invalid quiesce task reference"),
        ({"restore_check_task": "missing"}, "invalid restore_check task reference"),
        ({"volumes": ["absent"]}, "unmounted volume"),
    ],
)
def test_volume_recovery_rejects_each_invalid_reference(change, reason):
    value = recovery_payload()
    value["data_stores"][0].update(change)
    with pytest.raises(ValidationError, match=reason):
        manifest_type().model_validate(value)


@pytest.mark.parametrize("target", ["//", "//run/secrets", "//etc"])
def test_double_slash_mounts_cannot_bypass_protected_paths(target):
    value = payload()
    value["services"][0]["mounts"] = [{"volume": "data", "target": target}]
    with pytest.raises(ValidationError):
        manifest_type().model_validate(value)


@pytest.mark.parametrize("path", ["//api/omnia/auth", "/api/max", "/api/max/session"])
def test_platform_routes_and_ambiguous_authorities_are_reserved(path):
    value = payload()
    value["routes"].append({"path": path, "service": "api", "port": 8080})
    with pytest.raises(ValidationError):
        manifest_type().model_validate(value)
