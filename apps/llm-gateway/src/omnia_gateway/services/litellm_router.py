"""Single entry point for chat completions.

Dispatches by model ID:
- Yandex / Sber / vsegpt(DeepSeek) → custom httpx wrappers in `providers.*`
  (each fronts a RU endpoint LiteLLM can't route reliably from the prod VPS).
- Everything else → LiteLLM Router (Anthropic via proxyapi, OpenAI, OpenRouter,
  Gemini).

R-01 (deep module): callers see one async function (`acompletion`) regardless
of provider. Routing, fallback config, and error translation are hidden here.
R-07: this module depends on both providers and pricing, but the chat router
depends only on this — providers stay invisible to the HTTP layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import litellm
import structlog
from litellm import Router

from omnia_gateway.core.config import Settings, get_settings
from omnia_gateway.core.errors import (
    ModelNotFoundError,
    ModelUnavailableError,
    UpstreamProviderError,
)

log = structlog.get_logger(__name__)

# Below this length we consider the response an empty cold-start artifact
# from proxyapi.ru and retry once before letting the fallback chain fire.
# 50 chars is wider than any plausible legit reply (the classifier expects
# a single preset id like "wellness-casual"); legitimate ultra-short
# answers from acompletion's only other caller (the warmup ping) are
# discarded by the caller anyway.
_MIN_NONEMPTY_RESPONSE_CHARS = 50
_EMPTY_RETRY_DELAY_S = 0.2
# Models that opt OUT of the cold-start empty-retry below. Currently empty: the
# only member (deepseek-v4-flash-thinking) moved to the direct vsegpt provider,
# which never reaches this LiteLLM path. Kept as a seam for future thinking
# models routed through the Router.
_NO_EMPTY_RETRY_MODELS: frozenset[str] = frozenset()
from omnia_gateway.providers import sber as sber_provider
from omnia_gateway.providers import yandex as yandex_provider
from omnia_gateway.services.prompt_cache import apply_anthropic_cache
from omnia_gateway.services.pricing import PRICE_TABLE

# Suppress LiteLLM's own debug printing — we use structlog for everything.
litellm.suppress_debug_info = True

# Omnia model ID → LiteLLM model slug.
# Verify slugs against provider docs before bumping; some are best-effort
# substitutes per AGENT-C-LLM-GATEWAY.md (e.g. claude-sonnet-4-6 maps to
# anthropic/claude-sonnet-4-5 until a 4.6 alias ships).
_LITELLM_MODEL_SLUG: dict[str, str] = {
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4-5",
    # Opus 4.7 via proxyapi.ru native-Anthropic endpoint (key/base in _PROXY_ROUTES
    # below). Kept only as a fallback target — the LIVE art_director model is now
    # claude-opus-4-8 via the vsegpt provider (providers/vsegpt.py), dispatched
    # before this Router. The 2026-06-02 OpenRouter detour was reverted: the key
    # on prod is a vsegpt key (sk-or-vv-), not an OpenRouter key (sk-or-v1-), and
    # OpenRouter rejected it. This route needs proxyapi.ru topped up to work.
    "claude-opus-4-7": "anthropic/claude-opus-4-5",
    # Opus 4.8 — THE live model for every role — via oneprovider.dev (native
    # Anthropic). Key/base in _PROXY_ROUTES below; dispatched here (LiteLLM) now
    # that it's removed from the vsegpt map, with streaming.py applying cache_control.
    "claude-opus-4-8": "anthropic/claude-opus-4-8",
    # Haiku 4.5 routed via proxyapi.ru native-Anthropic endpoint (the proxyapi
    # /openai/v1 surface doesn't carry Claude models — they live under
    # /anthropic/v1 in raw Anthropic Messages format). The proxy key flows in
    # through _PROXY_ROUTES below, not the default anthropic_api_key channel.
    "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
    "gpt-4.1": "openai/gpt-4o",
    "gpt-5-mini": "openai/gpt-4o-mini",
    # GPT-5 family via proxyapi.ru/openai/v1 (LiteLLM treats proxyapi as a
    # regular OpenAI endpoint when we pass `api_base`). slugs MUST exactly
    # match the model names proxyapi proxies through to OpenAI — they
    # support the real `gpt-5` and `gpt-5-nano` names verbatim.
    "gpt-5": "openai/gpt-5",
    "gpt-5-nano": "openai/gpt-5-nano",
    "qwen-3-coder": "openrouter/qwen/qwen3-coder",
    # DeepSeek via proxyapi.ru OpenAI-compatible surface. LiteLLM treats it as a
    # plain OpenAI endpoint once we pass `api_base` (declared in _PROXY_ROUTES).
    # Slugs MUST match the model names proxyapi proxies through to DeepSeek.
    "deepseek-chat": "openai/deepseek-chat",
    "deepseek-reasoner": "openai/deepseek-reasoner",
    # NOTE: deepseek-v4-flash-thinking is NOT here — it's served by the direct
    # vsegpt provider (providers/vsegpt.py), dispatched before the Router path.
    # Google Gemini via AI Studio (not Vertex AI). LiteLLM reads the key from
    # GEMINI_API_KEY or whatever is passed explicitly in the model_list below.
    # Same key works for both free and paid tier on AI Studio.
    "gemini-2.5-pro": "gemini/gemini-2.5-pro",
    "gemini-2.5-flash": "gemini/gemini-2.5-flash",
}


@dataclass(frozen=True, slots=True)
class _ProxyRoute:
    """Override for models whose key/base differ from the default per-prefix routing."""

    api_key: Callable[[Settings], str | None]
    api_base: Callable[[Settings], str]


# Models that bypass slug-prefix routing — e.g. an Anthropic model served via a
# Russian OpenAI-compatible proxy whose key lives under a different env var.
# All three entries below share the same proxyapi.ru balance: one top-up
# covers Claude Haiku, GPT-5, and GPT-5 Nano simultaneously.
_PROXY_ROUTES: dict[str, _ProxyRoute] = {
    # Opus 4.8 (the live model for EVERY role) via oneprovider.dev native Anthropic
    # endpoint — its own key/base, separate from the proxyapi.ru balance below.
    "claude-opus-4-8": _ProxyRoute(
        api_key=lambda s: s.oneprovider_api_key.get_secret_value() if s.oneprovider_api_key else None,
        api_base=lambda s: s.oneprovider_base_url,
    ),
    "claude-haiku-4-5": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_base_url,
    ),
    "claude-sonnet-4-6": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_base_url,
    ),
    # Opus 4.7 via proxyapi.ru native-Anthropic endpoint — same balance as
    # Haiku/Sonnet. Fallback target only; the live art_director Opus is 4.8 via
    # the vsegpt provider. Needs proxyapi.ru balance to be non-zero.
    "claude-opus-4-7": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_base_url,
    ),
    # DeepSeek V3 / R1 via proxyapi.ru OpenAI-compatible surface. deepseek-chat
    # is the Polish/content role's model (best ₽/quality on Russian copy).
    "deepseek-chat": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_deepseek_base_url,
    ),
    "deepseek-reasoner": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_deepseek_base_url,
    ),
    # vsegpt.ru DeepSeek is handled by the direct provider, not this Router map.
    "gpt-5": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_openai_base_url,
    ),
    "gpt-5-nano": _ProxyRoute(
        api_key=lambda s: s.proxyapi_api_key.get_secret_value() if s.proxyapi_api_key else None,
        api_base=lambda s: s.proxyapi_openai_base_url,
    ),
}

_FALLBACKS: list[dict[str, list[str]]] = [
    # All fallback chains route through providers actually configured in prod
    # (proxyapi.ru for Anthropic models, Google AI Studio for Gemini, Sber for
    # GigaChat). gpt-* keys are not set on the Serverum VPS, so any chain that
    # ends on OpenAI was producing "no healthy deployments" 503s the moment
    # the primary 4xx'd. Anthropic Haiku via proxyapi.ru is the most reliable
    # bottom-of-stack — every chain terminates there.
    {"claude-opus-4-7": ["claude-sonnet-4-6", "claude-haiku-4-5"]},
    {"claude-sonnet-4-6": ["claude-haiku-4-5"]},
    {"claude-haiku-4-5": ["gpt-5-nano", "gigachat-2-pro"]},
    # DeepSeek chains terminate on proxyapi-backed Anthropic models (reliable
    # bottom-of-stack). deepseek-chat → Haiku; deepseek-reasoner → Opus → Sonnet.
    {"deepseek-chat": ["claude-haiku-4-5"]},
    {"deepseek-reasoner": ["claude-opus-4-7", "claude-sonnet-4-6"]},
    # deepseek-v4-flash-thinking (vsegpt) bypasses the Router, so its fallback
    # lives at the app layer (_EMPTY_RESPONSE_FALLBACKS in apps/api messages.py).
    {"gpt-4.1": ["gpt-5", "claude-haiku-4-5"]},
    {"gpt-5-mini": ["gpt-5-nano", "claude-haiku-4-5"]},
    {"gpt-5": ["claude-haiku-4-5", "gpt-5-nano"]},
    {"gpt-5-nano": ["gpt-5-mini", "claude-haiku-4-5"]},
    # Gemini Pro free tier may be hard-capped to 0 on accounts without billing
    # (the API reports `free_tier_input_token_count limit: 0`); fall back to
    # Flash, which has a real free quota. If both fail, hop to claude-haiku-4-5.
    {"gemini-2.5-pro": ["gemini-2.5-flash", "claude-haiku-4-5"]},
    {"gemini-2.5-flash": ["claude-haiku-4-5"]},
]

# Reverse map for billing: when a fallback fires, response.model holds the
# LiteLLM slug, possibly with a date suffix (e.g. "gpt-4o-2024-08-06"). We
# strip prefixes/suffixes via prefix-match.
_SLUG_TO_OMNIA: dict[str, str] = {v: k for k, v in _LITELLM_MODEL_SLUG.items()}


def slug_to_omnia(slug: str) -> str | None:
    """Map a LiteLLM model slug back to its Omnia ID. None if unknown."""
    if not slug:
        return None
    if slug in _SLUG_TO_OMNIA:
        return _SLUG_TO_OMNIA[slug]
    # LiteLLM sometimes returns the bare provider model (no `provider/` prefix)
    # or a date-stamped variant. Try both directions.
    for known_slug, omnia_id in _SLUG_TO_OMNIA.items():
        bare = known_slug.split("/", 1)[-1]
        if slug == bare or slug.startswith(known_slug) or slug.startswith(bare):
            return omnia_id
    return None


_router: Router | None = None


def _api_key_for(slug: str) -> str | None:
    s = get_settings()
    if slug.startswith("anthropic/") and s.anthropic_api_key:
        v = s.anthropic_api_key.get_secret_value()
        return v or None
    if slug.startswith("openai/") and s.openai_api_key:
        v = s.openai_api_key.get_secret_value()
        return v or None
    if slug.startswith("openrouter/") and s.openrouter_api_key:
        v = s.openrouter_api_key.get_secret_value()
        return v or None
    if slug.startswith("gemini/") and s.gemini_api_key:
        v = s.gemini_api_key.get_secret_value()
        return v or None
    return None


def _build_model_list() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    settings = get_settings()
    for omnia_id, slug in _LITELLM_MODEL_SLUG.items():
        proxy = _PROXY_ROUTES.get(omnia_id)
        if proxy is not None:
            api_key = proxy.api_key(settings)
            if not api_key:
                continue
            items.append(
                {
                    "model_name": omnia_id,
                    "litellm_params": {
                        "model": slug,
                        "api_key": api_key,
                        "api_base": proxy.api_base(settings),
                    },
                }
            )
            continue

        api_key = _api_key_for(slug)
        if api_key is None:
            # No key configured — skip; acompletion() will surface
            # ModelUnavailableError if such a model is requested.
            continue
        items.append(
            {
                "model_name": omnia_id,
                "litellm_params": {"model": slug, "api_key": api_key},
            }
        )
    return items


def get_router() -> Router:
    global _router
    if _router is None:
        _router = Router(
            model_list=_build_model_list(),
            fallbacks=_FALLBACKS,
            num_retries=1,
            timeout=get_settings().request_timeout_seconds,
        )
    return _router


def reset_router() -> None:
    """Test helper — drops the cached router so config changes take effect."""
    global _router
    _router = None


def is_supported(model_id: str) -> bool:
    return model_id in PRICE_TABLE


async def acompletion(
    *,
    model: str,
    messages: list[dict[str, str]],
    user: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    _skip_empty_retry: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """Unified async completion.

    Returns a dict in OpenAI chat-completion shape. Always includes `usage`
    with `prompt_tokens` / `completion_tokens` (callers price off these).

    Raises:
        ModelNotFoundError       — model unknown to the gateway.
        ModelUnavailableError    — provider key missing, auth/rate-limit fail.
        UpstreamProviderError    — transport / 5xx / malformed upstream reply.
    """
    if not is_supported(model):
        raise ModelNotFoundError(f"Unknown model: {model}")

    if yandex_provider.is_yandex_model(model):
        return await yandex_provider.acompletion(
            model=model,
            messages=messages,
            temperature=0.6 if temperature is None else temperature,
            max_tokens=2000 if max_tokens is None else max_tokens,
        )

    if sber_provider.is_sber_model(model):
        return await sber_provider.acompletion(
            model=model,
            messages=messages,
            temperature=0.7 if temperature is None else temperature,
            max_tokens=2000 if max_tokens is None else max_tokens,
        )

    # vsegpt REMOVED (owner 2026-06-30 «полный отказ от vsegpt»). Every model now
    # routes through the LiteLLM Router; claude-opus-4-8 → oneprovider.dev.
    if model not in _LITELLM_MODEL_SLUG:
        # Pricing knows it but routing doesn't — defensive guard.
        raise ModelNotFoundError(f"Model not routable: {model}")

    router = get_router()
    if not any(item["model_name"] == model for item in router.model_list):
        raise ModelUnavailableError(f"Provider key for model {model} is not configured")

    # Anthropic prompt caching on the new-provider (oneprovider) path — the
    # non-streaming half of «кэширование» (streaming.py:85 does the streaming
    # half): wrap the stable system prefix in cache_control so claude-opus-4-8
    # reuses it across calls. No-op for non-Anthropic models.
    messages = apply_anthropic_cache(model, messages)
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if user is not None:
        kwargs["user"] = user
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    kwargs.update(extra)

    # Gemini 2.5 AND GPT-5 family are reasoning models. Without an explicit
    # max_tokens AND minimal reasoning budget, ALL output tokens get spent
    # on hidden reasoning_tokens and the visible text response is 0–2 tokens
    # (we saw `tokens_out=1, content="От"` from Gemini, `content=""`,
    # `reasoning_tokens=30/30` from gpt-5-nano).
    #
    # Two safeguards apply to both families:
    #   1) max_tokens default 16k — leaves headroom for both reasoning and
    #      the actual answer.
    #   2) reasoning_effort: "disable" for Gemini (LiteLLM maps to
    #      `thinkingConfig.thinkingBudget=0`), "minimal" for OpenAI (lowest
    #      legal value on GPT-5 — "disable" isn't supported, "minimal"
    #      reserves only the bare minimum reasoning tokens).
    if model.startswith("gemini-"):
        kwargs.setdefault("max_tokens", 16384)
        kwargs.setdefault("reasoning_effort", "disable")
    elif model in ("gpt-5", "gpt-5-nano"):
        kwargs.setdefault("max_tokens", 16384)
        kwargs.setdefault("reasoning_effort", "minimal")
    elif model.startswith("claude-"):
        # oneprovider's claude-opus-4-8 enables EXTENDED THINKING by default, which
        # made the tiny meta-calls (discovery/result_type) slow AND token-starved
        # (thinking ate the small max_tokens → empty reply → retry), tripping the
        # api's gateway ReadTimeout → POST /prompt timed out. Disable thinking for
        # snappy, deterministic replies — matches the prior non-thinking vsegpt opus.
        kwargs.setdefault("thinking", {"type": "disabled"})

    async def _attempt() -> Any:
        try:
            return await router.acompletion(**kwargs)
        except litellm.AuthenticationError as exc:
            raise ModelUnavailableError(f"Auth failure for model {model}") from exc
        except litellm.RateLimitError as exc:
            raise ModelUnavailableError(f"Rate limited on model {model}") from exc
        except (litellm.APIConnectionError, litellm.Timeout) as exc:
            raise UpstreamProviderError(f"Upstream error for model {model}: {exc}") from exc
        except litellm.APIError as exc:
            raise UpstreamProviderError(f"Provider error for model {model}: {exc}") from exc

    response = await _attempt()

    # Empty-response retry: proxyapi.ru cold-starts can return <50 chars on
    # the first call after >5 min idle. Retry once before letting the
    # caller's empty-content fallback chain fire — saves a redundant
    # fallback call (and double-billing). Warmup loop in services/warmup.py
    # makes this rare; this is the in-band safety net.
    #
    # _skip_empty_retry: warmup calls explicitly request ≤4 tokens — the
    # short reply is the intended outcome, not a cold-start artifact.
    # Retrying there would double proxyapi spend every 240s.
    if not _skip_empty_retry and model not in _NO_EMPTY_RETRY_MODELS:
        try:
            first_text = response.choices[0].message.content or ""
        except (AttributeError, IndexError, KeyError):
            first_text = ""
        if len(first_text) < _MIN_NONEMPTY_RESPONSE_CHARS:
            log.info(
                "acompletion.retry_on_empty",
                model=model,
                first_len=len(first_text),
            )
            await asyncio.sleep(_EMPTY_RETRY_DELAY_S)
            response = await _attempt()

    return response.model_dump() if hasattr(response, "model_dump") else dict(response)
