from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellFenceRejected,
    CellResourceProfile,
    HostCapacitySnapshot,
    LifecycleMutation,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate
from omnia_orchestrator.services.cell_reservations import CellCapacityReservationStore


def _profile(state_path: Path) -> CellResourceProfile:
    return CellResourceProfile(
        profile_version="docker-owner-cell-resources-v1",
        postgres_image="postgres@sha256:" + "1" * 64,
        redis_image="redis@sha256:" + "2" * 64,
        backup_image="backup@sha256:" + "3" * 64,
        bundle_cpu_cores=2.0,
        bundle_memory_bytes=4 * 1024**3,
        host_cpu_reserve_cores=2.0,
        host_memory_reserve_bytes=4 * 1024**3,
        required_free_disk_bytes=20 * 1024**3,
        host_disk_reserve_bytes=10 * 1024**3,
        required_free_inodes=100_000,
        host_inode_reserve=50_000,
        state_path=str(state_path),
    )


def _snapshot(profile: CellResourceProfile) -> HostCapacitySnapshot:
    return HostCapacitySnapshot(
        cpu_count=8,
        load_1m=0.0,
        memory_available_bytes=11 * 1024**3,
        disk_free_bytes=55 * 1024**3,
        disk_free_inodes=260_000,
        active_bundle_count=0,
        disk_path="/var/lib/docker",
    )


def _mutation(fence: int, seed: str) -> LifecycleMutation:
    return LifecycleMutation(uuid4(), fence, seed * 64)


