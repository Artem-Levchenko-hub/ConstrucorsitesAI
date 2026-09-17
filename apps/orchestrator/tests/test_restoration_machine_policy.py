import json
from dataclasses import replace
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.restoration_database import read_controller_output


def backend(tmp_path):
    return DockerMachineBackend(
        client=None,
        workspace_id=uuid4(),
        project_id=uuid4(),
        owner_id=uuid4(),
        root=tmp_path,
        internal_network="test",
        workspace_volume="source",
        base_image="sha256:" + "a" * 64,
        guard_image="sha256:" + "b" * 64,
        postgres_image="sha256:" + "c" * 64,
        project_postgres_password="old-agent-password",
        project_postgres_memory_bytes=128 * 1024**2,
        project_postgres_cpu_cores=0.1,
        network_pool="172.20.0.0/16",
        denied_cidrs=("10.0.0.0/8",),
        cpu_cores=2,
        memory_bytes=2 * 1024**3,
        disk_bytes=2 * 1024**3,
        pids=512,
        resource_profile_version="docker-owner-cell-resources-v2",
        namespace="test",
    )


def _stale_policy(value):
    root = value.root / str(value.workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    # Written by the removed protected-database mode; it must never be read again.
    (root / "data-policy.json").write_text(json.dumps({
        "workspace_id": str(value.workspace_id), "project_id": str(value.project_id),
        "owner_id": str(value.owner_id), "epoch": 2, "password": "stale-runtime-password",
        "token_secret": "stale-token", "contract": {"version": 1, "tables": []},
        "blocked_deletes": [],
    }))
    (root / "postgres-hba.conf").write_text("local all all reject\n")


def test_runtime_always_connects_as_project_postgres_user_despite_stale_policy(tmp_path):
    value = backend(tmp_path)
    _stale_policy(value)
    env = value.project_database_env()
    assert env["PGUSER"] == "postgres"
    assert env["PGPASSWORD"] == "old-agent-password"
    assert env["DATABASE_URL"].startswith("postgresql://postgres:old-agent-password@")
    assert "omnia_runtime" not in json.dumps(env)
    assert "stale-runtime-password" not in json.dumps(env)
    assert replace(value, owner_id=uuid4()).project_database_env() == env


def test_project_postgres_uses_its_own_configuration_despite_stale_policy(tmp_path):
    value = backend(tmp_path)
    _stale_policy(value)
    options = value._project_postgres_options("guard", 2)
    assert "unix_socket_directories=" in options["command"]
    assert not any("hba_file" in item or "config_file" in item for item in options["command"])
    assert [mount["bind"] for mount in options["volumes"].values()] == ["/var/lib/postgresql/data"]


def _output(chunks, *, limit=16):
    from types import SimpleNamespace

    stream = iter(chunks)
    connection = SimpleNamespace(_sock=SimpleNamespace(recv=lambda _: next(stream, b"")))
    return read_controller_output(connection, max_bytes=limit)


def _frame(data, kind=1):
    return bytes([kind, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def test_controller_output_handles_fragmentation_without_returning_sql_errors():
    data = _frame(b"row") + _frame(b"private SQL", kind=2) + _frame(b"two")
    assert _output([data[:3], data[3:10], data[10:]]) == b"rowtwo"


@pytest.mark.parametrize(
    "chunks",
    [
        [_frame(b"a" * 17)],
        [_frame(b"a" * 10), _frame(b"b" * 7)],
        [_frame(b"row")[:-1]],
        [b"\x01\x00"],
        [_frame(b"invalid", kind=3)],
        [bytes([1, 0, 0, 0]) + (2**31).to_bytes(4, "big")],
    ],
)
def test_controller_output_rejects_unbounded_and_truncated_frames(chunks):
    with pytest.raises(CellResourceError):
        _output(chunks)
