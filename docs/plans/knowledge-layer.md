# Omnia — Knowledge Layer Plan: документация + enforcement для production-grade первой генерации

> Источник истины для воркстрима «наполнить агента документацией/стандартами, чтобы ПЕРВАЯ
> генерация была безопасной, юзабельной, быстрой и production-grade по умолчанию».
> Проектируется ПОД зафиксированное направление (`docs/plans/MASTER-PLAN-autonomous-engineer.md`):
> Omnia = автономный ИИ-инженер, реальный цикл plan→act→observe→verify→fix, реальный
> экспортируемый код на фиксированных скелетах. Этот документ — слой K (Knowledge), который
> встаёт между «костяком» (агент-цикл, §0 мастер-плана) и acceptance-gate (§5 мастер-плана).
>
> Русская проза, идентификаторы/пути — английские. Каждое утверждение о текущем поведении
> привязано к `file:line`. Честно помечено, что доказано, а что — гипотеза под A/B.

---

## 0. Executive summary

«Наполнить документацией» — это НЕ один воркстрим, а ДВА слоя, и путать их опасно:
**KNOWLEDGE** (что агент знает — skills/docs/golden-примитивы, поднимает ПОЛ первого черновика)
и **ENFORCEMENT** (что детерминированно проверяется перед ship — gates, гарантирует ПОТОЛОК).
Веди с доказанной половиной. Enforcement доказан: SAST остаётся необходимым, потому что
prompt-инжиниринг сам по себе НЕ снижает уязвимости надёжно ([F1] arXiv 2605.24298 — CWE-aware
CoT дал p>0.05, иногда УВЕЛИЧИЛ уязвимости и LoC; [F2] «systematic validation cannot be replaced
by instruction engineering»); 12.1% AI-сгенерированных файлов несут ≥1 CWE на корпусе из 7117
([F3] arXiv 2510.26103); топ-5 самых тяжёлых CWE в AI-коде (SQLi/CWE-89, OS-cmd-inj/CWE-78,
code-inj/CWE-94, hard-coded creds/CWE-798/259) — ровно то, что ловит Semgrep/secret-scanner ([F4]).
Это эмпирически подтверждает усвоенный проектом урок «модель игнорит промпт» (память:
`omnia_first_gen_quality_initiative` — «engine CAN enforce, model WON'T»).

Knowledge-половина — **честно НЕ доказана сама по себе.** Измеримая эффективность Cursor-rules на
качество вывода ОПРОВЕРГНУТА (killed-finding: правила пишутся из интуиции, влияние не измерено);
«few-shot golden example beats prose» — правдоподобно, но НЕ доказано. Поэтому: **доверяй gates,
относись к skills как к «может помочь», ТРЕБУЙ A/B-валидацию на собственном корпусе генераций
Omnia.** Вывод-стратегия (спина всего плана): толкай каждый стандарт как можно ВЫШЕ по лестнице
качества (encode-in-code > примитив скелета > kit-компонент > детерминированный gate > извлекаемый
doc > проза). Чем выше — тем меньше зависит от того, вспомнит ли модель. Knowledge поднимает пол
первого черновика; gates гарантируют потолок перед ship; ни то, ни другое поодиночке не достаточно.

### 0.1 Честная экономика: gates токен-free, но heal — НЕ бесплатен (резолв P0-1)

Критичная поправка, чтобы не строить план на ложной опоре. Два разных счётчика:

- **Сами проверки токен-free.** Semgrep/gitleaks/Lighthouse/axe/`_check_backend` — это CPU В
  контейнере (или Python-regex в API-процессе для `_check_backend`, messages.py:2491), gateway не
  дёргается. Запуск гейта стоит ~ноль рублей.
- **Но каждая heal-итерация — это ПОЛНЫЙ agent-прогон.** `messages.py:2510` (а также 2441/2689) на
  каждой попытке починки вызывает `agent_builder.run_agent_build(...)` — целый ReAct-цикл (`_agent_system`,
  до `_agent_steps` шагов, биллинг vsegpt по символам). Heal НЕ дешевле «прогнать writer дважды» —
  он той же природы (полный прогон модели), просто целевой по диагнозу.

Отсюда — **не маркетинговый, а инженерный** аргумент за gates: экономия не в том, что «heal дешевле
re-write», а в трёх рычагах, которые СОКРАЩАЮТ число дорогих agent-прогонов:
1. **Bounded heal.** `agent_gate_max_attempts` (config.py:770, default=2) жёстко ограничивает число
   heal-прогонов; `build_fix_instruction`/`should_retry` (agent_gate_feedback.py:42-50) возвращают
   `None`/`False` по достижении лимита → цикл останавливается, не жжёт прогоны бесконечно.
2. **Staging дорогих гейтов у `done`.** Дешёвые статические проверки гоняются на каждом релевантном
   проходе (токен-free), но heal от них тоже стоит agent-прогон — поэтому ДОРОГИЕ по числу находок
   гейты (Lighthouse, axe на живом маршруте) ставятся ОДИН раз ближе к `done`, чтобы не плодить
   heal-циклы на промежуточных недоделанных состояниях.
3. **Advisory-без-heal как дефолт.** На старте каждый гейт только логирует (operators видят находки),
   heal включается отдельным флагом. Видимость — бесплатна; agent-прогоны тратятся только когда
   осознанно включён heal.

Вывод: **gates ценны не потому что «дешевле writer'а дважды» (это неверно), а потому что они —
ЕДИНСТВЕННЫЙ детерминированный потолок качества; их стоимость управляема через bounded+staged+advisory.**

---

## 1. Лестница качества (quality ladder) — куда садится каждый стандарт Omnia

Каждый стандарт реализуется на САМОМ ВЫСОКОМ доступном уровне; проза — последнее средство.

| Уровень | Что это | Зависимость от модели | Примеры стандартов Omnia |
|---|---|---|---|
| **1. Encode-in-code** | стандарт невозможно нарушить — он часть фиксированного скелета | НОЛЬ | RLS-политика в миграции (`scoped.ts`, мастер-план §6); auth-таблицы (`_AUTH_TABLES_BLOCK` messages.py:1575); middleware-auth-floor (`src/middleware.ts`, память G1); CSP/security-заголовки в `next.config.ts` |
| **2. Примитив скелета** | готовый безопасный строительный блок, агент его ВЫЗЫВАЕТ, не пишет | очень низкая | `requireUser()`/`getCurrentUser()` (session.ts); `@/lib/sdk` (всегда scoped); Redis-presence-хелпер (мастер-план §4); members-ACL движок |
| **3. Kit-компонент** | готовый UI с зашитой a11y/responsive/состояниями | низкая | `<CrudResource>`/`<AppShell>`/`<UsersAdmin>`/`<UserSelect>` (`@/components/omnia`); shadcn-примитивы (focus-ring, ARIA) |
| **4. Детерминированный gate** | проверка перед ship, модель не уговорит | НОЛЬ (process-layer) | Semgrep p/owasp-top-ten; gitleaks; eslint-jsx-a11y strict; Lighthouse-budgets; backend_guardrail (уже есть, backend_guardrail.py); dead-link scan |
| **5. Извлекаемый doc-файл** | знание читается по требованию (`read_file`) при матче задачи | средняя (модель должна прочитать) | `.omnia/skills/*.md` (новое, §2); per-stack SYSTEM_PROMPT.md (уже есть) |
| **6. Проза в системном промпте** | always-in-context правило | ВЫСОКАЯ (модель может проигнорить) | LOOP_PROTOCOL (agent_builder.py:656); 10-правил-конституция (новое, K0) |

**Правило воркстрима.** Прежде чем писать doc/prose-правило, спроси: можно ли поднять это до gate?
до примитива? до encode-in-code? Документация для безопасности/a11y/perf — НЕ замена gate, а
дополнение: skill-автотриггер вероятностный, на критичных канонах полагаться на него нельзя (caveat
(a)) — они ДУБЛИРУЮТСЯ детерминированным gate. Память проекта это уже подтверждает: невидимый текст,
dead-кнопки, raw-DB-escape — все «починены» не промптом, а детерминированным rewrite/scan
(`omnia_invented_palette_vars_invisible_text`, `omnia_dead_auth_link_postprocessor`, backend_guardrail).

---

## 2. Механика knowledge-слоя: skills-as-files + эмулированный progressive disclosure

### 2.1 Почему именно файлы, а не вектор-БД

Anthropic Agent Skills = директория с `SKILL.md` (YAML-frontmatter `name`+`description` + markdown-тело);
ядро — **progressive disclosure**: всегда загружены только name+description (~100 токенов/skill),
полное тело читается с диска по матчу задачи, Level-3 reference-файлы бесплатны до прочтения ([F5][F6]).
У агента Omnia НЕТ нативного Skills-механизма (caveat (c)) — это text-protocol ReAct. Но у него УЖЕ
есть всё, чтобы это ЭМУЛИРОВАТЬ: инструмент `read_file` (agent_builder.py:727) и составной системный
промпт (`build_system_prompt`, agent_builder.py:685 = `LOOP_PROTOCOL` + stack-guide). **Никакой
вектор-БД/RAG не нужен** (caveat (c)) — агент сам читает файл.

### 2.2 Раскладка на диске (живёт В скелете, версионируется с ним)

```
apps/orchestrator/templates/<stack>/
  SYSTEM_PROMPT.md          # УЖЕ есть — per-stack guide (nextjs-entities ~190 строк)
  .omnia/
    skills/
      INDEX.md              # авто-сгенерированный индекс: name + description (одна строка на skill)
      security-rls.md       # «когда добавляешь таблицу/данные → RLS-политика, SET LOCAL app.user_id»
      a11y-forms.md         # «когда строишь форму/таблицу → labels, focus, ARIA, kit-компоненты»
      perf-images.md        # «когда кладёшь картинки/hero → next/image, размеры, lazy»
      realtime-presence.md  # (realtime) «presence через Redis TTL+pubsub, не in-memory»
      money-integrity.md    # «деньги → min:0, step:0.01, никогда negative; см. PravVyd batch»
      ...
```

`.omnia/skills/` — потому что слой принадлежит скелету (версионируется с залоченными зависимостями,
§5), а не глобальному агенту: realtime-presence бессмысленен в fastapi-скелете. Каждый файл — формат
[F5]: frontmatter `name`+`description` + тело. `INDEX.md` собирается build-скриптом из frontmatter'ов
(одна строка на skill: `- security-rls — применяй когда добавляешь таблицу/сущность/данные`).

### 2.3 Эмулированный progressive disclosure (привязка к коду)

Два изменения, оба аддитивные, оба под флагом `USE_SKILL_INDEX` (default False):

1. **Description-индекс всегда в промпте.** `load_stack_system_prompt` (agent_builder.py:693) уже
   читает `<template>/SYSTEM_PROMPT.md`. Добавить рядом `load_skill_index(orch_template)`, читающий
   `.omnia/skills/INDEX.md` (~10-20 строк = ~300 токенов, не тело). **Точка вставки INDEX —
   `build_system_prompt` (agent_builder.py:685), которая СЕЙЧАС лишь склеивает
   `LOOP_PROTOCOL + "\n\n" + stack_guide` (agent_builder.py:690).** Расширить её третьим аргументом
   `skill_index: str | None` и дописать блок ПОСЛЕ stack-guide:
   ```
   ДОСТУПНЫЕ SKILL-ФАЙЛЫ (читай ПОЛНЫЙ файл через read_file, когда задача матчит description):
   <INDEX.md содержимое>
   Прежде чем писать код по теме (безопасность/формы/картинки/realtime/деньги) — read_file
   .omnia/skills/<name>.md и следуй ему.
   ```
   Индекс попадает в system-сообщение (head окна, §5.2) → стоит ~300 токенов на КАЖДОМ шаге, но
   неизменен → кандидат на prompt-cache (когда он появится, §5.2 — caveat: кэша пока НЕТ).
2. **Тело читается по матчу.** Агент сам делает `read_file {"path":".omnia/skills/security-rls.md"}`
   когда видит задачу про данные. Это РЕАЛЬНОЕ действие цикла (read_file уже исполняется,
   agent_builder.py:727; тело режется до `_MAX_READ_CHARS = 16_000`, agent_builder.py:84),
   наблюдение возвращается как следующий ход — точно как progressive disclosure, но через существующий
   инструмент.

**Стоимость тела — НЕ «обнуляется окном» (резолв P0-2).** Важная честная поправка: окно
`_window_messages` (agent_builder.py:153) держит `head = 2` ВСЕГДА (agent_builder.py:156 — «system +
first user»). Read_file-тело попадает в СЕРЕДИНУ диалога (как observation после tool-call), не в head
→ да, оно выбрасывается окном при отъезде за горизонт, и его стоимость в длинном цикле затухает.
Это контрастирует с §2.4-preload (ниже), где тело кладётся в первый user-турн = head = НИКОГДА не
выбрасывается. Два механизма имеют ПРОТИВОПОЛОЖНЫЙ cost-профиль; см. §2.4 — выбор между ними сознательный.

**Caveat жёстко в плане:** триггер вероятностный (модель может НЕ прочитать skill). Поэтому security/
a11y/perf-skills — это уровень 5, а ИХ ГАРАНТИЯ — уровень 4 (gate, §3). Skill улучшает первый
черновик, gate ловит промах. Никогда не полагаться на skill как на единственную защиту.

### 2.4 task→skill селектор (ГИПОТЕЗА под A/B — резолв P1-1)

> **Статус: непроверенная гипотеза, не базовый механизм.** Точность keyword-роутинга НЕ измерена;
> это ровно тот класс «правил из интуиции», который killed-finding по Cursor-rules объявил
> недоказанным. K1 поставляется БЕЗ селектора (только INDEX+read_file, §2.3). Селектор — отдельный
> опытный слой K1b, чья ценность ПОДТВЕРЖДАЕТСЯ или ОТКЛОНЯЕТСЯ A/B (§6.1) по precision/recall матча.

Идея: не заставлять агента угадывать релевантные skills вслепую — у Omnia уже есть
discovery-классификатор (`discovery.py`, мастер-план §1: `_infer_stack_from_text`/
`_infer_realtime_from_text`). Расширить его дешёвой keyword/category-разметкой («оплата/деньги/каталог»
→ money-integrity; «чат/сообщения/доступ ролей» → security-rls; «форма/запись» → a11y-forms) и для
ПРЕДСКАЗУЕМО-релевантных skills предзагрузить тело.

**Куда предзагружать — сознательный cost-выбор (резолв P0-2):**
- **Вариант A (дорогой, persistent): preload в `_seed_block`.** `_seed_block` уходит в ПЕРВЫЙ
  user-турн (messages.py:2356/2372/2387, и в heal — 2512). Первый user-турн = `head` окна → тело
  НИКОГДА не выбрасывается → платится на КАЖДОМ шаге всего цикла (× число шагов). Допустимо ТОЛЬКО
  для 1-2 коротких, заведомо-критичных skill'ов (напр. money-integrity на shop-брифе), и только
  пока нет prompt-cache (§5.2).
- **Вариант B (дешёвый, on-match): только read_file по матчу.** Селектор НЕ кладёт тело в seed, а
  лишь повышает приоритет соответствующих строк INDEX / добавляет явную директиву «прочитай
  security-rls.md ПЕРВЫМ ходом». Тело тогда живёт в середине окна и затухает (§2.3).

**Рекомендация:** дефолт — вариант B; вариант A — точечно и только под измеренный выигрыш A/B.
Не путать профили: preload в seed_block ≠ on-match read; они противоположны по стоимости.

### 2.5 5-типовая таксономия содержимого skill ([F7] arXiv 2512.18925)

Эмпирика 401 OSS-репо с Cursor-rules: разработчики инжектят 5 типов контекста — Guidelines (89%),
Project Information (85%), Conventions (84%), LLM Directives (50%), Examples (50%). Шаблон каждого
`.omnia/skills/*.md`:
- **Guidelines** — что делать (RLS на каждой таблице; деньги min:0).
- **Project Information** — что УЖЕ есть в скелете (`requireUser()` в session.ts; `<CrudResource>`).
- **Conventions** — как принято (токены дизайна, не hex; страницы под `(app)/dashboard`).
- **LLM Directives** — поведенческое («НЕ изобретай presence», «НЕ заводи сущность User»).
- **Examples** — минимальный фрагмент (вторично, см. §2b — не переоценивать).

---

## 2b. Golden code: примитивы скелета > golden examples

Лестница (§1) диктует: знание, закодированное как ФИКСИРОВАННЫЙ примитив скелета или kit-компонент,
**невозможно сделать неправильно** — это всегда сильнее, чем doc/example, объясняющий как сделать
правильно. Приоритет вложений в knowledge-слой:

1. **Сначала — поднять в примитив/kit.** Прежде чем писать skill «как безопасно скоупить запрос»,
   убедись, что `@/lib/sdk` ВСЕГДА скоупит (он скоупит) и что `scoped.ts` (RLS, мастер-план §6) —
   фиксированный нередактируемый файл. Skill тогда лишь УКАЗЫВАЕТ на примитив, а не учит писать его
   руками. Это согласуется с retire-entities-as-runtime-but-keep-DSL-as-codegen (мастер-план §2.6):
   правильность — в сгенерированном коде, не в памяти модели.
2. **Golden examples — ВТОРИЧНЫЙ, A/B-тестируемый рычаг.** «Few-shot golden beats prose» —
   правдоподобно, но НЕ доказано (killed-finding) — НЕ переоценивай. Один-два минимальных примера на
   skill (тип Examples, [F7]), и только если A/B (§6) покажет выигрыш на корпусе Omnia. Не строить
   библиотеку из 50 примеров на вере.
3. **Anti-knowledge тоже примитив.** Самые дорогие ошибки память ловит не примером, а ЗАПРЕТОМ +
   детерминированной починкой: «никогда не заводи сущность User» (SYSTEM_PROMPT nextjs-entities:65)
   сильнее как gate-проверка, чем как абзац. ⚠️ **Scope (резолв P2-1):** этот конкретный запрет
   привязан к entities-формату (`entities/User.json` + `users-admin.tsx` существуют ТОЛЬКО в
   `nextjs-entities`). Для drizzle/realtime эквивалент — gate на schema-уровне (запрет ручной
   таблицы `users` в обход `_AUTH_TABLES_BLOCK`), а не на `entities/*.json` (его там нет). На
   nextjs-entities anti-User остаётся legacy-гейтом as-is (стек на пенсии, §4).

---

## 3. Enforcement gate stack (доказанные «зубы»)

Архитектурный принцип: **переиспользовать существующий gate-feedback контур, не плодить второй гейт.**
Контур уже есть и доказан в проде:
`agent_gate_feedback.GateOutcome` (agent_gate_feedback.py:22) + `build_fix_instruction(outcomes,
attempt, max_attempts)` (agent_gate_feedback.py:42) переводят вердикт гейта в КОНКРЕТНУЮ
fix-инструкцию, скармливаемую агенту через `run_agent_build` bounded-heal (messages.py:2486-2520).
Каждый новый gate — это просто ещё один `GateOutcome` в `_outcomes`-список. Phase-2
`require_green_before_done` (agent_builder.py:219, проверка 342-368) — финальный backstop: `done`
отклоняется (до `_DONE_REJECT_CAP` раз), пока build не зелёный + runtime_check после последнего write.

### 3.0 Чего в текущем контуре НЕТ (честная граница — резолв P1-3)

Чтобы «переиспользовать контур» не звучало как «всё уже готово», вот точная дельта между тем, что
есть, и тем, что добавляем:

- **Текущий heal-цикл гейтован на `not _is_edit`.** `while use_agent_gate_feedback and not _is_edit`
  (messages.py:2489): на правках (edit-режим) backend-gate-heal НЕ запускается вообще. Новые gates
  должны явно решить свою edit-политику — security/secret-скан осмысленен и на правке (правка может
  внести SQLi/секрет), поэтому SECURITY-gate НЕ должен наследовать `not _is_edit` слепо; a11y/perf на
  мелкой правке — скорее advisory. Это явное проектное решение, не наследование по умолчанию.
- **Единственный сегодняшний «gate» — `_check_backend(files)` (messages.py:2491) — это
  in-process Python-regex в API-процессе, НЕ бинарь в контейнере.** Он ловит raw-DB-escape по
  тексту файлов (backend_guardrail.py:44-51). Semgrep/gitleaks/Lighthouse/axe — это ПРИНЦИПИАЛЬНО
  новый путь: запуск бинаря/CLI ВНУТРИ dev-контейнера (через `bash`-инструмент или orchestrator-
  эндпоинт), парсинг его вывода в `GateOutcome`, и подключение к тому же `_outcomes`-списку и
  heal-решению. То есть переиспользуется ТОЛЬКО decision-слой (`GateOutcome` + `build_fix_instruction`
  + bounded-retry), а сбор находок — новый код на каждый инструмент.

**Все gates крутятся В контейнере = CPU, не gateway-токены — НО heal-ход стоит полный agent-прогон**
(§0.1). Поэтому стадирование: ДЕШЁВЫЕ статические gates на каждом релевантном проходе, ДОРОГИЕ
(Lighthouse, axe на живом маршруте) — ОДИН раз ближе к `done`, чтобы heal не плодил agent-прогоны на
недоделанных состояниях.

Каждый gate проходит лестницу advisory→heal→blocking: сначала только логирует (operators видят
находки даже когда heal off), потом кормит heal (bounded `agent_gate_max_attempts`), потом блокирует
ship через container-acceptance-gate (мастер-план §5).

### 3.1 SECURITY (доказанный приоритет — [F1][F2][F3][F4])

- **Инструмент:** Semgrep CE (offline с кешированными p/-rulesets, §5).
- **Команда (в контейнере, через `bash`-инструмент или orchestrator-эндпоинт):**
  `semgrep scan --config p/owasp-top-ten --config p/javascript --config p/typescript --config p/react --config p/secrets --error`
  (для fastapi-скелета: `--config p/python`). `--error` = блокирующий флаг (exit 0 ok / 1 findings / 2 error).
- **Целевые CWE из [F4]** (4 из 5 в MITRE Top-25): CWE-89 SQLi, CWE-78 OS-cmd-inj, CWE-94 code-inj,
  CWE-798/259 hard-coded creds. Numeric-пороги MITRE Top25 пере-верифицировать перед заморозкой (caveat (d)).
- **Secret-scanner:** gitleaks (отдельный проход, offline) поверх `p/secrets` — defense-in-depth по
  hard-coded creds (caveat (b)).
- **Обобщить существующий raw-DB scan.** backend_guardrail.py УЖЕ статически ловит `@/lib/db`/
  `drizzle-orm`/`pg` вне движка (backend_guardrail.py:44-51) — это узкий Omnia-специфичный SAST.
  Semgrep — его общее расширение на весь OWASP. Оставить backend_guardrail как Omnia-правило
  (access-model), добавить Semgrep как общий слой; оба → `GateOutcome` в один список.
- **Edit-политика:** в отличие от текущего backend-heal (`not _is_edit`, §3.0), SECURITY-скан гоняется
  И на правках (правка вносит уязвимость не реже первой генерации).
- **Флаг:** `USE_SECURITY_GATE` (default False → advisory-log → heal → blocking).

### 3.2 A11Y

- **Статически (дёшево, каждый проход):** eslint-plugin-jsx-a11y `flatConfigs.strict` (30+ WCAG/
  WAI-ARIA правил как errors) в `bash {"cmd":"pnpm lint"}`. Многие правила уже покрыты тем, что UI =
  kit-компоненты (уровень 3) — gate ловит ручной JSX в обход кита.
- **Динамически (дорого, один раз у `done`):** axe-core против ЖИВОГО маршрута. ⚠️ **Резолв P2-3:
  axe НЕ может ехать на `see`-инструменте.** `see` (agent_builder.py:828 → `agent_vision.see_page`) —
  это screenshot→vision-путь, и он СПЕЦИАЛЬНО fail-soft без Playwright-зависимости (нет
  инжектируемого browser-context, в который можно вставить axe-core JS). Поэтому axe — это ОТДЕЛЬНЫЙ
  новый путь: либо новый orchestrator-эндпоинт, гоняющий headless-браузер с `@axe-core/playwright`
  против host-порта живого dev-контейнера, либо axe-CLI в контейнере. Запускать ОДИН раз на главный
  маршрут после зелёного build. Пометить как требующее реализации, НЕ переиспользования `see`.
- **Флаг:** `USE_A11Y_GATE` (default False).

### 3.3 PERF

- **Инструмент:** Lighthouse CI на ТЁПЛОМ preview-контейнере (не холодном — известный артефакт
  замера, память `omnia_orchestrator_forensics_bad_design`: капча судила недогруженную страницу).
- **Команда:** `lhci assert --budgetsFile=./budgets.json` с boolean pass/fail-порогами [F10]:
  LCP≤2.5s, CLS≤0.1, performanceScore≥90 → overallPassed. Пороги пере-верифицировать (caveat (d)).
- **Стадия:** ОДИН раз ближе к `done` (дорого по heal). Vision-судья (`see`) остаётся advisory, не
  блокирующим (мастер-план §5: `acceptance_vision_block_enabled` отдельный) — perf-gate судит
  машинные метрики, не перцепцию.
- **Флаг:** `USE_PERF_GATE` (default False).

### 3.4 TYPES/BUILD — уже есть

`build`-инструмент (agent_builder.py:771 → orchestrator agent_build) = реальный typecheck/compile;
`require_green_before_done` (agent_builder.py:342-368) делает его обязательным перед done. Ничего не строить.

### 3.5 USABILITY (zero-dead-ends на контейнерах)

- **Что есть:** `find_dead_links`/`repair_dead_links_inline` (импортируются messages.py:105, реальный
  статический проход на сгенерённых файлах — messages.py:4003-4060) работают ТОЛЬКО на статике;
  контейнерные React-аппы их ЯВНО ПРОПУСКАЮТ — messages.py:1550-1551 («они рендерят React (не
  статический index.html), поэтому пропускают static-only-гарды: dead-link repair, omnia-kit CSS/JS»).
- **Изменение:** портировать dead-link/handler-scan на контейнер — статический проход по written-
  файлам (как backend_guardrail, без рендера): `<Link href>` указывает на маршрут, который агент
  создал; кнопки имеют handler; нет `href="#"`. Скормить как `GateOutcome` в тот же heal-контур.
- **Флаг:** `USE_CONTAINER_USABILITY_GATE` (default False).

---

## 4. Per-stack маппинг: какие skills + какие gates на скелет

| Скелет (`templates/`) | Skills (`.omnia/skills/`) | Gates |
|---|---|---|
| **nextjs-postgres-drizzle** (ts_unified, дефолт после Phase 3) | security-rls, a11y-forms, perf-images, money-integrity, auth-floor | Semgrep p/owasp+p/ts+p/react+p/secrets, gitleaks, jsx-a11y strict + axe, Lighthouse, backend_guardrail (raw-DB), schema-level no-`users`-table gate, dead-link/handler, build/typecheck |
| **nextjs-realtime** (acceptance-веха) | + realtime-presence (Redis TTL+pubsub), members-acl (доступ только членам канала) | всё из ts_unified + ws-check примитив (мастер-план §4) как functional `GateOutcome` |
| **fastapi-postgres** (Python lane) | security-py (Bandit-таргеты CWE-89/78/94), perf-api (a11y неприменим — API) | Semgrep `--config p/python` + Bandit, gitleaks, build (mypy/ruff), без a11y/Lighthouse (нет UI) |
| **vite-react-spa** (no-backend) | a11y-forms, perf-images | jsx-a11y strict + axe, Lighthouse, dead-link; без security-DB (нет backend), без RLS |
| **nextjs-entities** (на пенсию, живые не трогаем) | существующий SYSTEM_PROMPT.md as-is | существующий backend_guardrail + container-gate + legacy anti-`entities/User.json` запрет; НЕ навешивать новые gates на legacy (мастер-план §2.5) |
| **telegram-bot-aiogram** | security-py (token в env, не в коде) | gitleaks (бот-токен!), Semgrep p/python |

Skills и gates ПАРНЫ: на каждый критичный skill (уровень 5) есть дублирующий gate (уровень 4) —
это и есть «нельзя полагаться на вероятностный триггер» в действии.

---

## 5. Freshness + бюджет контекста + RU-специфика

### 5.1 Version-pin к залоченным зависимостям скелета

Doc/skill, описывающий API библиотеки, ДОЛЖЕН соответствовать версии в `package.json`/`requirements`
скелета (Next.js 15, React 19, Drizzle, Tailwind v4 — стек зафиксирован, CLAUDE.md). Skill-файл несёт
в frontmatter `pinned: { next: "15.x", tailwind: "4.x" }`. **Drift-guard** (паттерн уже применяется в
проекте — память `omnia_p2_client_errors`: «drift-guard×3» на инспекторе): CI-проверка, что версии в
`.omnia/skills/*.md` совпадают с lock-файлом скелета; рассинхрон → fail. Это закрывает риск
«документация устарела относительно кода» (рекуррентная боль: stale template-копии,
`omnia_ssh_deploy_alias` — «template CODE fix = REBUILD the image»).

### 5.2 Бюджет контекста

- **Индекс всегда в head:** ~300 токенов (§2.3), попадает в system-сообщение = head окна = платится
  на каждом шаге, но неизменен.
- **Тело skill через read_file** — в середине окна → выбрасывается `_window_messages`
  (agent_builder.py:153, `head = 2`) при отъезде за горизонт → стоимость затухает в длинном цикле.
- **Тело skill через §2.4-preload в `_seed_block`** — в head (первый user-турн) → НЕ выбрасывается →
  платится каждый шаг (× размер тела). Поэтому preload только для 1-2 коротких критичных skill'ов.
- **Конституция (K0, §6): подрезать.** Сейчас LOOP_PROTOCOL + per-stack SYSTEM_PROMPT (~190 строк
  для entities) — already-in-context. Не раздувать always-loaded прозой (уровень 6 — самый слабый);
  всё, что можно — вниз в skill-файлы (читаются по требованию) или вверх в gate.
- **Prompt-cache — пока НЕ реализован (резолв P2-2).** Память (`omnia_generation_cost_economics`):
  «кэша НЕТ». LOOP_PROTOCOL + stack-guide + INDEX неизменны в рамках билда (мастер-план §3.4) →
  ИДЕАЛЬНЫЙ кандидат на prompt-cache, но это БУДУЩАЯ работа, не данность. До его внедрения каждый
  always-in-context токен (включая INDEX и любой preload в head) платится полной ценой на каждом
  шаге — что УСИЛИВАЕТ аргумент §2.4 «дефолт = on-match read, не preload в seed».

### 5.3 RU-специфика и offline

- **Gates offline-capable, вписываются в Phase-1 egress-allowlist** (мастер-план §8: egress открыт
  сейчас, Phase-1 закрывает до npm/pip+gateway). Semgrep CE работает offline с кешированными
  p/-rulesets (вшить в dev-образ скелета при сборке, как anime.min.js — KIT_FILES); gitleaks —
  бинарь без сети; Lighthouse/axe бьют локальный preview, не интернет. Ни один gate НЕ требует egress
  за пределы контейнера → совместим с sandbox-hardening.
- **Char-billed gateway** (vsegpt биллит по символам, память `omnia_generation_cost_economics`):
  сами проверки токен-free (CPU в контейнере / regex в API-процессе), но каждый HEAL-ход = полный
  agent-прогон (§0.1, messages.py:2510 → `run_agent_build`). Управление стоимостью — НЕ «heal дешевле
  re-write» (это неверно), а bounded (`agent_gate_max_attempts`=2) + staged (дорогие гейты у `done`)
  + advisory-дефолт. Это структурно сдерживает число дорогих прогонов, а не делает heal бесплатным.
- Skills/контент — RU-проза в теле (как SYSTEM_PROMPT.md уже двуязычен), идентификаторы английские.

---

## 6. Честные пределы, A/B-тест и фазовая дорожная карта

### 6.1 Открытый вопрос, который не закрыл ни один источник — и операбельный A/B (резолв P1-4)

**Помогают ли skills на корпусе Omnia?** Эффективность knowledge-rules не доказана (killed-finding).
Поэтому встроенный **A/B как часть воркстрима, не «потом»**, с ЖЁСТКИМ протоколом (иначе стохастика
генерации сделает результат нечитаемым):

**Дизайн прогона:**
- **Корпус:** ≥8 фиксированных брифов из реальных типов памяти (CRM, магазин, мессенджер, блог,
  клиника, портфолио, лендинг, booking).
- **Повторность:** N≥5 прогонов на (бриф × режим) — генерация стохастична, один прогон ничего не значит.
- **Режимы:**
  - **(A) skills OFF, gate OFF** — baseline.
  - **(B) skills ON, gate OFF** — изолирует вклад knowledge-половины.
  - **(C) skills OFF, gate ON** — изолирует вклад enforcement-половины.
  - **(B1b) skills+селектор ON, gate OFF** — изолирует вклад task→skill-селектора (§2.4) поверх B.
  - (опц. D) обе ON — продакшн-цель.

**Метрики (на собственном корпусе):** % first-gen с зелёным build; # Semgrep-findings/билд;
# dead-links; # heal-ходов до зелёного; Lighthouse-score; **+ cost-столбец: # agent-прогонов и
символы gateway на билд** (heal не бесплатен, §0.1 — без cost-метрики решение слепое).

**Правило-порог (операбельное, не «B≈A»):**
- Skills (B) ОСТАВЛЯЕМ, только если по ≥2 метрикам качества (% зелёных build И # Semgrep-findings)
  улучшение статистически отделимо — медианы B и A НЕ перекрываются по IQR — И при этом cost-столбец
  не вырос непропорционально.
- Если IQR(B) перекрывает IQR(A) по обеим метрикам → skills не оправдывают бюджет контекста →
  оставляем только gates (C).
- Селектор (B1b) оставляем по тому же правилу против B (а не против A): precision/recall матча
  должны давать измеримый прирост поверх голого INDEX, иначе селектор режется как недоказанный.

Это прямое исполнение killed-finding «require A/B validation».

### 6.2 Честные пределы (caveats запечены)

- (a) Skill-автотриггер вероятностный → критичные каноны ДУБЛИРОВАНЫ gate (§3, §4).
- (b) Gate «enforces only what's in policy» ([F8]) → необходим, не достаточен → defense-in-depth
  (SAST+secret+perf+a11y+usability, не один инструмент).
- (c) Нет нативных Skills → эмуляция через INDEX+read_file, без вектор-БД (§2.3).
- (d) Numeric-пороги (MITRE Top25, perf-budgets) пере-верифицировать перед заморозкой.
- (e) **Heal не бесплатен** — каждая попытка = полный agent-прогон (§0.1); управляется bounded+staged,
  не «дешевизной».
- (f) **task→skill-селектор — гипотеза** (§2.4), поставляется как K1b под собственный A/B-branch.
- Skills сами по себе — НЕдоказанный рычаг; gates — доказанный. План не оверселлит knowledge.

### 6.3 Фазы K0..K4 (аддитивно, dark, под флагом; помечено, что даёт ценность СЕГОДНЯШНИМ аппам)

Все фазы — exit-критерий = E2E на живом provisioned-контейнере (паттерн мастер-плана §10), не unit.
Knowledge-слой ОРТОГОНАЛЕН фазам мастер-плана (стекам/sandbox) — встаёт поверх существующего
gate-feedback контура, который уже в проде.

- **K0 — Конституция-трим (1 проход, ценность сегодня).** Подрезать always-in-context прозу;
  вынести 10-правил-ядро (по образцу owner CLAUDE.md codex) в компактный блок LOOP_PROTOCOL; всё
  остальное — кандидаты в skill-файлы. Без новых флагов (улучшение существующего промпта).
  Exit: промпт не длиннее, первый билд не хуже на baseline-корпусе.
- **K1 — Skills-as-files (`USE_SKILL_INDEX`, ценность сегодня для entities/drizzle).** Создать
  `.omnia/skills/` + INDEX-генератор + `load_skill_index` + расширить `build_system_prompt`
  (agent_builder.py:685) третьим аргументом-индексом. 4-6 skill-файлов на дефолтный скелет по
  таксономии [F7]. БЕЗ селектора (on-match read_file, §2.3). Exit: агент читает skill по матчу
  (видно в transcript), A/B-режим B измерим.
- **K1b — task→skill селектор (опытный, гипотеза §2.4).** Расширить `discovery.py` keyword-разметкой;
  дефолт-вариант B (приоритизация INDEX/директива), preload в seed_block только точечно. Exit:
  precision/recall матча измерены; A/B-режим B1b даёт измеримый прирост над B — иначе режется.
- **K2 — Golden-примитивы (ценность сегодня).** Поднять самые частые ошибки из памяти в примитивы/
  запреты (User-entity-запрет: на entities — legacy-гейт `entities/User.json`, на drizzle —
  schema-level no-`users`-table gate, §2b/§4; money min:0 как schema-floor — уже частично, PravVyd
  batch). 1-2 golden-example на skill ТОЛЬКО если K-A/B покажет выигрыш. Exit: топ-3 рекуррентных
  дефекта памяти не воспроизводятся.
- **K3 — Gate-stack (`USE_SECURITY_GATE`/`USE_A11Y_GATE`/`USE_PERF_GATE`/`USE_CONTAINER_USABILITY_GATE`,
  доказанная половина — главная ценность).** Вшить Semgrep+gitleaks+jsx-a11y+Lighthouse+axe offline в
  dev-образы; каждый → новый сбор находок → `GateOutcome` в существующий decision-слой (§3.0; точка
  подключения heal — messages.py:2486-2520). Стадировать (дёшево каждый проход / дорого у done).
  advisory→heal→blocking; явная edit-политика на каждый гейт (§3.0: security гоняется и на edit).
  Зависит от мастер-план Phase 2 (container-acceptance-gate) для blocking-ступени; advisory/heal
  работают СЕГОДНЯ на контейнерных аппах. Exit: на корпусе Semgrep-findings и dead-links → ~0 после
  heal, build зелёный, # agent-прогонов в пределах bounded-лимита.
- **K4 — Freshness + drift-guard (надёжность во времени).** version-pin frontmatter + CI drift-check
  (§5.1) + per-stack маппинг (§4) закреплён. Exit: рассинхрон версии skill↔lock ломает CI; offline-
  прогон всех gates без egress зелёный (готовность к Phase-1 sandbox).

**Зависимости.** K0/K1/K2 ценны на СЕГОДНЯШНИХ контейнерных аппах (entities/drizzle) немедленно —
они улучшают вход агента и не требуют новых стеков. K1b — опытная надстройка над K1, режется первой
при отрицательном A/B. K3 advisory/heal — тоже сегодня; K3 blocking ждёт container-acceptance-gate
(мастер-план Phase 2). K4 готовит почву под sandbox (мастер-план Phase 1). Нельзя резать: K3
(доказанная enforcement-половина) — это и есть гарантия «безопасно по умолчанию». Можно резать
первыми: K1b (селектор) и K2 golden-examples — если A/B не подтвердит, оставить только
INDEX+read_file и примитивы/запреты.
