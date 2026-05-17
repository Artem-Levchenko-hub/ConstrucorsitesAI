"""Cursor afterAgentResponse hook: lightweight live flush during active sessions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
TMP_DIR = SCRIPTS_DIR / "tmp"
FLUSH_SCRIPT = SCRIPTS_DIR / "flush.py"


def _read_stdin_json() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _spawn_flush(payload_file: Path, payload: dict) -> None:
    cmd = [sys.executable, str(FLUSH_SCRIPT), "--payload-file", str(payload_file), "--event", "afterAgentResponse"]
    env = os.environ.copy()
    tp = payload.get("transcript_path") or env.get("CURSOR_TRANSCRIPT_PATH")
    if tp:
        env["CURSOR_TRANSCRIPT_PATH"] = str(tp)
    kwargs: dict = {"cwd": str(ROOT_DIR), "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    log_dir = SCRIPTS_DIR / "tmp"
    log_dir.mkdir(parents=True, exist_ok=True)
    err_log = open(log_dir / "flush-after-agent-response.err.log", "a", encoding="utf-8")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_log, **kwargs)


def main() -> int:
    if os.environ.get("CLAUDE_INVOKED_BY"):
        return 0
    payload = _read_stdin_json()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload_file = TMP_DIR / f"after-agent-response-{os.getpid()}.json"
    payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _spawn_flush(payload_file, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
