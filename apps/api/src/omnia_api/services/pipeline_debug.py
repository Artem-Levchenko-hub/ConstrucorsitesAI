"""Env-gated raw-artifact dump for the generation pipeline (debug-only).

Set ``PIPELINE_DEBUG_DUMP=1`` to capture each pass's RAW output — the
art-director brief, the writer's HTML, the system prompt, and the vision
verdict — to disk for offline quality forensics. Default OFF: zero overhead,
never raises.

Owner directive 2026-06-03: hunt *why* freeform pages still look generic by
reading what each model actually emitted, not just the ``[PP]`` summary lines.
Artifacts land under ``PIPELINE_DEBUG_DIR/<project_id>/<message_id>/`` so one
generation = one folder of numbered files.
"""

from __future__ import annotations

import os
from pathlib import Path

_DUMP = os.getenv("PIPELINE_DEBUG_DUMP", "").strip().lower() in {"1", "true", "yes", "on"}
_DIR = os.getenv("PIPELINE_DEBUG_DIR", "/tmp/omnia-pp")


def enabled() -> bool:
    """True when the dump is armed (cheap guard for callers)."""
    return _DUMP


def dump(project_id: object, message_id: object, name: str, content: str) -> None:
    """Write one artifact. No-op unless PIPELINE_DEBUG_DUMP is set. Never raises."""
    if not _DUMP:
        return
    try:
        folder = Path(_DIR) / str(project_id) / str(message_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(content or "", encoding="utf-8")
        print(f"[PP] debug_dump {folder / name} chars={len(content or '')}", flush=True)
    except Exception as exc:  # pragma: no cover - best-effort, never break a build
        print(f"[PP] debug_dump_failed {name}: {exc!r}", flush=True)


__all__ = ["enabled", "dump"]
