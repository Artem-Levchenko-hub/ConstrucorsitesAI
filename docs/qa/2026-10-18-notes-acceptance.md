# Ограниченная проверка обычных запросов о заметках

Статус: исходный срез подготовлен для независимого ревью; активации и выкладки нет.
Это не подтверждение полного Task13 или исходного приложения с PostgreSQL.

## Поддерживаемое поведение

Два неизменных обычных запроса находятся в `generation/notes_acceptance.py::PROMPTS`.
Для каждого сохраняются исходный текст, manifest и все FidelitySpec-обязанности.
Server-reviewed registry покрывает только CRUD, reload, новое авторизованное окно
того же пользователя, удаление и сохранение ранее существовавших записей.
Любой дополнительный текст, PostgreSQL/перезапуск сервера или discovery-axis остаётся WAIT.
Model PASS, BuildPlan, hash churn и stdout не являются свидетельствами.

## Установка и границы

Используется существующая GLOBAL настройка `MAX_BEHAVIOR_ADAPTER_REGISTRY`:
`{"max-miniapp-nextjs":{"preset":"notes_acceptance_v1"}}`.
Executable, SHA pin и production browser installation проверяются прежними guards.
Vendor registry для этого профиля пустой; default/все флаги остаются прежними OFF.
Это отдельный профиль для контролируемой QA-проверки, не per-project переключатель.
Он не выбирается из текста модели. Одновременно named и notes профиль не выбираются.
Прежняя named-конфигурация и GET-only browser/core/worker сохраняются без изменения.
Не активировать этот GLOBAL профиль на общем окружении как универсальную замену.

Перед чтением новых seals root должен добавить `acceptance:2` в ожидаемый API tuple
привилегированного observer и включить четыре новых notes source-модуля в source proof.
Старый decoder seal v1 отвергает seal v2; publication остаётся `publication_v2`.
Исторический read reload-ит owning candidate/proof/owner, не запускает браузер заново.
Runner version/hash входит в receipt. Изменение runner без новой совместимой версии
консервативно блокирует прежний receipt; его нельзя молча переаттестовать.

## Исполнение

Отдельный установленный worker использует существующий Linux guardian/subreaper.
Проект/run/workspace/claim/session/drain проверяются до записи. Durable intent и
activity коммитятся до browser dispatch. Операция детерминирована от run+binding.
Только одна синтетическая запись с уникальным `yleum-acceptance-` prefix; никаких
изменений прежних записей. Неизвестный HTTP outcome не повторяется.
До вызова browser отказ закрывает только точную activity FAILED. После dispatch
даже reaped browser не доказывает завершение серверного POST: ACTIVE/WAIT сохраняются.
Срез не реализует автоматическое разрешение такого unknown или повтор изменённого candidate.

DOM-контракт: `notes-title`, `notes-body`, `notes-save`, `notes-row[data-note-id]`,
`notes-edit`, `notes-delete`. Same-origin `/api/notes` возвращает точные UUID/title/body.
Разрешены только точные POST/PATCH/DELETE синтетической записи, по одному запросу.
GET candidate endpoint подтверждает readback, **не является доверенным DB witness**.
Отдельно не доказаны: server restart, DB durability, MAX, второй пользователь.

## Проверки

Настоящий Chromium: CRUD/reload/new-context/preservation; потеря данных, noop update/delete,
подмена compiled asset, серверный POST с commit после закрытия браузера.
Настоящий PG: два обычных запроса→seal2→publication→release→completed→owner REST;
фальшивый/foreign aggregate, неизвестный POST, дополнительные обязанности, claim/cancel/
fence/session/drain loss; прежние named/seal/publication tests остаются обязательными.
PG physical transport double и реальный browser проверяются отдельно, не выдаются за
единый production E2E. Полные результаты и hashes — в приватном author manifest.
