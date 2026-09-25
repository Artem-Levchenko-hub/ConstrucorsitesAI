"""Провалившаяся репетиция должна называть ногу, на которой споткнулась.

25.09.2026, живой прод, проект b5c4c26d: два адаптивных отката подряд вернули
`migration_required:probe_rehearsal_failed`. Отпечатки схемы, деловых и
технических данных у копии и источника при этом совпали до символа — то есть
расходилась ровно репетиция проверки, и больше ничего.

Узнать, что именно не прошло, было нельзя. Репетиция гоняет шесть ног подряд
(готовность службы, чтение владельцем, запись владельцем, перечитывание,
отказ чужому, отказ неопознанному), каждая падает со своей внятной фразой — но
фраза уничтожается дважды: внутри ноги `raise ... from None` рвёт цепочку
причин, а вызывающий код ловит исключение и заменяет его одним словом. В журнале
оркестратора за время обоих прогонов нет ни одной строки про репетицию — я
искал.

Цена этой немоты выше, чем кажется: тем же кодом формулируется задание агенту на
починку (`migration_required:<код>` уходит в NEEDS_EDIT). То есть чинить просили
вслепую — агент не знал, какая из шести ног упала.

Здесь закреплено, что нога доживает до отчёта: каждая нога получает свой код, а
незнакомый сбой по-прежнему сваливается в общий `probe_rehearsal_failed`, а не
роняет операцию. Словарь кодов знают ДВЕ стороны, поэтому выкатка двухфазная:
платформа учится принимать новые коды раньше, чем оркестратор начинает их слать
(тот же порядок, что в c39ebf21).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.schemas.restoration_adaptation import RestorationAdaptationProof
from omnia_orchestrator.services.restoration_adaptation_health import ProbeRehearsalFailure
from omnia_orchestrator.services.restoration_adaptation_workspace import (
    DockerAdaptationWorkspaceEngine,
)
from omnia_orchestrator.services.versioning.contracts import InventoryReport
from tests.test_restoration_adaptation_workspace import _proof_request, _request

# Нога репетиции → код причины. Порядок тот же, в котором они выполняются.
_LEGS = {
    "service_readiness": "probe_readiness_failed",
    "signed_owner_read": "probe_owner_read_failed",
    "signed_owner_mutation": "probe_owner_mutation_failed",
    "signed_owner_reload": "probe_owner_reload_failed",
    "cross_owner_denial": "probe_cross_owner_denial_failed",
    "unauthenticated_denial": "probe_unauthenticated_denial_failed",
}

_KNOWN = set(RestorationAdaptationProof.model_fields["reason_code"].annotation.__args__[0].__args__)


def _prove_fixture(monkeypatch: pytest.MonkeyPatch, rehearser: object):
    """Собрать движок доказательства так, чтобы падала ровно репетиция."""

    from omnia_orchestrator.routers.runtime import _workspace_revision
    from omnia_orchestrator.services import restoration_adaptation_workspace as module

    request = _request()
    source_files = {"src/app/page.tsx": "export default function Page(){return 'source'}"}
    candidate_files = {
        "src/app/page.tsx": "export default function Page(){return 'candidate'}",
        ".omnia/restoration-probe.json": "{}",
    }
    request = request.model_copy(
        update={"source_workspace_revision": _workspace_revision(source_files)}
    )
    proof = _proof_request(request, "a" * 64).model_copy(
        update={
            "candidate_workspace_id": uuid5(
                request.operation_id,
                f"restoration-adaptation:{request.generation_run_id}",
            ),
            "candidate_workspace_revision": _workspace_revision(candidate_files),
            "candidate_artifact_digest": DockerAdaptationWorkspaceEngine._files_digest(
                candidate_files
            ),
            "source_database_digest": "d" * 64,
        }
    )

    class Lock:
        def hold(self, _workspace_id):
            return self

        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return None

    def _state(epoch: int):
        return SimpleNamespace(
            project_id=request.project_id,
            owner_id=request.owner_id,
            active_generation_run_id=request.generation_run_id,
            active_generation_fencing_epoch=epoch,
        )

    source = SimpleNamespace(
        workspace_volume="source-code",
        project_postgres_password="source-password",
        internal_network="source-network",
        project_postgres_volume="source-db",
    )
    candidate = SimpleNamespace(
        workspace_volume="candidate-code",
        project_postgres_password="candidate-password",
        internal_network="candidate-network",
        project_postgres_volume="candidate-db",
    )
    source_manager = SimpleNamespace(
        operation_lock=Lock(),
        state_store=SimpleNamespace(load=lambda _id: _state(request.fencing_epoch)),
        machine_runtime=SimpleNamespace(parts=lambda _state: (object(), source)),
    )

    async def read_candidate_source(_volume):
        return {path: text.encode() for path, text in candidate_files.items()}

    candidate_manager = SimpleNamespace(
        operation_lock=Lock(),
        state_store=SimpleNamespace(load=lambda _id: _state(proof.candidate_fencing_epoch)),
        machine_runtime=SimpleNamespace(parts=lambda _state: (object(), candidate)),
        docker=SimpleNamespace(read_workspace_source_files=read_candidate_source),
    )

    engine = DockerAdaptationWorkspaceEngine(probe_rehearser=rehearser)
    monkeypatch.setattr(engine, "_manager", lambda _id: source_manager)
    monkeypatch.setattr(engine, "_database_digests", lambda *_args: ("d" * 64, "e" * 64))
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication_capacity.production_manager",
        lambda _manager, _settings: candidate_manager,
    )
    monkeypatch.setattr("omnia_orchestrator.core.config.get_settings", lambda: object())

    async def read_files(manager, volume):
        if manager is source_manager and volume == "source-code":
            return source_files
        return candidate_files

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", read_files
    )
    monkeypatch.setattr(
        module,
        "observe_database",
        lambda _backend, *, observed_on: InventoryReport(
            presence="empty",
            coverage="complete",
            schema_analysis="complete",
            observed_on=observed_on,
        ),
    )
    contract = SimpleNamespace(model_dump=lambda **_kwargs: {"tables": []})
    monkeypatch.setattr(module, "catalog_contract", lambda _backend: (contract, []))
    monkeypatch.setattr(
        module, "content_inventory_partition_digests", lambda *_args: ("b" * 64, "c" * 64)
    )
    # Манифест проверки годен — значит репетиция действительно запускается и
    # причиной может быть только её собственный провал.
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_adaptation_probe.validate_probe_contract",
        lambda *_args: SimpleNamespace(contract_digest="f" * 64, witnesses=()),
    )
    return engine, request, proof


def _rehearser_failing_with(exc: BaseException):
    class _Rehearser:
        async def rehearse_candidate(self, **_kwargs):
            raise exc

    return _Rehearser()


@pytest.mark.parametrize(("leg", "code"), sorted(_LEGS.items()))
async def test_each_leg_of_the_rehearsal_gets_its_own_reason(
    monkeypatch: pytest.MonkeyPatch, leg: str, code: str
) -> None:
    """Главное: по коду видно, какая из шести ног упала."""
    engine, request, proof = _prove_fixture(
        monkeypatch, _rehearser_failing_with(ProbeRehearsalFailure("проверка", leg=leg))
    )

    result = await engine.prove(request, proof)

    assert result.state == "migration_required"
    assert result.reason_code == code


async def test_an_unfamiliar_rehearsal_failure_still_lands_on_the_general_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Незнакомый сбой не роняет операцию и не выдумывает ногу."""
    engine, request, proof = _prove_fixture(
        monkeypatch, _rehearser_failing_with(CellResourceError("что-то третье"))
    )

    result = await engine.prove(request, proof)

    assert result.state == "migration_required"
    assert result.reason_code == "probe_rehearsal_failed"


