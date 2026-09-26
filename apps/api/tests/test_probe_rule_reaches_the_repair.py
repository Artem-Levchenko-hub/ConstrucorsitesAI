"""Нарушенное правило манифеста должно доезжать до того, кто чинит.

25.09.2026, прод, прогон ca6e4600: адаптация вернула `probe_manifest_invalid`.
Под этим кодом живут четырнадцать разных правил годности манифеста проверки, и
тем же ответом платформа ставит агенту задание на починку — то есть починка шла
вслепую, окно было потрачено впустую.

Оркестратор теперь присылает нарушенное правило отдельным полем. Здесь
закреплено, что платформа его принимает (и принимает ответ без него — старый
оркестратор поля не шлёт), что в это поле не пролезет ничего, кроме фразы
правила, и что правило попадает в текст, по которому чинят.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from yleum_api.services.max_finalization import _migration_required_reason
from yleum_api.services.orchestrator_client import _PROBE_RULE_NAME

_RULE = "adaptation business witness value is not text"


def test_a_proof_can_still_be_built_without_the_rule() -> None:
    """Новое поле не должно ломать всех, кто собирает доказательство без него.

    Первая попытка сделала поле обязательным и в середине структуры — и повалила
    каждый существующий вызов конструктора. Локально это не поймалось: те тесты
    требуют настоящей базы и здесь не запускаются, красноту нашёл CI. Поэтому
    проверка стоит отдельно и базы не требует.
    """
    from yleum_api.services.orchestrator_client import RestorationAdaptationProof

    proof = RestorationAdaptationProof(
        **{
            name: value
            for name, value in _minimal_proof_fields().items()
            if name != "reason_detail"
        }
    )

    assert proof.reason_detail is None


def _minimal_proof_fields() -> dict[str, object]:
    from uuid import uuid4

    return {
        "state": "migration_required",
        "reason_code": "probe_manifest_invalid",
        "source_workspace_id": uuid4(),
        "candidate_workspace_id": uuid4(),
        "operation_id": uuid4(),
        "project_id": uuid4(),
        "owner_id": uuid4(),
        "generation_run_id": uuid4(),
        "candidate_fencing_epoch": 1,
        "proof_attempt": 1,
        "source_workspace_revision": "0" * 64,
        "candidate_workspace_revision": "1" * 64,
        "candidate_proof_key": "2" * 64,
        "candidate_artifact_digest": "3" * 64,
        "source_database_digest": "4" * 64,
        "candidate_database_digest": "5" * 64,
        "source_schema_digest": "6" * 64,
        "candidate_schema_digest": "7" * 64,
        "source_business_digest": "8" * 64,
        "candidate_business_digest": "9" * 64,
        "source_technical_digest": "a" * 64,
        "candidate_technical_digest": "b" * 64,
        "probe_contract_digest": "c" * 64,
        "probe_rehearsal_digest": None,
        "probe_rehearsal_database_digest": None,
        "candidate_source_manifest_digest": "d" * 64,
        "proof_digest": "e" * 64,
        "capabilities": {},
        "reason_detail": None,
    }


def test_the_repair_instruction_names_the_broken_rule() -> None:
    reason = _migration_required_reason(
        SimpleNamespace(reason_code="probe_manifest_invalid", reason_detail=_RULE)
    )

    assert reason == f"migration_required:probe_manifest_invalid ({_RULE})"


def test_a_reason_without_a_rule_is_unchanged() -> None:
    """Совместимость: там, где правила нет, текст остаётся прежним."""
    reason = _migration_required_reason(
        SimpleNamespace(reason_code="candidate_schema_changed", reason_detail=None)
    )

    assert reason == "migration_required:candidate_schema_changed"


def test_a_missing_reason_still_falls_back() -> None:
    reason = _migration_required_reason(
        SimpleNamespace(reason_code=None, reason_detail=None)
    )

    assert reason == "migration_required:candidate_database_changed"


@pytest.mark.parametrize(
    "rule",
    [
        "adaptation business probe manifest is missing",
        "adaptation business witness overrides protected columns",
        "adaptation has no probeable business entity",
    ],
)
def test_real_rule_names_pass_the_shape_check(rule: str) -> None:
    assert _PROBE_RULE_NAME.fullmatch(rule)


@pytest.mark.parametrize(
    "smuggled",
    [
        "Клиент Иванов +79990000001",
        "leads.note = 'секрет'",
        "ADAPTATION BUSINESS WITNESS",
        "a" * 401,  # поле выросло под полный ответ, но границу держит
    ],
)
def test_nothing_but_a_rule_name_gets_through(smuggled: str) -> None:
    """Узость поля — это и есть защита: данным владельца в него не пролезть."""
    assert not _PROBE_RULE_NAME.fullmatch(smuggled)
