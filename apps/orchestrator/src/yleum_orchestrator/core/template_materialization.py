"""Materialize bundled templates into standalone trees; never overlay a live project."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
_SKIP = frozenset({"node_modules", ".next", ".git", "__pycache__"})


def _shared_asset_path(shared: Path, relative: str) -> Path:
    path = shared / relative
    for ancestor in (path, *path.parents):
        if ancestor == shared.parent:
            break
        if ancestor.is_symlink():
            raise ValueError("invalid shared source symlink")
    if not path.resolve().is_relative_to(shared.resolve()):
        raise ValueError("invalid shared source containment")
    if not path.is_file():
        raise FileNotFoundError(f"shared template asset unavailable: {relative}")
    return path


def _validate_source_path(relative: str) -> None:
    if (
        not relative.startswith("src/")
        or "\\" in relative
        or any(part in {"", ".", ".."} or ":" in part for part in relative.split("/"))
        or Path(relative).suffix not in {".ts", ".tsx"}
    ):
        raise ValueError("invalid shared source path")


def shared_public_files(source: Path) -> dict[str, Path]:
    """Resolve data from the trusted collection only, never by directory name alone."""
    source = source.resolve()
    if source.parent != TEMPLATES.resolve():
        return {}
    shared = TEMPLATES / "shared-public"
    manifest = json.loads((shared / "manifest.json").read_text(encoding="utf-8"))
    if source.name not in manifest["templates"]:
        return {}
    files: dict[str, Path] = {}
    for name in manifest["assets"]:
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".js"):
            raise ValueError("invalid shared public asset name")
        files[f"public/{name}"] = _shared_asset_path(shared, name)
    for relative, templates in manifest.get("source_files", {}).items():
        if source.name in templates:
            _validate_source_path(relative)
            files[relative] = _shared_asset_path(shared, f"source/{relative}")
    return files


def seed_shared_public_files(source: Path, destination: Path) -> None:
    """Fill missing assets with the same preserve-existing policy as workspace seeding."""
    for relative, asset in shared_public_files(source).items():
        target = destination / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset, target)


def materialize_template(source: Path, destination: Path) -> None:
    """Produce a complete fresh build/export tree with original bytes and modes."""
    assets = shared_public_files(source)  # Validate before copying any partial output.
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("template destination must be empty")
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=lambda _directory, names: [name for name in names if name in _SKIP],
    )
    for relative, asset in assets.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, target)


@contextmanager
def materialized_template(source: Path) -> Iterator[Path]:
    """Keep the context alive through the consuming operation and clean up on failure."""
    with tempfile.TemporaryDirectory(prefix="omnia-template-") as temporary:
        destination = Path(temporary) / "context"
        materialize_template(source, destination)
        yield destination
