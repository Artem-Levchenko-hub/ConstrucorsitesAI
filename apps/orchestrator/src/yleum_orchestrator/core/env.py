"""Чтение переменных окружения под новым и старым именем сразу.

При ребрендинге префикс переменных меняется с `OMNIA_` на `YLEUM_`. Переписать
файлы `.env` на серверах и выкатить новый код одномоментно нельзя: между этими
двумя действиями всегда есть зазор, и в этом зазоре сервис либо ещё не видит
нового имени, либо уже не видит старого. Поэтому код сначала учится читать оба
имени, предпочитая новое, и только потом переписываются файлы на хостах.

Чтение старого имени остаётся навсегда: стоит оно десять строк и страхует от
забытого хоста или от отката на старый `.env`.
"""

from __future__ import annotations

import os
from typing import overload

NEW_PREFIX = "YLEUM_"
LEGACY_PREFIX = "OMNIA_"


# Перегрузки нужны не для красоты: без них проверка типов считает, что значение
# может отсутствовать даже там, где задано умолчание, и требует лишних проверок
# в полутора десятках мест вызова.
@overload
def rebrand_env(suffix: str) -> str | None: ...


@overload
def rebrand_env(suffix: str, default: str) -> str: ...


def rebrand_env(suffix: str, default: str | None = None) -> str | None:
    """Значение `YLEUM_<suffix>`, иначе `OMNIA_<suffix>`, иначе `default`.

    Пустая строка считается заданным значением: если новое имя выставлено
    пустым намеренно, старое не должно его «воскрешать».
    """
    value = os.getenv(NEW_PREFIX + suffix)
    if value is None:
        value = os.getenv(LEGACY_PREFIX + suffix)
    return default if value is None else value
