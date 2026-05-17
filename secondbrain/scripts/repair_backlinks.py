"""Auto-add missing wiki backlinks between knowledge articles."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from config import KNOWLEDGE_DIR
from utils import ensure_structure, extract_wikilinks, list_wiki_articles


def _normalize_key(path: Path) -> str:
    return str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/").removesuffix(".md")


def _resolve_target(link: str) -> str | None:
    clean = link.strip().strip("/")
    if clean.startswith("knowledge/"):
        clean = clean[len("knowledge/") :]
    if clean.startswith(("daily/", "raw/")):
        return None
    return clean.removesuffix(".md")


def _append_backlinks(path: Path, src_keys: list[str], dry_run: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False
    for src in src_keys:
        token = f"[[{src}]]"
        token2 = f"[[knowledge/{src}]]"
        if token in text or token2 in text:
            continue
        changed = True
        if "## Backlinks" not in text:
            text = text.rstrip() + "\n\n## Backlinks\n\n"
        if not text.endswith("\n"):
            text += "\n"
        text += f"- [[{src}]]\n"
    if changed and not dry_run:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair missing wiki backlinks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ensure_structure()
    articles = list_wiki_articles()
    by_key = {_normalize_key(p): p for p in articles}
    reverse_links: dict[str, list[str]] = defaultdict(list)

    for src_path in articles:
        src_key = _normalize_key(src_path)
        text = src_path.read_text(encoding="utf-8")
        for raw_link in extract_wikilinks(text):
            target_key = _resolve_target(raw_link)
            if not target_key:
                continue
            if target_key in by_key:
                reverse_links[target_key].append(src_key)

    updated = 0
    for target_key, src_keys in reverse_links.items():
        path = by_key.get(target_key)
        if not path:
            continue
        # Stable, unique order for deterministic diffs.
        unique_sorted = sorted(set(src_keys))
        if _append_backlinks(path, unique_sorted, args.dry_run):
            updated += 1
            print(f"Updated backlinks: {target_key}")

    print(f"Backlink repair done. Updated files: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

