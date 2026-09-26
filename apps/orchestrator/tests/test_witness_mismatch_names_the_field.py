"""«Свидетель не совпадает со своей таблицей» — это девять разных бед под одним именем.

25.09.2026, прод, прогон c32cdde8. Отказ подготовки доказательства снова пришёл
с этим правилом, и на нём агент потратил целый круг починки. Правило честное, но
слишком широкое: под ним склеены девять проверок сразу — таблица служебная,
таблица только для чтения, владелец задан ссылкой, владельца нет вовсе, имя
колонки владельца не то, ключ не тот, ключ не uuid, колонки значения нет,
три колонки не различны.

Агенту это приходит как задание на починку. Девять возможных причин и ни одного
указания, какая — это ровно та немота, из-за которой откат неделю не двигался,
только этажом ниже. Причём таблица тут известна целиком: и её ключ, и колонка
владельца, и список колонок. То есть сказать точно ничего не стоит.

Здесь закреплено, что каждая из девяти бед называется своим именем и что имя
это по-прежнему помещается в узкое поле причины (строчные слова и имена колонок).
"""

from __future__ import annotations

import pytest

from tests.test_probe_manifest_names_its_rule import _proof_kwargs
from tests.test_restoration_adaptation_probe import _files
from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.schemas.restoration_adaptation import RestorationAdaptationProof
from yleum_orchestrator.services.restoration_adaptation_probe import validate_probe_contract


def _table(**changes: object) -> dict[str, object]:
    table: dict[str, object] = {
        "name": "orders",
        "columns": [
            {"name": "id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
            {"name": "max_user_id", "type": "text", "nullable": False},
            {"name": "probe_value", "type": "text", "nullable": False},
        ],
        "owner_column": "max_user_id",
        "primary_key": ["id"],
    }
    table.update(changes)
    return table


def _contract(**changes: object) -> dict[str, object]:
    return {"version": 1, "tables": [_table(**changes)]}


def _witness(**changes: object) -> dict[str, object]:
    witness: dict[str, object] = {
        "entity": "orders",
        "id_column": "id",
        "owner_column": "max_user_id",
        "value_column": "probe_value",
        "create_values": {},
    }
    witness.update(changes)
    return witness


def _fails_with(contract: dict[str, object], witness: dict[str, object]) -> str:
    with pytest.raises(CellIdentityConflict) as failure:
        validate_probe_contract(_files(witnesses=[witness]), contract)
    return str(failure.value)


def test_a_wrong_owner_column_says_which_one_is_right() -> None:
    """Самая частая беда: агент назвал колонку владельца не тем именем."""
    message = _fails_with(_contract(), _witness(owner_column="user_id"))

    assert "owner column" in message
    assert "max_user_id" in message, f"верное имя не названо: {message}"


def test_a_wrong_key_says_what_the_key_is() -> None:
    """Колонка владельца при этом названа верно — иначе сработало бы прошлое условие."""
    contract = _contract(
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
            {"name": "code", "type": "uuid", "nullable": True},
            {"name": "max_user_id", "type": "text", "nullable": False},
            {"name": "probe_value", "type": "text", "nullable": False},
        ]
    )

    message = _fails_with(contract, _witness(id_column="code"))

    assert "primary key" in message
    # Верный ключ назван — и в претензии, и в образце годного свидетеля,
    # который теперь идёт тем же сообщением.
    assert "primary key id" in message, f"верный ключ не назван: {message}"


def test_a_missing_value_column_is_named_as_such() -> None:
    message = _fails_with(_contract(), _witness(value_column="nope"))

    assert "value column" in message and "nope" in message


def test_three_columns_that_are_not_distinct_say_so() -> None:
    message = _fails_with(_contract(), _witness(value_column="id"))

    assert "distinct" in message


def test_a_key_that_is_not_a_uuid_says_so() -> None:
    contract = _contract(
        columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "max_user_id", "type": "text", "nullable": False},
            {"name": "probe_value", "type": "text", "nullable": False},
        ]
    )

    message = _fails_with(contract, _witness())

    assert "uuid" in message


def test_a_table_without_an_owner_is_refused_before_the_witness() -> None:
    """Без колонки владельца таблица вообще не проверяема — и это говорится раньше.

    Проверять свидетеля у такой таблицы бессмысленно: проверка опирается на то,
    что у записи есть прямой владелец.
    """
    message = _fails_with(_contract(owner_column=None), _witness())

    assert "no probeable business entity" in message


@pytest.mark.parametrize(
    ("contract", "witness"),
    [
        (_contract(), _witness(owner_column="user_id")),
        (_contract(), _witness(value_column="nope")),
        (_contract(), _witness(value_column="id")),
    ],
)
def test_every_new_name_still_fits_the_narrow_field(
    contract: dict[str, object], witness: dict[str, object]
) -> None:
    """Иначе точное имя не доедет до починки и мы вернёмся к немоте."""
    message = _fails_with(contract, witness)

    proof = RestorationAdaptationProof(**_proof_kwargs(reason_detail=message))

    assert proof.reason_detail == message
