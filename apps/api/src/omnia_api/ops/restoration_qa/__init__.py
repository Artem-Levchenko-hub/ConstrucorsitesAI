"""Одноразовый синтетический прогон восстановления и его границы.

Пакет намеренно отделён от `services/restorations.py`: испытательный код не
должен жить в том же модуле, что и рабочий путь восстановления, иначе граница
между «проверяем» и «делаем» стирается.
"""

from omnia_api.ops.restoration_qa.contracts import (
    CheckResult,
    QaManifest,
    QaScopeRefused,
)

__all__ = ["CheckResult", "QaManifest", "QaScopeRefused"]
