# Task13 — приёмка выполненных пакетов и оставшиеся проверки

Дополнение 9 сентября: Task11 preview runtime `2ae98520` доставлен web-only.
Добавлены8 real-consumer characterization tests, полный web496/78, browser
1440/390 BEFORE/AFTER и независимое review. Запуск/опрос runtime отделён от
PreviewFrame без смены поведения; actual image/release/health подтверждены.
[Новые доказательства и ограничения](2026-09-09-task11-preview-runtime-verification.md).
Ниже сохранён исходный срез `179a3b3f..ff442154`; его числа source scan не
выдаются за повторный подсчёт после нового пакета. Полный Task11 и матрица20
по-прежнему не завершены.

Срез: 9 сентября 2026, исходники `179a3b3f..ff442154`. P01 и пакеты Task4/5/8/9/10A/10B/Task11 transport доставлены. Production: API/worker/generation-worker `85593d30`, web `ff442154`, orchestrator `1011a0fd`. Ниже сопоставлены конкретные проверки и ограничения. **Вся программа и все20 end-to-end сценариев не объявляются завершёнными.**

## Индекс доказательств

| Код | Проверенный пакет и граница |
|---|---|
| E4 | `docs/plans/2026-09-08-task4-history-verification.md`: `project-version-refetch.test.tsx` 13/13; смежные 68/5 suites; полный web 472/75 на Node20. Edge с реальными UI и HTTP/WS fixtures, 1440/390: HEAD requests 2/1→1/0, две страницы 3/1→2/0; terminal/history/F5/A→B→A. Source `10c4ef12` доставлен. |
| E5 | `docs/plans/2026-09-08-task5-template-verification.md`: baseline Git-tree fixture `static_templates_810f0fbb.json`; 147 template/select/prompt/export/mapping tests; установленный wheel вне checkout прошёл пять materialize/Git/export fixtures. Correction release `4960f6b9` доставлен; полный API 3129 passed / 12 skipped / 8 xfailed. Последний delivery-раздел заменяет прежние записи о блокировках. |
| E8 | `docs/plans/2026-09-08-task8-artifacts-verification.md`: `test_generation_artifacts.py` 31 DB-free + 10 disposable PostgreSQL = **41 passed** в CI34265225657. Настоящие AST-участки двух callers Git→SQL→refresh, real pygit2/in-memory MinIO; не весь `_process_prompt`. Source `e03793b4` доставлен. |
| E9 | `docs/plans/2026-09-08-task9-cancellation-verification.md`: целый cancellation helper 10 DB-free + 8 disposable PostgreSQL; с двумя настоящими endpoint/worker consumers — **20 passed**, CI34267815377. Source `f00cdfac` доставлен. |
| E10A | `docs/plans/2026-09-08-task10-exact-edit-verification.md`: `test_exact_edit_contract.py` BEFORE26/AFTER26, включая 3 настоящих Cell-handle cases с disposable **контрольной** PostgreSQL и mocked runtime. Смежные agent/native/MAX suites 149 passed. Source `66411078` доставлен, CI34273376561 success. |
| E10B | `docs/plans/2026-09-08-task10b-browser-container-verification.md`: BEFORE50, AFTER212 и offline50 consumer tests. API/оба workers `85593d30` доставлены. CI34278360238 attempt2 success: API3264/12skip/8xfail; orchestrator1244/30skip/15xfail и9 live Docker. Причина первоначального зависания unit job не установлена. |
| E11 | `docs/plans/2026-09-09-task11-transport-verification.md`: BEFORE19/AFTER19, новые15; полный web487/77. Edge1440/390 BEFORE/AFTER: F5, watermark7, история, terminal. Linux web и image gates CI34282672170 success; web `ff442154` доставлен, exact image/release/health/routes подтверждены. Серверный handshake и бизнес-БД fixtures не проверяют. |
| E12 | `.artifacts/refactor-task12-20260908/existing-trace.md`: ограниченный read-only разбор старого пользовательского run. Детализации внутренних стадий ensure/release нет; новая IO-оптимизация не обоснована. Это не контрольный AFTER запуск. |
| ER | Независимые cumulative reviews: API/orchestrator `179a3b3f..85593d30`, полный web `179a3b3f..ff442154`: **No findings**. Task11 отдельно проверен ещё одним reviewer. Статический review не заменяет runtime/customer-flow; artifacts находятся в `.artifacts/refactor-task13-20260908/`. |

Числа полных CI gates подтверждают регрессионный gate пакета, но сами по себе не доказывают ниже каждый пользовательский сценарий. Для строки используются конкретные fixtures/границы, а не одно общее «CI green».

Финальный CI34282672170 для `ff442154` завершён полностью: все jobs success;
API3264/12skip/8xfail за779.01s, web487/77, orchestrator1244/30skip/15xfail
и9 live Docker. Последний job завершился 2026-09-08 22:11:13 UTC.

## Матрица

