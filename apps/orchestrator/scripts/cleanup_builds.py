"""Collect obsolete build archives and owned local images; dry-run by default."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scripts.cleanup_orphan_archives import collect_archives

from yleum_orchestrator.services.build_retention_inventory import collect_roots
from yleum_orchestrator.services.local_image_retention import ImageRoots, collect_local_images


def _image_roots(root: Path) -> ImageRoots:
    roots = collect_roots(root)
    return ImageRoots(roots.image_ids, roots.image_refs, roots.busy_workspaces)


def collect_builds(
    root: Path,
    *,
    apply: bool = False,
    min_age_seconds: int = 7200,
    registry_host: str = "registry.yleum.ru",
) -> dict[str, Any]:
    report: dict[str, Any] = {"status": "ok", "mode": "apply" if apply else "dry_run"}
    try:
        archives = collect_archives(root, min_age_seconds=min_age_seconds, apply=apply)
        report["archives"] = archives
        if archives["status"] != "ok":
            report["status"] = "aborted"
            return report
        report["images"] = asyncio.run(
            collect_local_images(
                root,
                lambda: _image_roots(root),
                apply=apply,
                min_age_seconds=min_age_seconds,
                registry_host=registry_host,
            )
        )
        if report["images"].get("errors", 0):
            report.update(status="aborted", error_type="image_cleanup_incomplete")
    except Exception as exc:
        # Docker/metadata exceptions may include private environment values.
        report.update(status="aborted", error_type=type(exc).__name__)
    return report


def _report_path(destination: Path, state: Path) -> Path:
    path = destination.absolute()
    parent = path.parent.resolve(strict=True)
    if (
        parent != path.parent
        or path.is_symlink()
        or path.is_relative_to(state.absolute())
        or path.resolve().is_relative_to(state.resolve())
    ):
        raise ValueError("unsafe report location")
    return path


def _save_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    created = False
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=Path("/opt/omnia-runtime/state"))
    parser.add_argument("--min-age-seconds", type=int, default=7200)
    parser.add_argument("--registry-host", default=os.getenv("IMAGE_REGISTRY", "registry.yleum.ru"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.min_age_seconds < 7200:
        parser.error("--min-age-seconds must be at least 7200")
    try:
        destination = _report_path(args.report, args.state_root) if args.report else None
        report = collect_builds(
            args.state_root,
            apply=args.apply,
            min_age_seconds=args.min_age_seconds,
            registry_host=args.registry_host,
        )
        if destination is not None:
            _save_report(destination, report)
    except Exception as exc:
        report = {"status": "aborted", "error_type": type(exc).__name__}
    print(json.dumps(report, sort_keys=True))
    return int(report["status"] != "ok")


if __name__ == "__main__":
    raise SystemExit(main())
