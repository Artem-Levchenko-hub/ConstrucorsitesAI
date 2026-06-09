# Routine: «Первый промпт → готовый full-stack продукт с личным кабинетом»

**Создан:** 2026-06-09 ~10:34 MSK · **Hard stop:** 2026-06-09 **14:00 MSK** · **Cadence:** ~каждые 20–30 мин, один поток (lock).

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

---

## ROADMAP (рутина идёт сверху вниз; `[ ]` = pending, `[x]` = done)

### EPIC A — Full-stack генерация работает надёжно
- [x] A1 — fullstack -> `.tsx` writer вместо freeform HTML (9c87d02). Проверено: 5 tsx, hot_reload=7, Compiled, GET / 200.
- [x] A2 — auth.ts `strategy:"jwt"` для Credentials (UnsupportedStrategy fix, 87d25ce). Проверено: ошибка ушла.
- [x] A3 — signin catch AuthError (CredentialsSignin не роняет апп, 8669da1).
- [x] A4 — **Публичное превью** DONE (verify-only, без правок кода). Внешне URL отдаёт **200**; раннее «timeout» был артефакт NAT-hairpin (curl С САМОГО VPS своего же публичного хоста). Оркестратор уже на `9b7cc9b` (wildcard-cert), публикует роут при provision (`nginx.published_https` + `nginx.cert_wildcard`). Проверено: `https://test-fullstack-v2-0497d7-dev.preview.lead-generator.ru` → 200, TITLE «Мои задачи», реальный лендинг (Начать бесплатно / Войти), без error-overlay.
- [ ] A5 — **Миграции БД на provision**: схема аппа (users + сущности) мигрируется (drizzle), иначе signup/login/CRUD не персистятся. Проверить `docker-entrypoint.sh`/provision + signup в тесте.
- [ ] A6 — **Качество кабинета**: structure_audit ругается «no AppShell/(app) layout, single-page». Усилить fullstack-систем-промпт: публичный лендинг `/`, auth-gated кабинет `(app)/dashboard` с общим шеллом, приватные данные юзера.
- [ ] A7 — **E2E acceptance**: свежий промпт -> signup->login->кабинет->CRUD реально работают (тест прогоняет полный путь и фиксит).

### EPIC B — Личный кабинет как гарантированный результат
- [ ] B1 — систем-промпт мандатит: public `/` + private `(app)/dashboard` (auth-gate, middleware), данные per-user.
- [ ] B2 — ownership-scoping в server actions/queries (юзер видит только своё). Тест: два юзера не видят данные друг друга.

### EPIC C — Шарабельность (скинул -> другой пользуется)
- [ ] C1 — стабильный публичный URL продукта (prod-deploy проекта, не только dev-preview): «Опубликовать» -> `<slug>.app...` живёт постоянно.
- [ ] C2 — тест: второй юзер регистрируется на опубликованном аппе и пользуется независимо.

### EPIC D — Live render (следующий эпик, только когда A–C зелёные)
- [ ] D1 — стрим кода в превью синхронно письму модели (апп строится на глазах).

---

## PROGRESS LOG (рутина дописывает снизу)

- 2026-06-09 ~10:00 MSK — A1 DONE (9c87d02): fullstack->.tsx. E2E: 5 tsx, hot_reload=7, Compiled, GET / 200. PASS.
- 2026-06-09 ~10:30 MSK — A2 DONE (87d25ce): jwt strategy. Проверено: UnsupportedStrategy ушёл, GET / 200. PASS.
- 2026-06-09 ~10:34 MSK — рутина заведена. Старт со следующего шага A4.
- 2026-06-09 ~10:46 MSK — ИТЕРАЦИЯ 1 (A4): публичное превью РАБОТАЕТ. Внешний curl → 200, TITLE «Мои задачи — простой список дел», реальный лендинг (Начать бесплатно/Войти), без error-overlay. Раннее «timeout» = NAT-hairpin (curl с самого VPS), не баг. Оркестратор на 9b7cc9b публикует роут+wildcard-cert при provision. Без правок кода. A4 [x]. PASS. Следующий: A5 (миграции БД на provision — проверить что signup/login/CRUD персистятся).
