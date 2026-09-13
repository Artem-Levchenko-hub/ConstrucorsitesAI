"""Shared non-MAX source files retain standalone bytes and per-project ownership."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnia_orchestrator.core import docker_client
from omnia_orchestrator.core import template_materialization as materialization
from omnia_orchestrator.services.provisioner import _copy_template

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
MAPPING = json.loads(
    (Path(__file__).parent / "fixtures/shared_source_git_mapping.json").read_text()
)
NAMES = ["nextjs-entities", "nextjs-postgres-drizzle", "nextjs-realtime"]


@pytest.fixture
def source_collection(tmp_path, monkeypatch):
    root = tmp_path / "templates"
    shared = root / "shared-public"
    shared.mkdir(parents=True)
    (shared / "manifest.json").write_text(
        json.dumps(
            {
                "templates": [*NAMES, "max-miniapp-nextjs"],
                "assets": [],
                "source_files": {"src/lib/utils.ts": NAMES},
            }
        )
    )
    canonical = shared / "source/src/lib/utils.ts"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("export const marker = 1;\n")
    for name in [*NAMES, "max-miniapp-nextjs"]:
        source = root / name
        source.mkdir()
        (source / "Dockerfile.dev").write_text("FROM scratch\n")
    monkeypatch.setattr(materialization, "TEMPLATES", root)
    return root


@pytest.mark.parametrize("name", NAMES)
def test_real_source_materialization_and_existing_empty_override(name, tmp_path):
    source = TEMPLATES / name
    output = tmp_path / "project"
    _copy_template(source, output)
    for relative, entry in MAPPING.items():
        if name not in entry["templates"]:
            continue
        target = output / relative
        data = target.read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], relative
        if os.name != "nt":
            assert target.stat().st_mode & 0o777 == 0o644
    custom = output / "src/lib/utils.ts"
    custom.write_bytes(b"")
    _copy_template(source, output)
    assert custom.read_bytes() == b""


def test_nested_source_overlay_is_available_only_to_participants(source_collection, tmp_path):
    output = tmp_path / "output"
    materialization.materialize_template(source_collection / NAMES[0], output)
    assert (output / "src/lib/utils.ts").read_text() == "export const marker = 1;\n"
    assert materialization.shared_public_files(source_collection / "max-miniapp-nextjs") == {}


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.ts",
        "/escape.ts",
        "src/../escape.ts",
        "src//escape.ts",
        "src\\escape.ts",
        "C:/escape.ts",
        "src/./escape.ts",
    ],
)
def test_invalid_source_mapping_fails_before_writes(source_collection, tmp_path, relative):
    manifest = source_collection / "shared-public/manifest.json"
    data = json.loads(manifest.read_text())
    data["source_files"] = {relative: NAMES}
    manifest.write_text(json.dumps(data))
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="shared source"):
        materialization.materialize_template(source_collection / NAMES[0], output)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["ancestor", "leaf", "root"])
def test_source_symlink_ancestor_is_rejected(source_collection, tmp_path, kind):
    shared = source_collection / "shared-public"
    external = tmp_path / "external"
    external.mkdir()
    (external / "utils.ts").write_text("outside")
    nested = shared / "source/src/lib"
    try:
        if kind == "root":
            shared.rename(external / "shared")
            shared.symlink_to(external / "shared", target_is_directory=True)
        elif kind == "leaf":
            (nested / "utils.ts").unlink()
            (nested / "utils.ts").symlink_to(external / "utils.ts")
        else:
            (nested / "utils.ts").unlink()
            nested.rmdir()
            nested.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="shared source"):
        materialization.materialize_template(source_collection / NAMES[0], tmp_path / "output")


def test_missing_source_does_not_partially_seed_existing_project(source_collection, tmp_path):
    (source_collection / "shared-public/source/src/lib/utils.ts").unlink()
    output = tmp_path / "project"
    output.mkdir()
    (output / "owned").write_text("preserve")
    with pytest.raises(FileNotFoundError, match="shared template asset unavailable"):
        _copy_template(source_collection / NAMES[0], output)
    assert sorted(p.name for p in output.iterdir()) == ["owned"]


async def test_shared_source_mtime_rebuilds_three_templates_but_not_max(
    source_collection, tmp_path, monkeypatch
):
    for file in source_collection.rglob("*"):
        if file.is_file():
            os.utime(file, (1000, 1000))
    canonical = source_collection / "shared-public/source/src/lib/utils.ts"
    os.utime(canonical, (5000, 5000))
    created = {name: 2000.0 for name in [*NAMES, "max-miniapp-nextjs"]}
    builds = []
    monkeypatch.setattr(docker_client, "_image_created_epoch", lambda name: created[name])
    monkeypatch.setattr(
        docker_client,
        "get_settings",
        lambda: SimpleNamespace(docker_cli_config_dir=tmp_path / "cli"),
    )

    def run(argv, **kwargs):
        name = argv[5]
        assert (Path(argv[-1]) / "src/lib/utils.ts").read_bytes() == canonical.read_bytes()
        builds.append(name)
        created[name] = 6000.0
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(docker_client.subprocess, "run", run)
    for _ in range(2):
        await asyncio.gather(
            *(
                docker_client.ensure_template_image_fresh(source_collection / name, name)
                for name in created
            )
        )
    assert sorted(builds) == sorted(NAMES)
    assert canonical.stat().st_mtime == 5000
