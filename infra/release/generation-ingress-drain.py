#!/usr/bin/env python3
"""Owned, reversible nginx admission barrier for the canonical production vhost."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

VHOST = Path("/etc/nginx/sites-available/yleum.ru")
MAP = Path("/etc/nginx/conf.d/yleum-generation-drain.conf")
STATE = Path("/var/lib/yleum/generation-ingress-drain")
MARKER = b"# yleum-owned-generation-ingress-drain-v1"
MAP_BODY = b"""map "$request_method:$uri" $yleum_generation_drain {
    default 0;
    ~^POST:/api/projects/[^/]+/generation/cancel$ 0;
    ~^POST:/api/projects/[^/]+/restorations/[^/]+/cancel$ 0;
    ~^(POST|PUT|PATCH|DELETE):/api/projects(/|$) 1;
}
"""
RULE = (
    b"\n        " + MARKER + b"\n"
    b"        if ($yleum_generation_drain) {\n"
    b'            return 503 \'{"error":{"code":"generation_draining",'
    b'"message":"Generation admission is temporarily paused"}}\';\n'
    b"        }\n"
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def render_vhost(source: bytes) -> bytes:
    if MARKER in source:
        raise RuntimeError("unowned ingress marker already exists")
    matches = list(re.finditer(rb"\blocation\s+/api/\s*\{", source))
    if len(matches) != 1:
        raise RuntimeError("expected exactly one canonical /api/ location")
    position = matches[0].end()
    return source[:position] + RULE + source[position:]


def write_atomic(path: Path, content: bytes, mode: int = 0o600) -> None:
    fd, name = tempfile.mkstemp(prefix=".yleum-drain-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def command(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=30)


def probe() -> None:
    # Unauthenticated impossible-project request cannot create paid work even
    # if the barrier is absent. Only the nginx 503/code is accepted as evidence.
    request = urllib.request.Request(
        "https://yleum.ru/api/projects/00000000-0000-0000-0000-000000000000/prompt",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
        except urllib.error.HTTPError as exc:
            try:
                if exc.code == 503:
                    try:
                        raw = exc.read(8193)
                        body = json.loads(raw) if len(raw) <= 8192 else None
                    except (ValueError, UnicodeDecodeError):
                        body = None
                    if isinstance(body, dict):
                        error = body.get("error")
                        if (
                            isinstance(error, dict)
                            and error.get("code") == "generation_draining"
                        ):
                            return
                retry = exc.code == 401 and attempt < 4
            finally:
                exc.close()
            if retry:
                time.sleep(1)
                continue
        raise RuntimeError("live ingress admission barrier was not confirmed")


def owned(release: str) -> dict[str, object]:
    data = json.loads((STATE / "active.json").read_text())
    if data["release_sha"] != release:
        raise RuntimeError("ingress drain belongs to another release")
    if digest(VHOST.read_bytes()) != data["blocked_sha"] or MAP.read_bytes() != MAP_BODY:
        raise RuntimeError("ingress configuration changed; preserve it and reconcile explicitly")
    return data


def begin(release: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (STATE / "active.json").exists():
        owned(release)
        probe()
        return
    if MAP.exists():
        raise RuntimeError("refusing to overwrite an existing ingress map")
    source = VHOST.read_bytes()
    blocked = render_vhost(source)
    mode = stat.S_IMODE(VHOST.stat().st_mode)
    backup = STATE / (release + ".original.conf")
    if backup.exists() and backup.read_bytes() != source:
        raise RuntimeError("existing backup differs; do not overwrite it")
    write_atomic(backup, source)
    write_atomic(MAP, MAP_BODY, 0o644)
    write_atomic(VHOST, blocked, mode)
    try:
        command("nginx", "-t")
    except Exception:
        write_atomic(VHOST, source, mode)
        MAP.unlink()
        raise
    data = {
        "release_sha": release,
        "original_sha": digest(source),
        "blocked_sha": digest(blocked),
        "mode": mode,
    }
    write_atomic(STATE / "active.json", json.dumps(data, sort_keys=True).encode())
    # From here onward errors retain the owned state and barrier. No EXIT cleanup.
    command("systemctl", "reload", "nginx")
    probe()


def end(release: str) -> None:
    data = owned(release)
    source = (STATE / (release + ".original.conf")).read_bytes()
    if digest(source) != data["original_sha"]:
        raise RuntimeError("saved ingress configuration checksum changed")
    blocked = VHOST.read_bytes()
    mode = int(data["mode"])
    write_atomic(VHOST, source, mode)
    MAP.unlink()
    try:
        command("nginx", "-t")
        command("systemctl", "reload", "nginx")
    except Exception:
        write_atomic(MAP, MAP_BODY, 0o644)
        write_atomic(VHOST, blocked, mode)
        # The old running nginx may still have the barrier. Retain durable state.
        raise
    (STATE / "active.json").unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "check", "end"))
    parser.add_argument("release_sha")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.release_sha):
        parser.error("full release SHA required")
    if args.action == "begin":
        begin(args.release_sha)
    elif args.action == "end":
        end(args.release_sha)
    else:
        owned(args.release_sha)
        probe()
    print(json.dumps({"action": args.action, "release_sha": args.release_sha, "ok": True}))
