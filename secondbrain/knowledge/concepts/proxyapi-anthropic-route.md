---
title: "proxyapi.ru — native-Anthropic route для Claude моделей"
aliases: [proxyapi-route, haiku-proxyapi]
tags: [llm-gateway, providers, proxyapi, anthropic, litellm]
sources:
  - "daily/2026-05-17.md"
created: 2026-05-17
updated: 2026-05-17
---

# proxyapi.ru — native-Anthropic route

Российский OpenAI-compatible прокси, через который роутится `claude-haiku-4-5` (и потенциально другие Claude-модели) когда у проекта нет нативного Anthropic-аккаунта. Баланс per-key, биллится в рублях на стороне proxyapi.

## Key Points

- **Два endpoint-а:** `/openai/v1` (OpenAI-совместимый — НЕ содержит Claude-моделей) и `/anthropic` (нативный Anthropic Messages API). Для Claude обязательно второй.
- **LiteLLM конфиг:** slug = `anthropic/claude-haiku-4-5`, `api_base = "https://api.proxyapi.ru/anthropic"` (без `/v1` — LiteLLM Anthropic-адаптер сам приклеит `/v1/messages`).
- **Override-механизм:** в `services/litellm_router.py` есть `_PROXY_ROUTES: dict[str, _ProxyRoute]` — модели в этом dict-е получают свой `api_key` и `api_base` вместо дефолтного per-prefix dispatch через `_api_key_for()`. Это позволяет одной анте-prefix модели использовать ДРУГОЙ ключ.
- **Availability:** `routers/models.py:_MODEL_KEY_OVERRIDE` зеркалит routes: `claude-haiku-4-5` → проверяем `PROXYAPI_API_KEY`, не `ANTHROPIC_API_KEY`. Чтобы `/v1/models` корректно показывал `available: true` без нативного Anthropic-ключа.
- **Env:** `PROXYAPI_API_KEY=sk-…` + опционально `PROXYAPI_BASE_URL` (дефолт `https://api.proxyapi.ru/anthropic`).
- **Цена:** консервативная RUB-конверсия Anthropic-прайса × 1.25 (наценка proxyapi). Для haiku — 0.15 / 0.75 ₽ за 1k input/output. Проверять против актуального прайса на proxyapi.ru.

## Details

### Почему отдельный механизм, а не очередной prefix

Существующая логика в `_api_key_for(slug)` диспатчит по префиксу:
- `anthropic/...` → `anthropic_api_key`
- `openai/...` → `openai_api_key`
- `openrouter/...` → `openrouter_api_key`

Проблема: одной модели нужен `anthropic/` префикс (чтобы LiteLLM использовал свой Anthropic-адаптер с правильным заголовком `x-api-key` и форматом запроса), но КЛЮЧ другой (proxyapi, не Anthropic). Prefix-based dispatch ломается.

Решение — `_PROXY_ROUTES`:

```python
@dataclass(frozen=True, slots=True)
class _ProxyRoute:
    api_key: Callable[[Settings], str | None]
    api_base: Callable[[Settings], str]

_PROXY_ROUTES: dict[str, _ProxyRoute] = {
    "claude-haiku-4-5": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_base_url,
    ),
}
```

В `_build_model_list()` сначала проверяется `_PROXY_ROUTES.get(omnia_id)`. Если есть — берём ключ+база оттуда. Иначе — дефолтный `_api_key_for(slug)`.

### Почему именно `/anthropic` без `/v1`

LiteLLM Anthropic-провайдер хардкодит постфикс пути. При `api_base` он:
- НЕ переписывает путь полностью
- Приклеивает `/v1/messages` к base

Поэтому корректные варианты:
- ✅ `https://api.proxyapi.ru/anthropic` → итог `https://api.proxyapi.ru/anthropic/v1/messages`
- ❌ `https://api.proxyapi.ru/anthropic/v1` → итог `https://api.proxyapi.ru/anthropic/v1/v1/messages` → 404 Not Found

Проверено эмпирически (см. daily-2026-05-17).

### Smoke-test

```python
import asyncio, litellm
async def go():
    r = await litellm.acompletion(
        model='anthropic/claude-haiku-4-5',
        messages=[{'role':'user','content':'Say PONG'}],
        max_tokens=20,
        api_key='sk-...',
        api_base='https://api.proxyapi.ru/anthropic',
    )
    print(r.model, r.choices[0].message.content)
asyncio.run(go())
```

Должен вернуть `claude-haiku-4-5-20251001` + текст.

## Related Concepts

- [[knowledge/concepts/file-extractor-pipeline]] — куда попадает ответ модели
- [[knowledge/concepts/zero-files-silent-failure]] — почему важно переключиться с GigaChat на Haiku

## Sources

- [[daily/2026-05-17.md]] — initial discovery, commit `84a8986`
