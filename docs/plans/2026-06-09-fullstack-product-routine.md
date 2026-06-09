# Routine: «Первый промпт → готовый full-stack продукт с личным кабинетом»

**Создан:** 2026-06-09 ~10:34 MSK · **Hard stop:** 2026-06-09 **16:00 MSK** (продлено владельцем) · **Cadence:** один поток (lock). С 14:10 рутина продолжается в ОТДЕЛЬНОЙ свежей сессии (scheduled task `fullstack-routine-1410`).

Цель: человек с **первого промпта** получает **готовое полноценное веб-приложение** с
личным кабинетом (auth), которое **работает** и которым можно **поделиться** —
другой человек заходит и пользуется. Следующий эпик после этого — **Live render**.

---

## ПРОТОКОЛ РУТИНЫ (выполняется каждый тик; читать целиком)

> Этот файл — единственный источник правды для рутины. Тик не помнит прошлых
> разговоров. Действуй строго по протоколу.

1. **Стоп-гейт.** `ssh i48ptgvnis@170.168.72.200 'TZ=Europe/Moscow date +%H%M'`.
   Если **>= 1400** → рутина окончена: `CronList` -> `CronDelete` оба job'а этой
   рутины -> допиши в PROGRESS LOG строку `STOPPED <время>` -> **стоп, ничего не делать**.
2. **Lock (один поток, без наложений).** Файл `.claude/routine.lock`.
   - Если существует и его возраст **< 35 мин** -> активный тик идёт -> **выйти молча**.
   - Иначе записать в него текущий UTC-таймстамп.
   - В конце тика (успех ИЛИ ошибка) — **удалить** `.claude/routine.lock`.
3. **Синхронизация.** `git fetch origin && git status` — main двигается под нами
   (4 агента). Если рабочее дерево этой сессии отстало — работать через свежий
   ворктри на `origin/main`: `git worktree add <ascii-path> origin/main`,
   правки -> commit -> `push origin HEAD:main` (rebase при race) -> worktree remove.
4. **Выбрать следующий шаг.** Первый `[ ]` PENDING в ROADMAP (сверху вниз).
   Делать **ровно один маленький слайс** (~1–3 файла). Не брать большой кусок.
5. **Имплементация.** Минимальный диф. Перед доставкой — typecheck/lint затронутого.
   Никогда не пушить и не деплоить сломанное.
6. **Доставка** (CLAUDE.md): atomic commit с трейлером
   `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` -> push main ->
   деплой если менялся runtime: api/worker -> `cd /opt/omnia/apps/llm-gateway/deploy/full && docker compose up -d --build api worker`;
   шаблон оркестратора -> `git pull` на VPS (читается с диска при provision) + при
   нужде hot-patch живого контейнера (`docker cp` в `/app` + touch -> HMR);
   orchestrator-код -> рестарт `omnia-orchestrator` (только при явной необходимости).
7. **ТЕСТИРОВЩИК (обязательно после каждой имплементации).** Прогнать server-side
   E2E (см. ниже): создать свежий `fullstack`-проект под тест-аккаунтом, отправить
   промпт, дождаться сборки, проверить: пишутся `.tsx`, контейнер `Compiled` +
   `GET / 200`, в логах **нет** `error/UnsupportedStrategy/CredentialsSignin(uncaught)`,
   и (по шагам) вход/CRUD работают. Сломано — **чинить в этом же тике**,
   пере-тестировать, только потом закрывать шаг.
8. **Лог.** Дописать в PROGRESS LOG: дата/время MSK, что сделано, коммит, результат
   теста (PASS/FAIL+причина). Отметить шаг `[x]`. Снять lock.
9. Один шаг за тик. Большой шаг — разбить, сделать первый под-слайс, остальное `[ ]`.

### Тест-доступ (server-side E2E, без браузера)
- Минт токена под тест-аккаунт (owner проекта `5f75add5` = `undj00x03@gmail.com`):
  `OWNER=$(docker exec omnia-prod-postgres psql -U omnia -d omnia -tAc "select owner_id from projects where id='5f75add5-0d0a-43ac-8eb6-989506bb400c'")`
  `TOKEN=$(docker exec omnia-prod-api /app/.venv/bin/python -c "from omnia_api.core.security import create_access_token; from uuid import UUID; print(create_access_token(UUID('$OWNER')))")`
