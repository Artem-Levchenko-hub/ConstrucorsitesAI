from dataclasses import replace
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.restoration_data_contract import DataContract
from omnia_orchestrator.services.restoration_database import read_controller_output, stage_policy


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


def test_protected_runtime_gets_nonowner_password_not_former_admin(tmp_path):
    value = backend(tmp_path)
    policy = stage_policy(value, DataContract(version=1), 2, blocked_deletes=[])
    env = value.project_database_env()
    assert env["PGUSER"] == "omnia_runtime"
    assert env["PGPASSWORD"] == policy["password"]
    assert "old-agent-password" not in str(env)
    assert value.project_database_env() == env


def test_hba_and_config_live_outside_project_and_are_readonly(tmp_path):
    value = backend(tmp_path)
    stage_policy(value, DataContract(version=1), 2, blocked_deletes=[])
    options = value._project_postgres_options("guard", 2)
    assert "unix_socket_directories=/tmp" in options["command"]
    assert "hba_file=/etc/omnia-pg-hba.conf" in options["command"]
    binds = {mount["bind"]: mount["mode"] for mount in options["volumes"].values()}
    assert binds["/etc/omnia-pg-hba.conf"] == "ro"
    assert binds["/etc/omnia-postgresql.conf"] == "ro"
    text = (tmp_path / str(value.workspace_id) / "postgres-hba.conf").read_text()
    assert "host all all 0.0.0.0/0 reject" in text
    assert "local all postgres trust" in text
    assert "host all postgres" not in text


def test_policy_retry_keeps_credentials_and_rejects_foreign_identity(tmp_path):
    import pytest

    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    value = backend(tmp_path)
    policy = stage_policy(value, DataContract(version=1), 2, blocked_deletes=[])
    assert stage_policy(value, DataContract(version=1), 2, blocked_deletes=[]) == policy
    with pytest.raises(CellIdentityConflict):
        replace(value, owner_id=uuid4()).project_database_env()


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
