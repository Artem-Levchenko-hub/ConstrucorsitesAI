"""Background task: keep proxyapi.ru connection warm.

Cold-start observation: the first request to ``claude-haiku-4-5`` or
``gpt-5-nano`` via proxyapi.ru after >5 min idle sometimes returns a
near-empty response (4-10 chars) before the upstream session is fully
established. Symptoms in production:
  * ``preset_classifier`` falls through to ``DEFAULT_PRESET_ID``
    (editorial-trust) because Haiku replies with whitespace.
  * Main generation flow trips ``_looks_truncated`` in
    ``apps/api/routers/messages.py`` and pays for the empty Haiku call +
    the gpt-5-nano fallback that follows.

Sending a tiny ping every ~4 min keeps the route hot (proxyapi.ru idle
timeout is ~5 min).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from omnia_gateway.services import litellm_router

log = structlog.get_logger(__name__)

# Models warmed once at startup. Both share the same proxyapi.ru balance,
# both feed the same cold-start symptom, so we warm them together.
WARMUP_MODELS: tuple[str, ...] = ("claude-haiku-4-5", "gpt-5-nano")

# Model kept hot in the periodic loop. Haiku is the workhorse for the
# preset classifier and the cheap-design generator path — the one that
# silently fell back to "editorial-trust" when cold. gpt-5-nano is only
# hit on the empty-fallback path, so the initial warmup is enough.
PERIODIC_MODEL: str = "claude-haiku-4-5"

# proxyapi.ru drops idle upstream TCP sessions after ~5 min. 240s is just
# under that window, with enough headroom for clock skew and the request
# itself taking a second or two.
PERIODIC_INTERVAL_S: float = 240.0


async def _ping(model: str) -> None:
    """Fire one minimal completion. Errors are logged, not raised — the
    warmup loop must survive every transient upstream failure.

    Model-specific kwargs: gpt-5 family rejects ``temperature=0.0``
    (only temperature=1 is allowed) and burns a tiny max_tokens budget
    entirely on hidden reasoning, returning 0 visible chars. Let
    ``litellm_router.acompletion`` patch sane defaults for those families
    (it already handles ``reasoning_effort`` + sufficient max_tokens for
    gpt-5/gemini). For Anthropic (Claude family) the small budget is fine.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        # Skip acompletion's retry-on-empty — a tiny intentional reply
        # is not a cold-start failure for warmup purposes.
        "_skip_empty_retry": True,
    }
    if model.startswith("claude-"):
        kwargs["max_tokens"] = 4
        kwargs["temperature"] = 0.0
    try:
        await litellm_router.acompletion(**kwargs)
    except Exception as exc:  # noqa: BLE001 — warmup must never crash
        log.warning("warmup.ping_failed", model=model, error=str(exc))


async def run_warmup_loop() -> None:
    """Initial parallel warmup + periodic single-model heartbeat.

    Cancellation-safe: ``asyncio.CancelledError`` exits the loop cleanly
    on shutdown without re-firing the ping.
    """
    await asyncio.gather(*(_ping(m) for m in WARMUP_MODELS), return_exceptions=True)
    log.info("warmup.initial_done", models=list(WARMUP_MODELS))
    while True:
        try:
            await asyncio.sleep(PERIODIC_INTERVAL_S)
        except asyncio.CancelledError:
            log.info("warmup.cancelled")
            return
        await _ping(PERIODIC_MODEL)
