"""Incremental ingestion of Omnia.AI markdown docs into daily logs.

Covers:
- root `README.md`, `CLAUDE.md`
- directories in DOC_DIRS (recursive *.md)
- EXTRA_PATTERNS for non-standard spots: Cursor rules (.mdc),
  per-agent briefs, per-app CLAUDE.md, alembic migration notes

Project root auto-derived from `config.PROJECT_ROOT` (= secondbrain/.., i.e.
the omnia-mvp repo root). Override with `--project-root` flag.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from config import DAILY_DIR, DOCS_STATE_FILE, PROJECT_ROOT
from utils import ensure_structure, file_hash, load_json, save_json

# Recursive *.md ingestion roots
DOC_DIRS = (
    "docs",
    "agents",
)

# Extra targeted patterns: (directory, glob). Each glob applied with rglob.
EXTRA_PATTERNS: tuple[tuple[str, str], ...] = (
    (".cursor/rules", "*.mdc"),
    (".cursor/agents", "*.md"),
    (".claude/agents", "*.md"),  # may not exist yet; ignored if so
    ("apps/api/migrations", "*.md"),
    ("apps/web", "CLAUDE.md"),
    ("apps/api", "CLAUDE.md"),
    ("apps/llm-gateway", "CLAUDE.md"),
    ("apps/llm-gateway/deploy", "*.md"),
)

# Tags that match --focus flag. When --focus is set, we restrict to matching groups.
FOCUS_GROUPS = {
    "docs": {"dirs": ("docs",), "patterns": ()},
    "agents": {"dirs": ("agents",), "patterns": ()},
    "apps": {
        "dirs": (),
        "patterns": (
            ("apps/web", "CLAUDE.md"),
            ("apps/api", "CLAUDE.md"),
            ("apps/llm-gateway", "CLAUDE.md"),
        ),
    },
    "rules-agents": {
        "dirs": (),
        "patterns": (
            (".cursor/rules", "*.mdc"),
            (".cursor/agents", "*.md"),
            (".claude/agents", "*.md"),
        ),
    },
    "migrations": {"dirs": (), "patterns": (("apps/api/migrations", "*.md"),)},
}


def _collect_markdown_files(
    project_root: Path,
    dirs: tuple[str, ...] = DOC_DIRS,
    patterns: tuple[tuple[str, str], ...] = EXTRA_PATTERNS,
    include_root_readme: bool = True,
) -> list[Path]:
    """Collect markdown files from DOC_DIRS + EXTRA_PATTERNS.

    Deduplicates by resolved path so the same file listed by both a directory
    and a pattern is ingested once.
    """
    seen: set[Path] = set()
    files: list[Path] = []

    if include_root_readme:
        readme = project_root / "README.md"
        if readme.exists():
            resolved = readme.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(readme)

    for folder in dirs:
        base = project_root / folder
        if base.exists():
            for p in sorted(base.rglob("*.md"), key=lambda x: str(x).lower()):
                resolved = p.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(p)

    for subdir, pattern in patterns:
        base = project_root / subdir
        if base.exists():
            for p in sorted(base.rglob(pattern), key=lambda x: str(x).lower()):
                resolved = p.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(p)

    return files


def _snippet(text: str, max_len: int = 1200) -> str:
    clean = text.strip().replace("\r", "")
    if len(clean) <= max_len:
        return clean
    return clean[:max_len] + "\n...(truncated)"


def _append_daily_block(lines: list[str]) -> Path:
    ensure_structure()
    now = datetime.now(timezone.utc).astimezone()
    daily_path = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    if not daily_path.exists():
        daily_path.write_text(
            f"# Daily Log: {now.strftime('%Y-%m-%d')}\n\n## Sessions\n\n## Docs Ingest\n\n## Memory Maintenance\n\n",
            encoding="utf-8",
        )

    body = "\n".join(lines)
    with daily_path.open("a", encoding="utf-8") as f:
        f.write(f"### Docs Ingest ({now.strftime('%H:%M')})\n\n{body}\n\n")
    return daily_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync markdown docs into daily logs")
    parser.add_argument(
        "--project-root",
        type=str,
        default=str(PROJECT_ROOT),
        help="Repo root (default: auto-derived from secondbrain/..)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--focus",
        type=str,
        choices=sorted(FOCUS_GROUPS.keys()),
        help="Restrict scan to one group: docs | frontend | loadtest | rules-agents | migrations",
    )
    args = parser.parse_args()

    ensure_structure()
    project_root = Path(args.project_root).resolve()
    state = load_json(DOCS_STATE_FILE, {"files": {}})
    tracked: dict = state.get("files", {})

    if args.focus:
        grp = FOCUS_GROUPS[args.focus]
        md_files = _collect_markdown_files(
            project_root,
            dirs=grp["dirs"],
            patterns=grp["patterns"],
            include_root_readme=False,
        )
    else:
        md_files = _collect_markdown_files(project_root)

    changed: list[tuple[Path, str]] = []
    for path in md_files:
        sig = file_hash(path)
        try:
            rel = str(path.relative_to(project_root)).replace("\\", "/")
        except ValueError:
            rel = str(path).replace("\\", "/")
        if tracked.get(rel) != sig:
            changed.append((path, rel))
            tracked[rel] = sig

    if not changed:
        print("No docs changes detected.")
        save_json(DOCS_STATE_FILE, {"files": tracked})
        return 0

    print(f"Docs changed: {len(changed)}" + (f" (focus={args.focus})" if args.focus else ""))
    if args.dry_run:
        for _path, rel in changed:
            print(f" - {rel}")
        return 0

    lines: list[str] = [
        f"Project root: `{project_root}`",
        f"Focus: `{args.focus}`" if args.focus else "",
        "",
        "**Changed markdown files:**",
    ]
    for path, rel in changed:
        content = path.read_text(encoding="utf-8", errors="ignore")
        lines.append(f"- `{rel}`")
        lines.append("")
        lines.append(f"#### Source: {rel}")
        lines.append("```markdown")
        lines.append(_snippet(content))
        lines.append("```")
        lines.append("")

    daily_file = _append_daily_block(lines)
    save_json(DOCS_STATE_FILE, {"files": tracked})
    print(f"Appended docs ingest to {daily_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
