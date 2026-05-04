"""Append-only JSON-line audit log per request.

Schema (one object per line):
    {timestamp, user_id, project_id, message_id, model, tokens_in, tokens_out,
     cost_rub, cache_hit, fallback_used, stream}

Rotation: file path is `logs/llm-{YYYY-MM-DD}.jsonl` — daily rotation by name.
Old files are kept until manual cleanup; structlog already handles process logs
to stdout.

PII rule: no message content is logged here.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

LOG_DIR = Path(os.environ.get("OMNIA_GATEWAY_LOG_DIR", "logs"))


def _today_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"llm-{datetime.now(tz=UTC).strftime('%Y-%m-%d')}.jsonl"


def _serialize(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value


def log_request(payload: dict[str, Any]) -> None:
    """Append one structured record. Synchronous — file I/O is fast enough at
    MVP volumes; switch to a queue/aio file if it ever shows up in flame graphs."""
    record = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        **{k: _serialize(v) for k, v in payload.items()},
    }
    with _today_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
