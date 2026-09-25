"""Три разные беды репетиции проверки — три разных ответа.

25.09.2026 адаптивный откат на проде закончился кодом
`migration_required:probe_rehearsal_failed`. По нему нельзя понять, что делать:
под одним кодом жили три положения, и они ведут к разным действиям.

  * приложение не объявило годную проверку — чинить должен агент адаптации;
  * проверка выполнилась и не прошла — разбираться с кодом приложения;
  * копия изменилась, пока шла проверка — операцию надо просто повторить.

Пока код один, оператор гадает, а владелец слышит «нужна миграция» независимо
от того, что произошло. Здесь закреплено само различение и то, что старый код
сохранил прежний смысл — он остался за настоящим провалом проверки.

Список кодов знают ДВЕ стороны, поэтому выкатка двухфазная: платформа учится
принимать новые коды раньше, чем оркестратор начинает их слать (тот же порядок,
что в c39ebf21).
"""

from __future__ import annotations

import pytest

from omnia_orchestrator.schemas.restoration_adaptation import RestorationAdaptationProof

_KNOWN = set(RestorationAdaptationProof.model_fields["reason_code"].annotation.__args__[0].__args__)


def test_the_three_causes_have_three_names() -> None:
    assert {
        "probe_manifest_invalid",
        "probe_rehearsal_failed",
        "candidate_changed_during_rehearsal",
    } <= _KNOWN


def test_the_old_code_keeps_its_narrow_meaning() -> None:
    """Старый код остаётся за настоящим провалом проверки, а не за всем подряд."""
    assert "probe_rehearsal_failed" in _KNOWN


@pytest.mark.parametrize(
    "code",
    [
        "candidate_schema_changed",
        "candidate_business_data_changed",
        "candidate_technical_data_changed",
        "source_code_changed",
        "source_database_changed",
    ],
)
def test_existing_codes_are_untouched(code: str) -> None:
    # Расширение списка не должно ничего из него вымывать.
    assert code in _KNOWN


def test_the_platform_accepts_every_code_the_orchestrator_can_send() -> None:
    """Главная защита двухфазной выкатки: приём шире отправки, а не наоборот.

    Если оркестратор научится слать код, которого платформа не знает, ответ
    будет отвергнут целиком — и владелец увидит не новую причину, а поломку.
    """
    import re
    from pathlib import Path

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
