"""Initialize local writable SecondBrain workspace directories."""

from __future__ import annotations

from config import CONCEPTS_DIR, CONNECTIONS_DIR, DAILY_DIR, KNOWLEDGE_DIR, QA_DIR, RAW_DIR, REPORTS_DIR
from utils import ensure_structure


def main() -> int:
    ensure_structure()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    QA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    print("SecondBrain workspace initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

