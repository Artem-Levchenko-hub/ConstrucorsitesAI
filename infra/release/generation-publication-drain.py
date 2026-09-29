#!/usr/bin/env python3
"""Read-only publication quiescence gate using the live controller's settings."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def require_publications_drained(root: Path) -> int:
    if not root.parent.is_dir() or root.is_symlink():
        raise RuntimeError("publication storage authority is unavailable")
    count = 0
    for path in root.glob("*/publication.json"):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("unsafe journal path")
            value = json.loads(path.read_text(encoding="utf-8"))
            if value["project_id"] != path.parent.name:
                raise ValueError("journal identity mismatch")
            history = value["history"]
            if not isinstance(history, list):
                raise ValueError("invalid journal history")
            if history and history[-1]["response"]["phase"] not in {"done", "failed"}:
                raise ValueError("unfinished or unknown publication")
            count += 1
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            # No log bodies/project data are emitted, including malformed JSON.
            raise RuntimeError("publication journals are not quiescent") from exc
    return count


def live_publication_root() -> Path:
    processes = set()
    for service in ("yleum-orchestrator", "omnia-orchestrator"):
        result = subprocess.run(
            ["systemctl", "show", service, "--property=MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            pid = int(result.stdout.strip())
            if pid > 0:
                processes.add(pid)
    if len(processes) != 1:
        raise RuntimeError("expected exactly one live controller process")
    proc = Path("/proc") / str(processes.pop())
    if b"yleum_orchestrator" not in (proc / "cmdline").read_bytes():
        raise RuntimeError("controller process identity is unconfirmed")
    environment = dict(
        item.decode().split("=", 1)
        for item in (proc / "environ").read_bytes().split(b"\0")
        if b"=" in item
    )
    # Changes affect this read-only helper only, never the live process. Settings
    # resolve the same environment/.env defaults as that controller; no secrets print.
    os.environ.clear()
    os.environ.update(environment)
    os.chdir((proc / "cwd").resolve())
    sys.path.insert(0, str(Path.cwd() / "src"))
    from yleum_orchestrator.core.config import get_settings

    return Path(get_settings().cell_state_path).parent / "cell-publications"


if __name__ == "__main__":
    try:
        count = require_publications_drained(live_publication_root())
    except Exception:
        print('{"publication_drained":false}')
        raise SystemExit(75) from None
    print(json.dumps({"publication_drained": True, "journals": count}))
