# MAX Studio: свежий E2E-аудит версионирования, откатов и генерации

Дата проверки: 19 сентября 2026 года

Ревизия, на которой выполнены live/code проверки: `58ab73926b786925e3c81e3c84b36bc6954a6c6d`

Перед поставкой отчёта `origin/main` дополнительно обновлён до `081774037ed307e3b9eefa17c86b0d6d1a82fb45`; изменения между этими ревизиями отделяли старый конструктор и не затронули разобранные модули versioning/restoration/generation.

Свежий изолированный QA-проект: `d68cc61a-4889-4b8a-9134-af521de07776`

Контрольный ранее созданный QA-проект: `b958d03c-38c0-4374-8a47-3f06cb721096`

## 1. Решение по результату проверки

Версионирование и откаты пока нельзя считать готовыми к безопасной эксплуатации с реальными бизнес-данными.

Свежий прогон с созданием приложения с нуля подтвердил, что проблема возникает уже при простом совместимом изменении. Система может показать «Изменения готовы», создать текущую версию и запустить preview, хотя требуемого поля в живом приложении нет. После ошибки финализации новый запрос preview возвращал «Приложение не найдено». Подготовка отката с последующей отменой временно перешла в состояние `reconciling` с неверным сообщением о применении; новый prompt был ошибочно классифицирован как уже запущенная генерация и затем потерялся из клиентской очереди после reload.

Затем несовместимое изменение было доведено до v7: обязательный `email` и `private_note` работали; A3 сохранился после reload. Exact v7 → v1 корректно вернул `needs_changes`. Адаптация создала v8 с историческим UI, сохранила A1/A2/A3 и позволила создать и изменить A4. Последующий exact v8 → v7 прошёл без AI, создал техническую v9; отдельный preview и reload подтвердили четыре записи и `updated-v8`. Потеря проверенных значений не воспроизведена; публикация не выполнялась. Независимые delete, two-user isolation и отказная матрица остаются незакрытыми.

Главные release blockers:

1. Успех генерации не связан с выполнением конкретного пользовательского требования в реально обслуживаемом артефакте.
2. Агентная адаптация имеет доступ к живому Project Cell, а отказ финализации откатывает прежде всего файлы. Это оставляет путь к рассинхронизации кода и БД.
3. Проверка отката доказывает структуру и наличие HTTP-методов, но не смысл ответов, owner isolation, работу кнопок и сохранение после reload.
4. Обнаруженный риск удаления новых связанных данных допускается как warning; сохранность JSON при старых write-операциях не доказана behavioral-проверкой.
5. Машина состояний `generation → finalization → ensure → restoration → cancel` выдаёт противоречивые статусы и неточные объяснения конфликтов.
6. Evidence index не доказывает полную матрицу FV001–FV120 на текущей ревизии.

## 2. Что именно проверялось

### 2.1. Источники

- пользовательский план `MAX_Studio_Versioning_Completion_Plan_v4 (1).md` использован как спецификация требований и нумерация AV/FV, а не как инструкция запускать непроверенные действия;
- текущий код `origin/main` после `git fetch` и `git merge --ff-only`;
- UI MAX Studio, история версий, preview и журналы шагов генератора;
- свежий проект, созданный специально для теста;
- ранее созданный QA-проект с клиентами, email и визитами;
- целевые pytest/vitest-наборы;
- независимый defect-first разбор GPT-5.6 Sol и итоговая приоритизация GPT-6 Astra.

### 2.2. Свежий сценарий

Исходное приложение:

- таблица `qa_clients`;
- поля `name`, `phone`, `status` и технические поля;
- реальные CRUD-маршруты `/api/qa-clients` и `/api/qa-clients/[id]`;
- история `/api/qa-clients/history`;
- owner-scoped доступ;
- без seed/demo-строк;
- без публикации.

В UI созданы синтетические записи:

| Маркер | Телефон | Статус | Результат |
|---|---|---|---|
| `A1 Маркер 001` | `+7 900 000-00-01` | Активен | сохранился после нового соединения |
| `A2 Маркер 002` | `+7 900 000-00-02` | Лид | сохранился после нового соединения |

Совместимая версия должна была добавить nullable-поле `private_note text NULL`, не пересоздавать таблицу, сохранить ID/owner/телефон/статус/время и добавить поле в форму/API.

Несовместимая версия должна добавить `email text NOT NULL`, выполнить backfill `qa-<id>@example.invalid` для A1/A2 и потребовать email в новых create/update. Старый код без email после этого должен быть структурно несовместим с будущими записями.

### 2.3. Границы доказательства

- Независимая вторая подписанная MAX-сессия не была доступна. Двухпользовательская изоляция подтверждалась тестом, созданным самим агентом, но не независимым hostile-user E2E.
- Логи браузерной консоли редактора и preview не содержали ошибок; воспроизведённые сбои приходили из backend/finalization/state-machine.
- Production и публикация сознательно не затрагивались.
- Некоторые локальные API-тесты требуют PostgreSQL, которого на рабочей машине не было. Это ограничение локального прогона, а не продуктовый pass/fail.
- Реальный delete A4 через UI не отправлялся: компьютерная политика требует отдельного подтверждения прямо перед удалением даже синтетической записи. Create/update/reload проверены live; delete покрыт только agent-authored тестом и потому не считается независимым live proof.
- Кнопка «Скачать» не создала доступный локальный файл, а прямой официальный download endpoint был заблокирован клиентом браузера (`ERR_BLOCKED_BY_CLIENT`). Код свежего generated artifact изучался через transcript/read/edit evidence; полный локальный ZIP этого проекта не получен.
- Production SSH `170.168.72.200:22` не завершил banner exchange за 10 секунд. Серверные журналы проекта и точная live-ревизия в этом прогоне не подтверждены; локальный CI не подменяет эту границу.

