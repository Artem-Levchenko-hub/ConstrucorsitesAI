# Omnia: матрица приёмки защиты данных

Дата: 9 сентября 2026 года. Это спецификация будущих проверок. Статус всех проверок ниже — **не выполнено в рамках подготовки плана**.

Связанные документы: [архитектура](../specs/2026-09-09-data-protection-design.md), [задачи разработки](../plans/2026-09-09-data-protection-implementation.md).

## 1. Правила доказательства

PASS означает наблюдаемый результат на реальном компоненте. Ссылка на код, успешный unit mock, наличие флага, checksum или HTTP 200 не заменяют проверку нужного свойства.

FAIL и UNKNOWN блокируют соответствующий этап. Пропущенный тест не становится PASS. Если проверка разрушительная, она выполняется только в отдельной лаборатории или на изолированной восстановленной копии.

Существующие данные клиентов не отправляются в отчёты, CI artifacts, внешнюю аналитику, LLM или скриншоты. Операционные проверки production используют разрешённые тестовые записи и редактированные metadata.

Каждый run evidence содержит: check_id, время, test revision, tool/image versions, environment identity, project scope, code/core/schema/policy digest, epoch, expected/actual outcome, digest артефакта и review status. Не содержит credential values или персональные строки.

## 2. Фикстуры

Два независимых бизнеса A/B. У каждого владелец, сотрудник и два клиента. Сотрудник A не является сотрудником B. После отзыва роли есть отдельная проверка уже открытой сессии.

Бизнес-сущности:

- Салон: услуга, слот, запись клиента, комментарий, вложение.
- Магазин: публичный товар, приватный заказ, адрес доставки, позиции заказа.
- Сервис: заявка, назначенный сотрудник, история статусов, файл клиента.

В каждом случае проверить разрешённый путь и запрещённый соседний путь. Таблица bookings с owner_id не заменяет все три сценария: нужны shared catalog, связи, staff scope и фоновые действия.

Fixtures содержат только синтетические значения. Постоянный canary marker позволяет обнаружить попадание значения в plaintext export/логи/prompts; отсутствие marker само по себе не доказывает криптографическую безопасность.

## 3. Сопоставление требований и задач

| Требование | Задачи | Доказательства |
|---|---|---|
| R01: предприниматель не настраивает security | T01/T11/T12/T14 | UX01–UX06, REL01–REL09 |
| R02: все места хранения покрыты | T01/T03/T09 | INV01–INV06, STO01–STO10 |
| R03: ключи отдельно и восстанавливаются | T02 | KEY01–KEY13 |
| R04: защищённая передача | T04 | NET01–NET12 |
| R05: другой бизнес/пользователь не получает данные | T05/T06 | ACC01–ACC20 |
| R06: runtime/agent не имеют лишних полномочий | T05/T08 | DB01–DB13, AI01–AI09 |
| R07: файлы/логи/кеши защищены | T07 | AUX01–AUX12 |
| R08: внешний backup покрывает проект | T09 | BAK01–BAK14 |
| R09: восстановление доказано | T10 | REC01–REC15 |
| R10: обновления не теряют данные | T08/T13/T14 | MIG01–MIG13 |
| R11: gate нельзя обойти | T11 | GATE01–GATE11 |
| R12: эксплуатация реагирует безопасно | T12/T14 | OPS01–OPS10 |

## 4. Inventory и версии

| ID | Действие | Приёмка |
|---|---|---|
| INV01 | Сопоставить все active/published проекты с DB/storage | Нет проекта без объяснённого storage mapping |
| INV02 | Проверить legacy/draft/public/candidate/recovery/external пути | Каждый имеет профиль или явно закрыт для защищённой публикации |
| INV03 | Сравнить фактический PG version/image и lockfile | Нет использования предполагаемой major или mutable latest |
| INV04 | Проверить logs/Redis/uploads/checkpoints/controller state | Каждое место сохранения включено в coverage |
| INV05 | Запустить inventory с редактированием | В output нет secret/DSN/private endpoint/customer rows |
| INV06 | Сбой получения provider/storage evidence | Статус UNKNOWN, а не «зашифровано» |

