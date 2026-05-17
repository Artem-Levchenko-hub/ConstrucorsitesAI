"""Utility helpers for SecondBrain scripts."""

from __future__ import annotations

import json
import re
import unicodedata
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from config import (
    CONCEPTS_DIR,
    CONNECTIONS_DIR,
    DAILY_DIR,
    FLUSH_STATE_FILE,
    INDEX_FILE,
    KNOWLEDGE_DIR,
    LOG_FILE,
    MAINTENANCE_STATE_FILE,
    QA_DIR,
    RAW_DIR,
    RAW_WEB_DIR,
    SCRIPTS_DIR,
    STATE_FILE,
    TEMPLATES_DIR,
    TMP_DIR,
)


def ensure_structure() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    RAW_WEB_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    QA_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    if not INDEX_FILE.exists():
        template = TEMPLATES_DIR / "knowledge" / "index.md"
        if template.exists():
            INDEX_FILE.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            INDEX_FILE.write_text(
                "# Knowledge Base Index\n\n| Article | Summary | Compiled From | Updated |\n| --- | --- | --- | --- |\n",
                encoding="utf-8",
            )
    if not LOG_FILE.exists():
        template = TEMPLATES_DIR / "knowledge" / "log.md"
        if template.exists():
            LOG_FILE.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            LOG_FILE.write_text("# Build Log\n\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()[:16]


def load_json(path: Path, default: dict | None = None) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {} if default is None else default


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    return load_json(STATE_FILE, {"ingested": {}, "query_count": 0, "total_cost": 0.0})


def save_state(state: dict) -> None:
    save_json(STATE_FILE, state)


def load_flush_state() -> dict:
    return load_json(FLUSH_STATE_FILE, {})


def save_flush_state(state: dict) -> None:
    save_json(FLUSH_STATE_FILE, state)


def load_maintenance_state() -> dict:
    return load_json(MAINTENANCE_STATE_FILE, {})


def save_maintenance_state(state: dict) -> None:
    save_json(MAINTENANCE_STATE_FILE, state)


def list_daily_logs() -> list[Path]:
    if not DAILY_DIR.exists():
        return []
    return sorted([p for p in DAILY_DIR.glob("*.md") if p.is_file()], key=lambda p: p.name)


def list_wiki_articles() -> list[Path]:
    candidates: list[Path] = []
    for root in (CONCEPTS_DIR, CONNECTIONS_DIR, QA_DIR):
        if root.exists():
            candidates.extend([p for p in root.rglob("*.md") if p.is_file()])
    return sorted(candidates, key=lambda p: str(p).lower())


def read_index() -> str:
    if INDEX_FILE.exists():
        return INDEX_FILE.read_text(encoding="utf-8")
    return ""


def append_log_block(header: str, lines: Iterable[str]) -> None:
    existing = LOG_FILE.read_text(encoding="utf-8") if LOG_FILE.exists() else "# Build Log\n\n"
    block_lines = [f"## {header}"]
    for line in lines:
        block_lines.append(f"- {line}")
    block = "\n".join(block_lines) + "\n\n"
    LOG_FILE.write_text(existing + block, encoding="utf-8")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, text
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return {}, text

    meta: dict = {}
    current_list_key: str | None = None
    for line in lines[1:end_idx]:
        raw = line.rstrip()
        if not raw.strip():
            continue
        if raw.startswith("  - ") and current_list_key:
            meta.setdefault(current_list_key, [])
            meta[current_list_key].append(raw[4:].strip().strip('"'))
            continue

        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            meta[key] = []
            current_list_key = key
            continue
        current_list_key = None

        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"') for v in value[1:-1].split(",") if v.strip()]
            meta[key] = items
        else:
            meta[key] = value.strip('"')

    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return meta, body


def slugify(value: str) -> str:
    original = value
    value = value.strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    slug = value.strip("-")
    if slug:
        return slug
    digest = sha256(original.encode("utf-8")).hexdigest()[:8]
    return f"untitled-{digest}"


def extract_wikilinks(text: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", text)


def article_rel(path: Path) -> str:
    return str(path.relative_to(KNOWLEDGE_DIR)).replace("\\", "/")

