#!/usr/bin/env python3
"""Fresh-process exact-target checks for an owned retry or a scoped first capture."""

from __future__ import annotations

import base64
import contextlib
import hashlib
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
EXPIRED_SCOPE_KEYS = {
    "version", "release_sha", "run_id", "attempt_id", "state_revision",
    "context_sha256", "source_sha256", "clock_sha256",
}
EXPIRED_COMMANDS = {
    "expired-provider": ("expired-provider-check", "expired_provider_reservation"),
    "resume-expired-provider": ("resume-expired-provider-check", "owned_expired_provider_capture"),
}
PARKED_CONTEXT_KEYS = {"version", "release_sha", "run_id"}
ARCHIVE_ROOTS = (
    ("apps", "api", "src"),
    ("apps", "api", "data"),
    ("apps", "api", "skills", "ui-ux-pro-max", "data"),
)


def require(condition: object) -> None:
    if not condition:
        raise ValueError("release retry source or context is unconfirmed")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def decode_release_context(encoded: str, revision: str) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", revision) and len(encoded) <= 5500)
    raw = base64.b64decode(encoded, validate=True)
    require(len(raw) <= 4096)
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(type(value) is dict)
    require(
        type(value["version"]) is int
        and value["version"] == 1
        and value["release_sha"] == revision
    )
    return value


def validate_capture(value: dict) -> None:
    require(str(UUID(value["owner_nonce"])) == value["owner_nonce"])
    for key in ("core_capture_sha256", "commerce_capture_sha256"):
        require(
            isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key])
        )



def decode_context(encoded: str, revision: str) -> dict:
    value = decode_release_context(encoded, revision)
    require(set(value) == CONTEXT_KEYS)
    validate_capture(value)
    return value


def decode_expired_context(encoded: str, revision: str, *, owned: bool) -> dict:
    value = decode_release_context(encoded, revision)
    required = (EXPIRED_SCOPE_KEYS | CONTEXT_KEYS if owned
                else EXPIRED_SCOPE_KEYS | {"reader_release_sha", "mode"})
    require(set(value) == required)
    for key in ("run_id", "attempt_id"):
        parsed = UUID(value[key])
        require(parsed.int and str(parsed) == value[key])
    require(type(value["state_revision"]) is int and value["state_revision"] >= 0)
    for key in ("context_sha256", "source_sha256", "clock_sha256"):
        require(isinstance(value[key], str) and re.fullmatch(r"[0-9a-f]{64}", value[key]))
    if owned:
        validate_capture(value)
    else:
        reader = value["reader_release_sha"]
        require(
            isinstance(reader, str) and reader != revision and re.fullmatch(r"[0-9a-f]{40}", reader)
            and value["mode"] in ("eligibility", "settled")
        )
    return value


def decode_parked_context(encoded: str, revision: str) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", revision) and len(encoded) <= 1024)
    value = json.loads(base64.b64decode(encoded, validate=True), object_pairs_hook=unique_object)
    require(type(value) is dict and set(value) == PARKED_CONTEXT_KEYS)
    require(
        type(value["version"]) is int and value["version"] == 1
        and value["release_sha"] == revision
        and isinstance(value["run_id"], str)
        and str(UUID(value["run_id"])) == value["run_id"]
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
            require(any(
                path.parts[:len(prefix)] == prefix
                or (member.isdir() and prefix[:len(path.parts)] == path.parts)
                for prefix in ARCHIVE_ROOTS
            ))
            if member.isfile() and path.parts[:3] != ARCHIVE_ROOTS[0]:
                require(path.suffix in {".csv", ".json"})
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
    require(len(sys.argv) in (3, 4, 5) and "yleum_api" not in sys.modules)
    revision, encoded = sys.argv[1:3]
    check = sys.argv[3] if len(sys.argv) == 4 else None
    parked = check == "parked-source"
    scoped_resume = len(sys.argv) == 5
    extra: list[str] = []
    expired_basis = None
    if check in EXPIRED_COMMANDS:
        command, expired_basis = EXPIRED_COMMANDS[check]
        context = decode_expired_context(encoded, revision, owned=check == "resume-expired-provider")
    elif parked:
        context = decode_parked_context(encoded, revision)
        command = "parked-source-check"
    else:
        require(check is None)
        context = decode_context(encoded, revision)
        command = "resume-check"
        if scoped_resume:
            scope, run_id = sys.argv[3:]
            require(scope == "resume-parked-source" and str(UUID(run_id)) == run_id)
            command = "resume-parked-source-check"
            extra = [run_id]
    payload = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
    with tempfile.TemporaryDirectory(prefix="reader-target-check-") as temp:
        source = extract_source(payload, revision, Path(temp))
        sys.path.insert(0, str(source))
        spec = importlib.util.find_spec("yleum_api")
        require(
            spec is not None and spec.origin == str(source / "yleum_api/__init__.py")
        )
        sys.argv = ["generation_deployment_drain", command, revision, *extra]
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
        require(type(result) is dict and type(result.get("drained")) is bool)
        if expired_basis is not None:
            mode = context.get("mode", "settled")
            canonical = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
            require(
                set(result) == {"version", "release_sha", "mode", "basis", "scope_sha256",
                                "active", "eligible", "drained"}
                and type(result["version"]) is int
                and result["version"] == 1
                and result["release_sha"] == revision
                and result["mode"] == mode
                and result["basis"] == expired_basis
                and result["scope_sha256"] == hashlib.sha256(canonical).hexdigest()
                and type(result["eligible"]) is bool
            )
            selected = result["eligible"]
        elif parked:
            require(
                set(result) == {"version", "release_sha", "basis", "active",
                                "selected_verified", "drained"}
                and result["basis"] == "parked_source_recovery"
                and type(result["selected_verified"]) is bool
                and type(result["version"]) is int
                and result["version"] == context["version"]
                and result["release_sha"] == revision
            )
            selected = result["selected_verified"]
        else:
            fields = CONTEXT_KEYS | {"basis", "active", "drained"}
            if scoped_resume:
                fields.add("parked_source_verified")
            require(set(result) == fields)
            require(all(result[key] == context[key] for key in CONTEXT_KEYS))
            require(result["basis"] == "owned_release_capture")
            selected = result.get("parked_source_verified", True)
            require(type(selected) is bool)
        counts = result["active"]
        require(
            type(counts) is dict
            and set(counts) == {"generations", "operations", "leases", "restorations"}
        )
        require(all(type(count) is int and count >= 0 for count in counts.values()))
        eligibility = expired_basis is not None and context.get("mode") == "eligibility"
        if expired_basis is not None and selected:
            expected = {"generations": int(eligibility), "operations": 0, "leases": 0, "restorations": 0}
            require(counts == expected)
        require(
            result["drained"] == (not eligibility and selected and not any(counts.values()))
            and (code == 0) == (selected if eligibility else result["drained"])
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