## 5. Носители

| ID | Действие | Приёмка |
|---|---|---|
| STO01 | Создать новый проект | Все DB/files/artifact writable paths находятся на подтверждённом encrypted backing |
| STO02 | Проверить WAL/temp/tablespaces | Нет выхода на незашифрованный носитель |
| STO03 | Проверить Docker overlay, logs, exports, swap/core dumps | Путь зашифрован либо чувствительное сохранение исключено |
| STO04 | Снять ожидаемый mount перед стартом в лаборатории | PostgreSQL не стартует; обычный fallback directory не появляется |
| STO05 | Подменить device identity при том же пути | Запуск блокируется |
| STO06 | Перезагрузить host с доступными ключами | Прежняя DB и записи доступны после проверенного unlock |
| STO07 | Перезагрузить host без ключа | Нет пустой новой DB; операция сообщает key/storage unavailable |
| STO08 | Подключить копию offline диска без ключа | Данные недоступны; есть evidence crypto mapping |
| STO09 | Создать checkpoint/support export | Внешний артефакт зашифрован, local staging защищён |
| STO10 | Проверить оставшиеся старые disks/snapshots | Утилизация/retention подтверждены, либо coverage ещё не 100% |

## 6. Ключи

| ID | Действие | Приёмка |
|---|---|---|
| KEY01 | Прочитать encrypted credential разрешённой identity | Получено ожидаемое синтетическое значение |
| KEY02 | Использовать identity другого проекта | Отказ |
| KEY03 | Использовать draft identity для production | Отказ |
| KEY04 | Подменить purpose/key reference | Отказ |
| KEY05 | Отключить KMS при provisioning | Нет plaintext fallback |
| KEY06 | Повернуть wrapping key и перечитать старые данные | Старые и новые records доступны уполномоченной identity |
| KEY07 | Прервать rewrap и перезапустить | Идемпотентное продолжение; ни одна версия не потеряна |
| KEY08 | Восстановить старый backup после rotation | Нужная версия ключа доступна; recovery проходит |
| KEY09 | Потерять primary key service в лаборатории | Recovery сервиса ключей из независимого контура проходит |
| KEY10 | Отозвать workload/admin access | Новые unwrap запрещены; incident plan учитывает уже открытый диск/пулы |
| KEY11 | Production backup → recovery с действующим RecoveryGrant | Только указанные source/backup/key и target разрешены |
| KEY12 | Подменить/просрочить grant, изменить operation/target epoch | Отказ; обычный runtime не может использовать grant |
| KEY13 | Повторить ту же recovery operation после сбоя | Идемпотентное продолжение; reuse для другой операции запрещён |

## 7. Сеть и TLS

| ID | Действие | Приёмка |
|---|---|---|
| NET01 | Внешний запрос с данными | HTTPS; приватные данные не передаются по HTTP |
| NET02 | Нормальное Data API → PG соединение | Сертификат проверен; сервер подтверждает SSL |
| NET03 | Неверный CA | Подключение отклонено |
| NET04 | Неверный SAN/hostname | Подключение отклонено |
| NET05 | Просроченный сертификат | Подключение отклонено, ошибка редактирована |
| NET06 | ssl disabled/plaintext DB клиент | Отклонён HBA и клиентской policy |
| NET07 | Ротация сертификата без отключения проверки | Новые connections работают с новой цепочкой; нет rejectUnauthorized:false |
| NET08 | Доступ из продукта/другой Cell к DB/admin/metadata | Отказ по network/identity boundary |
| NET09 | Restore импортирует конфигурацию trust | До открытия сервиса её заменяет trusted config; admin без/с неверным паролем запрещён |
| NET10 | Проверить все network_edges из inventory | Нет непокрытого внутреннего HTTP/TCP с данными; IPC отмечен отдельно |
| NET11 | Wrong peer/CA/plaintext на gateway→core/product и API→orchestrator | Ошибка до передачи приватных данных, без fallback |
| NET12 | Wrong peer/CA/plaintext на Redis/MinIO/backup/KMS/telemetry | Отказ; разрешённый локальный канал проверен по правам/identity |

