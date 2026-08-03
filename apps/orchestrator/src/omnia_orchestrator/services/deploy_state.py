"""Durable, single-flight deployment journal.

The public API returns immediately while a deployment continues in a task.
Every transition is atomically persisted, so a service restart can report an
interrupted run instead of pretending that no deployment happened.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from omnia_orchestrator.core.config import get_settings

_ACTIVE_PHASES = ("queued", "building", "pushing", "swapping", "cancelling")
_TERMINAL_PHASES = ("done", "failed", "cancelled")


class DeployRevisionConflict(RuntimeError):
    """An idempotency key or active run is bound to another immutable tree."""


@dataclass
class DeployRecord:
    project_id: str
    commit_sha: str | None = None
    run_id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = "queued"
    prod_url: str | None = None
    image_tag: str | None = None
    error: str | None = None
    detail: str | None = None
    target_label: str | None = None
    target_id: str | None = None
    can_cancel: bool = True
    logs: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


_records: dict[str, DeployRecord] = {}
_history: dict[str, list[DeployRecord]] = {}
_loaded = False


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _state_path() -> Path:
    override = os.getenv("OMNIA_DEPLOY_STATE_PATH")
    return Path(override or get_settings().deploy_state_path)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    changed = False
    for project_id, items in raw.get("history", {}).items():
        records: list[DeployRecord] = []
        for item in items:
            try:
                rec = DeployRecord(**item)
            except TypeError:
                continue
            if rec.phase in _ACTIVE_PHASES:
                rec.phase = "failed"
                rec.error = "Деплой прерван перезапуском сервиса. Запустите его повторно."
                rec.detail = rec.error
                rec.finished_at = now_iso()
                rec.can_cancel = False
                changed = True
            records.append(rec)
        if records:
            _history[project_id] = records[-20:]
            _records[project_id] = records[-1]
    if changed:
        _persist()


def _persist() -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "history": {
                project_id: [asdict(rec) for rec in records[-20:]]
                for project_id, records in _history.items()
            },
        }
        fd, tmp_name = tempfile.mkstemp(prefix=".deploy-runs-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    except OSError:
        # Runtime state must remain usable in read-only local/test environments.
        return


def get(project_id: str) -> DeployRecord | None:
    _load()
    return _records.get(project_id)


def history(project_id: str) -> list[DeployRecord]:
    _load()
    return list(_history.get(project_id, []))


def start(
    project_id: str,
    *,
    idempotency_key: str | None = None,
    commit_sha: str | None = None,
    target_label: str | None = None,
    target_id: str | None = None,
) -> DeployRecord:
    _load()
    current = _records.get(project_id)
    if current is not None and current.phase in _ACTIVE_PHASES:
        if commit_sha is not None and current.commit_sha != commit_sha:
            raise DeployRevisionConflict("another revision is already deploying")
        return current
    if idempotency_key:
        for rec in reversed(_history.get(project_id, [])):
            if rec.run_id == idempotency_key:
                if rec.commit_sha != commit_sha:
                    raise DeployRevisionConflict(
                        "idempotency key is already bound to another revision"
                    )
                return rec
    rec = DeployRecord(
        project_id=project_id,
        commit_sha=commit_sha,
        run_id=idempotency_key or str(uuid4()),
        phase="building",
        target_label=target_label,
        target_id=target_id,
        started_at=now_iso(),
    )
    _records[project_id] = rec
    _history.setdefault(project_id, []).append(rec)
    _history[project_id] = _history[project_id][-20:]
    _persist()
    return rec


def update(project_id: str, **fields: object) -> None:
    _load()
    rec = _records.get(project_id)
    if rec is None:
        return
    for key, value in fields.items():
        if not hasattr(rec, key):
            continue
        setattr(rec, key, value)
    if rec.phase in _TERMINAL_PHASES:
        rec.can_cancel = False
    _persist()


def append_log(project_id: str, message: str) -> None:
    _load()
    rec = _records.get(project_id)
    if rec is None:
        return
    rec.logs.append(message[:1000])
    rec.logs = rec.logs[-100:]
    rec.detail = message[:500]
    _persist()


def is_active(project_id: str) -> bool:
    _load()
    rec = _records.get(project_id)
    return rec is not None and rec.phase in _ACTIVE_PHASES


def reset_for_tests() -> None:
    """Clear module state; intentionally public for isolated tests."""
    global _loaded
    _records.clear()
    _history.clear()
    _loaded = False
