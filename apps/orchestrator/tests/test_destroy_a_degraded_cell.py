"""Ячейка, у которой упали контейнеры, всё равно должна сноситься.

Живой случай 26.09.2026: ячейка a0710ef0 на commerce была `resources_ready`,
её контейнеры остановились, сверка перевела связку в `degraded` — и дальше
ЛЮБАЯ попытка сноса уходила в `indeterminate`. Ячейка навсегда застряла в
состоянии «удаляется» и продолжала держать бронь ресурсов хоста.

Почему именно `degraded`. Снос ставленной на паузу ячейки идёт веткой
`retained` и переиспользует уже запечатанный снимок — это проверено отдельно
(test_cell_retained_destroy) и трогать нельзя. А `degraded` в эту ветку не
попадает, значит снимок снимается заново, значит нужен свежий дамп базы,
значит нужен exec внутрь контейнера, — а контейнер стоит.

Здесь закреплён исход, который видит владелец: ячейка сносится, и её снимок
содержит базу. Не «снос прошёл, а данных в снимке нет».

Тот же корень бьёт и по «усыпить приложение»: в тот же день операция pause на
ячейке a5a60aa7 (core) ушла в indeterminate с тем же 409 на том же дампе —
цепочка _execute_composite_pause_status → checkpoint create → postgres_dump.
Владелец нажимает «усыпить», ничего не происходит, а операция висит. Поэтому
пауза закреплена здесь же, рядом со сносом.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.test_cell_checkpoint import _make_fixture, _spec
from yleum_orchestrator.core.cell_resources import LifecycleMutation
from yleum_orchestrator.core.workspace_provider import ControlAction
from yleum_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider


async def _degraded_cell(tmp_path):
    """Готовая ячейка, у которой погасли контейнеры, — как после сбоя хоста."""
    manager, checkpoints, docker = _make_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    spec = _spec(uuid4())
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))

    state = manager._require_state(spec.workspace_id)
    names = state.resource_names
    assert names is not None
    for name in (names.postgres_container, names.redis_container, names.draft_container_name()):
        await docker.stop_container(name)

    observation = await manager.reconcile(
        spec.workspace_id, LifecycleMutation(uuid4(), 2, "b" * 64)
    )
    assert observation.state == "degraded", observation.state
    return provider, manager, checkpoints, docker, spec, names


@pytest.mark.asyncio
async def test_a_degraded_cell_can_be_destroyed(tmp_path) -> None:
    """Главное: снос доходит до конца, а не оставляет ячейку в «удаляется»."""
    provider, manager, _checkpoints, docker, spec, _names = await _degraded_cell(tmp_path)

    await provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="destroy"),
        LifecycleMutation(uuid4(), 3, "c" * 64),
    )

    # Ресурсов не осталось, а связка помечена снесённой — именно этого и не
    # происходило на проде: там снос падал до удаления первого ресурса.
    # Тома снос вычислительной части намеренно оставляет — удаляются контейнеры.
    assert not docker.containers
    state = manager.state_store.load(spec.workspace_id)
    assert state is None or state.phase != "indeterminate", state


@pytest.mark.asyncio
async def test_the_seal_of_a_degraded_cell_still_carries_the_database(tmp_path) -> None:
    """Снос без базы в снимке — это потеря данных, которую заметят только при откате."""
    provider, _manager, _checkpoints, docker, spec, names = await _degraded_cell(tmp_path)
    await docker.write_volume_files(
        names.postgres_volume, {"db.json": b'[{"id": 1, "note": "owner row"}]'}
    )
    dumps: list[bytes] = []
    original = docker.postgres_dump

    async def _record(container_name: str, password: str) -> bytes:
        payload = await original(container_name, password)
        dumps.append(payload)
        return payload

    docker.postgres_dump = _record  # type: ignore[method-assign]

    await provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="destroy"),
        LifecycleMutation(uuid4(), 3, "c" * 64),
    )

    assert dumps, "снос не снял ни одного дампа — снимок ушёл бы без базы"
    assert b"owner row" in dumps[-1], dumps[-1][:200]


@pytest.mark.asyncio
async def test_the_database_does_not_stay_up_after_the_seal(tmp_path) -> None:
    """Базу поднимали ради чтения — значит после снимка её не оставляют работать."""
    provider, _manager, _checkpoints, docker, spec, names = await _degraded_cell(tmp_path)

    await provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="destroy"),
        LifecycleMutation(uuid4(), 3, "c" * 64),
    )

    running = [
        item for item in docker.container_history
        if item.name == names.postgres_container and item.state == "running"
    ]
    assert running, "база не поднималась — значит дамп снят не с неё"
    assert names.postgres_container not in docker.containers


@pytest.mark.asyncio
async def test_a_degraded_cell_can_still_be_put_to_sleep(tmp_path) -> None:
    """Живой случай a5a60aa7: «усыпить» падало на том же дампе, что и снос."""
    provider, _manager, _checkpoints, docker, spec, names = await _degraded_cell(tmp_path)
    await docker.write_volume_files(
        names.postgres_volume, {"db.json": b'[{"id": 1, "note": "owner row"}]'}
    )
    dumps: list[bytes] = []
    original = docker.postgres_dump

    async def _record(container_name: str, password: str) -> bytes:
        payload = await original(container_name, password)
        dumps.append(payload)
        return payload

    docker.postgres_dump = _record  # type: ignore[method-assign]

    await provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="pause", checkpoint_ref="sleep-1"),
        LifecycleMutation(uuid4(), 3, "c" * 64),
    )

    assert dumps, "сон запечатан без базы"
    assert b"owner row" in dumps[-1]
