"""«Манифест проверки негоден» должен называть нарушенное правило.

25.09.2026, прод, проект b5c4c26d, прогон ca6e4600. Впервые адаптация дошла до
доказательства и вернула не общий `probe_rehearsal_failed`, а точный
`probe_manifest_invalid` — то есть репетиция даже не запускалась: приложение не
объявило годную проверку. Это уже прогресс, но дальше снова стена.

Годность манифеста решается четырнадцатью разными правилами (нет манифеста, не
разобрался, неподдерживаемая версия, пропущена изменившаяся сущность, свидетель
не совпадает со своей таблицей, колонка значения не текстовая, свидетель лезет в
защищённые колонки, не хватает обязательного значения и так далее). Каждое
правило падает со своей внятной английской фразой — и все четырнадцать приходят
одним кодом.

Это не абстрактная придирча. Тем же ответом платформа ставит агенту задание на
починку, и агент правит манифест вслепую: он не знает, какое из правил нарушил.
В этом прогоне окно починки открылось и всё равно было потрачено впустую.

Здесь закреплено, что нарушенное правило доезжает до отчёта отдельным полем.
Поле нарочно узкое: только строчные латинские слова, не длиннее 120 символов, —
фразы правил именно такие, а значениям из чужой базы в такое поле не пролезть.

Словарь знают ДВЕ стороны, поэтому выкатка двухфазная: платформа принимает поле
раньше, чем оркестратор начинает его слать (тот же порядок, что в c39ebf21).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.test_probe_rehearsal_names_its_leg import _prove_fixture
from yleum_orchestrator.schemas.restoration_adaptation import RestorationAdaptationProof

_FIELD = "reason_detail"


def _proof_kwargs(**overrides: object) -> dict[str, object]:
    from uuid import uuid4

    base: dict[str, object] = {
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
        "candidate_source_manifest_digest": "4" * 64,
        "probe_contract_digest": "5" * 64,
        "source_database_digest": "6" * 64,
        "candidate_database_digest": "7" * 64,
        "source_schema_digest": "8" * 64,
        "candidate_schema_digest": "9" * 64,
        "source_business_digest": "a" * 64,
        "candidate_business_digest": "b" * 64,
        "source_technical_digest": "c" * 64,
        "candidate_technical_digest": "d" * 64,
        "proof_digest": "e" * 64,
        "capabilities": {},
    }
    base.update(overrides)
    return base


def test_the_broken_rule_travels_with_the_proof() -> None:
    proof = RestorationAdaptationProof(
        **_proof_kwargs(reason_detail="adaptation business witness value is not text")
    )

    assert proof.reason_detail == "adaptation business witness value is not text"


def test_a_proof_without_the_rule_is_still_valid() -> None:
    """Совместимость: старый оркестратор поля не шлёт, и это не поломка."""
    assert RestorationAdaptationProof(**_proof_kwargs()).reason_detail is None


@pytest.mark.parametrize(
    "detail",
    [
        "Клиент Иванов +79990000001",  # значение из базы владельца
        "adaptation failed: leads.note = 'секрет'",
        "ADAPTATION BUSINESS WITNESS",
        "a" * 121,
        "витрина",
    ],
)
def test_anything_but_a_plain_rule_name_is_refused(detail: str) -> None:
    """Узость поля — это и есть защита: данным в него не пролезть."""
    with pytest.raises(ValidationError):
        RestorationAdaptationProof(**_proof_kwargs(reason_detail=detail))


def test_every_rule_the_validator_can_raise_fits_the_field() -> None:
    """Иначе правило не доедет: слишком длинная или «нештатная» фраза будет отвергнута.

    Проверяются настоящие строки из проверяющего модуля, а не их пересказ.
    """
    from yleum_orchestrator.services import restoration_adaptation_probe as probe

    source = Path(probe.__file__).read_text(encoding="utf-8")
    sentences = set(re.findall(r'CellIdentityConflict\(\s*"([^"]+)"', source))
    assert sentences, "не нашёл ни одной фразы — проверка стала бы бессмысленной"

    pattern = RestorationAdaptationProof.model_fields[_FIELD].metadata
    for sentence in sentences:
        RestorationAdaptationProof(**_proof_kwargs(reason_detail=sentence))
    assert pattern is not None


async def test_an_invalid_manifest_reports_the_rule_it_broke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Главное: конкретное правило доходит до отчёта, а не теряется."""
    from yleum_orchestrator.core.cell_resources import CellIdentityConflict

    class _Rehearser:
        async def rehearse_candidate(self, **_kwargs):  # pragma: no cover - не дойдёт
            raise AssertionError("репетиция не должна запускаться при негодном манифесте")

    engine, request, proof = _prove_fixture(monkeypatch, _Rehearser())
    monkeypatch.setattr(
        "yleum_orchestrator.services.restoration_adaptation_probe.validate_probe_contract",
        lambda *_args: (_ for _ in ()).throw(
            CellIdentityConflict("adaptation business witness value is not text")
        ),
    )

    result = await engine.prove(request, proof)

    assert result.reason_code == "probe_manifest_invalid"
    assert result.reason_detail == "adaptation business witness value is not text"


