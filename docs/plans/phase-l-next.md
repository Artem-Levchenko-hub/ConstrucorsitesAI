# Phase L4–L7 — plan for the next session

> Continuation of `~/.claude/plans/validated-humming-pebble.md`.
> Phase L1–L3 уже задеплоено (HEAD `36ccded`). Каталог + IR + рендерер +
> lean-prompt branch — всё на VPS, под флагом `USE_SECTION_CATALOG=false`.

---

## Pre-flight (5 минут, перед началом работы)

1. **Flip flag on prod, прогнать 1 промпт с Opus, собрать baseline:**
   ```bash
   ssh i48ptgvnis@170.168.72.200
   cd /opt/omnia/apps/llm-gateway/deploy/full
   grep -q USE_SECTION_CATALOG .env || echo "USE_SECTION_CATALOG=true" >> .env
   docker compose -p full restart api
   docker logs -f omnia-prod-api | grep -E '\[PP\] (catalog_ir|stream_complete)'
   ```
   Затем в браузере на `https://constructor.lead-generator.ru` — создать pet-проект, выбрать Opus, отправить промпт «лендинг для кофейни в Москве», смотреть лог:
   - ✅ `[PP] catalog_ir_ok sections=N html_len=N` — IR-путь работает, идём дальше
   - ⚠️ `[PP] catalog_ir_fail err=...` — модель не следует JSON-only инструкции, нужен fix в `_CATALOG_SYSTEM_PROMPT` (усилить выходной формат) перед L4+

2. **Если catalog_ir_fail повторяется** — это первая задача сессии:
   - Усилить `_OUTPUT_FORMAT` блок в `_CATALOG_SYSTEM_PROMPT` (`prompt_builder.py`)
   - Добавить few-shot пример валидного PageIR JSON в систему
   - Возможно нужен `response_format={"type": "json_object"}` через `llm_client.py` (если gateway поддерживает Anthropic structured-output API)

---

## L4 — Audit-driven retry loop (1 retry max)

**Цель:** при низком `ui_audit` score автоматически re-prompt модель с конкретными failures.

**Файлы:**
- `apps/api/src/omnia_api/services/ui_audit.py`:
  - Добавить `format_failures_for_retry(report: AuditReport) -> str` — превращает `per_check` fail-list в человекочитаемый русский bullet-список для подачи модели.
- `apps/api/src/omnia_api/routers/messages.py::_process_prompt`:
  - После `_extract_files_and_edits` + `ui_audit` хука (он уже считает score и льёт в WS — найти существующее место).
  - Если `score < 70` **И** `retry_count < 1` **И** `use_section_catalog=True`:
    - Собрать retry-сообщение: «Предыдущий ответ score=N. Failures: ...». Это user message.
    - Один новый `stream_chat_completion` с теми же base messages + retry_message в конце.
    - Re-parse как PageIR, re-render, перезаписать `accumulated`.
    - WS event `llm.retry` чтобы UI показал «улучшаем».
  - Cap = 1 retry (никакого while-loop).

**Тесты:**
- `apps/api/tests/test_retry_loop.py`:
  - Mock `stream_chat_completion` чтобы первый вызов вернул IR со score<70, второй — IR со score≥70.
  - Assert retry триггерится ровно 1 раз.
  - Assert при первом-же score≥70 retry не триггерится.
  - Assert retry НЕ триггерится когда `use_section_catalog=False`.

**Risk:** retry дублирует latency. Решение: показывать UI «улучшаем» через WS, чтобы юзер видел прогресс.

---

## L5 — RAG awwwards reference (top-1)

**Цель:** инжектить в lean prompt 1 reference IR-snippet, похожий на текущий запрос. Качество Opus ↑ через few-shot.

**Подготовка:**
- Inventory: где лежит awwwards corpus сейчас?
  ```bash
  cd "D:/Бизнес план/Конструктор/ConstrucorsitesAI"
  find apps/api -path '*awwwards*' -type f | head -20
  find apps/api -path '*corpus*' -type f | head -20
  ```
- Конвертировать corpus в IR-snippets. Если хранится как HTML — нужен скрипт `scripts/build_corpus_ir.py` который парсит HTML → приблизительный PageIR. ИЛИ если уже JSON IR — просто read-load.

