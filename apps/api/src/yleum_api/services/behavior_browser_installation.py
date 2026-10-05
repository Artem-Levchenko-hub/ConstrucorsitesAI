"""Image-owned browser pin and bounded startup check; never selects a fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

INSTALLATION_ROOT = Path("/opt/omnia/playwright")
IMAGE_OWNER_UID = 0
CONFIGURATION_KEYS = (
    "MAX_BEHAVIOR_BROWSER_EXECUTABLE",
    "MAX_BEHAVIOR_BROWSER_SHA256",
    "MAX_BEHAVIOR_ADAPTER_REGISTRY",
    "MAX_BEHAVIOR_VENDOR_ASSET_REGISTRY",
)


class BrowserInstallationUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("BEHAVIOR_BROWSER_INSTALLATION_UNAVAILABLE")


def _owned_file(path: Path, limit: int, executable: bool = False) -> tuple[str, int, bytes]:
    relative = path.relative_to(INSTALLATION_ROOT)
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError
    if not INSTALLATION_ROOT.is_absolute():
        raise ValueError
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for root_part in INSTALLATION_ROOT.parts[1:]:
            child = os.open(
                root_part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
    except BaseException:
        os.close(directory)
        raise
    descriptor = None
    try:
        for part in (*relative.parts[:-1], None):
            info = os.fstat(directory)
            if info.st_uid != IMAGE_OWNER_UID or info.st_mode & 0o022:
                raise ValueError
            if part is not None:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
        descriptor = os.open(
            relative.parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != IMAGE_OWNER_UID
            or before.st_mode & 0o022
            or not 0 < before.st_size <= limit
            or (executable and not before.st_mode & 0o111)
        ):
            raise ValueError
        chunks = []
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 65536):
            size += len(chunk)
            if size > limit:
                raise ValueError
            digest.update(chunk)
            if not executable:
                chunks.append(chunk)
        after = os.fstat(descriptor)

        def stamp(s: os.stat_result) -> tuple[int, int, int, int, int]:
            return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

        if stamp(before) != stamp(after):
            raise ValueError
        return digest.hexdigest(), size, b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def write_manifest(executable: str, playwright_version: str) -> None:
    """Build-only: pin installed bytes without launching browser or app code."""
    digest, size, _ = _owned_file(Path(executable), 384 * 1024**2, executable=True)
    manifest = {
        "version": 1,
        "executable": executable,
        "sha256": digest,
        "bytes": size,
        "playwright_version": playwright_version,
    }
    target = INSTALLATION_ROOT / "browser-manifest.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(descriptor, "w") as output:
        json.dump(manifest, output, sort_keys=True, separators=(",", ":"))


def validate_installation(executable: str, expected: str) -> None:
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError
        _, _, raw = _owned_file(INSTALLATION_ROOT / "browser-manifest.json", 8192)
        manifest = json.loads(raw)
        if (
            type(manifest) is not dict
            or set(manifest) != {"version", "executable", "sha256", "bytes", "playwright_version"}
            or type(manifest["version"]) is not int
            or manifest["version"] != 1
            or manifest["executable"] != executable
            or manifest["sha256"] != expected
            or type(manifest["bytes"]) is not int
            or not 0 < manifest["bytes"] <= 384 * 1024**2
            or manifest["playwright_version"] != "1.60.0"
        ):
            raise ValueError
        digest, size, _ = _owned_file(Path(executable), 384 * 1024**2, executable=True)
        if size != manifest["bytes"] or digest != expected:
            raise ValueError
    except Exception:
        raise BrowserInstallationUnavailable() from None


def validate_startup(environment: Mapping[str, str]) -> None:
    if not any(environment.get(key) for key in CONFIGURATION_KEYS):
        return
    if not all(environment.get(key) for key in CONFIGURATION_KEYS[:3]):
        raise BrowserInstallationUnavailable()
    validate_installation(environment[CONFIGURATION_KEYS[0]], environment[CONFIGURATION_KEYS[1]])


def main(arguments: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments == ["--write-image-manifest"]:
        from importlib.metadata import version

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            write_manifest(playwright.chromium.executable_path, version("playwright"))
        return
    try:
        validate_startup(os.environ)
    except BrowserInstallationUnavailable:
        raise SystemExit("BEHAVIOR_BROWSER_INSTALLATION_UNAVAILABLE") from None
    if len(arguments) < 2 or arguments[0] != "--exec":
        raise SystemExit("BEHAVIOR_BROWSER_STARTUP_ARGUMENTS_UNAVAILABLE")
    os.execvp(arguments[1], arguments[1:])


if __name__ == "__main__":
    main()