| № | Сценарий и применимость | Конкретное имеющееся доказательство | Что остаётся непроверенным |
|---|---|---|---|
| 1 | Новое приложение; static/fullstack/API/bot/MAX/code — разные пути | E5 `test_real_initial_commit_matches_frozen_tree`: четыре static и fullstack scaffold; standalone materialization/export. E10B50 фиксирует template routing. | Полная новая модельная генерация и рабочее приложение на **каждом** поддерживаемом template не запускались. Наличие scaffold не равно готовому приложению. |
| 2 | Edit; legacy container и Project Cell | E10A26: `test_container_exact_edit_original_consumer`, `test_disposable_db_cell_exact_edit_original_consumer`, path/content и fence cases. E5 custom kit export/rollback сохраняет пользовательские bytes. | Нет нового browser→edit→бизнес-запись→reload цикла на реальной ячейке. |
| 3 | Continue существующего run/project | E8 `test_caller_publication_rows_order_and_real_git`, exact_tree и сохранение старого Git-tree в составе41 фиксируют участок публикации. | Прямой continue dispatch, продолжение модели и прежние runtime proofs отдельным вертикальным fixture не подтверждены. |
| 4 | Native/text/one-shot | E10A149 включает реальные `test_agent_builder.py`, `test_agent_native.py`, `test_max_generation_contract.py`; E8 agent/oneshot publication41 сохраняет разные kwargs/model/tokens. | Нет полной генерации каждого транспорта с реальным провайдером. Native coverage не подменяет text/one-shot end-to-end. |
| 5 | API/worker ownership и recovery | E9 ранний20: целый helper плюс два endpoint/worker consumer; commit/events/locks остаются у callers. | Конкурентное ожидание advisory/run lock и рестарт worker не воспроизведены новым fixture. |
| 6 | Повтор request/idempotency key | E8 `test_caller_reexecution_preserves_existing_non_idempotent_semantics` и disposable повтор в41 сохраняют старое поведение: повтор участка создаёт ещё snapshot/списание. | Это **не доказательство HTTP idempotency**: ей владеет внешний dispatch, который здесь не воспроизводится. |
| 7 | Два запроса одному project; single flight/другой tenant | E9 проверяет message project/run фильтры и FOR UPDATE; E10A Cell fixture передаёт текущие lease/fence dimensions. | Нет нового race-теста двух одновременных accept/dispatch и независимого tenant. SQL shape не доказывает ожидание блокировки. |
| 8 | Cancel/timeout/error | E9 helper/endpoint/worker20; E8 Git/upload/flush/commit failures + post-commit refresh в41; E10A runtime failure и stale fence. | Нет нового полного timeout/cancel во время реального model/Docker side effect. E11 выявляет прежние unmount cleanup gaps; они не исправлены. |
| 9 | Crash/restart | E8 fault injection сохраняет durable/undurable SQL и исходный Git; источник прямо допускает непривязанный Git artifact при SQL failure. | Настоящий process kill/restart, reconciliation неизвестного внешнего эффекта и повторная выдача lease не проверялись. Fault injection не равно crash recovery. |
| 10 | F5/socket reconnect | E4 браузер1440/390 возвращает HEAD/history; E11 browser BEFORE/AFTER дополнительно проверяет active URL, watermark7 reconnect и terminal→F5 без нового socket. | Реальный WS handshake/cookies/proxy/server replay не проверялись; terminal persistence — HTTP fixture. |
| 11 | Duplicate/out-of-order/late events | E4 13 cases: поздняя страница прежнего HEAD и A→B→A, terminal во время запроса; смежный replay suite входит в68. E11 проверяет передачу watermark7. | Нет нового real-server duplicate/out-of-order потока после сетевого обрыва; известные stale socket/probe gaps исключены из claim. |
| 12 | Историческая версия и страницы | E4 two-page test сохраняет selection до всех актуальных страниц; браузер выбирает v31/v1, новый HEADv33 не сбрасывает историю; E11 BEFORE сохраняетv31 через reconnect/terminal. | Авторизованный production customer-flow не проверен. Холодная история без cache может ждать прежний5s poll; это не устранённая задержка. |
| 13 | Snapshot/run/message linkage | E8 `test_caller_publication_rows_order_and_real_git`, `test_disposable_db_caller_publication`: parent/current/message/run/model/prompt/changed_files, fresh-session readback;41 passed. | Не вся acceptance/finalization цепочка и не все version-number/provenance пути после настоящей модели. |
| 14 | Разрешённый/запрещённый restore | E5 `test_committed_custom_kit_survives_http_export_and_rollback`; E10B real `post_rollback` покрывает realtime/negatives; существующий `test_max_restore_never_mutates_repo_runtime_or_versions` сохраняет409. E4 success callback — mock. | Безопасный MAX restore не реализован/не включён этим рефакторингом. Реальный rollback production DB не выполнялся. |
| 15 | Новые бизнес-записи между версиями | E8 disposable PostgreSQL проверяет **служебные** Snapshot/Project/Message/Run/User, повтор и ошибки. | Сценарий новых заказов/задач в **проектной бизнес-БД** между версиями не проверен. Нельзя считать его покрытым служебной DB fixture или backup. |
| 16 | Установка библиотек и проектная БД | E10A сохраняет generated-lockfile response и fenced write контракты. E10B оставляет DB facet отдельным от browser family; api/bot/SPА различаются. | Реальный install библиотеки, создание бизнес-таблиц, restart persistence и права project DB не проверялись новым пакетом. |
| 17 | Source/deps/schema edit и proofs | E8 exact_tree/пустые файлы; E10A byte-exact source edit/fence; backend-specific команды/проверки не объединены. | Нет новой матрицы source-only/deps/schema edits с проверкой всех invalidation dimensions. E12 не даёт grounds удалять build/checkpoint. |
| 18 | Preview/publish/export | E5 real Git + HTTP export/custom assets и установленный wheel; E10B preview до page.goto/runtime routing50; E4 реальная preview UI с fixtures. Production docs фиксируют public health/routes/kit hashes. | Новое автономное опубликованное приложение, signed MAX customer session, настоящая capture acceptance и весь auth/access publish-flow не проверены. |
| 19 | RU/non-RU/mixed-language | E10A exact-edit19 вариантов сохраняет Unicode/case/CRLF; E8 literal prompt/model metadata сохраняется. E5 correction включает два RU onboarding wire UI tests. | Это не RU/non-RU/mixed-language model-output матрица. Task6/7 prompts не менялись и модельный smoke отсутствует. |
| 20 | Чужой project, stale fence, symlink/secret | E9 foreign-project message case +8DB commit/rollback; E10A `test_disposable_db_cell_rejected_fence_does_not_change_local_file` (mock runtime denial) в26. E5 populated-destination collisions отклоняются до writes. | Нет нового комплексного cross-tenant/symlink/secret penetration сценария. Cell контрольнаяDB+fake runtime не доказывают OS/network isolation или отсутствие любых утечек. |