**Файлы:**
- `apps/api/src/omnia_api/services/rag.py` (новый, ~120 LOC):
  - `class CorpusIndex` — singleton с in-memory `np.ndarray` embeddings + список IR-snippets.
  - `def embed(text: str) -> np.ndarray` — через LLM gateway embedding endpoint (если есть Voyage/Yandex) ИЛИ через `sentence-transformers` локально (`paraphrase-multilingual-MiniLM-L12-v2`, 470MB — добавить в Dockerfile).
  - `def top1(query: str, industry_hint: str | None = None) -> str | None` — cosine similarity, возвращает JSON-строку IR-snippet или None если порог ниже 0.5.
  - Build index lazily at first call (или при startup в `main.py`).
- `apps/api/src/omnia_api/services/prompt_builder.py::_build_catalog_system_prompt`:
  - После `header` (preset + ux_brief) добавить `_RAG_REFERENCE` блок если `rag.top1(user_prompt)` вернул не None.
- `apps/api/pyproject.toml`:
  - Добавить `sentence-transformers>=3.0,<4.0` ИЛИ `voyageai>=0.2,<1.0` (что проще интегрируется).
- Redis cache: `top1(industry, vibe)` → JSON string, TTL 24h. 95% запросов hit cache, не embedding.

**Тесты:**
- `apps/api/tests/test_rag.py`:
  - Mock corpus с 3 snippets разных vibes.
  - Query «SaaS B2B» → top1 возвращает swiss-minimal snippet.
  - Query «ресторан» → возвращает food snippet.
  - Empty corpus → top1 возвращает None, не падает.

**Risk:** sentence-transformers — 470MB бинарь, увеличит docker image. Альтернатива: использовать gateway embedding API (если есть) — нулевой image overhead.

---

## L6 — Lean prompt expansion (vibes + palette enum)

**Цель:** довести lean prompt до полного покрытия — vibes как enum (не описание), palette tail-anchor, RAG inject. Сократить до 3K токенов.

**Файлы:**
- `apps/api/src/omnia_api/services/prompt_builder.py`:
  - Перенести `_CATALOG_SYSTEM_PROMPT` константу в отдельный модуль `services/lean_prompt.py` (чтобы prompt_builder.py перестал быть монолитом 2200+ строк).
  - Добавить `_VIBE_TOKENS` enum: 8 vibes как однострочные spec'ы:
    ```
    swiss-minimal: #0F172A primary; Space Grotesk display; max-w-5xl; py-24; rounded-lg; motion:reveal+fade-up
    brutalist: #000000 primary; #FF6B35 accent; Inter display; max-w-6xl; py-32; rounded-none; motion:none
    ...
    ```
  - Добавить `_PALETTE_TAIL` блок в самый конец prompt'а (антиlost-in-middle).
- `apps/api/src/omnia_api/services/preset_classifier.py`:
  - Если ещё нет — добавить `vibe_for_industry(industry: str) -> str` — возвращает один из 8 vibes.

**Тесты:**
- `apps/api/tests/test_lean_prompt.py`:
  - Assert `_build_catalog_system_prompt(...)` ≤ 3500 tokens (через `tiktoken` или `len/4`).
  - Assert содержит все 8 vibe names.
  - Assert palette HEX появляется в последних 500 chars (anti lost-in-middle).

---

## L7 — Director→Polish 2-pass (OPTIONAL — только если L4+L5+L6 недостаточно)

**Когда подключать:** только если в golden eval (10 specs) средний score после L4-L6 остаётся <80 на Opus.

**Файлы:**
- `apps/api/src/omnia_api/services/director_polish.py` (новый, ~180 LOC):
  - `pass_director(prompt, base_msgs) -> PageIR` — короткий IR со structure + tokens, headlines плейсхолдерами `<HEADLINE>`.
  - `pass_polish(ir: PageIR, prompt: str) -> PageIR` — берёт director IR + user prompt → IR с реальным content.
- `core/config.py`: `use_director_polish: bool = False`.
- `services/prompt_builder.py`: если flag on И tier=premium → routing через director+polish.

**Тесты:**
- `apps/api/tests/test_director_polish.py`:
  - Mock LLM с детерминистическими ответами на каждый pass.
  - Assert director IR содержит placeholders.
  - Assert polish IR содержит реальный content (no placeholders).