## 3. Хронология свежего E2E

| Этап | Наблюдение | Статус |
|---|---|---|
| Создание v1 | Приложение собрано; агент создал `qa_clients` и `qa_client_history`; 2 внутренних теста прошли | PASS с замечаниями |
| Первичный CRUD | A1/A2 созданы через UI | PASS |
| Новое соединение/reload | A1/A2 снова видны | PASS |
| Первый compatible edit | `private_note` реализовывался, затем finalization получил HTTP 502 вместо ожидаемого 401 | FAIL |
| Preview после сбоя | новое соединение показало «Приложение не найдено» | FAIL |
| Retry compatible edit | UI одновременно показал «Изменения готовы» и `НЕ ЗАВЕРШЕНО` из-за step limit | FAIL |
| Первый Continue | `Previous Project Cell ensure needs reconciliation` | FAIL, safe refusal |
| Второй Continue | после длительного прогона появилась v5 current | PARTIAL |
| Проверка v5 | A1/A2 есть, но в форме и карточках нет `private_note` | FAIL |
| История версий | показаны v1, v3, v5; номера неуспешных попыток могли быть зарезервированы | INFO |
| Prepare restore v1 | подготовка началась | PASS до cancel |
| Cancel prepare | UI перешёл в `reconciling` и сообщил о разрыве «во время применения», хотя apply не запускался | FAIL |
| Retry + reload | некоторое время состояние не разрешалось; позднее стало `cancelled` | PARTIAL, слишком медленно/неясно |
| Новый prompt во время active restoration | показано «Генерация уже запущена» | FAIL, неверная классификация |
| Очередь prompt | запрос попал в клиентскую очередь, после reload исчез и не был отправлен | FAIL |
| Повтор после terminal cancel | несовместимая генерация стартовала, за 9м 47с дошла до step limit и финальная проверка не прошла | FAIL с сохранённой частичной работой |
| Continue incompatible edit | повторный прогон 13м 26с дошёл до `final_build`, `runtime_probe`, `snapshot`, `promote`, `complete`; создана v7 current | PASS после ручного Continue |
| Проверка v7 | A1/A2 сохранились с backfill `qa-<id>@example.invalid`; форма требует email; `private_note` появился | PASS |
| CRUD/reload v7 | через UI создан A3 с `a3@example.invalid` и заметкой; после reload видны A1/A2/A3 и их значения | PASS с ограничением второй сессии |
| Prepare restore v7 → v1 | exact-путь вернул `needs_changes`: v1 создаёт `qa_clients` без обязательного `email` | PASS, корректный fail-closed |
| Запуск адаптации v1 | первый клик сохранил durable-запрос, но запуск не подтвердился; идемпотентный повтор запустил генерацию | PARTIAL, безопасно, но требует повторного действия |
| Adaptive v1 | за 17м 41с создана v8 current с историческим UI; один `write_file` был вызван без path/content | PASS с замечаниями генерации |
| Проверка v8 | A1/A2/A3, email и заметка A3 сохранились; через старый UI создан и обновлён A4, update пережил reload | PASS с ограничением delete/two-user |
| Prepare exact v8 → v7 | операция долго оставалась `preparing`, пережила reload, затем вернула `ready` без AI; отчёт увидел 4 клиента и 5 history rows | PASS, но медленно |
| Apply exact v8 → v7 | создана техническая v9 current; отдельный preview переключился на v7-интерфейс | PASS |
| Reload после exact apply | A1/A2/A3/A4, email и `updated-v8` снова видны | PASS |

## 4. Подтверждённые live-дефекты, findings по коду и замечания генерации

### P1 E1. Ложный успех совместимого изменения

Система показала успешное завершение и текущую v5. В live UI и форме A1 отсутствовало поле `private_note`. Значит, build/health/version promotion не доказали пользовательское требование.

Пока не установлено, где именно потерялась правка: в сохранённом source, recovery, snapshot/promotion либо runtime binding. Это необходимо устанавливать по связанным `run_id`, source digest, build digest, candidate/release ID и runtime identity. Сам факт ложного продуктового успеха уже доказан.

**Исправление:** ввести независимый `BehaviorContract` на основе intent. Для этого изменения gate обязан:

1. открыть форму A1;
2. записать уникальную заметку;
3. получить её через прежний API;
4. проверить после reload/restart;
5. проверить, что A1/A2 сохранили ID и прежние поля;
6. проверить отрицательный доступ второй подписанной сессии;
7. связать receipt с реально активированным artifact.

Сообщение «готово» разрешено только после выполнения этого набора.

### P1 E2. После отказа finalization выданный preview оказался недоступен

Первый edit завершился:

```text
MAX_FINALIZATION_FAILED: portable boundary rejection check failed (HTTP 502; expected 401)
```

Новый запрос к выданному preview показал «Приложение не найдено». Проверка правильно отказала на HTTP 502 вместо ожидаемого 401. Подтверждены недоступность проверенного в этот момент endpoint и неясное recovery. Доступность всех прежних preview URL и точная причина сбоя binding/runtime не установлены.

**Исправление:** preview URL нельзя переключать до доказанного runtime binding. При провале новая версия остаётся кандидатом, предыдущий подтверждённый runtime продолжает обслуживаться. UI показывает точную фазу recovery. Router-level probe проверяет внешний preview origin, а не только localhost контейнера.

