---
title: "Daily Summary 2026-05-19"
tags: [corporate-messanger, daily, fallback]
sources:
  - daily/2026-05-19.md
created: 2026-05-26
updated: 2026-05-26
---

# Daily Summary 2026-05-19

Compiled fallback summary for 2026-05-19.md

## Key Points

- `services/pricing.py` — добавил `gemini-2.5-pro` ($1.25/$10 per 1M tokens → 0.15/1.20 ₽ per 1k с FX×100 + markup 20%) и `gemini-2.5-flash` (0.04/0.30 ₽). Context window — 1_000_000. `_MODEL_META` отмечает `recommended_for` (`quality` / `fast,budget`).
- `services/litellm_router.py`:
- `_LITELLM_MODEL_SLUG`: `gemini-2.5-pro` → `gemini/gemini-2.5-pro`, `gemini-2.5-flash` → `gemini/gemini-2.5-flash`
- `_api_key_for()` обрабатывает `gemini/` префикс, читает `settings.gemini_api_key`
- `_FALLBACKS`: `pro → flash → claude-haiku-4-5` (отказался от `gpt-5-mini` как fallback'а — в проде нет OpenAI-ключа, цепочка ломалась 503)
- `core/config.py` — новое поле `gemini_api_key: SecretStr`
- `routers/models.py` — `_PROVIDER_KEY_PRESENT["google"] = lambda s: _has(s.gemini_api_key)` — публичный `/v1/models` корректно отдаёт `available: true/false`
- `.env.example` — задокументировал `GEMINI_API_KEY` со ссылкой `aistudio.google.com/apikey`

## Details

# Daily — 2026-05-19

## Sessions

### Session: Gemini integration + UK proxy for RU geo-block + api crash-loop fix + workspace UX polish

**Контекст:** работа шла двумя длинными агент-сессиями (18-19 мая) над живым продом `constructor.lead-generator.ru`. Целью было подключить Google Gemini 2.5 (Pro + Flash) к LLM Gateway и сделать так, чтобы Gemini реально отвечала на сайте, несмотря на geo-block Google для RU IP.

#### Хронология

1. **Gemini в LLM Gateway (gateway-only patch, 7 файлов)**
   - `services/pricing.py` — добавил `gemini-2.5-pro` ($1.25/$10 per 1M tokens → 0.15/1.20 ₽ per 1k с FX×100 + markup 20%) и `gemini-2.5-flash` (0.04/0.30 ₽). Context window — 1_000_000. `_MODEL_META` отмечает `recommended_for` (`quality` / `fast,budget`).
   - `services/litellm_router.py`:
     - `_LITELLM_MODEL_SLUG`: `gemini-2.5-pro` → `gemini/gemini-2.5-pro`, `gemini-2.5-flash` → `gemini/gemini-2.5-flash`
     - `_api_key_for()` обрабатывает `gemini/` префикс, читает `settings.gemini_api_key`
     - `_FALLBACKS`: `pro → flash → claude-haiku-4-5` (отказался от `gpt-5-mini` как fallback'а — в проде нет OpenAI-ключа, цепочка ломалась 503)
   - `core/config.py` — новое поле `gemini_api_key: SecretStr`
   - `routers/models.py` — `_PROVIDER_KEY_PRESENT["google"] = lambda s: _has(s.gemini_api_key)` — публичный `/v1/models` корректно отдаёт `available: true/false`
   - `.env.example` — задокументировал `GEMINI_API_KEY` со ссылкой `aistudio.google.com/apikey`
   - `tests/conftest.py` + `tests/test_pricing.py` — добавил `GEMINI_API_KEY` в neutralize-список fixture-а, `google` в whitelist провайдеров

2. **Geo-block reality check**
   - Первый прод-вызов Gemini Flash вернул: `User location is not supported for the API use. — FAILED_PRECONDITION`. Google режет российские IP на уровне Generative Language API.
   - Free-tier ключа на `gemini-2.5-pro` имеет лимит `0` запросов/день (без billing-проекта). Это значит без обхода каждый Pro-запрос идёт в fallback на Flash, а Flash — в geo-block.

3. **UK прокси через NO_PROXY whitelist (per-container env, не код)**
   - Прокси-провайдер: `http://exsHhu:1ndjnT@45.153.20.222:13116` (UK, HTTPS+SOCKS5, IPv6)
   - Прокинул в gateway-сервис через `docker-compose.yml`: `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` (плюс lowercase-варианты — httpx разные парсит)
   - `NO_PROXY` whitelist: `api.proxyapi.ru, api.anthropic.com, api.openai.com, openrouter.ai, llm.api.cloud.yandex.net, gigachat.devices.sberbank.ru, ngw.devices.sberbank.ru, postgres, redis, minio, localhost, 127.0.0.1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, .local`
   - Эффект: то
...(truncated)

## Related Concepts

- [[knowledge/concepts/secondbrain-runtime]]
- [[knowledge/concepts/daily-ingestion-process]]

## Sources

- [[daily/2026-05-19.md]]
