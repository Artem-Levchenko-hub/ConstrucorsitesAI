"""Bot command + message handlers.

AI extends this file (or splits it into `handlers/` subpackage as it
grows) when the user describes the bot's behaviour. The default handlers
below cover the absolute minimum: `/start` greets the user, `/help`
links back to the user-described commands.

Pattern conventions AI must follow:
- One `Router()` exported as `router`. The main module includes it.
- Each command/handler decorated with `@router.message(Command(...))`
  or `@router.callback_query(...)`. Don't drop handlers on the bot
  directly — that breaks router composition.
- Long async work belongs in `bot/services/`, NOT inline in handlers —
  handler blocks the polling loop while it runs.
- Per-user state: in-memory `dict[user_id, State]` is fine for prototypes;
  for production, write to Postgres via `bot/db.py`.
"""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

router = Router(name="commands")


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    if not message.from_user:
        return
    name = message.from_user.full_name or "друг"
    await message.answer(
        f"Привет, <b>{name}</b>! Я — стартовый бот на Omnia.AI.\n\n"
        "AI добавит сюда команды по твоему промпту. Пока умею только "
        "/start и /help."
    )


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(
        "Это шаблон <code>telegram-bot-aiogram</code>. Опиши в чате "
        "Omnia, что бот должен уметь — AI допишет хендлеры в "
        "<code>src/bot/handlers.py</code> и подключит БД при необходимости."
    )