def test_reservation_ledger_aggregates_full_bundle_and_survives_reload(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    first_workspace = UUID("00000000-0000-0000-0000-000000000101")
    first_mutation = _mutation(1, "a")

    reservation = ledger.reserve(
        first_workspace,
        first_mutation,
        profile=profile,
        snapshot=_snapshot(profile),
        admission_gate=CellAdmissionGate(profile),
        running_bundle=False,
    )
    ledger.confirm(first_workspace, first_mutation)

    assert reservation.cpu_cores == 2.5
    assert reservation.memory_bytes == 5 * 1024**3
    assert reservation.disk_bytes == profile.required_free_disk_bytes
    assert reservation.inodes == profile.required_free_inodes
    assert CellCapacityReservationStore(tmp_path / "capacity-reservations").load(
        first_workspace
    ).status == "confirmed"

    with pytest.raises(CellCapacityUnavailable):
        ledger.reserve(
            UUID("00000000-0000-0000-0000-000000000102"),
            _mutation(1, "b"),
            profile=profile,
            snapshot=_snapshot(profile),
            admission_gate=CellAdmissionGate(profile),
            running_bundle=False,
        )


def test_provisional_reservation_has_expiry_but_confirmed_does_not(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    workspace_id = uuid4()
    mutation = _mutation(1, "9")
    now = datetime(2026, 9, 2, tzinfo=UTC)

    provisional = ledger.reserve(
        workspace_id,
        mutation,
        profile=profile,
        snapshot=_snapshot(profile),
        admission_gate=CellAdmissionGate(profile),
        running_bundle=False,
        now=now,
    )

    assert provisional.created_at == now
    assert provisional.expires_at == now + timedelta(minutes=5)
    assert ledger.expired_provisionals(now=now + timedelta(minutes=4)) == ()
    assert ledger.expired_provisionals(now=now + timedelta(minutes=6)) == (
        provisional,
    )
    assert ledger.confirm(workspace_id, mutation).expires_at is None


def test_reservation_release_requires_exact_rebound_fence(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    workspace_id = uuid4()
    ensure = _mutation(1, "c")
    pause = _mutation(2, "d")
    ledger.reserve(
        workspace_id,
        ensure,
        profile=profile,
        snapshot=_snapshot(profile),
        admission_gate=CellAdmissionGate(profile),
        running_bundle=False,
    )
    ledger.confirm(workspace_id, ensure)
    ledger.rebind(workspace_id, pause)

    with pytest.raises(CellFenceRejected):
        ledger.release(workspace_id, ensure)
    assert ledger.load(workspace_id) is not None

    ledger.release(workspace_id, pause)
    assert ledger.load(workspace_id) is None


def test_duplicate_reservation_is_idempotent_but_mismatched_envelope_fails(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    workspace_id = uuid4()
    mutation = _mutation(1, "e")
    first = ledger.reserve(
        workspace_id,
        mutation,
        profile=profile,
        snapshot=_snapshot(profile),
        admission_gate=CellAdmissionGate(profile),
        running_bundle=False,
    )
    replay = ledger.reserve(
        workspace_id,
        mutation,
        profile=profile,
        snapshot=_snapshot(profile),
        admission_gate=CellAdmissionGate(profile),
        running_bundle=False,
    )
    assert replay == first

    with pytest.raises(CellFenceRejected):
        ledger.reserve(
            workspace_id,
            LifecycleMutation(mutation.operation_id, 1, "f" * 64),
            profile=profile,
            snapshot=_snapshot(profile),
            admission_gate=CellAdmissionGate(profile),
            running_bundle=False,
        )


def _verification_gate(profile: CellResourceProfile, cores: float = 3.0) -> CellAdmissionGate:
    return CellAdmissionGate(
        profile, workload="verification", verification_cpu_cores=cores,
        verification_disk_bytes=8 * 1024**3,
    )


def _fill_runtime(ledger: CellCapacityReservationStore, profile: CellResourceProfile) -> None:
    """Two confirmed runtime bundles (2.5 cores each) + 2 reserved cores: 7 of 8."""
    for index in (1, 2):
        workspace = UUID(f"00000000-0000-0000-0000-00000000020{index}")
        mutation = _mutation(1, str(index))
        ledger.reserve(workspace, mutation, profile=profile, snapshot=_snapshot(profile),
                       admission_gate=CellAdmissionGate(profile), running_bundle=True)


def test_verification_candidate_is_admitted_when_runtime_ledger_is_full(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    _fill_runtime(ledger, profile)
    # A third running app does not fit the runtime ledger...
    with pytest.raises(CellCapacityUnavailable, match="insufficient_cpu"):
        ledger.reserve(uuid4(), _mutation(1, "c"), profile=profile,
                       snapshot=_snapshot(profile), admission_gate=CellAdmissionGate(profile),
                       running_bundle=False)
    # ...but a short-lived restoration check draws from its own budget.
    candidate = uuid4()
    reservation = ledger.reserve(candidate, _mutation(1, "d"), profile=profile,
                                 snapshot=_snapshot(profile),
                                 admission_gate=_verification_gate(profile), running_bundle=False)
    assert reservation.workload == "verification"
    assert reservation.disk_bytes == 8 * 1024**3  # not the long-lived 20 GiB bundle quota
    # The verification budget is its own limit: a second parallel check waits.
    with pytest.raises(CellCapacityUnavailable, match="insufficient_verification_cpu"):
        ledger.reserve(uuid4(), _mutation(1, "e"), profile=profile,
                       snapshot=_snapshot(profile),
                       admission_gate=_verification_gate(profile), running_bundle=False)
    reloaded = CellCapacityReservationStore(tmp_path / "capacity-reservations").load(candidate)
    assert reloaded is not None and reloaded.workload == "verification"


def test_verification_does_not_consume_runtime_cpu(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    ledger.reserve(uuid4(), _mutation(1, "a"), profile=profile, snapshot=_snapshot(profile),
                   admission_gate=_verification_gate(profile), running_bundle=False)
    ledger.reserve(UUID("00000000-0000-0000-0000-000000000301"), _mutation(1, "b"),
                   profile=profile, snapshot=_snapshot(profile),
                   admission_gate=CellAdmissionGate(profile), running_bundle=True)
    # 2.5 runtime + 2.5 new runtime + 2 host reserve = 7 <= 8: the verification
    # check's 2.5 cores do not count. (Memory and disk do, hence a roomier host.)
    from dataclasses import replace

    roomy = replace(_snapshot(profile), memory_available_bytes=40 * 1024**3,
                    disk_free_bytes=200 * 1024**3, disk_free_inodes=2_000_000)
    admitted = ledger.reserve(uuid4(), _mutation(1, "c"), profile=profile,
                              snapshot=roomy,
                              admission_gate=CellAdmissionGate(profile), running_bundle=False)
    assert admitted.workload == "runtime"


def test_verification_still_needs_physically_free_memory(tmp_path: Path) -> None:
    from dataclasses import replace

    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    starved = replace(_snapshot(profile), memory_available_bytes=6 * 1024**3)
    with pytest.raises(CellCapacityUnavailable, match="insufficient_memory"):
        ledger.reserve(uuid4(), _mutation(1, "a"), profile=profile, snapshot=starved,
                       admission_gate=_verification_gate(profile), running_bundle=False)


def test_runtime_reservation_files_keep_the_legacy_shape(tmp_path: Path) -> None:
    import json

    profile = _profile(tmp_path / "project-cells.json")
    ledger = CellCapacityReservationStore(tmp_path / "capacity-reservations")
    workspace = UUID("00000000-0000-0000-0000-000000000401")
    ledger.reserve(workspace, _mutation(1, "a"), profile=profile, snapshot=_snapshot(profile),
                   admission_gate=CellAdmissionGate(profile), running_bundle=True)
    (path,) = list((tmp_path / "capacity-reservations").glob("*.json"))
    assert "workload" not in json.loads(path.read_text())
    loaded = ledger.load(workspace)
    assert loaded is not None and loaded.workload == "runtime"
