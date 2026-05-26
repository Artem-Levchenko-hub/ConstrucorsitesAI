"""Entry point — boots the aiogram dispatcher and a tiny aiohttp health
server on :3000 (so orchestrator's container-up check passes).

The bot ALWAYS runs in long-polling mode in dev — webhooks need a
publicly reachable HTTPS endpoint and are deferred to prod / Phase B.
Long-polling needs zero infrastructure: bot.exe pulls updates from
Telegram, no inbound traffic to our VPS required.

Per-project Postgres (provisioned by orchestrator into `omnia-postgres-users`)
is reachable via DATABASE_URL — same DSN shape as the Next template.
"""

import asyncio
import logging
import os
import sys

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from bot.handlers import router as commands_router

logger = structlog.get_logger("bot.main")


async def _health_handler(_request: web.Request) -> web.Response:
    """Tiny `/` endpoint so the orchestrator's container reachability
    check passes. Returns plain text — no need for JSON noise."""
    return web.Response(text="omnia tgbot ok\n")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error(
            "missing TELEGRAM_BOT_TOKEN — set it in the Omnia secrets panel"
        )
        # Bot can't do anything without a token, but the health server can.
        # We keep the container alive so the orchestrator sees a green box;
        # AI's next prompt will likely add the env var dialogue.
        await _serve_health_only()
        return

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(commands_router)

    logger.info("bot.start", username=(await bot.get_me()).username)

    # Run health server + bot polling concurrently. asyncio.gather raises
    # if EITHER coroutine errors — fine, container restart-policy handles it.
    await asyncio.gather(_serve_health(), dp.start_polling(bot, polling_timeout=30))


async def _serve_health() -> None:
    """Run the health-only aiohttp app on :3000 alongside bot polling."""
    app = web.Application()
    app.router.add_get("/", _health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 3000)
    await site.start()
    # Block forever — runner cleanup happens on cancellation.
    await asyncio.Event().wait()


async def _serve_health_only() -> None:
    """Identical to _serve_health but with no concurrent bot — used when
    the token is missing so the container still presents itself as up."""
    await _serve_health()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
