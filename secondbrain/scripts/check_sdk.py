"""Health check for active LLM backend in SecondBrain."""

from __future__ import annotations

import json

from config import ROOT_DIR, SCRIPTS_DIR
from llm_provider import run_llm_text


def run_check() -> dict:
    result = run_llm_text("Return exactly PONG", ROOT_DIR, max_turns=1)
    if result.ok:
        return {
            "status": "ok",
            "backend": result.backend,
            "message": result.text.strip() or "No text response",
            "cost_usd": result.cost_usd,
        }
    return {
        "status": "error",
        "backend": result.backend,
        "error": result.error,
        "message": "LLM backend is unavailable in current environment.",
    }


def check_core_scripts() -> list[str]:
    required = [
        "compile.py",
        "query.py",
        "lint.py",
        "sync_project_docs.py",
        "ingest_web.py",
    ]
    return [name for name in required if not (SCRIPTS_DIR / name).exists()]


def main() -> int:
    missing = check_core_scripts()
    if missing:
        print(json.dumps({"status": "error", "missing_scripts": missing}, ensure_ascii=False, indent=2))
        return 1
    result = run_check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