## 8. Пользователи, роли и Data API

| ID | Действие | Приёмка |
|---|---|---|
| ACC01 | Клиент создаёт запись | Запись привязана к проверенному actor, не body.owner_id |
| ACC02 | Клиент A читает запись B | 404; содержимое B не возвращено |
| ACC03 | Клиент A меняет/удаляет запись B | Отказ; DB B не изменена |
| ACC04 | Клиент переносит owner_id на другого | Отказ |
| ACC05 | Сотрудник читает разрешённые записи своего бизнеса | Успех в границах роли |
| ACC06 | Сотрудник A читает бизнес B | Отказ |
| ACC07 | Владелец A управляет бизнесом B | Отказ |
| ACC08 | Посетитель открывает публичный каталог | Разрешены только public fields, без customer PII |
| ACC09 | Пользователь присылает role=admin/x-omnia-user | Самоназначение не влияет на trusted identity |
| ACC10 | Поддельная/истёкшая/чужая подпись | Отказ |
| ACC11 | Токен другого epoch/environment/purpose/audience | Отказ |
| ACC12 | Отозвать роль сотрудника с открытой сессией | Следующие действия запрещены в пределах заявленной revoke latency |
| ACC13 | 1000 перемежающихся A/B запросов через pool | Нулевая утечка; transaction context не переносится |
| ACC14 | Timeout/rollback/reconnect между A и B | Контекст следующего клиента чистый |
| ACC15 | POST повторяется с idempotency key | Ровно одна запись; изменённый payload с тем же ключом даёт conflict |
| ACC16 | PATCH со старой record version | 409; чужое/новое изменение сохранено |
| ACC17 | Webhook/job выполняет разрешённую операцию | Scope/purpose/idempotency проверены; нет общего admin |
| ACC18 | Raw SQL/неизвестное поле/неразрешённый filter/большой batch | Отказ до опасного DB действия |
| ACC19 | Старые contract revision/writer epoch для CREATE/PATCH/DELETE/batch | Проверенный адаптер либо 409; не применяется новая разрушительная семантика |
| ACC20 | Клиент подставляет новый revision для расширения прав | Текущая авторизация не расширяется; DELETE/batch требуют preconditions |

Дополнительные browser-проверки: CSRF, session cookies, Origin, CORS, logout/back, private cache, ограничения pagination и error messages без SQL/PII.

## 9. PostgreSQL и контейнерные полномочия

| ID | Действие | Приёмка |
|---|---|---|
| DB01 | Inspect effective runtime role | Нет SUPERUSER/BYPASSRLS/CREATEROLE/CREATEDB/REPLICATION |
| DB02 | SET ROLE postgres/owner | Runtime получает отказ |
| DB03 | Изменить/отключить RLS | Runtime получает отказ |
| DB04 | Прямой SQL без actor token | Private rows недоступны |
| DB05 | Произвольный SET actor/user_id | Без валидной подписи доступа нет |
| DB06 | COPY PROGRAM/server files/unsafe extension | Runtime получает отказ |
| DB07 | Старый SECURITY DEFINER/view/table owner | Preflight обнаруживает обход; либо доказанная безопасная политика |
| DB08 | INSERT/UPDATE неверного owner/reference | WITH CHECK отклоняет запись |
| DB09 | Удалить data-policy.json в продукте | Платформенная policy сохраняется; admin fallback не возникает |
| DB10 | Root продукта читает gateway /proc/secret/PGDATA/socket | Не получает отдельные trusted secrets и storage |
| DB11 | Старый пароль/сессия после credential migration | Доступ прекращён; проверено уже открытое соединение |
| DB12 | New table/sequence/function после миграции | Default grants/RLS соответствуют contract; неизвестный объект блокирует release |
| DB13 | Inspect protected production process | Non-root/read-only rootfs; нет лишних capabilities и незащищённых writable mounts |

## 10. Генератор и данные вне DB

