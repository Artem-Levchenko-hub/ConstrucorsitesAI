"""Ingest web pages into raw/web markdown snapshots."""

from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from config import RAW_WEB_DIR, now_iso
from utils import append_log_block, ensure_structure, list_wiki_articles, slugify


def _sanitize_text(html: str, max_len: int = 20000) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[:max_len] + " ...(truncated)"
    return text


def _fetch_url(url: str, timeout_sec: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (SecondBrain ingest_web.py)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="ignore")


def _build_output_path(url: str, title_hint: str | None) -> Path:
    parsed = urlparse(url)
    domain = slugify(parsed.netloc or "web")
    stem_base = title_hint or (parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else "index")
    stem = slugify(stem_base) or "page"
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    out_dir = RAW_WEB_DIR / today / domain
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{stem}.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest web page into raw/web markdown")
    parser.add_argument("url", help="HTTP/HTTPS URL to ingest")
    parser.add_argument("--title", type=str, default="", help="Optional title hint for filename")
    args = parser.parse_args()

    ensure_structure()
    wiki_before = len(list_wiki_articles())
    url = args.url.strip()
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        print("Only http/https URLs are supported.")
        return 1

    try:
        html = _fetch_url(url)
    except urllib.error.URLError as e:
        print(f"Failed to fetch URL: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected fetch error: {e}")
        return 1

    content = _sanitize_text(html)
    out_path = _build_output_path(url, args.title.strip() or None)
    fetched_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    body = (
        "---\n"
        f'url: "{url}"\n'
        f"fetched_at: {fetched_iso}\n"
        "---\n\n"
        f"# {args.title.strip() or 'Web Snapshot'}\n\n"
        f"Source: {url}\n\n"
        "## Extracted Text\n\n"
        f"{content}\n"
    )
    out_path.write_text(body, encoding="utf-8")
    append_log_block(
        f"[{now_iso()}] ingest-web | {url}",
        [
            f"Raw snapshot written: `{out_path}`",
            f"Wiki articles currently: {wiki_before}",
            "Next step: run compile/maintenance to convert raw notes into wiki concepts.",
        ],
    )
    print(f"Saved: {out_path}")
    print(f"Ingest metrics: raw_written=1, wiki_current={wiki_before}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

