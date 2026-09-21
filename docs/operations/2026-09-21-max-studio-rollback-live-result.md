# MAX Studio: итог живого прогона откатов и остаток полной интеграции

Дата: 21 сентября 2026 года

Production-ревизия API, worker, generation-worker и orchestrator во время итогового прогона:
`e3d73d4a7a5ca03773f27b04cb3e6f275b02192f`.

QA-проект: `760144ab-57ca-40c1-a9db-918d3f9b6816`.
Project Cell: `438e65bd-4fef-4a01-b1a8-875b13ec0259`.

Этот отчёт дополняет
`docs/operations/2026-09-20-max-studio-rollback-production-readiness.md` живым
прогоном после поставки исправления durable adaptive-proof handoff. Он фиксирует
фактический результат, а также работу, которая остаётся до полного product/production
приёмочного статуса.

## 1. Короткое решение

Три продуктовые ветки определены и реализованы:

1. Пустая бизнес-область — автоматический возврат без ИИ.
2. Совместимые бизнес-данные — автоматический exact-возврат без ИИ.
3. Несовместимые бизнес-данные — явный запуск ИИ, изолированный кандидат и
   независимое доказательство до активации.

Первые две ветки ранее прошли живой пользовательский путь. Третья ветка после
поставки `e3d73d4a` повторно дошла до запуска нового adaptation run, но завершилась
без активации: агент превысил 25-минутный generation deadline на фазе `edit`.
Исправления provenance/orphan прошли regression, полный CI и независимое review.
В живом run прежние сигнатуры ошибок не повторились, но он остановился раньше
финального proof/activation; живой успех handoff этим прогоном ещё не доказан. Новый
блокер — адаптация не успевает завершить edit/repair до общего deadline.

До полной интеграции откатов остаётся закрыть живую приёмку результата адаптации,
fault/recovery-матрицу активации и несколько эксплуатационных/UX замечаний. Пока эти
пункты не закрыты, несовместимый адаптивный откат нельзя объявлять production-ready.

## 2. Что поставлено и проверено

### 2.1. Код и поставка

- Исправлена передача provenance инвентаризации: физическое значение
  `source|candidate_copy` больше не подменяется названием фазы proof.
- Исправлена ложная orphan-финализация: generation worker сохраняет run, когда
  durable proof/activation handoff полностью подтверждён и принадлежит той же
  restoration operation.
- Невалидные, чужие или неполные handoff-журналы по-прежнему fail-closed.
- Ревизия `e3d73d4a7a5ca03773f27b04cb3e6f275b02192f` отправлена в `origin/main`.
- GitHub Actions run `35556875188` завершился успешно во всех семи job.
- Полный orchestrator pytest: `1957 passed, 63 skipped, 15 xfailed`.
- API generation-worker regression: `18 passed`.
- Полные mypy для API и orchestrator, Ruff и `git diff --check`: PASS.
- Astra defect-first review после исправлений: CLEAN, P0-P3 не найдено.
- Перед production-переключением проверены exact image source, PostgreSQL backup и
  конфигурация. После переключения API, worker, generation-worker и orchestrator
  подтвердили один release SHA; database, Redis, worker, generation worker,
  deploy control plane и preview storage вернули `ok`.

### 2.2. Повторная подготовка несовместимого отката

Новая restoration operation: `26a293c7-40c6-4699-8651-595458d05972`.

Живой путь v9 → v1 повторно подтвердил:

- бизнес-данные есть;
- `qa_tasks` содержит две записи;
- историческая v1 читает отсутствующую колонку `qa_tasks.title`;
- историческая v1 создаёт строки без обязательной `qa_tasks.summary`;
- текущая БД и текущий runtime не заменяются на стадии проверки;
- UI явно предложил `Адаптировать и восстановить` и сообщил о расходе лимита ИИ;
- запрос сначала был durable-сохранён, затем штатный идемпотентный retry создал
  ровно один новый generation run.

