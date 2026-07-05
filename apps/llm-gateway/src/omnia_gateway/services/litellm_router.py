"""Single entry point for chat completions.

Dispatches claude-opus-4-8 (the only model, every role) to the direct vsegpt
httpx wrapper in `providers.vsegpt` (fronts a RU endpoint LiteLLM can't route
reliably from the prod VPS), or to the LiteLLM Router → oneprovider.dev when
OPUS_VIA_VSEGPT=false (the reversible failover).

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
from omnia_gateway.providers import vsegpt as vsegpt_provider
from omnia_gateway.services.pricing import PRICE_TABLE
from omnia_gateway.services.prompt_cache import apply_anthropic_cache

# Suppress LiteLLM's own debug printing — we use structlog for everything.
litellm.suppress_debug_info = True

# Omnia model ID → LiteLLM model slug.
# Verify slugs against provider docs before bumping; some are best-effort
# substitutes per AGENT-C-LLM-GATEWAY.md (e.g. claude-sonnet-4-6 maps to
# anthropic/claude-sonnet-4-5 until a 4.6 alias ships).
_LITELLM_MODEL_SLUG: dict[str, str] = {
    # Opus 4.8 — THE live model for every role. Normally dispatched by the direct
    # vsegpt provider BEFORE this Router; this Router entry is the oneprovider.dev
    # failover target (OPUS_VIA_VSEGPT=false), with streaming.py applying cache_control.
    "claude-opus-4-8": "anthropic/claude-opus-4-8",
}


@dataclass(frozen=True, slots=True)
class _ProxyRoute:
    """Override for models whose key/base differ from the default per-prefix routing."""

    api_key: Callable[[Settings], str | None]
    api_base: Callable[[Settings], str]


# Models that bypass slug-prefix routing — an Anthropic model served via a
# 3rd-party endpoint whose key lives under a different env var. Only opus-4-8
# (→ oneprovider.dev) remains.
_PROXY_ROUTES: dict[str, _ProxyRoute] = {
    # Opus 4.8 (the live model for EVERY role) via oneprovider.dev native Anthropic
    # endpoint — its own key/base. This is the ACTIVE route when OPUS_VIA_VSEGPT=false
    # (also feeds routers/messages_native.py via proxy_route_for).
    "claude-opus-4-8": _ProxyRoute(
        api_key=lambda s: s.oneprovider_api_key.get_secret_value() if s.oneprovider_api_key else None,
        api_base=lambda s: s.oneprovider_base_url,
    ),
}

# No Router fallback chains: the only routed model is claude-opus-4-8, which has
# no fallback target (a vsegpt outage surfaces as a clean error the caller heals).
_FALLBACKS: list[dict[str, list[str]]] = []

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


def proxy_route_for(model: str) -> tuple[str, str] | None:
    """(api_key, api_base) for a model whose key/base is overridden in _PROXY_ROUTES
    (e.g. claude-opus-4-8 → oneprovider). None if the model has no override or its key
    is unset.

    Used by the native `/v1/messages` passthrough (routers/messages_native.py) to reach
    the SAME upstream the Router uses, but WITHOUT LiteLLM's OpenAI-shape normalization —
    which drops the Anthropic thinking-block `signature` the native tool-use agent must
    echo back verbatim across tool turns (Anthropic 400s on a modified thinking block).
    """
    route = _PROXY_ROUTES.get(model)
    if route is None:
        return None
    settings = get_settings()
    key = route.api_key(settings)
    if not key:
        return None
    return key, route.api_base(settings)


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

    # Opus 4.8 via vsegpt (owner 2026-07-01) — the direct vsegpt provider (sync
    # httpx, no-proxy) at ~3s/call with thinking OFF, vs oneprovider's ~71s. Checked
    # BEFORE the Router; the Router's oneprovider route stays as a fallback target.
    if vsegpt_provider.is_vsegpt_model(model):
        return await vsegpt_provider.acompletion(
            model=model,
            messages=messages,
            temperature=0.5 if temperature is None else temperature,
            max_tokens=8192 if max_tokens is None else max_tokens,
        )

    # Everything else routes through the LiteLLM Router (Anthropic via proxyapi,
    # OpenAI, OpenRouter, Gemini).
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

    if model.startswith("claude-"):
        # oneprovider's claude-opus-4-8 keeps EXTENDED THINKING on regardless of
        # {type: disabled} — but with a LIGHT default budget the agentic builder
        # tolerates. The canned-discovery-questions bug was NOT the thinking, it was
        # the tiny caller max_tokens (discovery's 900) being consumed by it → empty
        # JSON → fallback. The real fix is the max_tokens FLOOR below (room for the
        # light thinking + the full answer). NOTE (2026-07-01): we briefly enabled
        # thinking with an explicit budget_tokens=8000 ("самая думающая"); on a real
        # messenger build it changed Opus's action-output pattern in the text-action
        # agent loop → endless EXPLORE-STALL/CYCLE, ZERO files written. So we keep the
        # request at {type: disabled} (light default) and rely on the floor alone for
        # real, non-truncated results. Do NOT re-enable a big thinking budget without
        # first making agent_builder thinking-aware.
        kwargs.setdefault("thinking", {"type": "disabled"})
        kwargs["allowed_openai_params"] = ["thinking"]
        kwargs["max_tokens"] = max(int(kwargs.get("max_tokens") or 0), 32000)

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