- Создать проект: `POST http://localhost:8200/api/projects` body `{"name":"routine-test","template":"fullstack"}` куки `omnia_session=$TOKEN`.
- Промпт: `POST /api/projects/<id>/prompt` `{"prompt":"<spec>"}`. Clarify включён ->
  первый ответ `mode:clarify`, второй промпт «генерируй» -> `mode:build`.
- Контейнер `omnia-dev-<slug>`, порт `docker port <c> 3000`. Файлы аппа едут в
  контейнер через `hot_reload` (НЕ в host project dir). Проверять `docker exec <c>
  find /app/src/app -name '*.tsx'` + `docker logs <c>`.
- Прибраться: удалять старые `routine-test-*` контейнеры/проекты периодически.

### Безопасность/осторожность
- Не трогать живые клиентские аппы (`kofeinia`, `legomagazin`).
- Не ломать прод `constructor.lead-generator.ru` — после деплоя health-check (curl 200).
- Превью-контейнеры сканируются на SSRF — не плодить лишние, чистить тест-мусор.
- Запрещённые действия (оплата, удаление данных, права доступа) — не выполнять.
- **RESOURCE-GUARD (после инцидента 2026-06-09 — бокс лёг от перегрузки dev-контейнерами `next dev`, прод+SSH не отвечали, владелец ребутнул):** ПЕРЕД генерацией в тесте — `ssh … free -m`; если available RAM < 3000 МБ — НЕ генерировать, сперва снести старый тест-мусор (`docker rm -f` контейнеров `omnia-dev-{routine-test,verifyfix,provtest,test-}*`). ПОСЛЕ теста — СРАЗУ удалить созданный тест-контейнер. Не держать >1-2 тест-контейнеров одновременно. Не трогать чужие (`signal-telekom`, `crm-sistema`) и клиентские (`kofeinia`, `legomagazin`).

---

## ROADMAP (рутина идёт сверху вниз; `[ ]` = pending, `[x]` = done)

### EPIC A — Full-stack генерация работает надёжно
- [x] A1 — fullstack -> `.tsx` writer вместо freeform HTML (9c87d02). Проверено: 5 tsx, hot_reload=7, Compiled, GET / 200.
- [x] A2 — auth.ts `strategy:"jwt"` для Credentials (UnsupportedStrategy fix, 87d25ce). Проверено: ошибка ушла.
- [x] A3 — signin catch AuthError (CredentialsSignin не роняет апп, 8669da1).
- [x] A4 — **Публичное превью** DONE (verify-only, без правок кода). Внешне URL отдаёт **200**; раннее «timeout» был артефакт NAT-hairpin (curl С САМОГО VPS своего же публичного хоста). Оркестратор уже на `9b7cc9b` (wildcard-cert), публикует роут при provision (`nginx.published_https` + `nginx.cert_wildcard`). Проверено: `https://test-fullstack-v2-0497d7-dev.preview.lead-generator.ru` → 200, TITLE «Мои задачи», реальный лендинг (Начать бесплатно / Войти), без error-overlay.
- [x] A5 — **Миграции БД на provision** DONE. Корень глубже: generated аппы стартуют из ЗАПЕЧЁННОГО base-образа `omnia-template-nextjs-postgres-drizzle:dev`, который НЕ пересобирали после фиксов → старый package.json без `db:push` → 0 таблиц → `relation users does not exist`. Фикс: (1) db:push script (`7d5365c`); (2) rebuild-proof entrypoint `pnpm exec drizzle-kit push` (`5885ba7`); (3) **пересобран base-образ** на VPS. Проверено на свежем `verifyfix`: entrypoint выполнил drizzle push, users создан с `password_hash`+`role`, signup-insert PASS. ⚠️ Фиксы шаблона требуют rebuild :dev-образа (`scripts/build-template-images.sh`), НЕ только git pull.
- [ ] A5b — **drizzle-kit push ломается под per-project search_path**: генерит FK на `public.users` (таблицы живут в `proj_<id>`) → ALTER (добавление колонок) откатывается. Архфикс: БД-на-проект (всё в public) ЛИБО pgSchema-инъекция ЛИБО post-push raw-SQL reconcile в entrypoint.
- [x] A6a — **AI выбрасывает auth-колонки** DONE. Детерминированный guard `_preserve_auth_schema` в messages.py (`b015343`) ре-инжектит `passwordHash`+`role` если AI их дропнул в schema.ts. Юнит-тест PASS; проверено на свежей генерации (guard сработал, schema.ts сохранил auth-колонки + notes-сущность). PASS.
- [ ] A6 — **Качество кабинета**: structure_audit ругается «no AppShell/(app) layout, single-page». Усилить fullstack-систем-промпт: публичный лендинг `/`, auth-gated кабинет `(app)/dashboard` с общим шеллом, приватные данные юзера.
- [ ] A7 — **E2E acceptance**: свежий промпт -> signup->login->кабинет->CRUD реально работают (тест прогоняет полный путь и фиксит).