Новый adaptation run: `22f20a1e-372b-42c3-a779-2b117c1c2554`.

## 3. Терминальный результат повторной адаптации

<!-- TERMINAL_RESULT_START -->
Результат: **FAIL, безопасный отказ до активации**.

Фактические признаки:

- generation run: `22f20a1e-372b-42c3-a779-2b117c1c2554`;
- restoration operation: `26a293c7-40c6-4699-8651-595458d05972`;
- terminal generation status: `failed`;
- terminal restoration state/phase: `failed/generation`;
- UI: `Сборка не завершена за 25м 00с · 28 шаг.`;
- API error:
  `generation deadline exceeded; phase=edit; proof_key=e9790472b5501b2218f4933afa53ab8f616aebea64f46c962dd7a73ff9cd173e; operation_id=unknown`;
- агент прошёл чтение целевых schema/API/UI-файлов, записал restoration-probe
  contract/routes, изменил историческую страницу, запустил build и начал доработку
  по замечаниям проверки;
- `activation_effects_admitted=false`;
- `applied_version_id` и `applied_snapshot_id` не появились;
- один idempotency key соответствует ровно одному generation run;
- после отказа текущей остаётся v9;
- живой preview по-прежнему показывает обе исходные строки:
  `Adaptive v9 live fixture updated` и `Rollback E2E updated 4a20014f`.

Вывод: этот run не повторил прежние ошибки provenance/orphan, но остановился до
финального proof/activation, поэтому живой успех handoff остаётся непроверенным.
Агентная работа и repair заняли весь общий deadline. Поля `proof_key` и
`operation_id=unknown` относятся к edit-checkpoint агента: они не доказывают создание
durable proof intent и не означают потерю restoration ID. Диагностика всё равно
неоднозначна — terminal error должен отдельно возвращать обязательный
`restoration_operation_id` и nullable ID текущей agent/tool-операции.
<!-- TERMINAL_RESULT_END -->

## 4. Найденные замечания по живому пути

### P1. Старую terminal-failed операцию нельзя продолжить после исправления

Предыдущая operation `b5652eba-378d-4968-8c59-3fcd24508ff2` успела стать `failed`
до выкладки. Новый handoff-recovery намеренно не возобновляет уже терминальную
операцию: для доказательства понадобился новый restoration/adaptation run. Это
безопасное поведение, но оператору нужен явный action `Повторить откат` без ручного
перевыбора версии.

### P1. Живой успех адаптации ещё требует независимого data witness

Даже terminal `completed` недостаточно. После активации нужно проверить две исходные
строки по ID и значениям, create/update/reload/delete синтетической строки, restart и
запрет доступа второй signed identity. Proof receipt, version lineage и settlement
нужно сверить отдельно от UI.

### P2. Запуск адаптации требует понятного двухфазного UX

Первое нажатие отменяет подготовленный кандидат и сохраняет durable запрос. После
подтверждения cancel UI предлагает `Повторить запуск адаптации`; повтор использует тот
же idempotency key и не создаёт второй run. Технически это безопасно, но пользователь
видит дополнительное действие. UI должен сам продолжать запуск после подтверждённой
отмены либо ясно показывать один непрерывный процесс.

### P2. Временная занятость Project Cell отображается неточно

Во время release/wake UI показывал `Проект сейчас занят` и `Failed to fetch`, а после
завершения operation требовалось обновление. Orchestrator также логировал
`workspace_lock_timeout` для параллельного owner-business-config запроса. Нужно
преобразовать lock timeout в контролируемый 409/503, убрать ASGI traceback для
ожидаемой конкуренции и автоматически обновлять панель после terminal operation.

### P2. Production smoke ожидал устаревшие release SHA

Сами сервисы после deploy были здоровы, но repository variables для production smoke
не соответствовали фактическим API/worker/orchestrator SHA. Их нужно обновлять только
после подтверждённой поставки и затем запускать внешний smoke вручную.

### P2. Артефакты генерации остаются дорогими и неочевидными

