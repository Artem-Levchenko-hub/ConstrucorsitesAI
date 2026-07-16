"""Auto-heal on open — fix a RED dev build the moment a project is opened.

Owner ask (2026-07-16): "как только я заходил — ошибки отлавливались и агент уже
их решал". Today a compile/runtime error shows a card + a «Починить» button the
user must click. This runs that same edit-repair automatically, in the background,
right after the project is started — no click.

Safety (an unprompted agent run spends tokens, so this is deliberately cautious):
  * OFF by default behind ``use_autoheal_on_open`` — enable + observe on purpose.
  * Per-project Redis debounce (``autoheal_debounce_seconds``, SET NX) so a
    refresh storm or a re-open can't re-fire the heal.
  * Only fires on a REAL compile error (``compile_status.ok is False``) — a clean
    build is left alone.
  * Fire-and-forget + fail-soft: any error is swallowed, the app is never worse
    than the click-to-repair baseline.

Reuses the exact repair the «Починить» flow uses: ``run_agent_build`` on the EDIT
system prompt with the live container executor.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from omnia_api.core.config import get_settings
from omnia_api.core.redis import get_redis
from omnia_api.services import agent_builder, orchestrator_client

log = structlog.get_logger(__name__)

# The chat model — one model for every role in this project (claude-opus-4-8).
_HEAL_MODEL = "claude-opus-4-8"
_HEAL_MAX_STEPS = 24


def _repair_prompt(error: str, file: str | None) -> str:
    where = f" Файл: {file}." if file else ""
    return (
        "Приложение открылось со сломанной сборкой — почини её, как по кнопке "
        f"«Починить».{where}\n\nОшибка сборки ПРЯМО СЕЙЧАС:\n{error[:1500]}\n\n"
        "Найди причину (grep/read — максимум 1-2 чтения), затем СРАЗУ внеси правку "
        "через edit_file/write_file, запусти build, доведи до чистоты, потом done. "
        "Не переписывай работающее — только устрани ошибку."
    )


async def maybe_autoheal_on_open(project_id: UUID, slug: str) -> dict[str, Any]:
    """If the opened project's dev build is RED, repair it in the background.

    Returns a small summary dict. Never raises — the caller fires this and forgets.
    """
    settings = get_settings()
    if not settings.use_autoheal_on_open:
        return {"healed": False, "reason": "disabled"}

    # Debounce: one heal per project per window. SET NX — the first opener wins,
    # concurrent/refresh opens are no-ops until the key expires.
    try:
        redis = get_redis()
        key = f"autoheal:{project_id}"
        acquired = await redis.set(
            key, "1", nx=True, ex=max(60, int(settings.autoheal_debounce_seconds))
        )
        if not acquired:
            return {"healed": False, "reason": "debounced"}
    except Exception as exc:  # noqa: BLE001 — a redis hiccup must not block open
        log.info("autoheal.redis_skip", project_id=str(project_id), err=str(exc))
        # Fail-open on the debounce would risk a storm; fail-CLOSED (skip) is safer.
        return {"healed": False, "reason": "no-debounce-guard"}

    # Only heal a genuinely broken build.
    try:
        status = await orchestrator_client.compile_status(project_id, slug=slug)
    except Exception as exc:  # noqa: BLE001
        return {"healed": False, "reason": f"status-error: {type(exc).__name__}"}
    if status.get("ok", True):
        return {"healed": False, "reason": "build-clean"}

    error = str(status.get("error") or "ошибка сборки")
    file = status.get("file")
    log.info("autoheal.start", project_id=str(project_id), file=file)

    executor = agent_builder.make_container_executor(project_id=project_id, slug=slug)
    try:
        result = await agent_builder.run_agent_build(
            system_prompt=agent_builder.EDIT_SYSTEM_PROMPT,
            user_prompt=_repair_prompt(error, file),
            model=_HEAL_MODEL,
            execute=executor,
            max_steps=_HEAL_MAX_STEPS,
            edit_mode=True,
            user_id=None,
            project_id=str(project_id),
        )
    except Exception as exc:  # noqa: BLE001 — repair must never break open
        log.warning("autoheal.failed", project_id=str(project_id), err=str(exc))
        return {"healed": False, "reason": f"repair-error: {type(exc).__name__}"}

    # The executor writes straight into the live container (hot-reload), so a
    # successful repair is already visible in the preview. Verify it went green.
    try:
        check = await orchestrator_client.compile_status(project_id, slug=slug)
        healed = bool(check.get("ok", False))
    except Exception:  # noqa: BLE001
        healed = False
    log.info(
        "autoheal.done", project_id=str(project_id), healed=healed,
        files=len(getattr(result, "files", {}) or {}),
    )
    return {"healed": healed, "files": len(getattr(result, "files", {}) or {})}
