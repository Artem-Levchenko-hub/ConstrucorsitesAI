"""Prepare the public MAX starter archives on the host before draining a release."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import platform
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO = Path(__file__).resolve().parents[5]
LOCK = Path("apps/orchestrator/templates/max-miniapp-nextjs/pnpm-lock.yaml")
FONTS = Path("apps/api/src/yleum_api/data/typography-fontsource-v1.json")
CACHE_ROOT = Path("/var/cache/yleum-npm-tarballs")
REGISTRY = "registry.npmjs.org"
_PACKAGE = re.compile(r"(?:@[a-zA-Z0-9][\w.-]*/)?[a-zA-Z0-9][\w.-]*", re.ASCII)
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?")


def _cpu() -> str:
    value = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(value, value)


def _version(lock: bytes, fonts: bytes, cpu: str) -> str:
    inputs = b"\0".join((b"linux-npm-v1", cpu.encode(), lock, fonts))
    return hashlib.sha256(inputs).hexdigest()


def npm_seed_directory() -> Path | None:
    """Only mount a cache prepared for the trusted inputs of this checkout."""
    try:
        version = _version((REPO / LOCK).read_bytes(), (REPO / FONTS).read_bytes(), _cpu())
        target = CACHE_ROOT / version
        if (target / ".ready").read_text(encoding="ascii").strip() == version:
            return target
    except (OSError, UnicodeError):
        pass
    return None


def _platform_accepts(values: list[str], selected: str) -> bool:
    positive = [value for value in values if not value.startswith("!")]
    return "!" + selected not in values and (
        not positive or selected in positive or "any" in positive
    )


def _archives(lock: bytes, fonts: bytes, cpu: str) -> dict[str, str]:
    """Use only published, integrity-pinned packages from platform-owned inputs."""
    archives: dict[str, str] = {}

    def add(name: str, version: str, integrity: str) -> None:
        if not _PACKAGE.fullmatch(name) or not _VERSION.fullmatch(version):
            raise ValueError(f"Invalid starter package: {name}@{version}")
        algorithm, _, encoded = integrity.partition("-")
        digest = base64.b64decode(encoded, validate=True)
        if algorithm != "sha512" or len(digest) != 64:
            raise ValueError(f"Missing SHA-512 for {name}@{version}")
        path = f"/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"
        if archives.setdefault(path, digest.hex()) != digest.hex():
            raise ValueError(f"Conflicting integrity for {path}")

    for key, package in yaml.safe_load(lock)["packages"].items():
        if not _platform_accepts(package.get("os", []), "linux") or not _platform_accepts(
            package.get("cpu", []), cpu
        ):
            continue
        if "tarball" in package["resolution"]:
            # A custom source is owned by its lockfile and the normal registry path.
            continue
        name, _, version = key.rpartition("@")
        add(name, version, package["resolution"]["integrity"])
    for font in json.loads(fonts)["fonts"].values():
        add(font["package"], font["version"], font["integrity"])
    if not archives:
        raise ValueError("Empty MAX starter package set")
    return archives


def _download(path: str, dest: Path, expected: str) -> None:
    # A fixed origin with normal certificate verification, no ambient proxy credentials
    # or redirects. No project input supplies the destination or the expected digest.
    connection = http.client.HTTPSConnection(REGISTRY, timeout=30)
    digest = hashlib.sha512()
    try:
        connection.request("GET", path, headers={"Connection": "close"})
        with connection.getresponse() as response, dest.open("wb") as output:
            if response.status != 200:
                raise OSError(f"npm returned {response.status} for {path}")
            while chunk := response.read(128 * 1024):
                digest.update(chunk)
                output.write(chunk)
                if output.tell() > 512 * 1024 * 1024:
                    raise ValueError(f"Archive too large: {path}")
        if digest.hexdigest() != expected:
            raise ValueError(f"npm archive integrity mismatch: {path}")
    finally:
        connection.close()


def _matches(path: Path, expected: str) -> bool:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha512").hexdigest() == expected
    except FileNotFoundError:
        return False


def prepare(repo: Path, root: Path, cpu: str) -> dict[str, object]:
    lock, fonts = (repo / LOCK).read_bytes(), (repo / FONTS).read_bytes()
    version = _version(lock, fonts, cpu)
    archives = _archives(lock, fonts, cpu)
    target, objects = root / version, root / "objects"
    try:
        root.mkdir(parents=True)
    except FileExistsError:
        if not root.is_dir():
            raise
    else:
        root.chmod(0o755)
    objects.mkdir(exist_ok=True)
    target.mkdir(mode=0o755, exist_ok=True)
    target.chmod(0o755)
    with tempfile.TemporaryDirectory(dir=root, prefix=".prepare-") as tmp:
        staging = Path(tmp)

        def materialize(item: tuple[str, str]) -> bool:
            path, digest = item
            key = hashlib.sha256(path.encode()).hexdigest() + ".tgz"
            obj, entry, temporary = objects / digest, target / key, staging / key
            missing = not _matches(obj, digest)
            if missing:
                try:
                    _download(path, temporary, digest)
                except (OSError, http.client.HTTPException):
                    _download(path, temporary, digest)
                temporary.chmod(0o444)
                os.replace(temporary, obj)
            if not entry.exists() or not entry.samefile(obj):
                os.link(obj, temporary)
                os.replace(temporary, entry)
            return missing

        with ThreadPoolExecutor(max_workers=4) as workers:
            downloaded = sum(workers.map(materialize, archives.items()))
        # Publish readiness last: a partial preparation cannot activate a new version.
        ready = target / ".ready"
        if not ready.exists() or ready.read_text(encoding="ascii").strip() != version:
            marker = staging / "ready"
            marker.write_text(version + "\n", encoding="ascii")
            marker.chmod(0o444)
            os.replace(marker, ready)
    return {
        "version": version, "directory": str(target), "archives": len(archives),
        "downloaded": downloaded,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT)
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.cache_root, _cpu())))
