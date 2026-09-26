"""Снос остановленной ячейки: база поднимается ради дампа, а не роняет операцию.

26.09.2026, прод, ячейка a0710ef0 на commerce. Две попытки сноса подряд ушли в
`indeterminate`, ячейка навсегда застряла в состоянии «удаляется» и продолжала
держать бронь ресурсов хоста. В журнале оркестратора — 409 от Docker:

    container 4551ae4e… is not running

Цепочка ровно такая: снос обязан сначала запечатать снимок (`checkpoint_sealed`
журналируется ДО удаления первого ресурса), снимок обязан содержать
`postgres.dump`, а дамп снимается через `docker exec` внутри контейнера базы. В
неработающий контейнер Docker с exec не пускает — и снос падает.

Наивная починка «не снимать дамп, если контейнер не запущен» здесь запрещена:
снимок сноса без базы — это молчаливая потеря данных владельца, которую заметят
только при попытке восстановления, когда данных уже нет. Различать надо не
«запущен ли контейнер прямо сейчас», а что именно от нас требуется: данные лежат
на томе, поэтому базу поднимают ровно на время чтения и гасят обратно.

Почему стенд этого не ловил: его поддельный контейнер пускал exec в любом
состоянии. Теперь отказывает так же, как настоящий Docker, — иначе проверки
здесь зелёные, а прод красный.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_docker_py_cell_backend import (
    _backend,
    _FakeClient,
    _FakeVolume,
    _labels,
)
from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.docker_cell_resources import DockerContainerSpec

_NAME = "omnia-cell-test-postgres"


def _spec() -> DockerContainerSpec:
    return DockerContainerSpec(
        name=_NAME,
        image="postgres@sha256:" + "1" * 64,
        labels=_labels("postgres"),
        user="postgres",
        cap_add=[],
        cap_drop=["ALL"],
        read_only=True,
        privileged=False,
        security_opt=["no-new-privileges:true"],
        ports={},
        env={},
        volumes=("pg-vol",),
        mounts=(),
        network_names=("omnia-cell-test-internal",),
        helper=False,
        pids_limit=128,
        memory_limit_bytes=512 * 1024 * 1024,
        cpu_quota=1.5,
    )


async def _stopped_cell() -> tuple[Any, Any]:
    client = _FakeClient()
    client.volumes.items["pg-vol"] = _FakeVolume("pg-vol", _labels("postgres"))
    backend = _backend(client)
    await backend.create_container(_spec())
    container = client.containers.items[_NAME]
    assert container.status != "running", "ячейка должна быть остановлена, иначе проверка пустая"
    return backend, container


@pytest.mark.asyncio
async def test_a_stopped_cell_still_yields_its_dump() -> None:
    """Главное: снос остановленной ячейки больше не упирается в 409."""
    backend, _container = await _stopped_cell()

    assert await backend.postgres_dump(_NAME, "secret") == b"pg-dump-bytes"


@pytest.mark.asyncio
async def test_the_database_is_put_back_as_it_was_found() -> None:
    """База поднималась ради чтения — значит и гаснет обратно.

    Иначе снос оставлял бы за собой работающую базу удаляемой ячейки.
    """
    backend, container = await _stopped_cell()

    await backend.postgres_dump(_NAME, "secret")

    assert container.status != "running"
    assert container.stop_calls, "контейнер подняли и не погасили"


@pytest.mark.asyncio
async def test_a_running_cell_is_left_running() -> None:
    """Защита от перестраховки: обычный снимок живой ячейки её не гасит."""
    backend, container = await _stopped_cell()
    await backend.start_container(_NAME)

    await backend.postgres_dump(_NAME, "secret")

    assert container.status == "running"
    assert not container.stop_calls


@pytest.mark.asyncio
async def test_readiness_is_awaited_before_the_dump() -> None:
    """Postgres открывается не мгновенно: дамп без пробы готовности — гонка."""
    backend, container = await _stopped_cell()

    await backend.postgres_dump(_NAME, "secret")

    programs = [item["command"][0] for item in container.exec_calls]
    assert programs.index("psql") < programs.index("pg_dump"), programs


@pytest.mark.asyncio
async def test_a_database_that_never_opens_is_named_not_disguised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если база так и не открылась — сказать это словами, а не отдать сырой 409.

    И не бросить её поднятой: иначе неудачный снос оставит за собой мусор.
    """
    backend, container = await _stopped_cell()
    monkeypatch.setattr(
        type(backend), "postgres_smoke_query", lambda *_args, **_kwargs: _never()
    )
    monkeypatch.setattr(
        "yleum_orchestrator.services.docker_py_cell_backend._POSTGRES_WAKE_ATTEMPTS", 2
    )
    monkeypatch.setattr(
        "yleum_orchestrator.services.docker_py_cell_backend._POSTGRES_WAKE_INTERVAL_SECONDS",
        0.0,
    )

    with pytest.raises(CellResourceError) as failure:
        await backend.postgres_dump(_NAME, "secret")

    assert "could not wake its stopped database" in str(failure.value)
    assert container.status != "running", "неудача оставила базу поднятой"


async def _never() -> bool:
    return False
