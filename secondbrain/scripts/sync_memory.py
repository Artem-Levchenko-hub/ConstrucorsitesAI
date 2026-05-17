"""Ingest Claude Code auto-memory files into SecondBrain daily log.

Reads all .md files from the Claude Code project memory directory,
detects changes via SHA-256 hashing, and appends changed files as
structured entries in today's daily log.

Memory directory is auto-derived from the current project path using
Claude Code's convention (~/.claude/projects/<path-with-dashes>/memory/).
Override with CLAUDE_CODE_MEMORY_DIR env var or --memory-dir flag.

Usage:
    uv run python scripts/sync_memory.py
    uv run python scripts/sync_memory.py --dry-run
    uv run python scripts/sync_memory.py --memory-dir /custom/path
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from config import DAILY_DIR, PROJECT_ROOT, SCRIPTS_DIR
from utils import ensure_structure, file_hash, load_json, save_json

MEMORY_STATE_FILE = SCRIPTS_DIR / "memory-state.json"

# Files to skip (index only, not content)
SKIP_FILES = {"MEMORY.md"}


def _claude_project_hash(project_path: Path) -> str:
    """Replicate Claude Code's project-dir → memory-dir naming convention.

    Examples:
        C:\\Sms\\CorporateMessanger → C--Sms-CorporateMessanger
        /home/bob/proj              → -home-bob-proj
    """
    raw = str(project_path.resolve())
    return raw.replace(":", "-").replace("\\", "-").replace("/", "-")


def default_memory_dir() -> Path:
    """Auto-derive the Claude Code memory directory for this project."""
    override = os.environ.get("CLAUDE_CODE_MEMORY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    project_hash = _claude_project_hash(PROJECT_ROOT)
    return Path.home() / ".claude" / "projects" / project_hash / "memory"


def _collect_memory_files(memory_dir: Path) -> list[Path]:
    """Return all .md files in the memory directory, sorted."""
    if not memory_dir.exists():
        return []
    return sorted(
        (p for p in memory_dir.glob("*.md") if p.name not in SKIP_FILES),
        key=lambda p: p.name.lower(),
    )


def _categorise(name: str) -> str:
    """Return a human-readable category label from filename."""
    if name.startswith("feedback_"):
        return "Feedback"
    if name.startswith("user"):
        return "User Profile"
    if name.startswith("project"):
        return "Project"
    if name.startswith("reference"):
        return "Reference"
    return "Memory"


def _snippet(text: str, max_len: int = 800) -> str:
    clean = text.strip().replace("\r", "")
    if len(clean) <= max_len:
        return clean
    return clean[:max_len] + "\n...(truncated)"


def _append_daily_block(entries: list[dict]) -> Path:
    """Append memory ingest block to today's daily log."""
    ensure_structure()
    now = datetime.now(timezone.utc).astimezone()
    daily_path = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"

    if not daily_path.exists():
        daily_path.write_text(
            f"# Daily Log: {now.strftime('%Y-%m-%d')}\n\n"
            "## Sessions\n\n## Docs Ingest\n\n## Memory Maintenance\n\n",
            encoding="utf-8",
        )

    lines: list[str] = []
    for entry in entries:
        lines.append(f"#### {entry['category']}: `{entry['name']}`\n")
        lines.append(f"**Source:** `{entry['path']}`  ")
        lines.append(f"**Change:** {entry['change']}\n")
        lines.append(entry["content"])
        lines.append("")

    body = "\n".join(lines)
    time_str = now.strftime("%H:%M")
    block = f"### Memory Sync ({time_str})\n\n{body}\n"

    with daily_path.open("a", encoding="utf-8") as f:
        f.write(block)

    return daily_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Claude Code auto-memory files into SecondBrain daily log"
    )
    parser.add_argument(
        "--memory-dir",
        type=str,
        default=None,
        help="Path to Claude Code memory directory (default: auto-derived from project root)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument(
        "--force", action="store_true", help="Re-ingest all files regardless of hash"
    )
    args = parser.parse_args()

    ensure_structure()
    memory_dir = (
        Path(args.memory_dir).expanduser().resolve()
        if args.memory_dir
        else default_memory_dir()
    )

    if not memory_dir.exists():
        print(f"Memory directory not found: {memory_dir}")
        print(
            "  Hint: Claude Code creates this on first memory write. "
            "Set CLAUDE_CODE_MEMORY_DIR to override detection."
        )
        return 0  # not an error — just nothing to sync

    state = load_json(MEMORY_STATE_FILE, {"files": {}})
    tracked: dict = state.get("files", {})

    md_files = _collect_memory_files(memory_dir)
    if not md_files:
        print(f"No memory files in {memory_dir}")
        return 0

    changed: list[dict] = []
    for path in md_files:
        sig = file_hash(path)
        key = path.name
        prev_sig = tracked.get(key)

        if args.force or prev_sig is None or prev_sig != sig:
            change_label = "new" if prev_sig is None else "updated"
            content = path.read_text(encoding="utf-8")
            changed.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "category": _categorise(path.name),
                    "change": change_label,
                    "content": _snippet(content),
                }
            )
            tracked[key] = sig

    if not changed:
        print("No memory changes detected.")
        save_json(MEMORY_STATE_FILE, {"files": tracked})
        return 0

    print(f"Memory files changed: {len(changed)}")
    for entry in changed:
        print(f"  [{entry['change']:7s}] {entry['name']}")

    if args.dry_run:
        return 0

    daily_path = _append_daily_block(changed)
    print(f"Appended to: {daily_path}")
    save_json(MEMORY_STATE_FILE, {"files": tracked})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
