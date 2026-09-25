"""Адаптации не хватает двух починок: правил больше, чем два.

25.09.2026, прод, прогон c32cdde8. Адаптивный откат закончился отказом
«probe_manifest_invalid (adaptation business witness misses a required value
status)» — то есть в последнем ответе агенту БУКВАЛЬНО сказали, какую колонку
дописать, и на этом его остановили. Прогон при этом истратил 3185 секунд из
примерно 6300 доступных: упёрлись не во время, а в счётчик.

Цикл починки давал три прохода, то есть две починки. Адаптации этого мало по
устройству: годность проверочной точки решают четырнадцать правил, агент узнаёт
их по одному (каждое — отдельный круг с полной пересборкой), и двух кругов
хватает ровно на то, чтобы узнать второе правило и не успеть его применить.

Настоящая граница у адаптации и так есть — окно починки по времени. Счётчик
проходов поверх него не защищает ни от чего: агент, который ничего не изменил,
и так останавливается сразу (`updated == files`). Поэтому обычная генерация
сохраняет прежние три прохода, а адаптация получает столько, сколько успевает
в своё окно.
"""

from __future__ import annotations

import inspect

from yleum_api.services.max_finalization import MaxFinalizationCoordinator

_SOURCE = inspect.getsource(MaxFinalizationCoordinator.finalize_with_repair)


def test_an_adaptation_gets_more_rounds_than_an_ordinary_edit() -> None:
    """Главное: число проходов у адаптации не то же самое, что у обычной правки."""
    assert "_ADAPTATION_REPAIR_ROUNDS" in _SOURCE
    assert "_ORDINARY_REPAIR_ROUNDS" in _SOURCE


def test_the_ordinary_loop_keeps_its_three_passes() -> None:
    from yleum_api.services import max_finalization

    assert max_finalization._ORDINARY_REPAIR_ROUNDS == 3


def test_the_adaptation_loop_fits_more_than_the_two_rules_it_learned() -> None:
    """Двух починок не хватило ровно на одно применение известного правила."""
    from yleum_api.services import max_finalization

    assert max_finalization._ADAPTATION_REPAIR_ROUNDS > max_finalization._ORDINARY_REPAIR_ROUNDS


def test_an_agent_that_changed_nothing_still_stops_at_once() -> None:
    """Счётчик снимается, но защита от пустого цикла обязана остаться."""
    assert "updated == files" in _SOURCE


def test_the_deadline_still_bounds_every_pass() -> None:
    """Время остаётся настоящей границей — иначе прогон может идти вечно."""
    assert "_MIN_REPAIR_SECONDS" in _SOURCE
    assert "generation deadline exceeded before source repair" in _SOURCE


def test_the_loop_is_still_bounded_at_all() -> None:
    # Безграничный цикл вернул бы ту самую беду, от которой счётчик и заводили.
    assert "while True" not in _SOURCE
    assert "bounded finalization loop exhausted" in _SOURCE
