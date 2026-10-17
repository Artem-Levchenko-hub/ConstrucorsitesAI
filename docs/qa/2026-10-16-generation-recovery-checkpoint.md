# Приёмка durable recovery: протокол оператора

Активация по умолчанию выключена: `GENERATION_RECOVERY_MODE=off`,
`GENERATION_RECOVERY_EXECUTION_ENABLED=false`, allowlist пуст. Generation worker
наследует API environment. Этот документ и offline-матрица не разрешают активацию.
Root проводит её только после выкладки/проверки этапов10–14, полной CI и review.

Исторические22 запроса/15 приложений и24 фактические генерации сохранены в
существующем отчёте. Старые185PASS/8FAIL/279NOT_RUN не относятся к новому релизу.
Не повторять автоматически весь корпус. Сначала минимальный creation/edit/recovery,
затем четыре раунда по два параллельных запроса на одном проверенном SHA.
Смена релиза сбрасывает последовательность принятых раундов, сохраняя историю.
Одна реальная MAX identity; вторая — `NOT_RUN_INPUT_UNAVAILABLE`, без подмены контекстом.

## Неоплачиваемые проверки

`tools/qa/generation_recovery_fault_matrix.json` связывает15 сценариев с реальными
тестами owning-компонентов. Это список проверок, не результат приёмки.
Обычная API CI выполняет эти модули на disposable PostgreSQL/Redis и приватных
in-memory object fixtures. Авторские process/crash boundaries синтетические:
убийство настоящего daemon, NGINX/TLS, браузер и MAX требуют отдельных root receipts.
Локально использовать существующий проверенный isolated runner с новым nonce;
не подставлять production DSN/Redis, Docker socket или реальные provider endpoints.

## Campaign checkpoint и normal admission

`PYTHONPATH=apps/api/src python tools/qa/generation_recovery_campaign.py
--checkpoint PRIVATE.json --expected-release FULL_SHA --project-a UUID --project-b UUID`
по умолчанию только читает файл, не открывает сеть и ничего не записывает.
`--execute` отдельно разрешает максимум два normal owner API POST в этом вызове,
по одному на A/B. Он не вызывает dispatcher, не меняет ботов/владельцев/финансы,
не экспортирует cookie из открытого браузера и не повторяет неизвестный POST.

Минимальный checkpoint: `version:1`, `release_sha`, `project_ids:[A,B]`,
`requests:[]`, `attempts:[]`, `accepted_rounds:0`.
Для CLI execute дополнительно нужны `owner_id`, HTTPS `origin`, явный
`max_admissions` (счётчик записей этой кампании, не предположенный денежный лимит),
две текущие `qa_manifests` существующего `QaManifest` с mode production_disposable,
`bot_a_sha256` и `deployment_evidence:{path,sha256}`. Deployment receipt содержит
`observed_at` (UTC Unix seconds, возраст до300s) и точные SHA всех семи ролей:
api/web/gateway/worker/generation_worker/orchestrator_core/orchestrator_commerce.
Normal admission дополнительно проверяет текущую серверную готовность.

Request содержит `project:A|B`, `state:planned`, уникальный UUID `idempotency_key`,
`prompt_file` вне репозитория и SHA256 точных UTF-8 bytes `prompt_sha256`.
Полные prompts, cookies, приватные URL, server error bodies не копируются в receipt.
Приватный профиль `GENERATION_RECOVERY_QA_PROFILE` содержит origin/owner_id и путь
`storage_state` стандартного Playwright JSON. Допускается ровно одна действующая
Secure/HttpOnly `omnia_session` cookie точного hostname с path `/` или `/api`.
Нет готового CLI профиля — CLI execute заблокирован. Это не мешает root выполнить
тот же обычный admission через уже авторизованный UI и записать внешние receipts.

До POST атомарно фиксируются обе записи `outcome_unknown` и idempotency UUID.
После ACK сохраняется run_id. Ошибка/обрыв остаётся unknown, не разрешает повтор.
Переживший аварийное завершение `.lock` сохраняется для ручной root reconciliation.
На `admitted` не ставить PASS: требования, source/service SHA, run/attempt/provider
IDs, data hashes/owners, HTTP/browser/MAX evidence и расходы known/unknown заполняются
из независимых фактических receipts. `accepted_rounds` не вычисляется из build или
завершённого HTTP. Сам инструмент никогда не выдаёт full functional acceptance.
Неподтверждённые деньги/429/finance остаются unknown. Числового расходного лимита
в approved плане нет: root отдельно задаёт разрешённый размер кампании перед POST.

## Signed one-shot truncation

Только серверный оператор вызывает `arm_diagnostic_output_budget` в короткой SQL
транзакции для уже принятого обычным путём run с owner/project/message binding.
Она не создаёт run, не меняет queue/admission/lease и не запускает модель.
Intent подписан отдельным purpose/domain существующим response-signing secret,
связан с run/project/user/prompt/первой deterministic attempt; первая consumption
должна произойти в течение60s. Если worker уже создал попытку или держит row lock,
arm отказывает; не обходить гонку приватным dispatcher или новым admission.
Следующая попытка не может стать диагностической. Ключ/intent не печатать.

Ledger insert той же transaction — durable consumption до HTTP. max_tokens16
включён в request digest. Unknown commit не отправляет HTTP. Повторный физический
POST этой попытки запрещён даже после preconnect; cold path только сверяет/читает
квитанцию. Следующий обычный logical Sonnet request снова не содержит max_tokens.
Никаких глобальных лимитов/renewed deadlines/возврата spent work не меняется.

Приёмка требует фактических функций, неизменности принадлежащих владельцу строк,
reload/version-change, отказа foreign GET/PATCH/DELETE,390/768/1440 pointer/keyboard,
console/network и honest last accepted preview. Неподдерживаемые manual obligations
не становятся PASS от модели, checklist или нескольких технических проверок.
Восемь запусков не доказывают99%/100% надёжность. Modeon до полной приёмки запрещён.
