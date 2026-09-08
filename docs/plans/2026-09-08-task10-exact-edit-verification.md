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
