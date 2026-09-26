"""Отказ обязан выдавать всё сразу: и все нарушения, и верный вид свидетеля.

Шестнадцать живых прогонов за сутки, и каждый приносил ровно ОДИН факт. Причина
не в агенте: проверка манифеста останавливается на первом же нарушении, поэтому
пятидесятиминутный прогон на проде работал как инструмент отладки со скоростью
один бит за прогон. Прогон 4a154055 показал это прямым текстом — две попытки, два
разных правила, — и вывод надо было сделать тогда же.

При этом таблица платформе известна целиком: и ключ, и колонка владельца, и какие
колонки обязательны. То есть она может не только перечислить все претензии разом,
но и сразу показать, как выглядит годный свидетель для этой таблицы. Тогда агенту
не остаётся ничего открывать — он просто выполняет.

Здесь закреплено и то, и другое.
"""

from __future__ import annotations

import json

import pytest

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.schemas.restoration_adaptation import RestorationAdaptationProof
from yleum_orchestrator.services.restoration_adaptation_probe import validate_probe_contract

from tests.test_probe_manifest_names_its_rule import _proof_kwargs

_CONTRACT = {
    "version": 1,
    "tables": [
        {
            "name": "leads",
            "columns": [
                {"name": "id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
                {"name": "max_user_id", "type": "text", "nullable": False},
                {"name": "name", "type": "text", "nullable": False},
                {"name": "phone", "type": "text", "nullable": False},
                {"name": "note", "type": "text", "nullable": True},
                {"name": "status", "type": "text", "nullable": False, "default": "'новая'::text"},
            ],
            "owner_column": "max_user_id",
            "primary_key": ["id"],
        }
    ],
}


def _refusal(witness: dict[str, object]) -> str:
    manifest = {
        "version": 1,
        "endpoint": "/api/restoration-probe",
        "max_payload_bytes": 4096,
        "witnesses": [witness],
    }
    files = {".omnia/restoration-probe.json": json.dumps(manifest, ensure_ascii=False)}
    with pytest.raises(CellIdentityConflict) as failure:
        validate_probe_contract(files, _CONTRACT)
    return str(failure.value)


def test_two_mistakes_at_once_are_both_named() -> None:
    """Раньше агент узнавал вторую ошибку только следующим прогоном."""
    message = _refusal(
        {
            "entity": "leads",
            "id_column": "id",
            "owner_column": "user_id",  # ошибка раз
            "value_column": "note",
            "create_values": {},  # ошибка два
        }
    )

    assert "owner column" in message
    assert "status" in message, f"вторая ошибка не названа: {message}"


def test_the_refusal_shows_what_a_correct_witness_looks_like() -> None:
    """Таблица известна целиком — значит верный ответ можно показать сразу."""
    message = _refusal(
        {
            "entity": "leads",
            "id_column": "id",
            "owner_column": "user_id",
            "value_column": "note",
            "create_values": {},
        }
    )

    # Всё, что агенту нужно, чтобы написать манифест без единой догадки.
    for part in ("leads", "id", "max_user_id", "name", "phone", "status"):
        assert part in message, f"в подсказке нет «{part}»: {message}"


def test_a_correct_witness_is_still_accepted() -> None:
    """Защита от перестраховки: годный манифест не должен вдруг отвергаться."""
    manifest = {
        "version": 1,
        "endpoint": "/api/restoration-probe",
        "max_payload_bytes": 4096,
        "witnesses": [
            {
                "entity": "leads",
                "id_column": "id",
                "owner_column": "max_user_id",
                "value_column": "note",
                "create_values": {"name": "П", "phone": "+7", "status": "новая"},
            }
        ],
    }
    files = {".omnia/restoration-probe.json": json.dumps(manifest, ensure_ascii=False)}

    assert validate_probe_contract(files, _CONTRACT) is not None


def test_the_whole_answer_still_fits_the_field_that_carries_it() -> None:
    """Иначе подсказка не доедет до починки и всё это бессмысленно."""
    message = _refusal(
        {
            "entity": "leads",
            "id_column": "id",
            "owner_column": "user_id",
            "value_column": "note",
            "create_values": {},
        }
    )

    proof = RestorationAdaptationProof(**_proof_kwargs(reason_detail=message))

    assert proof.reason_detail == message


def test_the_platform_accepts_a_message_of_this_length() -> None:
    """Приём шире отправки: узкое поле на той стороне отвергло бы ответ целиком."""
    import re
    from pathlib import Path

    client = (
        Path(__file__).resolve().parents[2]
        / "api"
        / "src"
        / "yleum_api"
        / "services"
        / "orchestrator_client.py"
    ).read_text(encoding="utf-8")
    pattern = re.search(r'_PROBE_RULE_NAME = re\.compile\(r"([^"]+)"\)', client)
    assert pattern is not None
    assert re.fullmatch(pattern.group(1), _refusal(
        {
            "entity": "leads",
            "id_column": "id",
            "owner_column": "user_id",
            "value_column": "note",
            "create_values": {},
        }
    ))
