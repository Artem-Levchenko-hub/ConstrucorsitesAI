#!/usr/bin/env python3
"""Fresh-process exact-target SQL retry check; root verifies both captures first."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import sys
import tarfile
import tempfile
from uuid import UUID

MAX_ARCHIVE = 64 * 1024 * 1024
CONTEXT_KEYS = {
    "version",
    "release_sha",
    "owner_nonce",
    "core_capture_sha256",
    "commerce_capture_sha256",
}


def require(condition: object) -> None:
    if not condition:
        raise ValueError("release retry source or context is unconfirmed")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def decode_context(encoded: str, revision: str) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", revision) and len(encoded) <= 5500)
    raw = base64.b64decode(encoded, validate=True)
    require(len(raw) <= 4096)
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(type(value) is dict and set(value) == CONTEXT_KEYS)
    require(
        type(value["version"]) is int
        and value["version"] == 1
        and value["release_sha"] == revision
    )
    require(str(UUID(value["owner_nonce"])) == value["owner_nonce"])
    for key in ("core_capture_sha256", "commerce_capture_sha256"):
        require(
            isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key])
        )
    return value


def extract_source(payload: bytes, revision: str, root: Path) -> Path:
    require(0 < len(payload) <= MAX_ARCHIVE)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        require(archive.pax_headers.get("comment") == revision)
        members = archive.getmembers()
        require(0 < len(members) <= 10000)
        seen = set()
        total = 0
        # Validate the WHOLE manifest before writing even the first source file.
        for member in members:
            path = PurePosixPath(member.name)
            require(
                not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in member.name
            )
            require(member.name.rstrip("/") == str(path) and str(path) not in seen)
            seen.add(str(path))
            require(member.isdir() or member.isfile())
            prefix = ("apps", "api", "src")
            require(
                path.parts[:3] == prefix
                or member.isdir()
                and path.parts in {("apps",), ("apps", "api")}
            )
            require(0 <= member.size <= 8 * 1024 * 1024)
            total += member.size
            require(total <= MAX_ARCHIVE)
        for member in members:
            destination = root / member.name
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                require(stream is not None)
                with destination.open("xb") as target:
                    target.write(stream.read(member.size + 1))
                require(destination.stat().st_size == member.size)
                destination.chmod(0o400)
    source = root / "apps/api/src"
    require((source / "yleum_api/__init__.py").is_file())
    return source


def main() -> int:
    require(len(sys.argv) == 3 and "yleum_api" not in sys.modules)
    revision, encoded = sys.argv[1:]
    context = decode_context(encoded, revision)
    payload = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
    with tempfile.TemporaryDirectory(prefix="reader-target-check-") as temp:
        source = extract_source(payload, revision, Path(temp))
        sys.path.insert(0, str(source))
        spec = importlib.util.find_spec("yleum_api")
        require(
            spec is not None and spec.origin == str(source / "yleum_api/__init__.py")
        )
        sys.argv = ["generation_deployment_drain", "resume-check", revision]
        sys.stdin = io.StringIO(json.dumps(context))
        output, errors = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                runpy.run_module(
                    "yleum_api.services.generation_deployment_drain",
                    run_name="__main__",
                )
            except SystemExit as exc:
                require(type(exc.code) is int and exc.code in {0, 75})
                code = exc.code
        require(len(output.getvalue()) <= 8192)
        result = json.loads(output.getvalue(), object_pairs_hook=unique_object)
        require(
            type(result) is dict
            and set(result) == CONTEXT_KEYS | {"basis", "active", "drained"}
        )
        require(all(result[key] == context[key] for key in CONTEXT_KEYS))
        require(
            result["basis"] == "owned_release_capture"
            and type(result["drained"]) is bool
        )
        counts = result["active"]
        require(
            type(counts) is dict
            and set(counts) == {"generations", "operations", "leases", "restorations"}
        )
        require(all(type(count) is int and count >= 0 for count in counts.values()))
        require(
            result["drained"] == (not any(counts.values()))
            and (code == 0) == result["drained"]
        )
        print(json.dumps(result, sort_keys=True))
        return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Suppress imported exception/log payloads, including database/credential details.
        print(json.dumps({"drained": False, "error_class": type(exc).__name__}))
        raise SystemExit(75) from None
