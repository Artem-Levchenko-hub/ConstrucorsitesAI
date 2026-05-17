"""Update project-context.md when architectural files change.

Called by flush.py after session end/preCompact when context-dirty.json exists.
Reads changed file snippets, asks LLM to update relevant sections only.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import KNOWLEDGE_DIR, ROOT_DIR, SCRIPTS_DIR, now_iso
from llm_provider import run_llm_text
from utils import append_log_block

PROJECT_CONTEXT_FILE = KNOWLEDGE_DIR / "project-context.md"
DIRTY_FILE = SCRIPTS_DIR / "context-dirty.json"
MAX_FILE_SNIPPET = 600   # chars per changed file shown to LLM
MAX_CONTEXT_SIZE = 9_000  # keep project-context.md under this limit

logging.basicConfig(
    filename=str(SCRIPTS_DIR / "flush.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _load_dirty() -> dict:
    if not DIRTY_FILE.exists():
        return {}
    try:
        return json.loads(DIRTY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _clear_dirty() -> None:
    DIRTY_FILE.unlink(missing_ok=True)


def _read_snippet(file_path: str) -> str:
    p = Path(file_path)
    if not p.exists():
        return f"(file deleted: {file_path})"
    try:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        if len(text) > MAX_FILE_SNIPPET:
            return text[:MAX_FILE_SNIPPET] + "\n...(truncated)"
        return text
    except Exception:
        return f"(unreadable: {file_path})"


def update_context(dirty: dict) -> bool:
    if not PROJECT_CONTEXT_FILE.exists():
        logging.warning("project-context.md not found, skipping update")
        return False

    changed_files: list[str] = dirty.get("changed_files", [])
    sections: list[str] = list(dict.fromkeys(dirty.get("sections", [])))  # unique, ordered
    if not changed_files:
        return False

    current = PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")

    snippets_block = ""
    for fp in changed_files[:12]:  # cap to avoid huge prompts
        snippet = _read_snippet(fp)
        rel = fp.replace("\\", "/").split("omnia-mvp/")[-1]
        snippets_block += f"\n### {rel}\n```\n{snippet}\n```\n"

    sections_hint = ", ".join(sections) if sections else "any relevant section"

    prompt = f"""You maintain a project-context.md wiki article that is injected into every Claude Code session as persistent memory.

The following source files were changed during this session. Update ONLY the sections of project-context.md that are affected by these changes.
Sections likely affected: {sections_hint}

Rules:
- Keep the total file under {MAX_CONTEXT_SIZE} characters
- Keep bullet-point style (no paragraphs)
- Include file paths where relevant
- Write in Russian
- Return the COMPLETE updated project-context.md (frontmatter + all sections)
- Do NOT add new sections unless truly necessary
- If nothing meaningful changed for context purposes, return the original unchanged

## Current project-context.md
{current}

## Changed files this session
{snippets_block}
"""

    result = run_llm_text(prompt, ROOT_DIR, max_turns=5)
    if not result.ok:
        logging.warning("update_context LLM failed: %s", result.error)
        return False

    updated = result.text.strip()

    # Strip markdown code fence wrapper if LLM wrapped content in ```markdown ... ```
    if updated.startswith("```"):
        lines = updated.splitlines()
        # Drop first line (```markdown or ```) and last ``` if present
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        updated = "\n".join(inner).strip()

    # Strip any explanation text before frontmatter
    fm_start = updated.find("---")
    if fm_start > 0:
        updated = updated[fm_start:]

    if not updated or len(updated) < 500:
        logging.info("update_context: LLM returned empty/short, skipping")
        return False

    # If LLM decided nothing changed, it may return current content unchanged — that's fine

    # Trim to size limit
    if len(updated) > MAX_CONTEXT_SIZE:
        updated = updated[:MAX_CONTEXT_SIZE]

    PROJECT_CONTEXT_FILE.write_text(updated + "\n", encoding="utf-8")
    logging.info("project-context.md updated (%d chars), sections: %s", len(updated), sections_hint)
    append_log_block(
        f"[{now_iso()}] context-update | project-context.md",
        [f"Changed files: {len(changed_files)}", f"Sections: {sections_hint}"],
    )
    return True


def main() -> int:
    dirty = _load_dirty()
    if not dirty:
        return 0

    changed = dirty.get("changed_files", [])
    if not changed:
        _clear_dirty()
        return 0

    logging.info("Updating project-context.md for %d changed files", len(changed))
    ok = update_context(dirty)
    _clear_dirty()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
