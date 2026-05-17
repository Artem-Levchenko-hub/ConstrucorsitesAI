"""Wiki staleness checker.

For each concept/connection article in `knowledge/`, extract `updated:` from
frontmatter and every `sources:` entry. Run `git log --since=<updated>` for
each source path to count commits since the article was last updated.

Articles where commit count exceeds a threshold are flagged as stale.
Ranked report is written to `reports/staleness-YYYY-MM-DD.md`.

Usage:
    uv run python scripts/check_staleness.py
    uv run python scripts/check_staleness.py --threshold 20 --since-days 14
    uv run python scripts/check_staleness.py --project-root /abs/path
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config import KNOWLEDGE_DIR, PROJECT_ROOT, REPORTS_DIR, now_iso
from utils import ensure_structure, list_wiki_articles, parse_frontmatter


DEFAULT_HOT_THRESHOLD = 20
DEFAULT_COLD_THRESHOLD = 50


def _git_commit_count(project_root: Path, path: str, since: str) -> int:
    """Count commits to `path` in `project_root` since ISO date `since`.

    Returns 0 on any error (missing file, git failure). Designed to fail soft.
    """
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "log",
                f"--since={since}",
                "--oneline",
                "--",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return 0
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        return len(lines)
    except Exception:
        return 0


def _hot_source(path: str) -> bool:
    """Heuristic: "hot" paths are active code; use a lower threshold for them."""
    hot_markers = (
        "/hooks/",
        "/stores/",
        "/utils/calls/",
        "/crypto/",
        "/services/",
        "/routers/",
        "/components/",
        "main.py",
    )
    return any(m in path for m in hot_markers)


def scan_article(
    article_path: Path,
    project_root: Path,
    hot_threshold: int,
    cold_threshold: int,
) -> dict | None:
    """Return staleness verdict for one article, or None if fully fresh."""
    text = article_path.read_text(encoding="utf-8", errors="ignore")
    meta, _body = parse_frontmatter(text)
    updated = str(meta.get("updated") or "").strip()
    sources = meta.get("sources") or []
    if not updated or not sources or not isinstance(sources, list):
        return None

    stale_entries: list[dict] = []
    for src in sources:
        src = str(src).strip()
        if not src:
            continue
        # Only check paths that look like repo files (skip URL-like or
        # internal meta references).
        if src.startswith("http") or src.startswith("//"):
            continue
        commits = _git_commit_count(project_root, src, updated)
        threshold = hot_threshold if _hot_source(src) else cold_threshold
        if commits >= threshold:
            stale_entries.append(
                {
                    "source": src,
                    "commits": commits,
                    "threshold": threshold,
                    "hot": _hot_source(src),
                }
            )

    if not stale_entries:
        return None

    total_commits = sum(e["commits"] for e in stale_entries)
    return {
        "article": str(article_path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/"),
        "updated": updated,
        "stale_sources": stale_entries,
        "total_commits_since_update": total_commits,
    }


def write_report(verdicts: list[dict], project_root: Path) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"staleness-{today}.md"

    # Sort by highest total commit count first
    ranked = sorted(verdicts, key=lambda v: v["total_commits_since_update"], reverse=True)

    lines: list[str] = [
        "# SecondBrain Staleness Report",
        "",
        f"Generated: {now_iso()}",
        f"Project root: `{project_root}`",
        f"Articles flagged: {len(ranked)}",
        "",
    ]

    if not ranked:
        lines.append("No stale articles. :white_check_mark:")
    else:
        lines.append("## Ranked by commit activity since `updated:`")
        lines.append("")
        lines.append("| Article | Updated | Total commits | Stale sources |")
        lines.append("| --- | --- | ---: | --- |")
        for v in ranked:
            src_preview = ", ".join(
                f"`{e['source']}` ({e['commits']})" for e in v["stale_sources"][:3]
            )
            if len(v["stale_sources"]) > 3:
                src_preview += f" +{len(v['stale_sources']) - 3}"
            lines.append(
                f"| [[{v['article'].removesuffix('.md')}]] | {v['updated']} |"
                f" {v['total_commits_since_update']} | {src_preview} |"
            )
        lines.append("")
        lines.append("## Suggested action")
        lines.append("")
        lines.append(
            "- Top 3 — refresh first; re-read hot source files and append"
            " `## Update YYYY-MM-DD` section."
        )
        lines.append("- Hot-file threshold: hooks/stores/utils/calls/crypto/services/routers/components.")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check SecondBrain wiki staleness")
    parser.add_argument(
        "--project-root",
        type=str,
        default=str(PROJECT_ROOT),
        help="Repo root (default: auto-derived)",
    )
    parser.add_argument(
        "--hot-threshold",
        type=int,
        default=DEFAULT_HOT_THRESHOLD,
        help="Commits to a hot-path source since updated: to flag (default 20)",
    )
    parser.add_argument(
        "--cold-threshold",
        type=int,
        default=DEFAULT_COLD_THRESHOLD,
        help="Commits to a cold-path source since updated: to flag (default 50)",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Only check articles older than N days (skip very fresh)",
    )
    args = parser.parse_args()

    ensure_structure()
    project_root = Path(args.project_root).resolve()

    articles = list_wiki_articles()
    today = datetime.now(timezone.utc).astimezone()
    verdicts: list[dict] = []

    for article in articles:
        if args.since_days is not None:
            text = article.read_text(encoding="utf-8", errors="ignore")
            meta, _ = parse_frontmatter(text)
            updated = str(meta.get("updated") or "").strip()
            if updated:
                try:
                    updated_dt = datetime.strptime(updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if (today - updated_dt).days < args.since_days:
                        continue
                except ValueError:
                    pass

        verdict = scan_article(
            article,
            project_root,
            hot_threshold=args.hot_threshold,
            cold_threshold=args.cold_threshold,
        )
        if verdict:
            verdicts.append(verdict)

    report = write_report(verdicts, project_root)
    if verdicts:
        print(f"Stale articles: {len(verdicts)}")
        for v in sorted(verdicts, key=lambda v: v["total_commits_since_update"], reverse=True)[:5]:
            print(f"  [{v['total_commits_since_update']:4d}] {v['article']} (updated {v['updated']})")
    else:
        print("No stale articles.")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
