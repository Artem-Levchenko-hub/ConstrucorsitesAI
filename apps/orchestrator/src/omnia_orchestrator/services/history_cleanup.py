"""Durable cleanup journal for disposable historical runtimes.

The record is written before Postgres or Docker is touched.  If provisioning
dies before a labelled container exists, the startup/periodic sweeper can still
remove the isolated schema and nginx host.  Records contain identifiers only;
credentials are never persisted here.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from time import time

from omnia_orchestrator.core.config import get_settings

_LOCK = Lock()


@dataclass(frozen=True)
class HistoryCleanupRecord:
    project_id: str
    snapshot_id: str
    purpose: str
    database_id: str
    origin: str | None = None
    session_id: str | None = None
    created_epoch: float = 0.0

    def __post_init__(self) -> None:
        if self.created_epoch <= 0:
            object.__setattr__(self, "created_epoch", time())


def _state_path() -> Path:
    override = os.getenv("OMNIA_HISTORY_CLEANUP_PATH")
    return Path(override or get_settings().history_cleanup_path)


def _load_unlocked() -> dict[str, HistoryCleanupRecord]:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        # A corrupt journal must block new provisioning instead of losing the
        # only durable reference to already-created external resources.
        raise RuntimeError("history cleanup journal is unreadable") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("records", {}), dict):
        raise RuntimeError("history cleanup journal is invalid")
    records: dict[str, HistoryCleanupRecord] = {}
    for key, value in raw.get("records", {}).items():
        try:
            records[key] = HistoryCleanupRecord(**value)
        except (TypeError, ValueError):
            raise RuntimeError("history cleanup journal is invalid") from None
    return records


def _persist_unlocked(records: dict[str, HistoryCleanupRecord]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".history-cleanup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "records": {key: asdict(item) for key, item in records.items()}},
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def remember(record: HistoryCleanupRecord) -> None:
    with _LOCK:
        records = _load_unlocked()
        records[record.database_id] = record
        _persist_unlocked(records)


def forget(database_id: str) -> None:
    with _LOCK:
        records = _load_unlocked()
        if records.pop(database_id, None) is not None:
            _persist_unlocked(records)


def list_records() -> list[HistoryCleanupRecord]:
    with _LOCK:
        return list(_load_unlocked().values())
