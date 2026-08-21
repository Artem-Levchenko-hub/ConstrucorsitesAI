from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from omnia_api.ops.production_canary import (
    CanaryConfig,
    CanaryConfigurationError,
    CanaryFailure,
    ProductionCanary,
)

_FAILURE_REPORTS = {
    "build run returned the wrong response mode": "Тестовая генерация вернула неверный режим.",
    "edit run returned the wrong response mode": "Тестовое редактирование вернуло неверный режим.",
    "prompt returned the wrong generation mode": "Тестовая генерация вернула неверный режим.",
    "latest generation run identity changed": "Результат тестовой генерации не совпал с запуском.",
    "generation reached a failed terminal status": "Тестовая генерация завершилась с ошибкой.",
    "generation returned an invalid status": "Тестовая генерация вернула неизвестный статус.",
    "generation deadline exceeded": "Тестовая генерация превысила лимит времени.",
    "canary deadline exceeded": "Проверка тестовых генераций превысила лимит времени.",
    "runtime did not start": "Среда тестового проекта не запустилась.",
    "preview session URL is invalid": "Превью тестовой генерации вернуло неверную ссылку.",
    "preview session response is invalid": "Превью тестовой генерации вернуло неверный ответ.",
    "preview bootstrap request failed": "Превью тестовой генерации недоступно.",
    "preview bootstrap contract failed": "Превью тестовой генерации недоступно.",
    "preview request failed": "Превью тестовой генерации недоступно.",
    "preview did not become ready": "Превью тестовой генерации не запустилось.",
    "release changed during canary": "Версия production изменилась во время проверки.",
    "release health identity mismatch": "Версия production не прошла проверку здоровья.",
    "release dependency health mismatch": ("Production-зависимости не прошли проверку здоровья."),
    "public API request failed": "Production API недоступен.",
    "public API returned an unexpected status": "Production API вернул ошибку.",
    "public API returned invalid JSON": "Production API вернул неверный ответ.",
    "public API returned an invalid payload": "Production API вернул неверный ответ.",
    "generation did not advance the project snapshot": (
        "Тестовая генерация не создала новый результат."
    ),
    "snapshot identity mismatch": "Результат тестовой генерации не прошёл проверку.",
    "generated snapshot has no files": "Тестовая генерация не создала файлы.",
    "project_invalid": "Тестовый проект вернул неверный идентификатор.",
    "generation_invalid": "Тестовая генерация вернула неверный идентификатор.",
    "snapshot_invalid": "Тестовая генерация вернула неверный результат.",
    "project cleanup failed": "Не удалось удалить тестовый проект.",
    "logout cleanup failed": "Не удалось завершить тестовую сессию.",
}


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, separators=(",", ":"), sort_keys=True), flush=True)


def _write_result(status: str, *, error: str | None = None) -> None:
    result_file = os.getenv("PRODUCTION_CANARY_RESULT_FILE")
    if not result_file:
        return
    result = {"status": status}
    if error is not None:
        result["error"] = error
    Path(result_file).write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    try:
        config = CanaryConfig.from_env()
        ProductionCanary(config, emit=_emit).run()
    except CanaryConfigurationError:
        _write_result(
            "failure",
            error="Не удалось запустить тестовые генерации: неверная конфигурация.",
        )
        print("production canary configuration invalid", file=sys.stderr)
        return 1
    except CanaryFailure as exc:
        error = _FAILURE_REPORTS.get(str(exc), "Техническая ошибка production-canary.")
        _write_result("failure", error=error)
        print(exc.public_message, file=sys.stderr)
        return 1
    except Exception:
        _write_result("failure", error="Техническая ошибка production-canary.")
        print("production canary failed", file=sys.stderr)
        return 1
    _write_result("success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
