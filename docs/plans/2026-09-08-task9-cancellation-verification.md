# Task 9 — отмена генерации в существующем service

BASE `2e447f41565f7e61e32bd90875203508ccd5fff2`; production API `e03793b4`,
web `10c4ef12`, orchestrator `1011a0fd`. Task 8 полностью доставлен;
H141 опубликован отдельно: public version 73, сохранены 137 прежних записей.

## Граница и результат структуры

Чистый R-перенос одной завершённой обязанности: весь helper
`messages._apply_cancelled_generation_locked` переехал в существующий
`services.generation_runs.apply_cancelled_generation_locked`.
Тело AST совпадает до/после за исключением имени функции. Переключены два
router caller и один worker caller. Удалён один private router import у worker;
два других execution imports пока остаются, весь `_process_prompt` не переносится.
Новых service→router зависимостей нет. Source diff: 53 добавления / 53 удаления.

Run lock, проверка статуса, commit, publish event, worker advisory lock,
монитор отмены и ownership остаются у прежних callers. В helper сохраняются
message ID + project ID фильтр и FOR UPDATE; операции только того же run
со статусом pending/waiting_capacity; единый timestamp; очистка capacity reason
и next_attempt_at; частичный ответ и единственная отметка отмены; готовое
сообщение не переписывается. Memory compilation остаётся последним шагом
с прежним fail-soft/savepoint. HTTP/Redis/таймауты/схемы/права не меняются.

## Baseline и проверки

До production-правок новый полный helper baseline: **10 passed, 8 deselected**,
1.52 s. После переноса: **10 passed, 8 deselected**, Ruff changed files clean.
Изменены только import/target и module memory-spy, ожидаемые значения сохранены.
Исполняется целый helper, не вырезанный AST-фрагмент. Локально DB/Redis URLs
намеренно указывают на недоступный loopback, модельных вызовов нет.

Fake session проверяет точный SQL и FOR UPDATE, порядок message→operations→memory,
статусы других операций и отсутствие владения commit/rollback. Это доказательство
построения запроса, не конкурентного ожидания блокировки. Run lock — условие caller.

8 disposable PostgreSQL cases: pending/waiting_capacity × caller commit/rollback
× свой/чужой project сообщения; состояние перечитывается новой сессией.
В CI добавлен ранний запуск этих тестов и реальных endpoint/worker consumers;
полный существующий API gate сохраняется. Локально DB cases не запускались.
Source/tests/CI/docs и deployment helper: независимое review **No findings**.
Full Ruff clean; mypy — **275 source files**, ошибок нет; YAML/diff sanity чистые.
Коммит `f00cdfac10f5f07279d98d523bbe659570571601` отправлен в origin/main.
CI `34267815377`: все семь jobs success. Ранний cancellation шаг — **20 passed**
(8.54 s): 18 helper cases, включая 8 PostgreSQL, плюс два endpoint/worker consumer.
Snapshot publication — **41 passed**. Полный API — **3188 passed, 12 skipped,
8 xfailed**, 745.51 s. Полный лог: `.artifacts/refactor-task9-20260908/ci-api.log`.

## Поставка

API, worker и generation-worker обновлены до `f00cdfac10f5f07279d98d523bbe659570571601`.
Фактический image: `sha256:54fece6119511cc929c452f19d14cf32232bd74bf75606f4be79f9d592f16c47`.
Именно этот повторно собранный image прошёл 10 offline cancellation tests
с отключённой сетью и без production credentials перед переключением.

Перед изменениями проверены нулевые active runs/operations/leases, создан
PostgreSQL backup. Короткий write gate снят после проверки. Миграций,
пользовательских генераций, изменений Project Cell lifecycle не было.
API health/release и image/release всех трёх consumers подтверждены.
Web `10c4ef128006cfedc5e1a781b9dfaa8e1d94b77e`, orchestrator
`1011a0fdf7cc4f636e7550937c0e89447fc21bcd` и другие compose consumers сохранили
прежние процессы/ID/image/start/env hash. `/login`, `/max/register`,
`/max/product`, `/max/guide`: 200; `/max`: прежний 307 на регистрацию.

Пять dirty серверных документов сохранены побайтно: diff SHA256
`0faee2b7c90dfd954d0def658d80cd89f21312061c500b2d9efd6ae06f1d250d`.
Backup/result: `/opt/omnia-runtime/releases/task9-api-f00cdfac`.
Публичный отчёт обновляется только записью H142; H134–H136 не публикуются.
Предсуществующий global smoke с единым устаревшим SHA не менялся:
проверены настоящие отдельные component releases.
Ускорение генерации и новый пользовательский запуск не заявляются.

## Gate качества плана

| Требования | Доказательство / граница |
|---|---|
| 1–2: обязанность, имя | Один public helper завершает locked cancellation; два входа session/run, результата нет. |
| 3: владелец | Существующий generation_runs service; worker больше не ищет эту операцию в router. |
| 4: side effects | Только SQL/memory в helper; commit/event/Redis остаются у callers и проверяются consumer tests. |
| 5–6: структура | Нет новых классов/context bag/import cycle; перенесена одна целая обязанность, не файл по лимиту строк. |
| 7–8: совместимость | Кэш/параллелизм/схема не добавлены; тесты и самостоятельные приложения не удалены. |
| 9: алгоритм | AST тела сохранён; изменение имени только внутреннее. Ошибки/транзакции/порядок сохранены. |
| 10: карта | Router cancel/finalizer и worker execute_dispatch → generation_runs helper → terminal memory; проверки и команды ниже. |

Worker по-прежнему импортирует две другие private execution-функции router.
Количество реализаций отмены остаётся одним; уменьшается связность, а не число
операций или строк. Изолированное имя не доказывает качество всей программы 10/10.

Команды из apps/api, только disposable DB/Redis:
`uv run --frozen pytest -o addopts='' -q tests/test_generation_cancellation.py`
и существующие `tests/test_generation_runs.py`, `tests/test_generation_worker.py`.
Локально добавлять `-k 'not disposable_db'` и использовать dead-loopback env.
Полный release gate задан в `.github/workflows/ci.yml`.
