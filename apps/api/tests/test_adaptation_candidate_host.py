"""Кандидат адаптации живёт там же, где ячейка проекта.

23.09.2026 адаптивный откат на проде умер на первом шаге за четыре минуты:
«Orchestrator rejected request: workspace state not found». Ячейка проекта жила
на втором хосте, кандидат был создан там же, а запрос по кандидату ушёл на хост
по умолчанию, где его отродясь не было.

Причина — не опечатка, а рассуждение: маршрутизация ищет рабочую область в
реестре ячеек и, не найдя, отдаёт запрос дефолтному хосту, «который честно
ответит 404». Для настоящей ячейки это верно. Для кандидата — нет: его в
реестре нет никогда, потому что он не ячейка владельца, а временная копия для
проверки. Значит отказ получал КАЖДЫЙ проект, чья ячейка живёт не на дефолтном
хосте, и только на одном хосте поломка была невидима.

Ниже закреплено само правило («кандидат наследует хост источника»), а не
конкретный текст ошибки.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from omnia_api.services import orchestrator_client, orchestrator_hosts, readiness
from omnia_api.services.orchestrator_hosts import (
    bind_candidate_to_source_host,
    forget_bindings,
    host_for_path,
    host_for_workspace,
    remember_workspace_host,
)

TWO_HOSTS = json.dumps(
    [
        {
            "name": "core",
            "url": "http://172.19.0.1:8003/",
            "preview_host_suffix": "dev.yleum.ru",
        },
        {
            "name": "commerce",
            "url": "http://10.10.0.3:8003",
            "preview_host_suffix": "dev2.yleum.ru",
            "weight": 2,
        },
    ]
)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    forget_bindings()
    orchestrator_hosts._registry_cache = None
    yield
    forget_bindings()
    orchestrator_hosts._registry_cache = None


def _configure(monkeypatch: pytest.MonkeyPatch, hosts: str, default: str = "core") -> None:
    settings = SimpleNamespace(
        orchestrator_hosts=hosts,
        default_orchestrator=default,
        orchestrator_url="http://localhost:8003",
        project_cell_preview_host_suffix="dev.yleum.ru",
        gate_preview_resolver_rules="MAP *.dev.yleum.ru 172.19.0.1",
        orchestrator_internal_token=SecretStr("t"),
    )
    from omnia_api.core import config

    for module in (config, orchestrator_client, readiness):
        monkeypatch.setattr(module, "get_settings", lambda settings=settings: settings)
    orchestrator_hosts._registry_cache = None


@pytest.mark.asyncio
async def test_a_candidate_of_a_cell_on_the_second_host_is_not_sent_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ровно живой случай 23.09: ячейка на commerce, кандидат уходил на core."""
    _configure(monkeypatch, TWO_HOSTS)
    source, candidate = uuid4(), uuid4()
    remember_workspace_host(source, None, "commerce")

    await bind_candidate_to_source_host(candidate, source)

    assert await host_for_workspace(candidate) == "commerce"
    assert await host_for_path(f"/internal/workspaces/{candidate}/agent/bootstrap") == "commerce"


@pytest.mark.asyncio
async def test_every_later_call_about_the_candidate_follows_the_same_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Агент ходит по кандидату много раз; одного верного запроса мало.
    _configure(monkeypatch, TWO_HOSTS)
    source, candidate = uuid4(), uuid4()
    remember_workspace_host(source, None, "commerce")

    await bind_candidate_to_source_host(candidate, source)

    for path in ("agent/exec", "agent/files", "agent/build", "resources"):
        assert await host_for_path(f"/internal/workspaces/{candidate}/{path}") == "commerce"


@pytest.mark.asyncio
async def test_a_candidate_of_a_cell_on_the_default_host_stays_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    source, candidate = uuid4(), uuid4()
    remember_workspace_host(source, None, "core")

    await bind_candidate_to_source_host(candidate, source)

    assert await host_for_workspace(candidate) == "core"


@pytest.mark.asyncio
async def test_the_candidate_does_not_claim_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кандидат не ячейка проекта и не должен собой определять хост проекта.

    Иначе временная копия начала бы диктовать маршрут настоящим запросам
    проекта, и ошибка в ней разъехалась бы по всему проекту.
    """
    _configure(monkeypatch, TWO_HOSTS)
    source, candidate = uuid4(), uuid4()
    remember_workspace_host(source, None, "commerce")
    before = dict(orchestrator_hosts._project_hosts)

    await bind_candidate_to_source_host(candidate, source)

    assert dict(orchestrator_hosts._project_hosts) == before


@pytest.mark.asyncio
async def test_one_host_deployments_are_left_completely_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # На одном хосте маршрутизировать нечего, и запись в кеш была бы лишней.
    _configure(monkeypatch, "")
    source, candidate = uuid4(), uuid4()

    await bind_candidate_to_source_host(candidate, source)

    assert candidate not in orchestrator_hosts._workspace_hosts
    assert await host_for_workspace(candidate) == "core"


@pytest.mark.asyncio
async def test_preparing_an_adaptation_binds_the_candidate_without_being_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Привязку ставит тот, кто впервые узнаёт id кандидата.

    Если оставить это вызывающему, однажды его забудут — и поломка вернётся
    ровно в том же виде: молчаливый уход на хост по умолчанию.
    """
    _configure(monkeypatch, TWO_HOSTS)
    source, candidate = uuid4(), uuid4()
    operation, project, owner, run = uuid4(), uuid4(), uuid4(), uuid4()
    remember_workspace_host(source, None, "commerce")

    async def fake_request(_method: str, _path: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "state": "ready",
            "source_workspace_id": str(source),
            "candidate_workspace_id": str(candidate),
            "operation_id": str(operation),
            "project_id": str(project),
            "owner_id": str(owner),
            "generation_run_id": str(run),
            "candidate_fencing_epoch": 1,
            "source_database_digest": "1" * 64,
            "proof_digest": "2" * 64,
            "capabilities": {
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
            },
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.project_cell_prepare_restoration_adaptation(
        source,
        operation_id=operation,
        project_id=project,
        owner_id=owner,
        generation_run_id=run,
        fencing_epoch=7,
        source_workspace_revision="0" * 64,
        source_snapshot_id=uuid4(),
        base_draft_snapshot_id=uuid4(),
        source_commit_sha="a" * 40,
        adaptation_bundle_digest="b" * 64,
    )

    assert result.candidate_workspace_id == candidate
    assert await host_for_path(f"/internal/workspaces/{candidate}/agent/bootstrap") == "commerce"


@pytest.mark.asyncio
async def test_an_unbound_candidate_still_goes_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Поведение без привязки не меняется — это и есть та поломка, которую
    # чинит вызов; тест держит разницу видимой.
    _configure(monkeypatch, TWO_HOSTS)

    async def no_row(_column: Any, _value: Any) -> None:
        return None  # кандидата нет в реестре ячеек — так и есть в жизни

    monkeypatch.setattr(orchestrator_hosts, "_lookup", no_row)

    assert await host_for_path(f"/internal/workspaces/{uuid4()}/agent/bootstrap") == "core"