История показала повторное исследование проекта, нестабильный счётчик шагов,
процедурную миграцию `DO` вопреки контракту и случай, когда успешная сборка не дала
доступный preview. Нужны короткий file/schema plan, перенос фактического diff между
продолжениями, детерминированные шаблоны безопасных миграций и terminal success только
после внешнего preview/proof.

## 5. Что осталось до полной интеграции

Порядок ниже является release gate, а не списком необязательных улучшений.

### P0 — завершить живую adaptive acceptance

1. Разделить deadline агентного редактирования, repair и финального proof; переход к
   proof должен происходить только при отдельном гарантированном бюджете, а принятый
   durable intent не должен обрываться общим edit deadline.
2. Разделить terminal telemetry: всегда возвращать `restoration_operation_id`, фазу и
   nullable ID текущей agent/tool-операции вместо неоднозначного `operation_id=unknown`.
3. Передавать агенту компактный schema/file plan и результаты предыдущих проверок,
   чтобы не тратить значительную часть 25 минут на повторное чтение.
4. Повторить один изолированный сценарий и получить terminal `completed`.
5. Проверить исторический интерфейс на текущей `summary`-схеме.
6. Доказать сохранность двух исходных строк и скрытых значений независимым SQL witness.
7. Выполнить create, read, update, reload и delete отдельной синтетической строки.
8. Проверить запрет чтения/изменения строк другой signed identity.
9. Перезапустить приложение/Project Cell и повторить чтение.
10. Сверить proof receipt, source/candidate/activation bindings, новую версию и ровно
   одно quota/wallet settlement.

### P0 — доказать recovery после точки невозврата

1. Crash API после `proof_ready`, до SQL commit.
2. Crash worker после durable activation offer.
3. Restart orchestrator во время activation.
4. Потеря HTTP-ответа при уже принятом apply.
5. Два callback одного результата.
6. Для каждого сценария доказать forward recovery, одну активную версию, одно
   settlement и отсутствие возврата старого кода к уже изменённой БД.

### P1 — обновить свежие доказательства двух автоматических веток

После текущей production-ревизии одним скриптовым QA-профилем повторить:

- пустая бизнес-область → автоматический возврат без compatibility/ИИ;
- совместимые бизнес-данные → автоматический exact без ИИ;
- сохранность и первая запись после возврата;
- отсутствие generation run и settlement для обеих веток.

Ранее эти ветки прошли живой путь; свежий прогон нужен как release regression для
единой production-ревизии.

### P1 — эксплуатационный контур

- обновить `PRODUCTION_EXPECTED_API_RELEASE_SHA`,
  `PRODUCTION_EXPECTED_WORKER_RELEASE_SHA` и
  `PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA` после финальной поставки;
- подтвердить фактический web SHA отдельно, не подменяя его backend SHA;
- запустить production smoke и постоянный MAX canary;
- добавить dashboard/alert для stuck restoration, proof intent, activation intent,
  reconcile age, duplicate callback и settlement mismatch;
- оформить operator action для безопасного повторения terminal-failed отката.

### P2 — UX и генерация

- автоматически продолжать durable adaptation request после завершения cancel;
- различать `последняя рабочая версия`, `кандидат`, `proof`, `activation` и `recovery`;
- показывать точную фазу и безопасную причину вместо `Failed to fetch`;
- хранить immutable terminal step count отдельно от live progress;
- передавать агенту две точные версии, schema diff, row/value invariants и разрешённый
  migration profile одним компактным контрактом;
- не засчитывать генерацию успешной до preview, CRUD/reload и owner-boundary proof.

## 6. Критерий завершения

Полная интеграция откатов завершена только когда все три ветки decision tree проходят
на одной production-ревизии, adaptive activation выдерживает fault/restart-матрицу,
данные и owner boundary подтверждены независимо, а production smoke зелёный с точными
release identity. До этого обычный и compatible exact можно считать MVP-доказанными,
а несовместимый adaptive — реализованным, но не полностью принятым в production.
