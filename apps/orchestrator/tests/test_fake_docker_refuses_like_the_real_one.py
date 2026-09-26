"""Стенд обязан отказывать там же, где отказывает настоящий Docker.

Сессия откатов 26.09 нашла ячейку, которая не сносилась на проде и при этом была
зелёной в проверках: поддельный Docker пускал команду внутрь остановленного
контейнера, а настоящий отвечает 409. Проверка была мягче мира и потому не могла
увидеть дефект.

Здесь закрыты ещё два места того же класса, найденные сверкой подложки с
настоящим бэкендом (docker_py_cell_backend):

  * сеть удаляется без force, и демон отвечает 403 «has active endpoints», пока
    к ней подключён хоть один контейнер;
  * `docker volume rm --force` снимает только «нет такого тома»; том,
    смонтированный в существующий контейнер, всё равно отвечает 409 «in use».

Обе защиты проверены мутацией на настоящем коде: переставь снос сети или тома
перед контейнерами — и набор краснеет с той же фразой, что дал бы прод.
"""

from __future__ import annotations

import pytest

from tests._cell_fakes import FakeDockerBackend
from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.docker_cell_resources import DockerContainerSpec


def _spec(name: str, *, networks: tuple[str, ...], volumes: tuple[str, ...]) -> DockerContainerSpec:
    return DockerContainerSpec(
        name=name,
        image="postgres@sha256:" + "1" * 64,
        labels={"omnia.workspace_id": "00000000-0000-0000-0000-000000000001"},
        user="postgres",
        cap_add=[],
        cap_drop=["ALL"],
        read_only=True,
        privileged=False,
        security_opt=["no-new-privileges:true"],
        ports={},
        env={},
        volumes=volumes,
        mounts=(),
        network_names=networks,
        helper=False,
    )


@pytest.mark.asyncio
async def test_a_network_with_a_container_attached_cannot_be_removed() -> None:
    docker = FakeDockerBackend()
    await docker.create_network("cell-internal", labels={}, internal=True)
    await docker.create_container(_spec("cell-postgres", networks=("cell-internal",), volumes=()))

    with pytest.raises(CellResourceError) as refusal:
        await docker.remove_network("cell-internal")

    assert "active endpoints" in str(refusal.value)
    assert await docker.get_network("cell-internal") is not None, "сеть должна остаться"

    await docker.remove_container("cell-postgres")
    await docker.remove_network("cell-internal")
    assert await docker.get_network("cell-internal") is None


@pytest.mark.asyncio
async def test_a_volume_mounted_by_a_container_cannot_be_removed() -> None:
    docker = FakeDockerBackend()
    await docker.create_volume("cell-secret-staging", labels={})
    await docker.create_container(
        _spec("cell-postgres-init", networks=(), volumes=("cell-secret-staging",))
    )

    with pytest.raises(CellResourceError) as refusal:
        await docker.remove_volume("cell-secret-staging")

    assert "in use" in str(refusal.value)
    assert await docker.get_volume("cell-secret-staging") is not None, "том должен остаться"

    await docker.remove_container("cell-postgres-init")
    await docker.remove_volume("cell-secret-staging")
    assert await docker.get_volume("cell-secret-staging") is None


@pytest.mark.asyncio
async def test_removing_what_is_already_gone_stays_quiet() -> None:
    """Настоящий бэкенд молча выходит, если объекта нет, — стенд тоже."""
    docker = FakeDockerBackend()

    await docker.remove_network("no-such-network")
    await docker.remove_volume("no-such-volume")


@pytest.mark.asyncio
async def test_a_stopped_container_still_holds_its_network_and_volume() -> None:
    """Остановленный контейнер существует, значит держит и сеть, и том.

    Именно поэтому снос обязан удалять контейнеры первыми, а не просто гасить их.
    """
    docker = FakeDockerBackend()
    await docker.create_network("cell-internal", labels={}, internal=True)
    await docker.create_volume("cell-data", labels={})
    await docker.create_container(
        _spec("cell-postgres", networks=("cell-internal",), volumes=("cell-data",))
    )
    await docker.start_container("cell-postgres")
    await docker.stop_container("cell-postgres")

    with pytest.raises(CellResourceError):
        await docker.remove_network("cell-internal")
    with pytest.raises(CellResourceError):
        await docker.remove_volume("cell-data")


@pytest.mark.asyncio
async def test_creating_an_existing_volume_keeps_its_data() -> None:
    """`docker volume create` на существующем имени возвращает ТОТ ЖЕ том.

    Это ложь в опасную сторону, и потому она хуже «разрешаем лишнее»: стенд
    раньше делал новую пустую запись, то есть стирал данные. Путь, который
    пересоздаёт том, видел здесь пустоту, а на проде — старое содержимое. Такая
    проверка зелёная ровно до того дня, когда чужое прошлое всплывает в свежей
    ячейке.
    """
    docker = FakeDockerBackend()
    await docker.create_volume("cell-data", labels={"omnia.resource_kind": "postgres"})
    await docker.write_volume_files("cell-data", {"db.json": b"[1]"})

    again = await docker.create_volume("cell-data", labels={"omnia.resource_kind": "redis"})

    assert again.files == {"db.json": b"[1]"}, "данные тома обязаны пережить повторное создание"
    assert again.labels == {"omnia.resource_kind": "postgres"}, (
        "метку существующему тому поменять нельзя — настоящий Docker её не трогает"
    )
    assert await docker.read_volume_files("cell-data") == {"db.json": b"[1]"}


@pytest.mark.asyncio
async def test_creating_an_existing_network_is_refused() -> None:
    """Настоящий бэкенд зовёт create с check_duplicate=True, и демон отвечает отказом.

    Проверено мутацией: убери проверку существования в _ensure_network — и набор
    краснеет здесь же, на жизненном цикле черновика.
    """
    docker = FakeDockerBackend()
    await docker.create_network("cell-internal", labels={}, internal=True)

    with pytest.raises(CellResourceError) as refusal:
        await docker.create_network("cell-internal", labels={}, internal=True)

    assert "already exists" in str(refusal.value)
