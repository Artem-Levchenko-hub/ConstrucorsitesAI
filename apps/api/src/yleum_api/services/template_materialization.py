"""Materialize bundled scaffolds only when initializing a new project.

Snapshots, exports, restores and imported projects own their committed files;
they must never receive an overlay from the current template assets.
"""

import shutil
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
STATIC_TEMPLATES = frozenset({"blank", "landing", "portfolio", "blog"})
SHARED_ASSETS = ("omnia-kit.css", "omnia-kit.js", "anime.min.js")


def read_kit_asset(name: str) -> bytes:
    """Read one whitelisted bundled resource for the public kit endpoint."""
    if name in SHARED_ASSETS:
        return (TEMPLATES / "shared-assets" / name).read_bytes()
    raise ValueError(f"unknown kit asset: {name}")


def materialize_template(source: Path, destination: Path) -> None:
    """Copy a scaffold into a fresh directory, preserving standalone paths.

    Only the exact bundled static directories receive shared resources.
    Reject populated destinations before writing so this helper cannot update
    an existing user's project. Missing scaffolds retain empty-init behavior.
    """
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("template destination must be empty")
    if not source.exists():
        return
    shutil.copytree(source, destination, dirs_exist_ok=True)
    if source.resolve() in {TEMPLATES / name for name in STATIC_TEMPLATES}:
        (destination / "assets").mkdir(exist_ok=True)
        for name in SHARED_ASSETS:
            shutil.copy2(TEMPLATES / "shared-assets" / name, destination / "assets" / name)
