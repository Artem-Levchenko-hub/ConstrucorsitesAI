"""Lint checks for SecondBrain knowledge health."""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from config import DAILY_DIR, KNOWLEDGE_DIR, RAW_DIR, REPORTS_DIR, now_iso
from llm_provider import run_llm_text
from utils import ensure_structure, extract_wikilinks, list_daily_logs, list_wiki_articles


def _link_to_path(link: str) -> Path:
    clean = link.strip().strip("/")
    if clean.startswith("knowledge/"):
        clean = clean[len("knowledge/") :]
        rel = clean if clean.endswith(".md") else f"{clean}.md"
        return KNOWLEDGE_DIR / rel
    if clean.startswith("daily/"):
        rel = clean[len("daily/") :]
        rel = rel if rel.endswith(".md") else f"{rel}.md"
        return DAILY_DIR / rel
    if clean.startswith("raw/"):
        rel = clean[len("raw/") :]
        rel = rel if rel.endswith(".md") else f"{rel}.md"
        return RAW_DIR / rel
    rel = clean if clean.endswith(".md") else f"{clean}.md"
    return KNOWLEDGE_DIR / rel


def run_structural_checks() -> dict:
    articles = list_wiki_articles()
    rel_map = {
        str(p.relative_to(KNOWLEDGE_DIR)).replace("\\", "/").removesuffix(".md"): p
        for p in articles
    }

    broken_links: list[str] = []
    inbound: defaultdict[str, int] = defaultdict(int)
    missing_backlinks: list[str] = []
    sparse: list[str] = []
    stale: list[str] = []

    all_daily_names = {p.name for p in list_daily_logs()}
    cited_daily: set[str] = set()

    for path in articles:
        rel = str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        words = re.findall(r"\w+", text, flags=re.UNICODE)
        if len(words) < 200:
            sparse.append(rel)

        for daily_name in all_daily_names:
            if daily_name in text:
                cited_daily.add(daily_name)

        links = extract_wikilinks(text)
        normalized: set[str] = set()
        for link in links:
            target = _link_to_path(link)
            if target.exists():
                try:
                    rel_target = str(target.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
                    key = rel_target.removesuffix(".md")
                    normalized.add(key)
                    inbound[key] += 1
                    if target.stat().st_mtime > path.stat().st_mtime:
                        stale.append(rel)
                except ValueError:
                    # Daily/raw links are valid, but they are not part of wiki backlink graph.
                    pass
            else:
                broken_links.append(f"{rel} -> [[{link}]]")

        for target_key in normalized:
            target_path = rel_map.get(target_key)
            if not target_path:
                continue
            target_text = target_path.read_text(encoding="utf-8")
            src_key = rel.removesuffix(".md")
            if f"[[{src_key}]]" not in target_text and f"[[knowledge/{src_key}]]" not in target_text:
                missing_backlinks.append(f"{rel} -> {target_key}")

    orphan_pages = [
        str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
        for path in articles
        if inbound.get(str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/").removesuffix(".md"), 0) == 0
    ]
    orphan_sources = sorted(all_daily_names - cited_daily)

    return {
        "broken_links": sorted(set(broken_links)),
        "orphan_pages": sorted(set(orphan_pages)),
        "orphan_sources": orphan_sources,
        "stale_articles": sorted(set(stale)),
        "missing_backlinks": sorted(set(missing_backlinks)),
        "sparse_articles": sorted(set(sparse)),
    }


def run_contradiction_check() -> list[str]:
    articles = list_wiki_articles()
    if len(articles) < 2:
        return []

    summaries: list[str] = []
    for path in articles[:40]:
        rel = str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        summaries.append(f"### {rel}\n{text[:1800]}")

    prompt = (
        "Identify possible contradictions between the following knowledge articles. "
        "Return a short markdown bullet list. If none, return 'No contradictions found.'\n\n"
        + "\n\n".join(summaries)
    )
    result = run_llm_text(prompt, KNOWLEDGE_DIR.parent, max_turns=2)
    if not result.ok:
        return [f"LLM contradiction check skipped: {result.error}"]
    if not result.text.strip():
        return []
    return [line for line in result.text.splitlines() if line.strip()]


def write_report(structural: dict, contradictions: list[str]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    report = REPORTS_DIR / f"lint-{today}.md"

    def _section(title: str, items: list[str]) -> str:
        if not items:
            return f"## {title}\n\n- OK\n\n"
        return f"## {title}\n\n" + "\n".join(f"- {item}" for item in items) + "\n\n"

    content = "# SecondBrain Lint Report\n\n"
    content += f"Generated: {now_iso()}\n\n"
    content += _section("Broken links", structural["broken_links"])
    content += _section("Orphan pages", structural["orphan_pages"])
    content += _section("Orphan sources", structural["orphan_sources"])
    content += _section("Stale articles", structural["stale_articles"])
    content += _section("Missing backlinks", structural["missing_backlinks"])
    content += _section("Sparse articles", structural["sparse_articles"])
    content += _section("Contradictions", contradictions)
    report.write_text(content, encoding="utf-8")
    return report


def main() -> int:
    ensure_structure()
    parser = argparse.ArgumentParser(description="Run SecondBrain lint checks")
    parser.add_argument("--structural-only", action="store_true", help="Skip contradiction check")
    args = parser.parse_args()

    structural = run_structural_checks()
    contradictions: list[str] = []
    if not args.structural_only:
        contradictions = run_contradiction_check()

    report = write_report(structural, contradictions)
    print(f"Lint report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

