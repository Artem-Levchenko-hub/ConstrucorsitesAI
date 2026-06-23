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

ErrorCategory = Literal["build", "compile", "schema", "runtime", "client", "incomplete"]

# Human title per category — shown bold on the card when the caller doesn't pass
# a more specific one.
_DEFAULT_TITLE: dict[str, str] = {
    "build": "Ошибка сборки",
    "compile": "Ошибка компиляции",
    "schema": "Ошибка миграции базы данных",
    "runtime": "Ошибка среды выполнения",
    "client": "Ошибка в браузере",
    # Not an error — a resumable partial agentic build (ran out of step budget).
    # Rendered by the web UI as a neutral amber card with a «Продолжить» button.
    "incomplete": "Сборка не завершена",
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


def client_card_signature(message: str, source: str, line: int) -> tuple[str, str | None]:
    """Title + file locator for a client-side JS error card.

    Title = the error's first line; file = ``filename:line`` (just the basename,
    full URLs are noisy). Used both to render the card and to dedup it — the same
    broken preview re-fires the error on every reload.
    """
    first = message.strip().splitlines()[0] if message.strip() else ""
    title = first[:140] if first else "JavaScript-ошибка"
    file: str | None = None
    if source:
        name = source.rsplit("/", 1)[-1].split("?", 1)[0]
        file = f"{name}:{line}" if line else name
    return title, file


def client_card_detail(
    message: str, stack: str, route: str, crumbs: list[str]
) -> str:
    """Card body for a client-side JS error: message, context, then stack.

    Context = the route the error fired on plus the last few user actions
    (element identity only — the inspector never sends typed values). Surfaced in
    the card and therefore in the «Починить» fix-prompt, so the model knows what
    the user was doing.

    Ordering matters: the route and breadcrumbs come BEFORE the raw stack. A
    framework stack trace can run hundreds of chars and ``_body`` clamps the whole
    body to 600 — if the stack led, a long one would push the most actionable
    context (what the user did, on which page) off the end and out of the fix
    prompt. The stack trails, so a clamp truncates its tail (its top frames, which
    matter most, survive). Per-item clamped here too (R-10): the schema bounds the
    list count, this bounds each string.
    """
    parts: list[str] = [message]
    if route:
        parts.append(f"Страница: {route[:300]}")
    steps = [c.strip()[:120] for c in crumbs if c.strip()]
    if steps:
        parts.append("Шаги до ошибки:\n" + "\n".join(f"• {s}" for s in steps))
    if stack:
        parts.append(stack)
    return "\n\n".join(parts)


def has_client_card(content: str, title: str, file: str | None) -> bool:
    """True if a client-error card with this title/file is already in the message.

    Conservative dedup (R-10): we'd rather skip an occasional genuine card than
    spam the chat with the same error on every preview reload. Requires the
    ``client`` category plus the sanitised title (and file, when present) to all
    appear — a cross-card substring collision can only ever *suppress* a card.
    """
    if 'category="client"' not in content:
        return False
    if _attr(title) not in content:
        return False
    if file is not None and _attr(file) not in content:
        return False
    return True


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