### P1 R1. Агентная адаптация может менять live DB до успешной финализации

`apps/api/src/omnia_api/services/agent_native.py:317-325` разрешает portable bash с выделенной PostgreSQL. `apps/api/src/omnia_api/services/generation/runtime.py:283-297` отправляет действия в Project Cell. При ошибке финализации `apps/api/src/omnia_api/services/generation/agent_finalization.py:95-113` восстанавливает source-файлы, но из этого не следует откат SQL/DML.

Сценарий: миграция выполнилась, затем provider/build/probe упал. Старый код вернулся, а схема/данные остались новыми.

**Исправление:** агент и repair работают в отдельном изменяемом rehearsal; immutable baseline S0 недоступен им для записи. Отказ кандидата не меняет live schema/source/данные своими эффектами. Законные параллельные записи пользователей сохраняются и учитываются через S1; требование byte-identical ко всей живой БД при работающих пользователях неприменимо.

### P1 R2. Допуск проверяет маршруты, но не поведение

`apps/api/src/omnia_api/services/versioning_capabilities.py:279-305` сравнивает method/path manifest. Аналогичная логика применяется в restoration engine. Она не отличает рабочий `GET` от `GET`, который возвращает `[]`, чужие строки или неверные значения.

**Исправление:** независимые signed two-user probes, per-ID/value/relation assertions, UI create/edit/delete/reload, негативные мутации FV033–FV036. Baseline проверки хранится вне кандидата и не может быть изменён агентом.

### P1 R3. Опасные удаления новых связанных данных допускаются с предупреждением

`blocked_deletes` отражает новые поля и опасные каскадные зависимости, но `delete_warnings()` выдаёт `severity=warning`; само наличие такого риска не блокирует `ready`. Старый `DELETE` может затронуть новые дочерние записи. Отдельно остаётся непроверенным full-object `PATCH` с неизвестными JSON-ключами. При этом известная потеря объявленных JSON keys уже блокируется в `apps/orchestrator/src/omnia_orchestrator/services/restoration_data_contract.py:375-385`; нельзя утверждать, что любая JSON-несовместимость лишь предупреждается.

**Исправление:** блокировать конкретную недоказанную опасную операцию либо переход, которому она необходима. После появления проверенного write adapter на копии данных проверять create/update/delete, omission/null/array semantics, каскады и owner boundaries; общий запрет на проекты с JSON не требуется.

### P1 R4. Crash window и недоказанная очистка кандидата

`apps/orchestrator/src/omnia_orchestrator/services/code_restoration_engine.py:347-351` возвращает существующий `prepared.json`. Результат записывается до выхода из `finally` (`:533-550`). Сбой после записи receipt способен оставить candidate resources; повторный prepare уже вернёт receipt.

`_discard_code` на `:1076-1085` только логирует ошибку удаления `code.tar`; устойчивой cleanup-очереди здесь нет.

**Исправление:** отдельный durable cleanup ledger со статусом, owned resource manifest, retry/backoff и terminal cleanup receipt. Sweeper обрабатывает completed/failed/cancelled; kill после каждого шага не оставляет owned resources и не удаляет чужие.

### P1 R5. `database_backup_ref` не доказывает реальную резервную копию

`apps/api/src/omnia_api/services/max_finalization.py:846-850` создаёт ссылку из digest схемы. Это правдоподобный identifier, но не свидетельство существующего immutable backup данных.

**Исправление:** создавать реальный artifact с checksum, source identity, cutoff/LSN, manifest и restore-smoke. Если резервная копия не требуется, поле нужно переименовать и исключить из доказательств DR.

### P2 E3. Одновременно «готово» и «не завершено»

После retry карточка показала «Готово — правка применена и проверена», а рядом — step-limit и кнопку Continue.

Причина видна в коде: `agent_messages.py:158-169` считает `needs_finalization` достаточным для готового текста, а `agent_pipeline.py:515-530` отдельно публикует incomplete-card, если `done=false` и `stop_reason=max_steps`. Финализация меняет итоговый текст (`agent_finalization.py:114-121`), но исходный `done` не становится единым terminal verdict.

**Исправление:** один authoritative terminal result. `max_steps`, `needs_finalization`, `finalization_complete`, `promotion_complete` и `behavior_passed` сводятся в одну детерминированную state machine. Модельный summary не управляет статусом.

### P2 E4. Cancel prepare описывается как неопределённое apply

`apps/web/src/components/max/MaxRestorationPanel.tsx:43` для любого `reconciling` пишет: «Связь прервалась во время применения». В свежем тесте это сообщение появилось после отмены подготовки, когда apply не запускался.

**Исправление:** хранить intent/phase (`prepare`, `cancel_prepare`, `apply`, `cancel_after_admission`) и показывать точный текст. В UI нужны возраст операции, последняя подтверждённая фаза, следующий retry и safe action.

### P2 E5. Любой `409 conflict` изображается как активная генерация

Точная цепочка:

- `apps/api/src/omnia_api/services/generation_runs.py:415-418` вызывает `assert_no_active_restoration`;
- `apps/api/src/omnia_api/services/restorations.py:73-80` возвращает общий `code="conflict"`;
- `apps/web/src/hooks/usePromptStream.ts:942-972` трактует любой такой conflict как duplicate generation, ставит `streamingRef=true` и показывает «Генерация уже запущена»;
- если `getLatestGeneration()` возвращает null, состояние и сообщение не исправляются.

**Последствие:** следующий prompt попал в локальную очередь как будто идёт генерация; после reload очередь исчезла без отправки.