| ID | Действие | Приёмка |
|---|---|---|
| AI01 | Inspect generation environment | Нет live DB credentials и keys |
| AI02 | Generation/install/build/start пытается достичь live DB | Отказ |
| AI03 | Prompt просит выгрузить реальные заказы | Инструменты не дают live data |
| AI04 | Agent читает snapshot/rootfs/checkpoint | Production artifacts отсутствуют в его разрешённом наборе |
| AI05 | Candidate/recovery запускает job/webhook | Нет внешнего production эффекта |
| AI06 | Inspect agent command results/prompts | Нет canary из live fixture |
| AI07 | Удалить manifest/отключить bridge | Защита DB/data boundary сохраняется |
| AI08 | Импорт seed с altered HBA/roles/functions | Trusted production bootstrap не наследует authority |
| AI09 | Ошибка миграции/SQL в diagnostic output | Техническая причина доступна без клиентских строк и credentials |
| AUX01 | Клиент A скачивает файл B | Отказ |
| AUX02 | Перебрать object keys | Нет доступа вне разрешённых файлов |
| AUX03 | Истёкший/подменённый download link | Отказ |
| AUX04 | Файл HTML/SVG с активным содержимым | Не исполняется на trusted application origin |
| AUX05 | Oversize upload/неверный MIME | Отказ до завершения опасной записи |
| AUX06 | Один URL/кеш обслуживает A затем B | Не возвращает private response A |
| AUX07 | Logout и browser back/service worker | Приватные данные не выдаются из общего/устаревшего кеша |
| AUX08 | Inspect logs/Sentry/queue payloads | Нет credential values и canary PII |
| AUX09 | Support/debug export | Только авторизованный scope, encryption и audit |
| AUX10 | Потеря DB reference/неполный upload | Незавершённый объект не показывается как готовый файл |
| AUX11 | Retention удаляет старую file version | Версия удерживаемого DB recovery point не удалена |
| AUX12 | Файл создан/удалён между backup, затем GC и PITR между событиями | Файл и ссылка доступны в промежуточной точке |

## 11. Backup

| ID | Действие | Приёмка |
|---|---|---|
| BAK01 | Сопоставить backup manifest и inventory | Есть DB, файлы, release/core, policy, state, key refs |
| BAK02 | Новая/пустая DB до первой публикации | Initial backup и отдельный restore пройдены |
| BAK03 | Удалить local plaintext siblings | Внешняя копия остаётся проверяемой и восстанавливаемой |
| BAK04 | Отключить сеть при upload | Нет committed marker неполной копии |
| BAK05 | Прервать encryption/архивирование | Нет доступного plaintext; операция resumable/failed корректно |
| BAK06 | Взять object credentials бизнеса A | Не читает/не удаляет B |
| BAK07 | Подменить archive и соседний checksum | Trusted signature/version evidence отклоняет подмену |
| BAK08 | Выполнить retention около 35-day boundary | Сохранены нужная full copy, WAL и файлы |
| BAK09 | Потерять последнюю копию | Предыдущая committed цепочка восстанавливается; alert с фактическим RPO |
| BAK10 | Отставание WAL >15 минут | Цель RPO отмечена нарушенной; нет зелёного скрытого fallback |
| BAK11 | Сменить backup passphrase | Новый repository проверен; старые копии доступны старым ключом |
| BAK12 | Закончилась quota/место spool | Alert/backpressure согласно runbook; необходимые WAL не выброшены молча |
| BAK13 | Backup shared/platform cluster | Отдельный scope/IAM/key; не изображает project-isolated physical backup |
| BAK14 | Backup control DB/state/access ledger/object catalog | Независимый cold recovery набор полный и identity проверяемы |

## 12. Recovery

