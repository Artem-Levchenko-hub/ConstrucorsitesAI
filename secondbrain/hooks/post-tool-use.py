"""PostToolUse hook — real-time activity tracker for Obsidian.

Fires after every Write/Edit/Bash tool use.
Updates knowledge/live-activity.md with a rolling log of recent changes.
No LLM calls — pure local I/O, completes in <50ms.

Obsidian picks up the file change immediately (Live Preview auto-refreshes).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recursion guard
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
LIVE_ACTIVITY_FILE = ROOT_DIR / "knowledge" / "live-activity.md"
DIRTY_FILE = SCRIPTS_DIR / "context-dirty.json"

TRACKED_TOOLS = {"Write", "Edit", "Bash"}
MAX_ENTRIES = 50
MAX_CMD_LEN = 60

# Emoji per tool for visual scan
TOOL_ICON = {
    "Write": "✍️",
    "Edit": "✏️",
    "Bash": "⚡",
}

# Patterns that signal project-context.md needs updating.
# Order matters — first match wins for section hint.
# Omnia.AI structure: apps/{web, api, llm-gateway}, infra/, docs/, agents/
CONTEXT_TRIGGERS: list[tuple[str, str]] = [
    ("apps/api/src/omnia_api/main.py",              "Стек и структура"),
    ("apps/api/src/omnia_api/core/",                "Стек и структура"),
    ("apps/web/src/app/",                           "Стек и структура"),
    ("apps/llm-gateway/src/omnia_gateway/main.py",  "Стек и структура"),
    ("apps/llm-gateway/src/omnia_gateway/core/",    "Стек и структура"),
    ("apps/api/src/omnia_api/routers/",             "Ключевые модули"),
    ("apps/api/src/omnia_api/services/",            "Ключевые модули"),
    ("apps/api/src/omnia_api/services/repo",        "Pipeline промпт→файлы"),
    ("apps/api/src/omnia_api/services/file_extractor", "Pipeline промпт→файлы"),
    ("apps/api/src/omnia_api/services/prompt_builder", "Pipeline промпт→файлы"),
    ("apps/llm-gateway/src/omnia_gateway/services/litellm_router", "LLM Gateway"),
    ("apps/llm-gateway/src/omnia_gateway/services/pricing", "LLM Gateway"),
    ("apps/llm-gateway/src/omnia_gateway/services/streaming", "LLM Gateway"),
    ("apps/llm-gateway/src/omnia_gateway/providers/", "LLM Gateway"),
    ("apps/llm-gateway/src/omnia_gateway/routers/",  "LLM Gateway"),
    ("apps/web/src/components/workspace/",           "Workspace UI"),
    ("apps/web/src/hooks/",                          "Workspace UI"),
    ("apps/web/src/store/",                          "Workspace UI"),
    ("apps/web/src/lib/",                            "Workspace UI"),
    ("apps/api/src/omnia_api/models/",              "База данных"),
    ("apps/api/migrations/",                        "База данных"),
    ("infra/",                                      "Инфраструктура"),
    ("apps/llm-gateway/deploy/",                    "Инфраструктура"),
    ("docs/",                                       "Текущие задачи"),
    ("agents/",                                     "Брифинги агентов"),
    ("CLAUDE.md",                                   "Важные правила"),
    ("apps/api/src/omnia_api/schemas/",             "Ключевые модули"),
]


def _section_for_path(norm_path: str) -> str | None:
    """Return section hint if path matches a context trigger, else None."""
    for pattern, section in CONTEXT_TRIGGERS:
        if pattern in norm_path:
            return section
    return None


def _mark_context_dirty(file_path: str, section: str) -> None:
    """Append file to context-dirty.json (no LLM, pure JSON write, <5ms)."""
    try:
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        dirty: dict = {}
        if DIRTY_FILE.exists():
            try:
                dirty = json.loads(DIRTY_FILE.read_text(encoding="utf-8"))
            except Exception:
                dirty = {}
        files: list[str] = dirty.get("changed_files", [])
        sections: list[str] = dirty.get("sections", [])
        if file_path not in files:
            files.append(file_path)
        if section not in sections:
            sections.append(section)
        dirty["changed_files"] = files
        dirty["sections"] = sections
        dirty["last_updated"] = datetime.now(timezone.utc).isoformat()
        DIRTY_FILE.write_text(json.dumps(dirty, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # never block the hook


def _short_path(raw: str) -> str:
    """Extract the last 2 path components for display."""
    p = raw.replace("\\", "/")
    parts = [x for x in p.split("/") if x]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else raw


def _entry_from_hook(tool_name: str, tool_input: dict) -> str:
    """Build a single markdown table row."""
    now = datetime.now(timezone.utc).astimezone()
    time_str = now.strftime("%H:%M:%S")

    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        label = f"`{_short_path(file_path)}`" if file_path else "—"
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "").replace("\n", " ").strip()
        if len(cmd) > MAX_CMD_LEN:
            cmd = cmd[:MAX_CMD_LEN] + "…"
        label = f"`{cmd}`"
    else:
        label = "—"

    icon = TOOL_ICON.get(tool_name, "·")
    return f"| `{time_str}` | {icon} {tool_name} | {label} |"


def _read_existing_entries(content: str) -> list[str]:
    """Extract existing table rows from the file."""
    rows = []
    in_table = False
    for line in content.splitlines():
        if line.startswith("| `") and ":" in line:
            rows.append(line)
            in_table = True
        elif in_table and not line.startswith("|"):
            break
    return rows


def _build_file(entries: list[str], last_update: str) -> str:
    header = (
        "# Live Activity\n\n"
        f"> Последнее обновление: **{last_update}**  \n"
        "> Обновляется автоматически после каждого Write / Edit / Bash.\n\n"
        "| Время | Инструмент | Что изменилось |\n"
        "|-------|-----------|----------------|\n"
    )
    return header + "\n".join(entries) + "\n"


def main() -> int:
    try:
        raw = sys.stdin.read().strip()
        hook_input = json.loads(raw) if raw else {}
    except Exception:
        return 0

    tool_name: str = hook_input.get("tool_name", "")
    if tool_name not in TRACKED_TOOLS:
        return 0

    tool_input: dict = hook_input.get("tool_input", {})

    # Skip internal SecondBrain file ops (avoid infinite loop in compile/flush)
    file_path = tool_input.get("file_path", "")
    if file_path and "SecondBrain" in file_path.replace("\\", "/"):
        return 0

    # Mark context dirty if an architectural file changed
    if tool_name in ("Write", "Edit") and file_path:
        norm = file_path.replace("\\", "/")
        section = _section_for_path(norm)
        if section:
            _mark_context_dirty(file_path, section)

    new_entry = _entry_from_hook(tool_name, tool_input)

    # Read existing entries
    existing: list[str] = []
    if LIVE_ACTIVITY_FILE.exists():
        existing = _read_existing_entries(LIVE_ACTIVITY_FILE.read_text(encoding="utf-8"))

    # Prepend new entry (newest first), cap at MAX_ENTRIES
    all_entries = [new_entry] + existing
    all_entries = all_entries[:MAX_ENTRIES]

    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    LIVE_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIVE_ACTIVITY_FILE.write_text(_build_file(all_entries, now_str), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