**Исправление:** типизированные коды `generation_active`, `restoration_active`, `cell_reconciling`, `source_changed`, `idempotency_conflict`. `streamingRef=true` допустим только после подтверждённого active run. Queue должна быть server-durable либо UI обязан честно сообщать, что она локальная и не переживает reload.

### P2 E6. Terminal step count меняется после reload

Во время активного run естественный рост счётчика сам по себе не дефект. Отдельно наблюдалось уменьшение числа уже после terminal/reload. Сырые наблюдения для тех же карточек:

- v1: наблюдались 306/330 шагов, затем 88;
- failed compatible edit: 101/111, затем 40;
- retry: 121/137, затем 43;
- Continue compatible: 45/57, затем 38;
- failed incompatible: 57, затем 65/81/97/105/121;
- successful Continue incompatible: 26/61/72, затем 82/102/122, а после reload — 46;
- adaptive run: 36, после reload — 20.

Эти снимки смешивают активный рост и terminal-сравнения, поэтому не доказывают ошибку для каждого числа. Доказанный UI-риск — отсутствие стабильного immutable summary завершённой попытки.

**Исправление:** canonical event ID + monotonic sequence, дедупликация replay, immutable attempt summary. После terminal UI должен показывать стабильный `unique persisted steps`, а активный счётчик — явно обозначаться как незавершённый.

### P2 E7. Повторные правки сопровождаются высоким расходом шагов; полезность повторов не измеряется

В incompatible run последовательно повторялись правки обоих API-файлов и не менее девяти правок `src/app/page.tsx`. Продолжение снова сначала прочитало тот же набор через `/workspace/...`, затем через относительные пути, дважды переписало оба API-файла и только после второго длинного прогона дошло до финализации. В compatible run также были повторные полные записи страницы.

Повторные правки и достижение step limit наблюдались. Без сравнения diff/diagnostics нельзя доказать, что каждая повторная запись была избыточной или именно она вызвала исчерпание бюджета.

**Исправление:** change plan до мутаций, AST/patch-based edit, compile/typecheck после законченного логического пакета. Loop detector применяется к семантически одинаковым действиям или ошибкам без прогресса; последовательные необходимые изменения одного файла не запрещаются.

### P2 E8. Неверный порядок bootstrap

Первичная генерация запустила `pnpm db:push` до установки зависимостей и получила:

```text
drizzle-kit: not found
Local package.json exists, but node_modules missing, did you mean to install?
```

Затем установила 97 пакетов и повторила работу.

**Исправление:** manifest-driven prepare: install/sync dependencies → verify toolchain → migration preflight → migration. Известная ошибка отсутствующего `node_modules` не должна расходовать модельный шаг.

### P2 E9. Абсолютные пути повторяются после уже известного отказа

Retry снова пытался использовать `/workspace/...` вместо относительных путей. Это даёт `unsafe path` и расходует шаги.

**Исправление:** tool schema должна выдавать canonical workspace-relative path; ошибка нормализуется в capability feedback; повтор того же invalid path в одном run блокируется локально до обращения к модели.

### P2 E10. Wizard добавляет противоречащее требование

Пользовательский brief содержал «Без демоданных». Собранный prompt всё равно добавил фразу «реальные русские тексты и демонстрационные данные». Фактически приложение открылось пустым, но prompt противоречив.

**Исправление:** отрицательные ограничения имеют приоритет. Prompt composer должен удалять boilerplate, конфликтующий с явным `без demo/seed/заглушек`; добавить unit-тест на conflict resolution.

### P3 E11. Агент отправил некорректный tool-call, который исполнитель безопасно отклонил

В adaptive run один `write_file` не содержал `path`/`content` и был отклонён сообщением `write_file needs path + content`. Проверка обязательных аргументов уже есть до записи в `project_cell_executor.py:989-994`. Повреждение source этим вызовом не установлено; весь run затем успешно завершился.

**Исправление:** согласовать model-facing schema с требованиями исполнителя; возвращать структурированный validation error и разрешать ограниченное исправление аргументов. Повторяющиеся одинаковые ошибки учитывать loop detector. Ошибочные вызовы сохранять в диагностике отдельно от успешно выполненных изменений. Не завершать весь run автоматически после второго вызова без общего budget/repair policy.

### P3 E12. Сгенерированный мобильный UI формально помещается, но ухудшает управление

Проверка выполнялась в мобильном режиме с заданным размером 390×844; при измерении страница сообщила `innerWidth=319` и `scrollWidth=304`. Общего горизонтального overflow при фактической измеренной ширине не было. Строка фильтров получила постоянно видимый горизонтальный scrollbar. Видимая высота filter chips — 32 px, кнопок «Изменить» и удаления — 40 px; наличие расширенной невидимой hit-area отдельно не проверено. Плавающая кнопка добавления при прокрутке визуально перекрывает правую часть нижней карточки. Точная проверка на 390 CSS px требует повторного замера viewport/zoom.

**Исправление:** обеспечить целевую интерактивную область 44×44 CSS px и отсутствие перекрытия доступных действий FAB. Фильтры могут переноситься или оставаться доступной горизонтально прокручиваемой строкой; скрытие scrollbar само по себе не критерий качества. Проверять на фактических 390 CSS px длинные email, три и более карточки, открытые формы и нижнюю часть списка.

### P2 R6. ABA-гонка reconciliation lease

`apps/api/src/omnia_api/services/restoration_reconciliation.py:63-68` ставит только `lease_until`, без owner token. Поздний worker безусловно очищает lease на `:96-108`.

**Исправление:** UUID lease token/monotonic generation; renew/release/update через CAS `WHERE operation_id AND lease_token`; тест с A timeout, B claim и поздним A.