| ID | Действие | Приёмка |
|---|---|---|
| REC01 | Скачать внешнюю копию на новый recovery node | Начало восстановления без исходного host |
| REC02 | Неверный ключ | Отказ до открытия восстановленных данных |
| REC03 | Нет нужной key version | Явная ошибка, данные/старые копии не удалены |
| REC04 | Неполный/corrupt artifact/WAL | Fail до переключения live route |
| REC05 | Backup незаявленного source project/environment | Отказ; ожидаемый production→recovery переход требует RecoveryGrant |
| REC06 | Несовместимая PG major/system identifier | Отказ или отдельная утверждённая migration; не best-effort restore |
| REC07 | Проверить контрольные записи и связи | Значения/owners/FK/file digests соответствуют recovery point |
| REC08 | Запустить приложение после restore | Read/write/reload и cross-user denial проходят |
| REC09 | Старый backup после code/key/policy rotation | Совместимый запуск с текущим minimum security |
| REC10 | Потеря production диска + key service | Независимое восстановление data и keys проходит |
| REC11 | Проверить scratch cleanup | Удаляет только recovery lab UUID resources; production не затронут |
| REC12 | Измерить RPO/RTO и очередь массового recovery | Значения соответствуют принятому size/capacity профилю либо обещание не выпущено |
| REC13 | После backup отозвать staff/job, затем восстановить backup | Права не возвращаются; старая и новая сессии отозванного субъекта отклоняются |
| REC14 | Актуальный AccessLedger/head недоступен после restore | Доступ закрыт до независимой проверки; старый membership не источник истины |
| REC15 | Потеря исходной platform DB и controller state | Key service/control plane/ledger/catalog/projects восстановлены по порядку; старые leases/jobs не ожили |

## 13. Изменения, миграции и публикация

| ID | Действие | Приёмка |
|---|---|---|
| MIG01 | Изменить UI и публиковать заново | Live rows/files сохранены |
| MIG02 | Добавить поле и откатить старый UI | Новые значения не потеряны |
| MIG03 | Старый JSON replace/DELETE cascade | Contract предотвращает стирание новых данных |
| MIG04 | Миграция требует опасную функцию/расширение | Автоматический apply остановлен |
| MIG05 | Backfill interrupted/retried | Продолжается с checkpoint, без двойного изменения |
| MIG06 | Не удалось получить fence/drain writers | Cutover не выполняется |
| MIG07 | Осталась старая вкладка/job/webhook | Несовместимая запись отклоняется, повтор не дублирует эффект |
| MIG08 | Сбой до переключения route | Старый release/data остаются работоспособны |
| MIG09 | Потеря ответа после switch | Reconcile обнаруживает фактический результат, без повторного apply |
| MIG10 | Появилась новая запись после switch и нужен rollback | Новая запись сохраняется, старый volume не возвращается вслепую |
| MIG11 | Legacy policy не совместима | Нет admin/plaintext fallback; сохранённый maintenance state |
| MIG12 | Утилизация старого volume/backup | Retention и recoverability проверены до удаления |
| MIG13 | Попытка массового cutover до успешной canary волны | Release controller отклоняет расширение; next wave только после наблюдения/recovery |
| GATE01 | Нет security profile | Новая защищённая публикация запрещена |
| GATE02 | Report из draft/другого проекта | Отказ |
| GATE03 | Report из другой DB/storage identity | Отказ |
| GATE04 | Изменить schema/policy/core после proof | Report инвалидирован |
| GATE05 | Expired/поддельный/неполный report | Отказ |
| GATE06 | Feature flag пытается выключить production guard | Обязательный minimum остаётся |
| GATE07 | Legacy publish/redeploy/rollback endpoint | Тот же gate, без обхода |
| GATE08 | Wake/reconcile/import older snapshot | Защищённый профиль не понижается |
| GATE09 | Два concurrent publish/cutover | Единственный epoch/lease winner |
| GATE10 | Candidate fails security test | Предыдущая публичная версия не заменяется |
| GATE11 | First publish до initial backup/restore | Не открывается пользователям |

## 14. UX, эксплуатация и финальная поставка

