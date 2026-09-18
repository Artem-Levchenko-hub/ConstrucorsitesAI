"""P01: durable publication substages, heartbeat, byte progress, reason codes.

One trace per publication run. Stage transitions and byte counters may be
recorded from any thread — blocking Docker work runs in the executor — but the
journal is written only by the event-loop owner: immediately when a stage
changes on the loop thread, otherwise by the heartbeat task at most once per
``heartbeat_seconds``. A transferred chunk never costs an fsync.

Timestamps are UTC ISO strings and elapsed values are monotonic deltas taken at
record time, so a restart of the controller keeps the finished stages exact and
never re-bases them on a new monotonic origin.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

FORMAT_VERSION = 2

STAGES = (
    "preflight",
    "source_wake",
    "source_schema",
    "preflight_target",
    "capture_rootfs",
    "capture_volumes",
    "verify_artifacts",
    "resume_source",
    "prepare_target",
    "seed_data",
    "activate",
    "start_app",
    "verify_runtime",
    "tls",
    "observe",
)

# Fixed reason codes; raw Docker/SQL/env text never reaches the public status.
_REASON_BY_MESSAGE = {
    "publication_migration_required": "migration_required",
    "publication startup changed database schema": "schema_changed",
    "public product service readiness failed": "service_readiness_failed",
    "public HTTPS activation failed": "tls_failed",
    "public HTTPS bootstrap readiness failed": "public_probe_failed",
    "public gateway missing after readiness": "gateway_missing",
    "publication environment capture missing": "capture_missing",
    "publication source identity or fence changed": "source_changed",
    "publication source revision changed": "source_changed",
    "publication accepted machine epoch changed": "source_changed",
    "publication disabled": "publication_disabled",
    "publication needs free disk space": "insufficient_disk",
    "another publication is active": "publication_active",
}
_REASON_BY_TYPE = {
    "CellIdentityConflict": "identity_conflict",
    "CellResourceError": "resource_error",
    "EnvironmentIntegrityError": "artifact_integrity",
    "PublicationRecoveryRequired": "recovery_required",
    "WorkspaceLockTimeout": "lock_timeout",
}


def reason_code(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "deadline_exceeded"
    message = str(exc)
    if message in _REASON_BY_MESSAGE:
        return _REASON_BY_MESSAGE[message]
    return _REASON_BY_TYPE.get(type(exc).__name__, "internal_error")


class PublicationTrace:
    """Thread-safe record of one publication's stages and transfer progress."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock
        self._now = now or (lambda: datetime.now(UTC).isoformat())
        self._lock = threading.Lock()
        self._owner_thread = threading.get_ident()
        self._current: dict[str, Any] | None = None
        self._stages: list[dict[str, Any]] = []
        self._bytes_done = 0
        self._bytes_total: int | None = None
        self._files_done: int | None = None
        self._dirty = True
        self.metrics: dict[str, int] = {}
        # Set by the owner: writes the current snapshot into the durable journal.
        self.flush: Callable[[], None] | None = None

    # -- recording (any thread) -------------------------------------------------

    def stage(self, name: str, *, bytes_total: int | None = None) -> None:
        if name not in STAGES:
            raise ValueError(f"unknown publication stage {name!r}")
        with self._lock:
            self._close_locked()
            self._current = {
                "stage": name,
                "started_at": self._now(),
                "finished_at": None,
                "elapsed_ms": None,
                "_t0": self._clock(),
            }
            self._bytes_done = 0
            self._bytes_total = bytes_total
            self._files_done = None
            self._dirty = True
        self._flush_if_owner()

    def add_bytes(self, count: int) -> None:
        with self._lock:
            self._bytes_done += count
            self._dirty = True

    def add_files(self, count: int = 1) -> None:
        with self._lock:
            self._files_done = (self._files_done or 0) + count
            self._dirty = True

    def end_stage(self) -> None:
        with self._lock:
            self._close_locked()
        self._flush_if_owner()

    def current_stage(self) -> str | None:
        with self._lock:
            return None if self._current is None else str(self._current["stage"])

    # -- reading (owner) -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Additive response fields for the journal; ``heartbeat_at`` is the
        moment this snapshot was taken, i.e. the last proof of liveness."""
        with self._lock:
            current = None
            if self._current is not None:
                current = {key: value for key, value in self._current.items() if key != "_t0"}
            progress = None
            if current is not None:
                progress = {
                    "bytes_done": self._bytes_done,
                    "bytes_total": self._bytes_total,
                    "files_done": self._files_done,
                }
            self._dirty = False
            return {
                "format_version": FORMAT_VERSION,
                "stage": None if current is None else current["stage"],
                "stage_started_at": None if current is None else current["started_at"],
                "heartbeat_at": self._now(),
                "progress": progress,
                "stages": [dict(item) for item in self._stages],
                "metrics": dict(self.metrics),
            }

    def dirty(self) -> bool:
        with self._lock:
            return self._dirty

    # -- internals ----------------------------------------------------------------

    def _close_locked(self) -> None:
        current = self._current
        if current is None:
            return
        elapsed = round((self._clock() - current.pop("_t0")) * 1000)
        current["finished_at"] = self._now()
        current["elapsed_ms"] = elapsed
        current["bytes_done"] = self._bytes_done
        current["bytes_total"] = self._bytes_total
        self._stages.append(current)
        key = f"{current['stage']}_ms"
        self.metrics[key] = self.metrics.get(key, 0) + elapsed  # a stage may repeat
        self._current = None
        self._dirty = True

    def _flush_if_owner(self) -> None:
        # Journal writes stay on the loop thread; worker-thread transitions are
        # picked up by the heartbeat within a second.
        if self.flush is not None and threading.get_ident() == self._owner_thread:
            self.flush()
