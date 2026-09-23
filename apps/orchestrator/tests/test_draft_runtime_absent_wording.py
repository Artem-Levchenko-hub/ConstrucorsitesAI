"""Фраза «draft runtime is not running» — часть договора между сторонами.

Платформа по ней отличает «живой среды нет» от «не смогли привести среду в
порядок». Разница для владельца принципиальная: в первом случае показывать
небезопасный черновик попросту некому, во втором среда может его отдавать.

Признак сейчас именно текстовый (`services/project_cell_executor.py`,
`draft_runtime_absent`) — отдельный код ответа был бы надёжнее, но это правка на
двух сторонах с жёстким порядком выкатки. Пока признак текстовый, менять фразу
без второй стороны нельзя, и этот тест не даёт сделать это молча: иначе на проде
вернулась бы ложная тревога «среда может показывать небезопасный черновик»
ровно в том случае, когда среды нет.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROUTER = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "omnia_orchestrator"
    / "routers"
    / "workspace.py"
)
_PHRASE = "draft runtime is not running"


def test_the_refusal_keeps_its_agreed_wording() -> None:
    assert _PHRASE in _ROUTER.read_text(encoding="utf-8")


def test_every_such_refusal_uses_a_status_the_platform_recognises() -> None:
    """Платформа принимает этот признак только с кодами 409 и 503."""
    source = _ROUTER.read_text(encoding="utf-8")
    statuses = {
        int(match.group("code"))
        for match in re.finditer(
            r"message=\s*\n?\s*[\"']" + re.escape(_PHRASE) + r"[\"'],?\s*\n?\s*"
            r"status_code=(?P<code>\d+)",
            source,
        )
    }
    inline = {
        int(match.group("code"))
        for match in re.finditer(
            r"code=[\"'][a-z_]+[\"'],\s*message=[\"']" + re.escape(_PHRASE) + r"[\"'],\s*"
            r"status_code=(?P<code>\d+)",
            source,
        )
    }
    found = statuses | inline

    assert found, "ни один отказ с этой фразой не распознан — проверьте форму записи"
    assert found <= {409, 503}, f"неизвестный код ответа: {sorted(found - {409, 503})}"
