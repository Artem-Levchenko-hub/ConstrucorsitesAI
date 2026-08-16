"""Self-heal a container app that fails to compile / 5xx at runtime — the
Claude-Code «verify → read the real error → fix → repeat» step, for the web path.

`exe_doctor` does this for PyInstaller/NSIS builds; this is its web sibling. Given
a REAL Next.js compile error (or a runtime 5xx) probed from the live dev container
(`orchestrator_client.compile_status` / `runtime_status`), plus the failing file's
current content, it asks the `app_doctor` model role (DeepSeek — the workhorse) for
a MINIMAL fix as a `<edit>` SEARCH/REPLACE (preferred) or a full `<file>`, parses it
with the same `file_extractor` pipeline the chat path uses, applies it, and returns
ONLY the changed files (path → new content) — or ``None`` when the model gives
nothing usable / nothing changed (so the caller's loop stops instead of spinning).

Pure-ish + testable like `exe_doctor.heal`: the model call is the only I/O, and the
parse/apply is the deterministic `file_extractor` code. The probe + hot-reload loop
and persistence live in the caller (`routers/messages.py`).
"""

from __future__ import annotations

import logging

from omnia_api.core.config import model_for_role
from omnia_api.services.file_extractor import (
    UnsafePathError,
    apply_edits,
    extract_edits,
    extract_files,
)
from omnia_api.services.llm_client import LLMError, complete_chat

log = logging.getLogger(__name__)

# Cap on how much of the failing file we feed the model. A Next route/component is
# almost always well under this; truncating a giant file keeps the repair prompt
# bounded (the error message points at the relevant region anyway).
_MAX_FILE_CHARS = 16_000

_SYSTEM = (
    "Ты — старший инженер, чинишь ОДНУ ошибку в Next.js/React приложении (TypeScript, "
    "App Router, Tailwind v4, shadcn/ui). Тебе дают ТЕКСТ реальной ошибки сборки/"
    "рантайма из dev-сервера и СОДЕРЖИМОЕ проблемного файла. Верни МИНИМАЛЬНУЮ правку, "
    "которая устраняет ИМЕННО эту ошибку и ничего больше не ломает.\n\n"
    "ФОРМАТ ОТВЕТА — предпочтительно точечная правка <edit> с SEARCH/REPLACE:\n"
    '<edit path="src/app/page.tsx">\n'
    "<<<<<<< SEARCH\n<точный фрагмент из файла, байт-в-байт>\n=======\n"
    "<исправленный фрагмент>\n>>>>>>> REPLACE\n</edit>\n\n"
    "Если правка большая или структурная — верни полный файл целиком в "
    '<file path="...">...</file>. Только код, без объяснений и без ``` ограждений. '
    "Не трогай несвязанные строки. Не добавляй новых зависимостей, если можно обойтись."
)


def _build_prompt(category: str, detail: str, file_path: str | None, content: str) -> str:
    head = (
        "КОМПИЛЯЦИЯ НЕ ПРОХОДИТ" if category == "compile" else "ПРИЛОЖЕНИЕ ПАДАЕТ В РАНТАЙМЕ"
    )
    loc = f"\nФайл с ошибкой: {file_path}" if file_path else ""
    body = content[:_MAX_FILE_CHARS]
    truncated = "\n…(файл обрезан)…" if len(content) > _MAX_FILE_CHARS else ""
    file_block = (
        f"\n\nТЕКУЩЕЕ СОДЕРЖИМОЕ ФАЙЛА `{file_path}`:\n{body}{truncated}"
        if file_path and content
        else ""
    )
    return (
        f"{head}.\n\nОШИБКА:\n{(detail or '').strip()[:4000]}{loc}{file_block}\n\n"
        "Верни правку, устраняющую эту ошибку."
    )


async def _ask_model(prompt: str, model: str) -> str:
    try:
        return await complete_chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            model=model,
            max_tokens=4000,
            temperature=0.0,
        )
    except LLMError as exc:
        log.warning("app_doctor: model call failed (give up): %r", exc)
        return ""


def parse_fix(answer: str, files: dict[str, str]) -> dict[str, str]:
    """Parse the model's repair answer (`<edit>`/`<file>`) into changed files.

    Deterministic, browser-free, no I/O — the unit-testable core. Applies any
    `<edit>` SEARCH/REPLACE against ``files`` and folds in any full `<file>`
    blocks, returning ONLY the files that actually changed. A SEARCH that doesn't
    match is dropped (file_extractor records it as a conflict); a `<file>` whose
    body equals the current content is not a change. Returns ``{}`` when nothing
    usable / nothing changed.
    """
    changed: dict[str, str] = {}
    try:
        full = extract_files(answer)
    except (UnsafePathError, ValueError):
        full = {}
    for path, body in full.items():
        if files.get(path) != body:
            changed[path] = body
    try:
        edits = extract_edits(answer)
    except (UnsafePathError, ValueError):
        edits = {}
    if edits:
        # Apply against the already-`<file>`-patched view so an <edit> can refine a
        # freshly rewritten file in the same answer.
        base = {**files, **changed}
        patched, _conflicts = apply_edits(edits, base)
        for path, body in patched.items():
            if files.get(path) != body:
                changed[path] = body
    return changed


async def propose_fix(
    *,
    category: str,
    detail: str,
    file_path: str | None,
    files: dict[str, str],
    model: str | None = None,
) -> dict[str, str] | None:
    """Ask the model for a fix to a compile/runtime error; return changed files or None.

    ``files`` is the current AI-generated file set (read-only here). ``file_path`` is
    the orchestrator's blamed file; its content is pulled from ``files`` for the
    prompt. Returns ``{path: new_content}`` of the files that changed, or ``None``
    when the model produced nothing usable / no change — the signal for the caller's
    loop to stop (mirrors ``exe_doctor.heal``).
    """
    content = files.get(file_path or "", "") if file_path else ""
    answer = await _ask_model(
        _build_prompt(category, detail, file_path, content),
        model or model_for_role("app_doctor"),
    )
    if not answer.strip():
        return None
    changed = parse_fix(answer, files)
    return changed or None


__all__ = ["parse_fix", "propose_fix"]