async def test_a_successful_rehearsal_is_not_disturbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Защита от перестраховки: удачная репетиция по-прежнему даёт готовое доказательство."""

    class _Rehearser:
        async def rehearse_candidate(self, **_kwargs):
            return "9" * 64

    engine, request, proof = _prove_fixture(monkeypatch, _Rehearser())

    result = await engine.prove(request, proof)

    assert (result.state, result.reason_code) == ("proof_ready", None)


def test_the_six_legs_are_named_in_the_shared_vocabulary() -> None:
    assert set(_LEGS.values()) <= _KNOWN


def test_the_general_code_survives_for_everything_else() -> None:
    # Он остаётся смыслом «репетиция провалилась, а ногу назвать не смогли».
    assert "probe_rehearsal_failed" in _KNOWN


@pytest.mark.parametrize(
    "code",
    [
        "probe_manifest_invalid",
        "candidate_changed_during_rehearsal",
        "candidate_schema_changed",
        "candidate_business_data_changed",
        "candidate_technical_data_changed",
        "source_code_changed",
        "source_database_changed",
    ],
)
def test_existing_codes_are_untouched(code: str) -> None:
    assert code in _KNOWN


def test_the_platform_accepts_every_code_the_orchestrator_can_send() -> None:
    """Защита двухфазной выкатки: приём шире отправки, иначе ответ отвергнут целиком."""
    client = (
        Path(__file__).resolve().parents[2]
        / "api"
        / "src"
        / "omnia_api"
        / "services"
        / "orchestrator_client.py"
    ).read_text(encoding="utf-8")
    block = client.split("valid_reasons = {", 1)[1].split("}", 1)[0]
    accepted = set(re.findall(r'"([a-z_]+)"', block))

    assert _KNOWN <= accepted, f"платформа не примет: {sorted(_KNOWN - accepted)}"


def test_every_leg_the_prober_runs_is_labelled() -> None:
    """Иначе словарь ног разойдётся с тем, что репетиция на самом деле гоняет.

    Без этой проверки можно добавить седьмую ногу и молча вернуться к немоте:
    её падение снова стало бы безымянным.
    """
    from omnia_orchestrator.services import restoration_adaptation_health as health

    source = Path(health.__file__).read_text(encoding="utf-8")
    body = source.split("async def rehearse_candidate", 1)[1].split("async def verify_", 1)[0]
    ran = set(re.findall(r'\(\s*"([a-z_]+)"\s*,\s*self\.verify_', body))

    assert ran == set(_LEGS), f"ноги разошлись: {ran ^ set(_LEGS)}"
