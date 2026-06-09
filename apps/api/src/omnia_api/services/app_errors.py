"""Surface app build/runtime failures as structured cards in the chat.

After a container-app build, three things can go wrong *after* the model has
already produced a snapshot: the Postgres migration (`drizzle-kit push`) fails,
the file sync into the dev container fails, or the dev server fails to compile
the new code. None of these are model errors, so they don't belong in
`llm.error`; they're *app* errors the user can act on.

This module renders them into an ``<app-error …>`` block that:
  * is appended to the assistant message content, so it persists and re-renders
    when the user reloads the project (single source of truth = the DB row), and
  * is announced live via an ``app.error`` WebSocket event so the chat shows the
    card without a manual refresh.

The frontend mirror is `apps/web/src/lib/parse-assistant.ts` (block parsing) and
`ChatMessage.tsx` (the card). Keep the tag grammar in sync.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.redis import publish_event
from omnia_api.models.message import Message

ErrorCategory = Literal["build", "compile", "schema", "runtime"]

# Human title per category — shown bold on the card when the caller doesn't pass
# a more specific one.
_DEFAULT_TITLE: dict[str, str] = {
    "build": "Ошибка сборки",
    "compile": "Ошибка компиляции",
    "schema": "Ошибка миграции базы данных",
    "runtime": "Ошибка среды выполнения",
}


def render_block(
    *,
    category: ErrorCategory,
    title: str | None,
    detail: str,
    file: str | None,
    fixable: bool,
) -> str:
    """Build the ``<app-error …>…</app-error>`` chat block.

    All interpolated values are sanitised so they can't break out of the tag or
    forge another ``<file>``/``<edit>``/``<app-error>`` block (the chat content
    is plain text the frontend re-parses).
    """
    safe_title = _attr(title or _DEFAULT_TITLE[category])
    attrs = f'category="{category}" title="{safe_title}" fixable="{"1" if fixable else "0"}"'
    if file:
        attrs += f' file="{_attr(file)}"'
    return f"\n\n<app-error {attrs}>{_body(detail)}</app-error>\n\n"


async def publish(
    factory: async_sessionmaker[AsyncSession],
    project_id: UUID,
    message_id: UUID,
    *,
    category: ErrorCategory,
    detail: str,
    title: str | None = None,
    file: str | None = None,
    fixable: bool = True,
) -> None:
    """Append an error card to the assistant message and announce it live.

    Fail-soft (R-10): a Redis/DB hiccup here must not abort the build — the
    snapshot already shipped. Callers wrap this, but we also swallow our own
    persistence error so the live event still fires (and vice-versa).
    """
    block = render_block(
        category=category, title=title, detail=detail, file=file, fixable=fixable
    )

    try:
        async with factory() as session:
            msg = await session.get(Message, message_id)
            if msg is not None:
                msg.content = (msg.content or "") + block
                await session.commit()
    except Exception as exc:
        # Persistence is best-effort — never abort the build over a card.
        print(f"[app_errors] persist failed: {exc!r}", flush=True)

    try:
        await publish_event(
            project_id,
            "app.error",
            {
                "message_id": str(message_id),
                "category": category,
                "title": title or _DEFAULT_TITLE[category],
            },
        )
    except Exception as exc:
        # Live event is best-effort — the card is already persisted above.
        print(f"[app_errors] publish failed: {exc!r}", flush=True)


def _attr(value: str) -> str:
    """Make a string safe to sit inside a double-quoted tag attribute."""
    return (
        value.replace("<", "‹")
        .replace(">", "›")
        .replace('"', "'")
        .replace("\n", " ")
        .strip()[:200]
    )


def _body(value: str) -> str:
    """Make a string safe to sit as the tag body (no tag-forging characters)."""
    return value.replace("<", "‹").replace(">", "›").strip()[:600]