### P2 R7. Один повреждённый journal способен сорвать общий recovery sweep

В `apps/orchestrator/src/omnia_orchestrator/services/code_restorations.py:464-494` один ошибочный UUID/JSON прерывает sweep до следующих записей. Обход recovery должен обрабатывать повреждение одной записи локально. Нужны quarantine + alert с сохранением evidence; остальные операции продолжают обработку.

### P2 R8. Dump целиком удерживается в памяти и ограничен 64 MiB

`code_restoration_engine.py:620-621` ограничивает вывод 64 MiB и задаёт socket I/O timeout через `machine_remaining_seconds(65)`. Это не доказанный общий 65-секундный дедлайн всего dump: timeout применяется к socket operations. Превышение размера вызывает отказ, а не успешную молчаливую обрезку.

**Исправление:** streaming custom-format dump/restore, checksum, disk reservation, stage-specific timeout, cancellation. Тесты 64 MiB−1/64 MiB+1, 100k/1m rows, ENOSPC и truncated archive.

### P2 R9. Evidence index неполон

Текущий `docs/operations/versioning-v4-evidence-index.json` содержит 30 FV: 20 `passed`, 1 `partial`, 9 `not_run`; остальные 90 не внесены. Часть pass-ссылок указывает на наличие тестового файла, а не на актуальный run artifact. Эти статусы не являются приёмкой всех сценариев на текущей live-ревизии.

**Исправление:** для каждого FV хранить SHA, среду, команду/job, run ID, result, artifact checksum, ограничения и reviewer. `not_run`, обязательный skip/xfail и исторический pass не превращаются в текущий pass.

### P3 R10. Длинная цепочка может потерять признак текущей версии

Ancestor walk в `apps/api/src/omnia_api/routers/project_versions.py:59` ограничен глубиной 256. После 257+ технических descendants UI может не определить текущую user-version.

**Исправление:** durable effective `ProjectVersion.id` либо cycle-aware ancestry без нормального лимита. Тесты 257+ descendants и отдельный corrupt cycle.

### P3 R11. Windows-тест capability fixture сейчас красный

