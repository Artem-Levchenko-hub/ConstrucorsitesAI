# Task 10A — общая проверка точной замены файла

## Baseline и граница

BASE `99b2234604bfee87229b8688afe7b6128a8f9527`; local/origin/main совпадают.
Production checkout тот же; API/worker/generation-worker `f00cdfac10f5f07279d98d523bbe659570571601`,
web `10c4ef12`, orchestrator по предыдущей поставке `1011a0fd`.
Read-only preflight: активных runs/operations/leases нет, свободно 42.3 GiB.
Пять dirty серверных документов сохранены: diff SHA256
`0faee2b7c90dfd954d0def658d80cd89f21312061c500b2d9efd6ae06f1d250d`.

Этот первый commit добавляет только baseline tests и ранний CI шаг.
Исходный алгоритм ещё не меняется. После успеха реального Cell baseline
разрешён узкий перенос; окончательный commit обязан пройти полный CI и
полную API-only поставку. Промежуточный test-only CI может быть superseded
после ранней проверки: его не называть полным gate.

Дублируются только membership/count правила точного search внутри current
в agent_builder.make_container_executor и project_cell_executor. Path,
permissions, fenced write, local state, sanitizers, hot reload и различия
observation остаются у backend-specific callers. Registry — отдельный пакет.

Нельзя менять empty search, non-overlap str.count, Python str(replacement),
Unicode/case/CRLF или старые ошибки. Успешная проверка не гарантирует успешную
запись: транспорты и отказ fence остаются отдельными стадиями.

## Проверки до изменения

`tests/test_exact_edit_contract.py` вызывает настоящие executors с Action(edit_file),
подменяет только существующие транспортные границы. Ожидаемые observations и
изменения файлов заданы независимо от новой предполагаемой функции.
Container executor: **23 passed, 3 deselected**, 1.73 s; Ruff clean.

Три PostgreSQL cases подготовлены для настоящего Cell handle через существующий
`test_project_cell_executor._prepare_executor`: 19 вариантов точной замены,
отдельный path/content контракт и отказ устаревшего fence без local mutation.
Это реальная контрольная PostgreSQL с mocked runtime transport, не запущенная
Project Cell и не проверка пользовательской БД. До CI эти три cases не пройдены.
Fixture очищает схему: только disposable DB, никогда production URL.

Ранний CI запускает весь новый файл; прежние release-critical/full gates
сохраняются. Пользовательские генерации и модельные запросы не запускаются.

Первый baseline commit `7efadb99`, CI `34272318755`: 23 passed / 3 failed.
Все три DB cases упали до алгоритма на `ModuleNotFoundError` из-за bare
импорта test_project_cell_executor. Локальная ручная проверка с добавлением
папки tests в sys.path ошибочно скрыла эту проблему. В repository tests —
пакет; импорт исправлен на `tests.test_project_cell_executor`, как у соседних
реальных consumer tests. Проверено без изменения sys.path: exact module path,
26 collected и 23 passed / 3 deselected (2.32 s), Ruff clean. DB baseline
ещё ожидает повторного CI; production source не изменён.

## Перенос после успешного Cell baseline

На исходном алгоритме commit `03289047703981e151228e8c061ce801eeb8d072`,
CI `34272760186`: ранний exact-edit шаг success. Только после этого изменены
два caller и добавлен чистый `services/exact_edit.py::validate_exact_edit`.
Он возвращает прежний error string либо None; search/count порядок сохранён.
Сами replacement, observations, пути и side effects не переносились.

После переноса: **23 passed, 3 deselected**, 2.33 s. Смежные реальные
agent_builder/agent_native/max_generation_contract suites: **149 passed**,
69.21 s. Full Ruff clean; mypy: **276 source files**, ошибок нет.
Независимое source/test/CI/deployment helper review: No findings.
Финальный full CI и реальная поставка ещё ожидаются; пакет не завершён.

## Критерии качества плана

| Требования | Доказательство |
|---|---|
| 1–2 | Одна чистая обязанность, два строковых аргумента, явный error-or-None результат. |
| 3 | Два источника правил/ошибок заменены одним; разные backend контракты не объединены. |
| 4 | Helper без IO. Чтение, запись, hot reload и fenced операции видны в прежних callers. |
| 5–6 | Нет классов, state bag, циклических импортов и механического дробления большого файла. |
| 7–8 | Кэш, потоки, схемы и дополнительные зависимости не добавлялись; тесты не удалялись. |
| 9 | Алгоритм, ошибки, str conversion и порядок побочных действий сохранены. |
| 10 | Карта: два executor edit_file → чистая проверка → прежняя backend запись; consumer tests приведены выше. |

Меньше дублирующихся правил, но дополнительная функция не является измеренным
ускорением генерации. Registry facets и lifecycle WebSocket — другие пакеты.

Команды из apps/api: `uv run --frozen pytest -o addopts='' -q tests/test_exact_edit_contract.py`
на disposable PostgreSQL, `uv run pytest -q tests/test_agent_builder.py tests/test_agent_native.py tests/test_max_generation_contract.py`,
`uv run ruff check .`, `uv run mypy src`. Локальный baseline запускать с
`-k 'not disposable_db'` и dead-loopback DB/Redis, не с production environment.
