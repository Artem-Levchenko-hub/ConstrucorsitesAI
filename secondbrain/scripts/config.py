"""Path constants and runtime configuration for repo-local SecondBrain."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def _resolve_root() -> Path:
    env_root = os.environ.get("SECOND_BRAIN_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _resolve_root()

# Auto-load `secondbrain/.env` so every script + hook + cron job that imports
# this module picks up SECOND_BRAIN_LLM_BACKEND, GEMINI_API_KEY, optional
# GEMINI_HTTPS_PROXY etc. without each entry point having to remember to
# `load_dotenv` first. We only set values that aren't already in the real env
# — explicit shell exports always win over the file.
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]

    load_dotenv(ROOT_DIR / ".env", override=False)
except Exception:
    # python-dotenv is listed in pyproject deps, so this should never fire in
    # a normal `uv sync`-ed env. Keep the import defensive so a missing
    # package doesn't bring the whole compiler down — just silently skips.
    pass
PROJECT_ROOT = ROOT_DIR.parent
RAW_DIR = ROOT_DIR / "raw"
RAW_WEB_DIR = RAW_DIR / "web"
DAILY_DIR = ROOT_DIR / "daily"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
REPORTS_DIR = ROOT_DIR / "reports"
SCRIPTS_DIR = ROOT_DIR / "scripts"
HOOKS_DIR = ROOT_DIR / "hooks"
AGENTS_FILE = ROOT_DIR / "AGENTS.md"
README_FILE = ROOT_DIR / "README.md"
TEMPLATES_DIR = ROOT_DIR / "templates"

INDEX_FILE = KNOWLEDGE_DIR / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
STATE_FILE = SCRIPTS_DIR / "state.json"
FLUSH_STATE_FILE = SCRIPTS_DIR / "last-flush.json"
DOCS_STATE_FILE = SCRIPTS_DIR / "docs-state.json"
MAINTENANCE_STATE_FILE = SCRIPTS_DIR / "maintenance-state.json"
TMP_DIR = SCRIPTS_DIR / "tmp"

COMPILE_AFTER_HOUR = int(os.environ.get("SECOND_BRAIN_COMPILE_AFTER_HOUR", "18"))


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

