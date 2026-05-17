"""Cursor/Claude Code sessionStart hook: inject SecondBrain context.

Also fires a once-per-day background sync of Claude Code auto-memory
(MEMORY.md + feedback_*.md) into the SecondBrain daily log so that
memory edits flow through the same compile→wiki pipeline.

## Tier-gated concept injection

Each concept file declares `tier: hot | warm | archive` in frontmatter.
- `hot`    — always injected first, sorted by `updated:` DESC.
- `warm`   — injected after hot, sorted by `updated:` DESC, until the
             hard budget (MAX_CONCEPT_CHARS) is reached.
- `archive` — NEVER injected; still searchable via /sb-query and /sb-compile.

Result: the 28k char context budget is spent on active invariants first,
stale/one-off concepts don't compete. Raise the wiki without token tax —
just add `tier: archive` to the new file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config import DAILY_DIR, INDEX_FILE  # noqa: E402
from utils import ensure_structure  # noqa: E402

try:
    from sync_memory import default_memory_dir  # noqa: E402
except Exception:
    default_memory_dir = None  # type: ignore[assignment]

MAX_CONTEXT_CHARS = 28_000
# Concept-section sub-budget — leaves room for project-context, memory,
# and recent daily sections in the overall 28k budget.
MAX_CONCEPT_CHARS = 20_000
MAX_RECENT_LOG_LINES = 40
PROJECT_CONTEXT_FILE = ROOT_DIR / "knowledge" / "project-context.md"
SYNC_MEMORY_SCRIPT = SCRIPTS_DIR / "sync_memory.py"
LAST_SYNC_MARKER = SCRIPTS_DIR / "last-memory-sync.json"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z_]+)\s*:\s*(?P<val>.*?)\s*$", re.MULTILINE)


def _neg_iso(s: str) -> str:
    """Return a key that sorts ISO date strings DESC when used in ASC sort.

    Missing/empty strings pad to '0000-00-00' → sort LAST within their tier
    (behind real dates). Char codes are inverted so newer dates (higher raw
    codes) produce lexicographically smaller keys.
    """
    padded = (s or "")[:12].ljust(12, "0")
    return "".join(chr(255 - ord(c)) for c in padded)


def _memory_dir() -> Path | None:
    if default_memory_dir is None:
        return None
    try:
        return default_memory_dir()
    except Exception:
        return None


def _read_project_context() -> str:
    if PROJECT_CONTEXT_FILE.exists():
        return PROJECT_CONTEXT_FILE.read_text(encoding="utf-8")
    return ""


def _read_memory_index() -> str:
    """Read MEMORY.md (the Claude Code auto-memory index) if present."""
    mem_dir = _memory_dir()
    if not mem_dir:
        return ""
    memory_index = mem_dir / "MEMORY.md"
    if memory_index.exists():
        try:
            return memory_index.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def _maybe_spawn_memory_sync() -> None:
    """Fire sync_memory.py in the background once per day.

    Guards against re-running during the same UTC date. Never raises —
    session start must not block on this.
    """
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return
    if not SYNC_MEMORY_SCRIPT.exists():
        return
    mem_dir = _memory_dir()
    if not mem_dir or not mem_dir.exists():
        return

    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    try:
        if LAST_SYNC_MARKER.exists():
            marker = json.loads(LAST_SYNC_MARKER.read_text(encoding="utf-8"))
            if marker.get("date") == today:
                return
    except Exception:
        pass

    try:
        env = os.environ.copy()
        env["CLAUDE_INVOKED_BY"] = "secondbrain_session_start_memory_sync"
        kwargs: dict = {
            "cwd": str(ROOT_DIR),
            "env": env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [sys.executable, str(SYNC_MEMORY_SCRIPT)],
            **kwargs,
        )
        LAST_SYNC_MARKER.write_text(
            json.dumps({"date": today}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        # Never block session start on memory sync failure.
        pass


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract the YAML-ish top block `---\\n...\\n---\\n` into a flat dict."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    block = m.group(1)
    result: dict[str, str] = {}
    for fm in _FIELD_RE.finditer(block):
        result[fm.group("key")] = fm.group("val")
    return result


def _concept_title(meta: dict[str, str], path: Path) -> str:
    """Use frontmatter `title:` if present; strip quotes. Fallback to stem."""
    t = (meta.get("title") or "").strip().strip('"').strip("'")
    return t or path.stem


def _read_concept_articles() -> str:
    """Tier-gated concept injection with compact overflow catalog.

    Pass 1: full-content inject of `hot` → `warm` sorted by `updated:` DESC
            until MAX_CONCEPT_CHARS - CATALOG_RESERVE is reached.
    Pass 2: for every non-archive article that didn't fit in pass 1, emit a
            1-line catalog entry (title + tier + updated + keywords) so the
            LLM knows the article exists and can pull it via /sb-query.
    Archive tier is never injected and never listed — it stays searchable
    but invisible to SessionStart.
    """
    concepts_dir = ROOT_DIR / "knowledge" / "concepts"
    if not concepts_dir.exists():
        return ""

    # Reserve ~3k chars of the concept sub-budget for the catalog footer.
    CATALOG_RESERVE = 3_000
    full_budget = max(MAX_CONCEPT_CHARS - CATALOG_RESERVE, 8_000)

    items: list[tuple[str, str, Path, dict[str, str], str]] = []  # (tier, updated, path, meta, content)
    for article in concepts_dir.glob("*.md"):
        try:
            content = article.read_text(encoding="utf-8")
        except Exception:
            continue
        if len(content) < 200:
            continue  # fallback-only stubs
        meta = _parse_frontmatter(content)
        tier = (meta.get("tier") or "warm").strip().lower()
        if tier == "archive":
            continue  # never injected, never catalogued — /sb-query only
        if tier not in ("hot", "warm"):
            tier = "warm"
        updated = (meta.get("updated") or "").strip()
        items.append((tier, updated, article, meta, content))

    # Primary: tier (hot before warm). Secondary: updated DESC (ISO lex).
    _order = {"hot": 0, "warm": 1}
    items.sort(key=lambda row: (_order.get(row[0], 9), _neg_iso(row[1])))

    full_parts: list[str] = []
    overflow: list[tuple[str, str, Path, dict[str, str]]] = []
    running = 0
    for tier, updated, path, meta, content in items:
        if running + len(content) > full_budget and full_parts:
            overflow.append((tier, updated, path, meta))
            continue
        full_parts.append(content)
        running += len(content) + 7  # separator overhead

    result = "\n\n---\n\n".join(full_parts)

    if overflow:
        catalog_lines = [
            "\n\n---\n\n## Knowledge catalog (pull via /sb-query if needed)\n",
        ]
        for tier, updated, path, meta in overflow:
            title = _concept_title(meta, path)
            if len(title) > 80:
                title = title[:77] + "…"
            keywords = (meta.get("keywords") or "").strip()
            if len(keywords) > 120:
                keywords = keywords[:117] + "…"
            kw_suffix = f" — {keywords}" if keywords else ""
            upd = f" ({updated})" if updated else ""
            line = f"- [[concepts/{path.stem}]] **{title}** `{tier}`{upd}{kw_suffix}"
            catalog_lines.append(line)
        catalog = "\n".join(catalog_lines)
        # Hard-trim the catalog if it overshoots its reserve.
        if len(catalog) > CATALOG_RESERVE:
            catalog = catalog[:CATALOG_RESERVE] + "\n…(catalog truncated)"
        result += catalog

    return result


def _read_recent_daily_tail() -> str:
    today = datetime.now(timezone.utc).astimezone()
    for offset in range(3):
        day = today - timedelta(days=offset)
        path = DAILY_DIR / f"{day.strftime('%Y-%m-%d')}.md"
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            # Get only Session/decisions sections, skip raw docs ingest
            filtered: list[str] = []
            skip = False
            for line in lines:
                if line.startswith("### Docs Ingest"):
                    skip = True
                elif line.startswith("### ") and "Docs Ingest" not in line:
                    skip = False
                if not skip:
                    filtered.append(line)
            tail = filtered[-MAX_RECENT_LOG_LINES:] if len(filtered) > MAX_RECENT_LOG_LINES else filtered
            return "\n".join(tail)
    return "(no daily entries yet)"


def build_context() -> str:
    ensure_structure()
    _maybe_spawn_memory_sync()

    today = datetime.now(timezone.utc).astimezone().strftime("%A, %Y-%m-%d")
    project_ctx = _read_project_context()
    recent_daily = _read_recent_daily_tail()
    memory_index = _read_memory_index()

    parts: list[str] = [f"## Today\n{today}"]

    if memory_index.strip():
        parts.append(
            "## Claude Code Auto-Memory Index\n"
            "(Source of truth for user/feedback/project/reference memories — "
            "edits here are synced into SecondBrain daily log once per day.)\n\n"
            + memory_index
        )

    if project_ctx:
        parts.append(f"## Project Knowledge (SecondBrain)\n{project_ctx}")

    concepts = _read_concept_articles()
    if concepts:
        parts.append(f"## Recent Knowledge Articles\n{concepts}")

    if recent_daily.strip():
        parts.append(f"## Recent Session Activity\n{recent_daily}")

    context = "\n\n".join(parts)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"
    return context


def main() -> int:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": build_context(),
        }
    }
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=True).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