### EPIC E — Жизненный цикл проекта (⚠️ ВЫСОКИЙ приоритет — владелец заблокирован; делать ПЕРЕД B/C/D)
- [ ] E1 — **Удаление не работает у большинства проектов** (владелец: «не могу удалить большинство»). Диагностика 2026-06-09: сам API `DELETE /api/projects/{id}` отрабатывает (204; teardown-first/fail-closed: `orchestrator.destroy` → `repo.delete_repo` → ORM cascade). На снесённом контейнере delete=204 OK, оркестратор чисто сносит container+schema(archive)+nginx. Значит ломается ЛИБО (a) UI delete-flow (`apps/web` — кнопка ⋮/подтверждение по имени/обработка ошибки/инвалидация списка), ЛИБО (b) teardown ЖИВОГО контейнера таймаутит/4xx → fail-closed → проект выживает. ЧИНИТЬ: проверить UI-флоу (браузером); сделать teardown полностью идемпотентным/устойчивым (не падать если контейнер/схема/nginx уже снесены/недоступны); опц. force-delete. ТЕСТ: создать апп → удалить из UI → контейнер+схема+роут+row снесены; список обновился.
- [ ] E2 — **Остановка проекта (stop)** из UI работает (пауза контейнера → потом «Запустить»). Проверить `runtime/stop` + UI.
- [ ] E3 — **Любой проект открывается и ВСЕГДА работает**: НЕ застревает на стартер-странице «Новый проект на Omnia.AI» (= генерация не применилась: прерванная сборка / не доехал hot_reload / провал гейта). Гарантировать: апп показывает свой контент ЛИБО явную ошибку+«Починить», но не молчаливый стартер. При прерванной генерации — авто-retry / резюм. ТЕСТ: 5 свежих аппов подряд → у всех свой контент, ни одного стартера.

### EPIC B — Личный кабинет как гарантированный результат
- [ ] B1 — систем-промпт мандатит: public `/` + private `(app)/dashboard` (auth-gate, middleware), данные per-user.
- [ ] B2 — ownership-scoping в server actions/queries (юзер видит только своё). Тест: два юзера не видят данные друг друга.

### EPIC C — Шарабельность (скинул -> другой пользуется)
- [ ] C1 — стабильный публичный URL продукта (prod-deploy проекта, не только dev-preview): «Опубликовать» -> `<slug>.app...` живёт постоянно.
- [ ] C2 — тест: второй юзер регистрируется на опубликованном аппе и пользуется независимо.

### EPIC D — Live render (следующий эпик, только когда A–C + E зелёные)
- [ ] D1 — стрим кода в превью синхронно письму модели (апп строится на глазах).

---

## PROGRESS LOG (рутина дописывает снизу)

