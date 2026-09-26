"""Копию велят менять, а потом отказывают за то, что она изменилась.

25.09.2026, прод, прогон 0a058f14. Впервые манифест проверки прошёл: первая
попытка дала точное «misses a required value status», агент это применил, и
вторая попытка вернулась уже с другой причиной — `candidate_business_data_changed`.
То есть деловые данные изолированной копии перестали совпадать с данными
владельца.

Разбор показал противоречие в самих требованиях. Задание агенту прямо велит
делать на копии настоящие записи («real reads and writes»), потому что иначе
работу с данными не доказать. А доказательство берётся с той же копии и
отвергается, если её данные отличаются от источника. Указания привести копию в
исходный вид в задании при этом нет вовсе.

Проверка именно такая, какой должна быть: она гарантирует, что код доказан на тех
же данных, что у владельца, а не на выдуманных. Значит менять надо не проверку, а
задание: копию можно и нужно трогать, но к моменту доказательства она обязана
выглядеть ровно так, как её дали.
"""

from __future__ import annotations

from yleum_api.services.restoration_adaptation import restoration_probe_requirements


def _instruction() -> str:
    """Текст задания так, как его увидит агент, а не как он записан в коде.

    Задание собирается из соседних строковых литералов, поэтому фраза, которая
    для агента сплошная, в исходнике разрезана переносом. Проверять надо
    склеенный текст — иначе проверка ловит форматирование, а не смысл.
    """
    import inspect
    import re

    from yleum_api.services import restoration_adaptation as module

    return re.sub(r'"\s*\n\s*"', "", inspect.getsource(module))


def test_the_agent_is_told_to_test_only_on_rows_it_created() -> None:
    """Главное требование: чужие записи не трогать вовсе.

    Первая редакция говорила «верни копию как было: удали созданное и отмени
    изменённое». Офлайн-проверка на настоящей базе показала, что вторая половина
    невыполнима: вернуть видимое значение мало — у строки есть колонки, на которые
    агент не смотрит (отметка времени изменения), и сверка видит их тоже. Поэтому
    формулировка ужесточена: проверять изменение и удаление только на своих
    записях.
    """
    text = _instruction()

    assert "Test only on rows you create yourself" in text
    assert "never update or delete a row that was already there" in text


def test_the_reason_is_explained_not_just_ordered() -> None:
    """Голое «убери за собой» агент трактует как необязательную уборку.

    Ему важно знать, что именно от этого зависит: доказательство берут с той же
    копии и сверяют с данными владельца построчно.
    """
    text = _instruction()

    assert "compared with the owner's live data" in text


def test_the_invisible_columns_trap_is_spelled_out() -> None:
    """Без этого агент «вернёт как было» видимое значение и всё равно получит отказ."""
    text = _instruction()

    assert "Restoring the text you changed is NOT enough" in text


def test_the_permission_to_write_is_not_withdrawn() -> None:
    """Запретить записи нельзя: без них работу с данными не доказать."""
    text = _instruction()

    assert "real reads and writes" in text


def test_the_probe_contract_itself_is_left_alone() -> None:
    # Требования к проверочной точке — отдельный договор, его не трогаем.
    assert "restoration-probe.json" in restoration_probe_requirements()