На Windows `test_versioning_customer_visits_scenario.py::test_durable_operation_keeps_format_2_fields` не находит `/api/visits`: fixture создаёт ключи через `str(path.relative_to(root))` с `\`, а route regex ожидает `/`. На Linux CI этот путь мог быть зелёным.

Это не доказанный production-дефект Linux orchestrator, но он скрывает регрессию в основной локальной среде пользователя.

**Исправление:** нормализовать fixture/runtime path через `.as_posix()` перед route parsing и добавить Windows/Linux corpus test.

### P2 R12. Готовый ProofBundle может обойти обычную ветку transport-security checks

`apps/api/src/omnia_api/services/release_proof.py:32-35` при готовом `ProofBundle` возвращает verdict до обычной ветки transport-security checks. Положительный runtime probe сам по себе не заменяет эти проверки.

**Исправление:** требуемые security checks должны входить в криптографически/структурно связанный проверяемый proof либо исполняться независимо при любом пути выдачи verdict. Добавить отрицательный тест с положительным runtime probe и проваленным transport-security check.

### P2 R13. Документация data evolution может расходиться с фактическими migrations/catalog

В логах ранее созданного QA-проекта обнаружено устаревание `.omnia/data-evolution.md` относительно фактических migrations/catalog. На свежем `qa_clients` это отдельно не воспроизводилось.

**Исправление:** строить data-evolution manifest из применённых migration receipts и фактического catalog snapshot; сравнивать digests перед finalization и откатом. Расхождение делает evidence неполным и требует regeneration/review.

## 5. Что уже работает и должно сохраниться

- v1 генерируется с реальной PostgreSQL-схемой и реализацией owner-filter; независимая проверка второй подписанной сессией ещё не выполнена.
- A1/A2 пережили новое browser connection.
- v7 сохранила A1/A2 и добавила обязательный email; A3 пережил reload.
- adaptive v8 сохранила A1/A2/A3 и позволила через исторический UI создать и обновить A4.
- exact apply v8 → v7 создал техническую v9; четыре строки и проверенные значения пережили переключение и reload.
- required `email` корректно блокирует точный возврат к старому create без email.
- после первоначально неопределённой отправки адаптации идемпотентный повтор запустил операцию; автоматическое завершение dispatch без повторного клика этим не доказано.
- finalization не принял 502 вместо ожидаемого 401.
- неопределённый previous ensure не был молча перезаписан новым effect.
- direct restore использует отдельную копию PostgreSQL и activation journal.
- stale source/head/fence guards и идемпотентные operation IDs уже существуют.
- unsupported mounts/stores в direct restore fail closed.
- публикация отделена от восстановления редактора.

Исправления не должны убирать эти предохранители ради более быстрого зелёного UI.

## 6. Матрица сценариев

| Сценарий | Свежий live | Код/тесты | Итог |
|---|---|---|---|
| Новое приложение, пустая БД | проверено | есть starter/runtime checks | PASS |
| CRUD и reload v1 | A1/A2 проверены | agent-owned test | PASS с ограничением второй сессии |
| Compatible nullable add | поле отсутствует после success | structural gate зелёный недостаточно | FAIL |
| Ошибка finalization после edit | 502 вместо 401; preview недоступен | source rollback есть | FAIL recovery |
| Step-limit + Continue | противоречивый статус; первый Continue blocked | resumable card есть | FAIL UX/state |
| Prepare compatible restore | ранний prepare отменён; поздний v8 → v7 достиг `ready` без AI после reload | direct candidate path | PASS позднего prepare; длительность требует улучшения |
| Cancel до apply | позднее reached cancelled | background reconciler есть | PARTIAL, неверный текст/задержка |
| Prompt при active restore | показана активная генерация | generic conflict path подтверждён | FAIL |
| Локальная очередь после conflict | исчезла после reload | server receipt отсутствует | FAIL |
| Incompatible required field | v7 создана только после failed run + Continue; A1/A2 backfill и A3 CRUD/reload проверены | structural blocker есть | PASS после дорогого recovery; UX/state FAIL |
| Exact restore v7 → v1 | live report указал: old create не пишет обязательный `qa_clients.email` | structural blocker | PASS, корректный `needs_changes` |
| Adaptive restore incompatible code | v8 создана; A1/A2/A3 сохранены; A4 create/update/reload через исторический UI прошли | агент имеет live DB write path | live positive, архитектурный release blocker остаётся |
| Exact apply compatible versions | v8 → v7: ready без AI, apply создал v9, reload сохранил 4 строки и значения | direct candidate path есть | PASS fresh live |
| Новые записи через адаптированный исторический UI | A4 create/update/reload в v8 прошли; `updated-v8` сохранился после v9 | положительный сценарий | PASS в проверенном сценарии |
| Необновлённый старый WebView/payload | независимый stale-client запрос не отправлялся | отдельный protocol/write-adapter контур | NOT ACCEPTED |
| DELETE с новой связью | не проверено live | warning path | NOT ACCEPTED |
| JSON hidden keys | не проверено live | warning/partial analysis | NOT ACCEPTED |
| Два подписанных пользователя | нет второй сессии | self-authored tests | PARTIAL |
| Lost response/crash apply | не fault-injected live | durable intent tests | PARTIAL |
| Kill после prepared receipt | не проверено live | crash window найден | FAIL BY REVIEW |
| Large DB >64 MiB | не проверено live | hard limit найден | FAIL BY REVIEW |
| Production roundtrip | не выполнялся | digest guard, нет полного S1 | NOT ACCEPTED |
| Old WebView/stale writer | не выполнялся | protocol/writer barrier не завершены | NOT ACCEPTED |
| Files/Redis/SQLite/managed MAX | не выполнялся | adapters incomplete | NOT ACCEPTED |
| External payment/message retry | не выполнялся | once-only ledger incomplete | NOT ACCEPTED |
| 257+ version descendants | не выполнялся | depth limit 256 | FAIL BY REVIEW |

## 7. План исправлений G0–G6

### G0. Зафиксировать состояние и ограничить опасные эффекты

1. Собрать correlated receipt свежего проекта: generation/restoration/cell operation IDs, source/build/runtime digest, version IDs, lease/epoch, preview binding.
2. Зафиксировать A1–A4, проверенные `email`/`private_note` и актуальный schema digest до любых исправлений.
3. Запретить agent/adaptive SQL на live volume feature flag до AV10 isolated candidate.
4. Ограничение применяется к admission новых небезопасных эффектов. Уже начатые reconcile/recovery/cleanup продолжаются до подтверждённого безопасного исхода.
5. Не снимать lease вручную по timeout и не удалять volumes ради разблокировки.
6. Проверить, какой source обслуживала v5 и почему отсутствовал `private_note`.

**Выход:** любой обслуживаемый artifact однозначно связан с run и проверенным behavior receipt; live DB не доступна агенту.

### G1. Исправить lifecycle, конфликты и recovery

1. Ввести типизированные conflict codes и operation reference.
2. Исправить `usePromptStream`: подтверждать active generation до `streamingRef=true`.
3. Сделать queue durable или запретить обещание сохранности после reload.
4. Свести agent/finalization/promotion в один terminal verdict.
5. Сделать phase-aware cancel/reconcile UI.
6. Добавить lease token + CAS.
7. Изолировать corrupt journals.
8. Добавить durable cleanup ledger.

**Выход:** при доступных зависимостях цепочка автоматически достигает честного terminal-state; во время недоступности остаётся наблюдаемое `reconciling`/`recovery_required` с причиной и автоматическим продолжением после восстановления.

### G2. Доказать compatible edit и безопасную адаптацию

1. Immutable S0/source baseline.
2. Отдельный Project Cell candidate с отдельной БД.
3. Intent-derived BehaviorContract вне agent workspace.
4. Реальные backup artifacts или честная смена семантики `database_backup_ref`.
5. Проверка external preview binding.
6. Forced source repair test.
7. Signed two-user isolation.

**Выход:** `private_note` работает через UI/API и reload, A1–A4 и проверенные `email`/`private_note` сохранены, ошибка кандидата не меняет live DB; обязательные отрицательные behavioral-примеры не получают success.

### G3. Безопасная эволюция данных

1. MigrationPlan с preconditions/postconditions и hash.
2. Durable ledger каждого SQL-шага.
3. Writer barrier для HTTP, webhook, worker, scheduler и старых соединений.
4. Актуальный S1 после barrier.
5. После S1 повторить проверки, зависящие от данных. Для перехода с переносом данных построить чистую activation target из S1; рабочую копию агента с тестовыми изменениями не применять.
6. Привязать permit к project/environment, исходному release, source/build/schema/binding/intent digests и fencing epoch; существенное изменение инвалидирует permit.
7. До открытия writers подтвердить runtime identity и маршрутизацию; запись разрешить только одному активному поколению.
8. После первой новой записи не восстанавливать старый dump. Recovery использует актуальные данные: совместимый fallback либо roll-forward; неопределённый исход сохраняет ресурсы и видимый `recovery_required`.
9. Для empty-scope пути повторно доказать пустоту непосредственно перед применением; появившиеся записи запрещают прежний empty permit.
10. Write adapters для create/update/delete и hidden fields.
11. Semantic policy registry для backfill/rename/split/merge.
12. Phase-aware recovery после каждого fault point.

**Выход:** compatible/incompatible различаются фактическими проверками; старый код не стирает новые значения и не создаёт неверные записи.

### G4. Production, stores и длинные цепочки

1. Отдельный production candidate/data permit.
2. Files/SQLite/Redis/queue/managed MAX adapters.
3. Protocol version для stale clients.
4. Outbox/idempotency ledger для внешних эффектов.
5. Long-chain identity без depth=256.
6. Streaming snapshot/restore и capacity admission.

**Выход:** draft и production не смешиваются; stale writer блокируется; внешний effect не повторяется; большие БД не делают rollback недоступным.

### G5. Улучшить обычную генерацию

1. Согласовать explicit negative requirements с wizard boilerplate.
2. Install dependencies до `db:push`.
3. Workspace-relative tool paths.
4. План изменений и patch-based edits вместо многократной полной перезаписи.
5. Loop detector и budget по полезным изменениям.
6. Независимые product tests вместо agent-authored no-op task.
7. Materialized business tables вместо ограниченного event fold.
8. Decimal для денег, пагинация, строгий QR/entity JSON gate.

Дополнительные задачи из аудита Sol других кодовых путей/артефактов: слабый portable test gate; event-fold с `limit=500` в найденном generated artifact; QR/entity JSON xfail; денежная арифметика и пагинация. Они не были все воспроизведены в свежем приложении `qa_clients`.

**Выход:** простой edit проходит коротким путём; повторные tool errors не съедают бюджет; generated app доказывает нужное поведение.

### G6. Полная приёмка и поставка

1. Запустить FV001–FV120 на точном SHA.
2. Для каждого FV сохранить machine-readable receipt.
3. Fault injection на SQL-effect, provider error, 502, lost reply, kill, ENOSPC и stale client.
4. Независимый review результатов и artifact identity.
5. Controlled rollout с rollback rehearsal.
6. Retention/DR и cleanup reconciliation.

**Выход:** каждый обязательный FV имеет актуальный pass; P1, `not_run`, обязательный skip/xfail блокируют акт готовности.

## 8. Обязательные новые регрессии

| ID | Сценарий | Критерий |
|---|---|---|
| NEW-01 | v1 → nullable `private_note` | заметка пишется/читается после reload; A1/A2 неизменны |
| NEW-02 | boundary probe даёт 502 вместо 401 | success запрещён; предыдущий preview доступен |
| NEW-03 | max_steps после частичной правки | нет одновременно ready и incomplete |
| NEW-04 | Continue при previous ensure | присоединяется к тому же intent, без второго owner/effect |
| NEW-05 | prepare → cancel → закрыть страницу | terminal cancelled достигается worker-ом |
| NEW-06 | prompt при active restoration | показано восстановление, не генерация |
| NEW-07 | generic conflict без generation | нет fake streaming/queue |
| NEW-08 | queued prompt → reload | prompt либо server-durable, либо явно не принят |
| NEW-09 | повтор событий после reload | step count и IDs неизменны |
| NEW-10 | required email NOT NULL | exact restore старого create получает точный blocker |
| NEW-11 | adaptive email version | агент работает только на candidate copy |
| NEW-12 | fault после migration, до build | live schema/rows/source неизменны |
| NEW-13 | fault после build, до promote | live runtime остаётся прежним |
| NEW-14 | old PATCH с новым nullable field | hidden field сохраняется |
| NEW-15 | old DELETE с новой CASCADE relation | delete запрещён или relation сохраняется по policy |
| NEW-16 | JSON new nested keys | omission/null/array semantics не стирают новые ключи |
| NEW-17 | две signed sessions | каждая сессия видит только разрешённые ей строки; get/update/delete чужого ID запрещены и не меняют данные; операции со своими строками работают |
| NEW-18 | A lease expired, B claimed, A returned | A не очищает lease B |
| NEW-19 | corrupt journal между двумя valid | valid operations завершаются |
| NEW-20 | crash после `prepared.json`, до cleanup | sweeper освобождает только owned resources |
| NEW-21 | archive unlink error | durable retry удаляет archive после restart |
| NEW-22 | 64 MiB−1/64 MiB+1 dump | streaming path работает либо даёт точный capacity blocker |
| NEW-23 | preview binding mismatch | version не становится current |
| NEW-24 | 257 descendants | current version определяется корректно |
| NEW-25 | explicit «без демоданных» | prompt composer не добавляет demo requirement |
| NEW-26 | missing node_modules | bootstrap сам устанавливает зависимости до DB task |
| NEW-27 | repeated unsafe `/workspace` path | второй вызов блокируется локально |
| NEW-28 | six identical page edits | loop detector останавливает повтор и сохраняет budget |
| NEW-29 | payment/webhook lost reply | provider idempotency или сверка результата исключает повтор в поддерживаемом контракте; неопределённый исход не вызывает слепую повторную отправку |
| NEW-30 | вся свежая цепочка | v1 → failed edit → retry → Continue → cancel → incompatible v7 → `needs_changes` v1 → adaptive v8 → exact v9 → reload |
| NEW-31 | фактический viewport 390 CSS px, 3+ карточки | нет общего overflow; фильтры доступны; hit-area соответствует целевому размеру; FAB не перекрывает действия |
| NEW-32 | `write_file` без path/content | model-facing schema совпадает с executor contract; структурированная ошибка допускает bounded repair без повреждения source |

## 9. Привязка к плану v4

| Новое доказательство | Карточки v4 |
|---|---|
| false success v5 | AV06.2, AV13, AV22, AV26 |
| preview unavailable after 502 | AV18, AV19, AV22, AV25 |
| ready + incomplete | AV19, AV22, AV26 |
| cancel/reconciling | AV19.1, AV18, AV22 |
| generic conflict misclassification | AV19, AV22 |
| prompt queue loss | AV19, AV22, AV25 |
| live DB agent risk | AV08, AV10, AV11, AV14, AV16 |
| destructive future writes | AV12, AV13, AV17 |
| cleanup crash window | AV23.1, AV25, AV29 |
| fake backup ref | AV08, AV18, AV21, AV29 |
| 64 MiB limit / bounded socket I/O | AV25, AV27 |
| unstable step counts | AV22, AV26, AV29 |
| repeated edits/tool misuse | AV20, AV25, AV26 |

Свежая отмена позднее достигла `cancelled`, поэтому бессрочное зависание не доказано. Она выявила неверную фазу в UI, задержку в пределах наблюдения и неправильную классификацию следующего prompt. Для приёмки AV19.1 отдельно нужны длительность, server-side terminal receipt и запуск без клиентских GET; текущий прогон этого полного доказательства не даёт. AV06 остаётся незакрытым: ложный success v5 показал недостаточность проверки маршрутов, хотя последующие v7/v8/v9 прошли положительные пользовательские сценарии.

## 10. Порядок конкретных изменений в коде

### Пакет A: статусы и конфликтные коды

- `apps/api/src/omnia_api/services/restorations.py`
- `apps/api/src/omnia_api/services/generation_runs.py`
- `apps/web/src/hooks/usePromptStream.ts`
- `apps/web/src/lib/use-max-restoration.ts`
- `apps/web/src/components/max/MaxRestorationPanel.tsx`
- тесты prompt-stream/restoration UI/API.

### Пакет B: единый terminal verdict

- `apps/api/src/omnia_api/services/generation/agent_messages.py`
- `agent_pipeline.py`
- `agent_finalization.py`
- `agent_recovery.py`
- generation run/status schemas и tests.

### Пакет C: isolation и receipts

- Project Cell candidate provisioning;
- migration/backup artifact services;
- `max_finalization.py`;
- `code_restoration_engine.py`;
- signed BehaviorContract runner.

### Пакет D: reconciliation и cleanup

- `restoration_reconciliation.py`;
- restoration model/migration для lease token;
- durable cleanup queue;
- corrupt journal quarantine;
- kill/restart tests.

### Пакет E: генератор

- prompt composer;
- bootstrap/task planner;
- tool path normalization;
- patch/loop detector;
- independent generated-app acceptance suite.

Пакеты A/B устраняют противоречивые статусы и объяснения конфликтов. E1 закрывается только после пакета C и отрицательного теста: отсутствующее требуемое поведение запрещает success. Пакет C блокирует безопасное включение adaptive rollback на реальных данных.

## 11. Проверки репозитория во время аудита

На исходной ревизии `bb783fd4` и после обновления до `58ab7392` выполнены целевые проверки:

- API pure-contract: 37 passed;
- Web restoration/generation UI: 58 passed при `NODE_OPTIONS=--no-experimental-webstorage`;
- Orchestrator основной выбранный набор без Windows-specific scenario: 129 passed;
- расширенный orchestrator-набор: 129 passed, 4 skipped, 1 failed;
- единственный отдельный failure: Windows path normalization в `test_versioning_customer_visits_scenario.py`;
- DB-backed API suite локально не запущен из-за отсутствующего PostgreSQL на `127.0.0.1:5432`;
- первый Web-run на Node 25 дал setup errors `localStorage.clear is not a function`; повтор с отключённым experimental Node webstorage прошёл.
- [GitHub Actions run 35436912804](https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI/actions/runs/35436912804) для точного `58ab73926b786925e3c81e3c84b36bc6954a6c6d` завершился `success`: `api-release-gate`, `orchestrator-release-gate`, `web`, `py-syntax`, `image-build`, `workflow-lint`, `gateway-tests`.
- Production server-side evidence не снято: SSH завершился `Connection timed out during banner exchange`; это operational blocker для сверки live SHA и backend-журналов, а не доказательство дефекта конкретного кода.

Эти зелёные тесты не отменяют live-дефекты: текущие unit/integration tests не воспроизводят весь свежий сценарий.

## 12. Критерий окончательного закрытия

Работа считается законченной, когда одновременно выполнено следующее:

1. Все P1 устранены и повторены свежим E2E на новом проекте.
2. Compatible и incompatible цепочки проходят на пустой и заполненной БД.
3. Каждый fault point сохраняет S0/S1 согласно фазе.
4. Реальная signed two-user проверка доказывает row isolation.
5. Старые формы не стирают новые значения/связи/JSON и не создают семантически ложные данные.
6. Cancel/recovery/cleanup завершаются без открытой страницы.
7. Draft, preview и production имеют доказанную identity.
8. FV001–FV120 имеют актуальные artifact-bound receipts на точном SHA.
9. Независимый reviewer не находит P0/P1; P2 либо исправлены, либо имеют явное решение владельца.
10. Controlled rollout и rollback rehearsal подтверждены health и реальным пользовательским путём.

До этого статус версионирования: **не готово к безопасному общему выпуску; доступно только как ограниченный QA-контур с сохранёнными предохранителями**.