- 2026-06-09 ~10:00 MSK — A1 DONE (9c87d02): fullstack->.tsx. E2E: 5 tsx, hot_reload=7, Compiled, GET / 200. PASS.
- 2026-06-09 ~10:30 MSK — A2 DONE (87d25ce): jwt strategy. Проверено: UnsupportedStrategy ушёл, GET / 200. PASS.
- 2026-06-09 ~10:34 MSK — рутина заведена. Старт со следующего шага A4.
- 2026-06-09 ~11:05 MSK — ИТЕРАЦИЯ 2 (A5): юзер поймал `relation "users" does not exist` при регистрации. Root cause: entrypoint звал `pnpm db:push --force`, но скрипта `db:push` НЕ БЫЛО в package.json → 0 таблиц. FIX: добавлен db:push (`7d5365c`, push+VPS pull). На тест-контейнере db:push создал users/accounts/sessions/tasks/verification_tokens. Вскрылись 2 более глубоких бага: (A6a) AI выкинул passwordHash+role из users в schema.ts; (A5b) drizzle-kit push под search_path генерит FK на public.users → ALTER падает. Тест-апп разблокирован raw SQL (добавил password_hash+role), signup-insert проверен PASS. Свежие аппы — чинить A5b+A6a след. тиками. A5 [~].
- 2026-06-09 ~10:46 MSK — ИТЕРАЦИЯ 1 (A4): публичное превью РАБОТАЕТ. Внешний curl → 200, TITLE «Мои задачи — простой список дел», реальный лендинг (Начать бесплатно/Войти), без error-overlay. Раннее «timeout» = NAT-hairpin (curl с самого VPS), не баг. Оркестратор на 9b7cc9b публикует роут+wildcard-cert при provision. Без правок кода. A4 [x]. PASS. Следующий: A5 (миграции БД на provision — проверить что signup/login/CRUD персистятся).
- 2026-06-09 ~13:40 MSK — ОКНО ПРОДЛЕНО до 16:00, добавлен EPIC E. Заведена scheduled-сессия `fullstack-routine-1410` (старт 14:10 → продолжит в СВЕЖЕЙ сессии, непрерывно до 16:00). Диагностика для next-session: **(E1 удаление)** API `DELETE /api/projects/{id}` РАБОТАЕТ (204; teardown-first/fail-closed чисто сносит container+schema(archive)+nginx; провтест удалён 204). Значит корень в (a) UI delete-flow (`apps/web` — на main, НЕ в локальном `fix/live-design`, поэтому грепом локально не видно; смотреть origin/main/worktree + браузером) ЛИБО (b) teardown ЖИВОГО контейнера таймаутит → fail-closed. **(E3 always-works)** `verifyfix` завис на СТАРТЕРЕ, хотя генерация УСПЕШНА: assistant msg = **7 .tsx, 43 KB**, лежит в git-снапшоте, НО `hot_reload` НЕ применил файлы в контейнер (файлы dev-контейнера живут только в writable-слое без bind-mount → теряются при краше/ребуте/recreate; при рестарте сохраняются). E3-фикс: надёжный hot_reload + АВТО-РЕСИНК git→контейнер при открытии/«Запустить»; НИКОГДА не показывать молчаливый стартер если есть снапшот с кодом. Тест-мусор почищен, RAM ок.
- 2026-06-09 ~13:20 MSK — ИТЕРАЦИЯ 3 + РЕКАВЕРИ: (1) A6a guard `_preserve_auth_schema` (`b015343`) — verified. (2) **ИНЦИДЕНТ**: бокс лёг от перегрузки dev-контейнерами (OOM/thrash), прод+SSH не отвечали ~10 мин; владелец ребутнул. (3) Нашёл КОРЕНЬ почему свежие аппы без таблиц: стартуют из ЗАПЕЧЁННОГО base-образа, не пересобранного после фиксов → старый package.json без db:push. ФИКС: rebuild-proof entrypoint (`5885ba7`) + **пересобрал base-образ** (`scripts/build-template-images.sh`). (4) Проверено на свежем `verifyfix`: drizzle push отработал, users с password_hash+role, signup-insert PASS. **МАЙЛСТОУН: первый промпт → свежий full-stack апп с РАБОЧЕЙ регистрацией.** A5 [x], A6a [x]. Добавлен RESOURCE-GUARD в протокол. Почистил тест-мусор. Осталось: A5b (FK-public, non-fatal), A6 (кабинет (app)-layout), A7 (E2E click-through), B/C/D.