async def test_a_rehearsal_failure_carries_no_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Правило есть только у манифеста; у провала репетиции его выдумывать нечего."""
    from yleum_orchestrator.services.restoration_adaptation_health import ProbeRehearsalFailure

    class _Rehearser:
        async def rehearse_candidate(self, **_kwargs):
            raise ProbeRehearsalFailure("нет", leg="signed_owner_read")

    engine, request, proof = _prove_fixture(monkeypatch, _Rehearser())

    result = await engine.prove(request, proof)

    assert (result.reason_code, result.reason_detail) == ("probe_owner_read_failed", None)


def test_the_missing_columns_are_named_not_merely_counted() -> None:
    """Живой прогон 4a154055: агент открывал недостающие колонки по одной.

    Две попытки доказательства подряд дали два разных правила — то есть агент
    чинит то, что ему называют, и сходится. Но каждая попытка стоит полного
    круга с копией проекта, около тринадцати минут, и прогон закончился по сроку
    на третьем круге. Проверка знает недостающие колонки поимённо в тот самый
    момент, когда отказывает, — значит и называть их должна сразу, а не по одной
    за круг.
    """
    from tests.test_restoration_adaptation_probe import _files
    from yleum_orchestrator.core.cell_resources import CellIdentityConflict
    from yleum_orchestrator.services.restoration_adaptation_probe import (
        validate_probe_contract,
    )

    # Зеркало живого случая: колонка добавлена позже, обязательная, с ДЕЛОВЫМ
    # умолчанием. Техническим считается только now()/gen_random_uuid() и им
    # подобные, поэтому такую колонку свидетель обязан задавать сам — и ровно
    # на ней прогон 4a154055 и споткнулся во второй раз.
    contract = {
        "version": 1,
        "tables": [
            {
                "name": "orders",
                "columns": [
                    {
                        "name": "id",
                        "type": "uuid",
                        "nullable": False,
                        "default": "gen_random_uuid()",
                    },
                    {"name": "max_user_id", "type": "text", "nullable": False},
                    {"name": "probe_value", "type": "text", "nullable": False},
                    {"name": "title", "type": "text", "nullable": False},
                    {
                        "name": "status",
                        "type": "text",
                        "nullable": False,
                        "default": "'новая'::text",
                    },
                ],
                "owner_column": "max_user_id",
                "primary_key": ["id"],
            }
        ],
    }
    witness = {
        "entity": "orders",
        "id_column": "id",
        "owner_column": "max_user_id",
        "value_column": "probe_value",
        "create_values": {},
    }
    with pytest.raises(CellIdentityConflict) as failure:
        validate_probe_contract(_files(witnesses=[witness]), contract)

    message = str(failure.value)
    assert "misses a required value" in message
    # Обе недостающие названы сразу, а не по одной за круг.
    assert "title" in message and "status" in message, f"колонки не названы: {message}"


def test_a_named_column_still_fits_the_narrow_field() -> None:
    """Имена колонок — это схема, а не данные владельца, и поле их вмещает."""
    proof = RestorationAdaptationProof(
        **_proof_kwargs(
            reason_detail="adaptation business witness misses a required value title max_user_id"
        )
    )

    assert "max_user_id" in (proof.reason_detail or "")


def test_the_platform_accepts_the_new_field() -> None:
    """Приём идёт впереди отправки, иначе ответ будет отвергнут целиком."""
    client = (
        Path(__file__).resolve().parents[2]
        / "api"
        / "src"
        / "yleum_api"
        / "services"
        / "orchestrator_client.py"
    ).read_text(encoding="utf-8")

    assert '"reason_detail"' in client, "платформа не знает поля — ответ будет отвергнут"
