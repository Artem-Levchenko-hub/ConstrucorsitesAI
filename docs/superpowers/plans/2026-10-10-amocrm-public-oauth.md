# amoCRM public OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Подготовить безопасный переход на публичную amoCRM-интеграцию и воспроизводимое демо для текущей модерации.

**Architecture:** Старые подключения сохраняют legacy-профиль; новые state и grants фиксируют исходный OAuth-профиль. Общая PostgreSQL-блокировка владельца и провайдера сериализует start/callback/refresh. Существующий доступ не заменяется без отдельного подтверждённого сценария.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, Next.js, React, pytest, Vitest.

**Spec:** Публичное приложение `1c5f6755-da59-4d78-b7c6-7c0527fb9c80`, callback `https://yleum.ru/api/integrations/oauth/amocrm/callback`; версия 1 уже на проверке. Приёмка описана в `docs/qa/2026-10-10-amocrm/acceptance.md`.

## Global Constraints

- Не менять активный Coffee, его привязки и данные; не переподавать заявку и не писать модератору.
- Не заменять legacy client ID/secret; public OAuth выключен до отдельного согласования credentials/grants.
- Юридические тексты — только черновики, старые записи согласий сохранять.
- Синтетические внешние записи и новые права требуют отдельного согласования; модератору не выдавать администратора.
- Git/deploy serial owner — root; production full + hostdb, exact green main SHA, protected backup, owned drain/locks.

## Review Focus

- NULL/unknown profile: legacy mapping либо отказ без потери ciphertext/status.
- Два callback и stale snapshot: одна выдача доступа, конфликт до внешнего HTTP.
- Параллельный refresh: одно обновление с перечитыванием состояния после lock.
- Разные owners/projects: отсутствие чужого доступа и невозможность общей замены без подтверждения.
- Произвольный/дублирующий OAuth outcome: интерфейс сохраняет работоспособность, не заявляет выдачу доступа.

## Task 1: OAuth identity and concurrency

Files: `apps/api/src/yleum_api/{core/config.py,core/errors.py,models/app_integration.py,services/integration_oauth.py,services/integration_credentials.py,routers/app_integrations.py}`; migration `0082_amocrm_oauth_profiles.py`; profile/concurrency/callback/migration tests.

- [x] Добавить failing tests профилей, race, stale callback и сохранности старых grants.
- [x] Реализовать два профиля, state snapshot, grant revision и общую owner/provider lock.
- [x] Запретить reconnect существующего доступа (409), отклонить старые state без snapshot.
- [x] Additive upgrade: четыре колонки, без переписывания IDs/токенов/привязок; downgrade отказывает при используемых профилях.
- [x] Целевые API119 и migration15 прошли; Linux mypy282 файлов, Ruff чистый.
- [ ] Полный exact-revision CI и production schema/legacy preservation gate.

## Task 2: UI and owner documentation

Files: integration redirect route, `FigmaIntegrationHub.tsx`, owner guide and relevant Vitest files; compose and env example.

- [x] Сохранить allowlisted OAuth outcome после возвращения в редактор.
- [x] Показать безопасные сообщения; Map + ровно один query value исключают prototype/duplicate input.
- [x] RED4fail→GREEN; 66 focused и полный web1023 passed; types/lint0errors, существующее apple-icon warning.
- [x] Инструкция: настройка, форма, публикация, MAX, история, disconnect/delete/revoke.
- [x] Черновики политики/согласия и сверка тарифов подготовлены без публикации юридических текстов.
- [x] Независимое review: No findings после исправления P2.

## Task 3: Delivery and live acceptance

- [ ] Focused commit, upstream push, PR/exact-main green CI.
- [ ] Свободное окно, защищённый backup и legacy connection/binding baseline.
- [ ] API migration0082 → workers/controllers → web → billing; publicdisabled.
- [ ] Exact-role health, migration/профильная сохранность, production smoke, owned undrain.
- [ ] После отдельного согласования: public credentials/new OAuth с нуля и два независимых CRM клиента.
- [ ] Stable demo: второй бот после модерации, точная ссылка, синтетическая заявка, stage/history/reentry.
- [ ] Приёмка doubleclick/lostresponse, A↔B denial, legacy refresh, revoke/reconnect A без изменения B.

Recovery: additive schema сохраняется. При сбое API остаются drain/locks; совместимый forward-fix той же schema. Не выполнять автоматический downgrade/restore DB; старый API image со штатным Alembic startup не считается доказанным rollback после0082.
