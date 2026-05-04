"""Сборка messages для LLM Gateway: system prompt + текущее состояние проекта + история + новый промпт."""

from __future__ import annotations

from collections.abc import Sequence

SYSTEM_PROMPT = (
    "Ты — Omnia.AI, AI-сайт-билдер для русского рынка.\n"
    "Твоя задача — генерировать или модифицировать статические сайты "
    "(HTML+CSS+JS, без сборщиков) на основе запроса пользователя.\n\n"
    "ПРАВИЛА:\n"
    '1. ВСЕГДА отдавай файлы целиком в формате <file path="...">...</file>.\n'
    "2. Если файл не нужно менять — не упоминай его.\n"
    "3. Если файл нужно удалить — отдай его с пустым содержимым.\n"
    "4. Используй Tailwind через CDN: "
    '<script src="https://cdn.tailwindcss.com"></script>.\n'
    "5. Сайт по умолчанию — на русском, если пользователь явно не попросит другое.\n"
    "6. Никаких build-инструментов. Только статика."
)

HISTORY_LIMIT = 6


def build_messages(
    current_files: dict[str, str],
    history: Sequence[dict[str, str]],
    user_prompt: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if current_files:
        files_block = "\n\n".join(
            f'<file path="{path}">\n{content}\n</file>'
            for path, content in current_files.items()
        )
        messages.append(
            {
                "role": "user",
                "content": f"Текущее состояние проекта:\n{files_block}",
            }
        )

    for m in list(history)[-HISTORY_LIMIT:]:
        if m.get("role") in {"user", "assistant"} and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})

    messages.append({"role": "user", "content": user_prompt})
    return messages