## Повторный просмотр исходников и результат

Одинаковые правила применены к BASE и final: canonical Git blobs из `apps/api/src`, `apps/orchestrator/src`, `apps/web/src`; расширения py/ts/tsx/js/css; исключены tests, __tests__, __pycache__, migrations. Exact duplicate groups считаются для файлов от512 bytes. Это ограниченный source scan, не анализ всей достижимости программы.

| Метрика | BASE | Final |
|---|---:|---:|
| Файлы в этой выборке | 621 | 619 |
| Строки | 159208 | 153261 |
| Canonical Git bytes | 7786191 | 7466930 |
| Группы одинаковых файлов от512 bytes | 3 | 1 |
| Источники собственного omnia-kit.css | 4 | 1 |
| Источники собственного omnia-kit.js | 4 | 1 |

Общие CSS68730 bytes/1209 строк и JS40110 bytes/835 строк побайтово совпадают со всеми четырьмя прежними копиями. Шесть удалённых дублирующих файлов —6132 поддерживаемые строки; generated outputs остаются самостоятельными благодаря проверенной materialization. Первоначальный scan через Windows git archive не принят: его EOL filters меняли представление старого дерева. Финальный scan читает canonical blobs через git cat-file, без checkout/archive преобразований.

Оставшаяся группа —четыре копии стороннего anime.min.js по17384 bytes. Они входят в существующие template trees и asset contract; один hash не доказывает, что эти файлы можно удалить без изменения упаковки. Новых недостижимых реализаций, удаление которых доказано этим scan, не установлено. Дополнительных удалений не делали.

Task4 отдельно доказал уменьшение HTTP работы истории: 2 запроса/1 отмена →1/0; для двух страниц3/1→2/0. Это измерение тех же browser fixtures, не ускорение всей генерации. Последний пользовательский run d6356635 завершился за11m20s на прежнем API: dispatch2.4s, ensure43s, release25.7s. Его завершение не является canary последующих поставок и не подтверждает бизнес-приёмку строк1–20.

## Что осталось

- Task3: согласовать числовую точность/округление агрегатов расходов и проверить golden JSON на disposable PostgreSQL. Billing contract не менялся.
- Task6/7: язык и сокращение внутренних инструкций требуют согласованного model smoke. Пользователь запускает генерации самостоятельно; prompts не менялись.
- Task11: доставлен только перенос транспорта. Выявленные до изменения unmount/stale-probe cleanup gaps остаются отдельным изменением поведения; дальнейшее деление lifecycle требует самостоятельной проверки.
- Task12: доступные логи не показывают внутренние затраты43-секундного ensure и25.7-секундного release. Удаление build/capture или новый cache без измерений не обоснованы.
- Task13: по выполненным пакетам есть source review, регрессионные gates и production delivery. Колонка непроверенного сохраняется: нет нового полного набора model/customer flows, cross-user бизнес-БД, реального crash recovery и signed MAX publication. Отметки100% или10/10 не выставляются.

Delivery записи: `/opt/omnia-runtime/releases/task10b-api-85593d30` и `/opt/omnia-runtime/releases/task11-web-ff442154`. Сохранены пять dirty документов и незатронутые сервисы; write gates сняты. Итоги дальнейших запусков и оставшиеся вопросы продолжать из `docs/plans/2026-09-08-refactoring-handoff.md`.
