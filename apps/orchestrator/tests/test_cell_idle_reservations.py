"""Бронь, за которой не стоит ни один живой контейнер, должна отпускаться.

26.09.2026 владелец не смог создать приложение на почти пустой машине: 8 ядер,
загрузка 1.06, свободно 27 ГБ. Держала бронь остановленной ячейки — канарейка
мониторинга стояла с 23.09 с отметкой «операция не удалась», её контейнеры
лежали, а 3.2 ядра числились за ней. Пауза ячейки бронь отпускает давно, но
ячейка, остановленная НЕ через паузу (упавшая операция, перезагрузка сервера),
оставляла запись навсегда.

Здесь проверяется и обратное: у идущей операции место не отбирают.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from yleum_orchestrator.core.cell_resources import (
    CellResourceNames,
    LifecycleMutation,
    identity_labels,
)
from yleum_orchestrator.services.cell_admission import CellAdmissionGate
from yleum_orchestrator.services.docker_cell_resources import DockerContainerSpec

from .test_docker_cell_resources import _make_manager, _mutation, _spec

NOW = datetime(2026, 9, 26, 7, 0, tzinfo=UTC)


def _container(manager: object, spec: object, name: str) -> DockerContainerSpec:
    return DockerContainerSpec(
        name=name,
        image=manager.profile.redis_image,  # type: ignore[attr-defined]
        labels=identity_labels(spec, "redis"),  # type: ignore[arg-type]
        user="redis",
        cap_add=[],
        cap_drop=["ALL"],
        read_only=True,
        privileged=False,
        security_opt=["no-new-privileges:true"],
        ports={},
        env={},
        volumes=(),
        mounts=(),
        network_names=(),
        helper=False,
    )


async def _book(
    manager: object,
    workspace_id: UUID,
    mutation: LifecycleMutation,
    *,
    created_at: datetime,
) -> None:
    ledger = manager._capacity_reservation_store()  # type: ignore[attr-defined]
    ledger.reserve(
        workspace_id,
        mutation,
        profile=manager.profile,  # type: ignore[attr-defined]
        snapshot=replace(manager.capacity_reader.read(), cpu_count=16),  # type: ignore[attr-defined]
        admission_gate=manager.admission_gate,  # type: ignore[attr-defined]
        running_bundle=False,
        now=created_at,
    )
    ledger.confirm(workspace_id, mutation)


@pytest.mark.asyncio
async def test_a_stopped_cell_stops_holding_its_booking(tmp_path: Path) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    mutation = _mutation("a", 1)
    await _book(manager, workspace_id, mutation, created_at=NOW - timedelta(hours=3))
    await docker.create_container(_container(manager, spec, "cell-redis"))
    await docker.stop_container("cell-redis")
    state_store.begin(
        spec,
        mutation,
        kind="ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
    )
    state_store.complete(workspace_id, mutation, phase="completed", bundle_state="resources_ready")

    assert await manager.recover_capacity_reservations(now=NOW) == 1
    assert manager._capacity_reservation_store().load(workspace_id) is None


@pytest.mark.asyncio
async def test_a_running_cell_keeps_its_booking(tmp_path: Path) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    mutation = _mutation("b", 1)
    await _book(manager, workspace_id, mutation, created_at=NOW - timedelta(hours=3))
    await docker.create_container(_container(manager, spec, "cell-redis"))
    await docker.start_container("cell-redis")
    state_store.begin(
        spec,
        mutation,
        kind="ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
    )
    state_store.complete(workspace_id, mutation, phase="completed", bundle_state="resources_ready")

    assert await manager.recover_capacity_reservations(now=NOW) == 0
    assert manager._capacity_reservation_store().load(workspace_id) is not None


@pytest.mark.asyncio
async def test_an_operation_in_flight_is_never_stripped_of_its_place(tmp_path: Path) -> None:
    """Операция бронирует РАНЬШЕ, чем поднимает контейнеры. Отбирать нельзя."""
    manager, _docker, state_store, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    mutation = _mutation("c", 1)
    await _book(manager, workspace_id, mutation, created_at=NOW - timedelta(hours=3))
    state_store.begin(
        spec,
        mutation,
        kind="ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
    )

    assert await manager.recover_capacity_reservations(now=NOW) == 0
    assert manager._capacity_reservation_store().load(workspace_id) is not None


@pytest.mark.asyncio
async def test_a_fresh_booking_is_left_alone_until_the_grace_passes(tmp_path: Path) -> None:
    manager, _docker, _state, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    mutation = _mutation("d", 1)
    await _book(manager, workspace_id, mutation, created_at=NOW - timedelta(seconds=30))

    assert await manager.recover_capacity_reservations(now=NOW) == 0
    assert manager._capacity_reservation_store().load(workspace_id) is not None


@pytest.mark.asyncio
async def test_a_published_application_keeps_its_place(tmp_path: Path) -> None:
    """У опубликованного приложения своя отметка: его бронь не наша забота."""
    manager, docker, _state, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    mutation = _mutation("e", 1)
    await _book(manager, workspace_id, mutation, created_at=NOW - timedelta(hours=3))
    await docker.create_container(_container(manager, spec, "cell-redis"))
    await docker.stop_container("cell-redis")
    identities = Path(manager.profile.state_path).parent / "cell-publications" / "identities"
    identities.mkdir(parents=True, exist_ok=True)
    (identities / f"{workspace_id}.json").write_text(json.dumps({"kept": True}), encoding="utf-8")

    assert await manager.recover_capacity_reservations(now=NOW) == 0
    assert manager._capacity_reservation_store().load(workspace_id) is not None


@pytest.mark.asyncio
async def test_a_verification_candidate_is_never_touched(tmp_path: Path) -> None:
    """Кандидат отката бронирует раньше, чем поднимает контейнеры.

    Предупреждение пришло со стороны откатов и описывает живой случай: несколько
    минут у кандидата может не быть ни одного запущенного контейнера. Отобрать у
    него бронь — значит уронить адаптацию отказом «не хватило ядер», который
    укажет не на ту причину.
    """
    manager, _docker, _state, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    mutation = _mutation("f", 1)
    ledger = manager._capacity_reservation_store()
    ledger.reserve(
        workspace_id,
        mutation,
        profile=manager.profile,
        snapshot=replace(manager.capacity_reader.read(), cpu_count=16),
        admission_gate=CellAdmissionGate(
            manager.profile, workload="verification", verification_cpu_cores=8.0
        ),
        running_bundle=False,
        now=NOW - timedelta(hours=3),
    )
    ledger.confirm(workspace_id, mutation)

    assert await manager.recover_capacity_reservations(now=NOW) == 0
    assert ledger.load(workspace_id) is not None


@pytest.mark.asyncio
async def test_a_cell_that_is_still_coming_up_keeps_its_place(tmp_path: Path) -> None:
    """Контейнер создан, но ещё не запущен — ячейка поднимается, а не спит."""
    manager, docker, state_store, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    mutation = _mutation("9", 1)
    await _book(manager, workspace_id, mutation, created_at=NOW - timedelta(hours=3))
    await docker.create_container(_container(manager, spec, "cell-redis"))
    state_store.begin(
        spec,
        mutation,
        kind="ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
    )
    state_store.complete(workspace_id, mutation, phase="completed", bundle_state="resources_ready")

    assert await manager.recover_capacity_reservations(now=NOW) == 0
    assert manager._capacity_reservation_store().load(workspace_id) is not None
