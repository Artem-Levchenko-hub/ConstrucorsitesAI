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
Промежуточный статус: полный gate, независимое review и поставка ещё завершаются.
Ускорение генерации и новый пользовательский запуск не заявляются.
