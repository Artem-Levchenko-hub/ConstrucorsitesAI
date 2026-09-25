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

from omnia_api.services.max_finalization import _migration_required_reason
from omnia_api.services.orchestrator_client import _PROBE_RULE_NAME

_RULE = "adaptation business witness value is not text"


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
        "a" * 121,
    ],
)
def test_nothing_but_a_rule_name_gets_through(smuggled: str) -> None:
    """Узость поля — это и есть защита: данным владельца в него не пролезть."""
    assert not _PROBE_RULE_NAME.fullmatch(smuggled)