| ID | Действие | Приёмка |
|---|---|---|
| UX01 | Предприниматель создаёт обычное приложение | Нет вопросов про шифр/DB password/KMS |
| UX02 | Защита готовится | Понятный preparing status, нет преждевременного зелёного статуса |
| UX03 | Все проверки актуальны | «Защита данных включена» и фактическое время копии |
| UX04 | Backup stale/проверка неизвестна | Статус честно обновляется, действие адресовано Omnia |
| UX05 | Страница на desktop/768/390 px и клавиатуре | Статусы читаемы, нет overflow, focus/touch корректны |
| UX06 | Refresh/reconnect/invite/revoke staff | Состояния и реальные права не расходятся |
| OPS01 | 5-minute reconciler замечает drift | Deduplicated incident и блокировка рискованного выпуска |
| OPS02 | Backup lag без признака утечки | Сайт не выключается только из-за stale metric |
| OPS03 | Подтверждён доступ к чужим данным | Опасный route/credential закрыт, data/evidence сохранены |
| OPS04 | Emergency access оператора | Временное разрешение, цель, scope и audit |
| OPS05 | Удаление ключа | Второй уполномоченный оператор и проверенный retention impact |
| OPS06 | Удаление проекта/экспорт по разрешённому запросу | Scope/retention/integrations корректны; чужие данные не затронуты |
| OPS07 | Security patch образа/зависимости | Регрессии, canary и exact image evidence |
| OPS08 | Restore job конкурирует с live workload | Admission/capacity не нарушает принятый performance budget |
| OPS09 | KMS timeout при текущей работе | Нет plaintext fallback и массового беспорядочного удаления/перезапуска |
| OPS10 | Инцидент требует уведомления | Оператор получает конкретный actionable status; персональные данные в уведомление не включены |
| REL01 | Full regression | Все обязательные suites PASS, skip/unknown перечислены как блокеры |
| REL02 | Independent review | P0/P1 и блокирующие P2 закрыты |
| REL03 | Commit/push | Intended diff и exact SHA; чужой WIP не включён |
| REL04 | Production target | Документированный full compose и host orchestrator, не infra/dev |
| REL05 | Image/process/worker identity | Соответствуют поставленному revision по компонентам |
| REL06 | Health + бизнес-сценарий | HTTP health и реальный CRUD/reload/cross-user |
| REL07 | Backup + external recovery | Выполнены после значимых изменений окружения |
| REL08 | Inventory coverage | 100% поддерживаемых действующих проектов, включая legacy |
| REL09 | RPO/RTO/retention/остаточные копии | Измерены и отражены без необоснованных гарантий |

## 15. Пример минимального release evidence

Формат ниже — схема отчёта, без подставленных «успешных» результатов:

~~~yaml
release:
  revision: string
  component_image_digests: mapping
  production_target_id: opaque-id
  inventory_digest: sha256
  scope_count: integer
  protected_scope_count: integer
  unknown_scope_count: integer
verification:
  suites: list-of-check-results
  independent_review_ref: artifact-reference
  business_flow_ref: artifact-reference
  encrypted_storage_ref: artifact-reference
  offhost_backup_ref: artifact-reference
  isolated_recovery_ref: artifact-reference
  rpo_seconds_observed: integer
  rto_seconds_observed: integer
  measurement_profile: string
operations:
  key_recovery_runbook_ref: artifact-reference
  incident_owner_role: string
  next_restore_due_at: timestamp
  retained_legacy_plaintext_count: integer
decision:
  status: pass-or-blocked
  blocking_check_ids: list
  recorded_at: timestamp
~~~

Все значения заполняются автоматическими проверками и ответственным release owner. Нельзя копировать пример в production report как готовый результат.

## 16. Финальное условие закрытия проекта

- [ ] Приняты T01–T14.
- [ ] Выполнены все обязательные сценарии этого документа.
- [ ] Обязательный профиль включён для новых приложений.
- [ ] Переведены все существующие поддерживаемые приложения.
- [ ] Закрыты наследованные незашифрованные носители и копии.
- [ ] Есть реальный external restore и recovery ключей.
- [ ] Есть owner/staff/client бизнес-проверка и cross-project denial.
- [ ] Выпуск доставлен и подтверждён production evidence.
- [ ] Операторы умеют восстановить и обслуживать систему без участия предпринимателя.

Только после этого формулировка «пользователь получает готовое приложение с управляемой защитой данных» соответствует проверенному поведению.