---

## Golden eval — критерий приёмки L4-L6

**Откуда брать:** уже есть 10 golden specs (`apps/api/tests/golden_specs/` или похожее место — найти в начале сессии).

**Скрипт:** `scripts/eval_lean_vs_freeform.py`:
- На каждом golden:
  1. Run Opus с `USE_SECTION_CATALOG=false` → собрать HTML, прогнать через `ui_audit`, записать score + token usage.
  2. Run Opus с `USE_SECTION_CATALOG=true` → то же.
- Csv: `golden_id, freeform_score, freeform_tokens, lean_score, lean_tokens, delta_score, delta_tokens`.

**Acceptance после L4-L6:**
- avg `delta_score` ≥ 0 (lean не хуже freeform)
- avg `delta_tokens` ≤ -60% (lean экономит ≥60% токенов)
- worst-case `delta_score` ≥ -5 (никаких катастрофических регрессий)

Если ниже порога → debug `_CATALOG_SYSTEM_PROMPT` или подключить L7.

---

## Production rollout sequence

После завершения L4-L6 + golden eval pass:

1. Deploy → flag всё ещё OFF.
2. SSH + `USE_SECTION_CATALOG=true` для одного pet-проекта (через override env конкретного docker exec, НЕ глобально).
3. 24h наблюдение в logs:
   - `grep '\[PP\] catalog_ir_fail'` — частота fail. Acceptance: <5%.
   - `grep '\[PP\] retry_triggered'` — частота retry. Acceptance: <30% (если выше — проблема с initial generation).
4. Global flip — `USE_SECTION_CATALOG=true` в `.env`, restart api.
5. Канареечный rollout: сначала только Opus tier, потом Sonnet, потом GPT-5. Haiku/Nano НЕ включать (multipass для них уже работает).

---

## Critical files map (для агента следующей сессии)

```
apps/api/src/omnia_api/
├── core/
│   └── config.py                    — USE_SECTION_CATALOG flag (есть)
├── routers/
│   └── messages.py                  — _process_prompt, IR→HTML branch (есть), retry hook (НЕТ)
├── sections/                        — Phase L1+L2 (есть)
│   ├── ir.py                        — PageIR + 16 Pydantic variants
│   ├── catalog.py                   — REGISTRY + CATALOG_BLURB
│   ├── renderer.py                  — render_page() + render_to_files()
│   └── templates/                   — 16 Jinja templates
├── services/
│   ├── prompt_builder.py            — _build_catalog_messages (есть), _VIBE_TOKENS (НЕТ)
│   ├── lean_prompt.py               — НЕТ (создать в L6, extract _CATALOG_SYSTEM_PROMPT)
│   ├── rag.py                       — НЕТ (создать в L5)
│   ├── director_polish.py           — НЕТ (создать в L7 если нужно)
│   ├── ui_audit.py                  — есть, добавить format_failures_for_retry в L4
│   ├── design_presets.py            — PRESETS dict (есть, reuse)
│   └── preset_classifier.py         — classify_preset (есть), vibe_for_industry (НЕТ)
└── tests/
    ├── test_sections.py             — 40 tests Phase L1+L2 (есть)
    ├── test_retry_loop.py           — НЕТ (L4)
    ├── test_rag.py                  — НЕТ (L5)
    ├── test_lean_prompt.py          — НЕТ (L6)
    └── test_director_polish.py      — НЕТ (L7 опционально)
```

---

## Commit/deploy contract

Каждый шаг = отдельный atomic commit:
- L4 → `feat(api): Phase L4 — audit-driven retry loop`
- L5 → `feat(api): Phase L5 — RAG awwwards reference (top-1)`
- L6 → `feat(api): Phase L6 — lean prompt vibes enum + palette tail`
- L7 → `feat(api): Phase L7 — director→polish 2-pass (premium-only fallback)`

Между шагами — push + VPS pull + restart + manual smoke с 1 промптом.

---

## Запуск следующей сессии

```bash
# В новом claude-инстансе, в корне репо:
claude
```

Первое сообщение юзера:
> «Прочитай `docs/plans/phase-l-next.md` и продолжай с Pre-flight шага. Если IR-путь работает на Opus — иди в L4. Если падает — fix prompt сначала.»
