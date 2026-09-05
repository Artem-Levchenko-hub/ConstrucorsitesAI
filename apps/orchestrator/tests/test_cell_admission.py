from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from omnia_orchestrator.core.cell_resources import (
    AdmissionDecision,
    CellResourceProfile,
    HostCapacitySnapshot,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate, DockerHostCapacityReader
from omnia_orchestrator.services.cell_reservations import ReservedCapacity


def _profile() -> CellResourceProfile:
    return CellResourceProfile(
        profile_version="docker-owner-cell-resources-v1",
        postgres_image="",
        redis_image="",
        backup_image="",
        bundle_cpu_cores=2.0,
        bundle_memory_bytes=4 * 1024**3,
        host_cpu_reserve_cores=2.0,
        host_memory_reserve_bytes=4 * 1024**3,
        required_free_disk_bytes=20 * 1024**3,
        host_disk_reserve_bytes=10 * 1024**3,
        required_free_inodes=100_000,
        host_inode_reserve=50_000,
        state_path="/opt/omnia-runtime/state/project-cells.json",
    )


def test_admission_preserves_all_headroom() -> None:
    profile = _profile()
    gate = CellAdmissionGate(profile)

    decision = gate.check(
        HostCapacitySnapshot(
            cpu_count=8,
            load_1m=1.0,
            memory_available_bytes=profile.host_memory_reserve_bytes
            + profile.bundle_memory_bytes
            - 1,
            disk_free_bytes=10**12,
            disk_free_inodes=10**7,
            active_bundle_count=0,
            disk_path="/daemon-disk",
        ),
        existing_bundle=False,
        running_bundle=False,
    )

    assert decision == AdmissionDecision(False, "insufficient_memory")


def test_capacity_uses_selected_daemon_root_not_projects_root() -> None:
    docker = SimpleNamespace(
        info=lambda: {"ID": "test-daemon", "DockerRootDir": "/daemon-disk"},
        api=SimpleNamespace(base_url="unix:///var/run/docker.sock"),
    )
    stat_calls: list[str] = []

    def statvfs(path: str) -> SimpleNamespace:
        stat_calls.append(path)
        values = {
            "/daemon-disk": SimpleNamespace(f_bavail=100, f_frsize=1024**3, f_favail=10**7),
            "/opt/omnia-runtime/state": SimpleNamespace(
                f_bavail=90, f_frsize=1024**3, f_favail=10**6
            ),
        }
        return values[path]

    reader = DockerHostCapacityReader(
        docker=docker,
        state_path="/opt/omnia-runtime/state/project-cells.json",
        statvfs=statvfs,
        meminfo_reader=lambda: 64 * 1024**3,
        loadavg_reader=lambda: (1.0, 0.5, 0.25),
        cpu_count_reader=lambda: 8,
        active_bundle_counter=lambda: 0,
    )

    snapshot = reader.read()

    assert stat_calls == ["/daemon-disk", "/opt/omnia-runtime/state"]
    assert snapshot.disk_path == "/daemon-disk"
    assert snapshot.disk_free_bytes == 90 * 1024**3
    assert [item.path for item in snapshot.filesystem_evidence] == [
        "/daemon-disk",
        "/opt/omnia-runtime/state",
    ]


def test_remote_or_unverifiable_daemon_fails_closed() -> None:
    docker = SimpleNamespace(
        info=lambda: {"ID": "remote", "DockerRootDir": "/var/lib/docker"},
        api=SimpleNamespace(base_url="ssh://remote-host"),
    )
    reader = DockerHostCapacityReader(
        docker=docker,
        meminfo_reader=lambda: 1,
    )
    reader.local_daemon_root_is_verifiable = False

    assert reader.read().failure_reason == "daemon_filesystem_unverifiable"


def test_unknown_endpoint_fails_closed_even_with_absolute_root() -> None:
    docker = SimpleNamespace(
        info=lambda: {"ID": "daemon", "DockerRootDir": "/var/lib/docker"},
        api=SimpleNamespace(base_url=""),
    )
    reader = DockerHostCapacityReader(
        docker=docker,
        statvfs=lambda _path: SimpleNamespace(f_bavail=1, f_frsize=1, f_favail=1),
        meminfo_reader=lambda: 1,
    )

    assert reader.read().failure_reason == "daemon_filesystem_unverifiable"


@pytest.mark.parametrize(
    ("endpoint", "failure_reason"),
    [
        ("unix:///var/run/docker.sock", None),
        ("npipe:////./pipe/docker_engine", None),
        ("http+docker://localhost", None),
        ("http+docker://localnpipe", None),
        ("tcp://127.0.0.1:2375", "daemon_filesystem_unverifiable"),
        ("http://localhost:2375", "daemon_filesystem_unverifiable"),
        ("https://127.0.0.1:2376", "daemon_filesystem_unverifiable"),
        ("http+docker://ssh", "daemon_filesystem_unverifiable"),
    ],
)
def test_daemon_endpoint_must_use_local_socket_transport(
    endpoint: str,
    failure_reason: str | None,
) -> None:
    docker = SimpleNamespace(
        info=lambda: {"ID": "daemon", "DockerRootDir": "/var/lib/docker"},
        api=SimpleNamespace(base_url=endpoint),
    )
    reader = DockerHostCapacityReader(
        docker=docker,
        statvfs=lambda _path: SimpleNamespace(f_bavail=1, f_frsize=1, f_favail=1),
        meminfo_reader=lambda: 1,
        loadavg_reader=lambda: (0.0, 0.0, 0.0),
        cpu_count_reader=lambda: 1,
        active_bundle_counter=lambda: 0,
    )

    assert reader.read().failure_reason == failure_reason


def test_running_bundle_reuse_does_not_consume_capacity_slot() -> None:
    gate = CellAdmissionGate(_profile())
    decision = gate.check(
        HostCapacitySnapshot(
            cpu_count=0,
            load_1m=0.0,
            memory_available_bytes=0,
            disk_free_bytes=0,
            disk_free_inodes=0,
            active_bundle_count=1,
            disk_path="/daemon-disk",
        ),
        existing_bundle=True,
        running_bundle=True,
    )

    assert decision == AdmissionDecision(True, "running_bundle_reuse")


def test_admission_ignores_bundle_count_when_physical_headroom_exists() -> None:
    decision = CellAdmissionGate(_profile()).check(
        HostCapacitySnapshot(
            cpu_count=8,
            load_1m=1.0,
            memory_available_bytes=12 * 1024**3,
            disk_free_bytes=200 * 1024**3,
            disk_free_inodes=1_000_000,
            active_bundle_count=999,
            disk_path="/var/lib/docker",
        ),
        existing_bundle=False,
        running_bundle=False,
    )

    assert decision == AdmissionDecision(True, "admitted")


def test_admission_subtracts_aggregate_reserved_quantities() -> None:
    profile = _profile()
    decision = CellAdmissionGate(profile).check(
        HostCapacitySnapshot(
            cpu_count=8,
            load_1m=0.0,
            memory_available_bytes=11 * 1024**3,
            disk_free_bytes=55 * 1024**3,
            disk_free_inodes=260_000,
            active_bundle_count=1,
            disk_path="/var/lib/docker",
        ),
        existing_bundle=False,
        running_bundle=False,
        reserved=ReservedCapacity.from_profile(profile),
    )

    assert decision == AdmissionDecision(False, "insufficient_memory")


def test_admission_uses_total_quota_without_double_counting_running_usage() -> None:
    profile = _profile()
    full = ReservedCapacity.from_profile(profile)
    decision = CellAdmissionGate(profile).check(
        HostCapacitySnapshot(
            cpu_count=7,
            load_1m=2.5,
            memory_available_bytes=9 * 1024**3,
            memory_total_bytes=14 * 1024**3,
            disk_free_bytes=50 * 1024**3,
            disk_total_bytes=70 * 1024**3,
            disk_free_inodes=250_000,
            disk_total_inodes=350_000,
            active_bundle_count=1,
            disk_path="/var/lib/docker",
        ),
        existing_bundle=False,
        running_bundle=False,
        reserved=full,
    )

    assert full.cpu_cores == 2.5
    assert full.memory_bytes == 5 * 1024**3
    assert decision == AdmissionDecision(True, "admitted")


@pytest.mark.parametrize("load_1m", [2.31, 8.0, 32.0])
def test_v2_cpu_admission_uses_reserved_envelope_not_load_average(load_1m: float) -> None:
    profile = replace(_profile(), profile_version="docker-owner-cell-resources-v2")
    full = ReservedCapacity.from_profile(profile)
    snapshot = HostCapacitySnapshot(
        cpu_count=8,
        load_1m=load_1m,
        memory_available_bytes=12 * 1024**3,
        memory_total_bytes=16 * 1024**3,
        disk_free_bytes=200 * 1024**3,
        disk_free_inodes=1_000_000,
        active_bundle_count=0,
        disk_path="/var/lib/docker",
    )
    gate = CellAdmissionGate(profile)
    assert full.cpu_cores == pytest.approx(4.2)
    assert gate.check(snapshot, existing_bundle=False, running_bundle=False) == AdmissionDecision(
        True, "admitted"
    )
    # Confirmed and provisional claims both remain in the aggregate reservation.
    # A busy host never permits a second 4.2-core cell to consume protected CPU.
    for provisional in (ReservedCapacity(), full):
        assert gate.check(
            snapshot,
            existing_bundle=False,
            running_bundle=False,
            reserved=full,
            provisional=provisional,
        ) == AdmissionDecision(False, "insufficient_cpu")
