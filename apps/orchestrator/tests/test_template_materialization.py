"""Standalone template bytes and existing project overrides are an output contract."""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from yleum_orchestrator.core import docker_client
from yleum_orchestrator.core import template_materialization as materialization
from yleum_orchestrator.services.provisioner import _copy_template

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
GOLDEN = json.loads((Path(__file__).parent / "fixtures/shared_public_git_golden.json").read_text())
README_OVERRIDES = json.loads(
    (Path(__file__).parent / "fixtures/shared_public_readme_overrides.json").read_text()
)
# The site builder's three Next templates left and took their README overrides
# with them; the MAX template ships the file the golden hash already pins.
assert README_OVERRIDES == {}


def assert_golden(name: str, root: Path) -> None:
    expected = GOLDEN["templates"][name]
    actual = {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and not p.name.endswith(".tsbuildinfo")
    }
    assert actual.keys() == expected.keys()
    for relative, entry in expected.items():
        entry = README_OVERRIDES.get(f"{name}/{relative}", entry)
        data = actual[relative].read_bytes()
        if os.name == "nt":
            data = data.replace(b"\r\n", b"\n")  # Git autocrlf; Linux gate checks raw bytes.
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], relative
        if os.name != "nt":
            mode = "100755" if actual[relative].stat().st_mode & 0o111 else "100644"
            assert mode == entry["mode"], relative


@pytest.mark.parametrize("name", GOLDEN["templates"])
def test_real_seed_full_tree_and_existing_public_overrides(name, tmp_path):
    source, destination = TEMPLATES / name, tmp_path / "project"
    _copy_template(source, destination)
    assert_golden(name, destination)
    custom = destination / "public/omnia-inspector.js"
    custom.write_bytes(b"")
    missing = destination / "public/omnia-brief-narration.js"
    missing.unlink()
    _copy_template(source, destination)
    assert custom.read_bytes() == b""
    assert (
        hashlib.sha256(missing.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        == (GOLDEN["templates"][name]["public/omnia-brief-narration.js"]["sha256"])
    )


@pytest.fixture
def sparse_collection(tmp_path, monkeypatch):
    root = tmp_path / "templates"
    shutil.copytree(TEMPLATES / "shared-public", root / "shared-public")
    for name in GOLDEN["templates"]:
        source = root / name
        source.mkdir()
        (source / "Dockerfile.dev").write_text("FROM scratch\n")
        (source / "original.txt").write_text("unchanged")
    monkeypatch.setattr(materialization, "TEMPLATES", root)
    return root


def test_sparse_materialization_preserves_modes_and_rejects_existing_destination(
    sparse_collection, tmp_path
):
    source = sparse_collection / "max-miniapp-nextjs"
    destination = tmp_path / "output"
    materialization.materialize_template(source, destination)
    for relative, asset in materialization.shared_public_files(source).items():
        assert (destination / relative).read_bytes() == asset.read_bytes()
        assert (destination / relative).stat().st_mode == asset.stat().st_mode
    with pytest.raises(ValueError, match="must be empty"):
        materialization.materialize_template(source, destination)


def test_generic_matching_basename_gets_no_shared_overlay(tmp_path):
    source = tmp_path / "max-miniapp-nextjs"
    source.mkdir()
    (source / "custom.txt").write_text("owned")
    destination = tmp_path / "standalone"
    materialization.materialize_template(source, destination)
    assert list(destination.iterdir()) == [destination / "custom.txt"]


def test_missing_canonical_fails_before_materialization_or_existing_seed(
    sparse_collection, tmp_path
):
    source = sparse_collection / "max-miniapp-nextjs"
    (sparse_collection / "shared-public/omnia-inspector.js").unlink()
    destination = tmp_path / "output"
    with pytest.raises(FileNotFoundError, match="shared template asset unavailable"):
        materialization.materialize_template(source, destination)
    assert not destination.exists()
    destination.mkdir()
    (destination / "custom").write_text("owned")
    with pytest.raises(FileNotFoundError):
        _copy_template(source, destination)
    assert list(destination.iterdir()) == [destination / "custom"]


@pytest.mark.parametrize("name", GOLDEN["templates"])
async def test_shared_only_edit_rebuilds_complete_context_once(
    sparse_collection, tmp_path, monkeypatch, name
):
    source = sparse_collection / name
    for path in sparse_collection.rglob("*"):
        if path.is_file():
            os.utime(path, (1000, 1000))
    changed = sparse_collection / "shared-public/omnia-remix-cta.js"
    os.utime(changed, (5000, 5000))
    created = [2000.0]
    monkeypatch.setattr(docker_client, "_image_created_epoch", lambda _: created[0])
    monkeypatch.setattr(
        docker_client,
        "get_settings",
        lambda: SimpleNamespace(docker_cli_config_dir=tmp_path / "cli"),
    )
    contexts = []

    def run(argv, **kwargs):
        context = Path(argv[-1])
        contexts.append(context)
        assert Path(argv[3]) == context / "Dockerfile.dev"
        assert (context / "public/omnia-remix-cta.js").read_bytes() == changed.read_bytes()
        assert kwargs["timeout"] == 900
        created[0] = 6000.0
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(docker_client.subprocess, "run", run)
    assert await docker_client.ensure_template_image_fresh(source, f"qa-{name}") is True
    assert await docker_client.ensure_template_image_fresh(source, f"qa-{name}") is False
    assert len(contexts) == 1 and not contexts[0].exists()
    assert changed.stat().st_mtime == 5000.0


@pytest.mark.parametrize("failure", ["exit", "timeout"])
async def test_failed_build_keeps_existing_fallback_and_cleans_context(
    sparse_collection, tmp_path, monkeypatch, failure
):
    source = sparse_collection / "max-miniapp-nextjs"
    contexts = []
    monkeypatch.setattr(docker_client, "_image_created_epoch", lambda _: None)
    monkeypatch.setattr(
        docker_client,
        "get_settings",
        lambda: SimpleNamespace(docker_cli_config_dir=tmp_path / "cli"),
    )

    def run(argv, **kwargs):
        contexts.append(Path(argv[-1]))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 900)
        return SimpleNamespace(returncode=1, stderr="test build failure", stdout="")

    monkeypatch.setattr(docker_client.subprocess, "run", run)
    assert await docker_client.ensure_template_image_fresh(source, f"qa-failure-{failure}") is False
    assert len(contexts) == 1 and not contexts[0].exists()


def test_cli_materializes_outside_checkout_with_no_installed_package(tmp_path):
    output = tmp_path / "complete"
    script = TEMPLATES.parent / "scripts/materialize-template.py"
    subprocess.run(
        [sys.executable, "-I", str(script), "max-miniapp-nextjs", str(output)],
        cwd=tmp_path,
        check=True,
    )
    assert_golden("max-miniapp-nextjs", output)


async def test_cancellation_keeps_build_context_until_worker_exits(
    sparse_collection, tmp_path, monkeypatch
):
    started, finish = threading.Event(), threading.Event()
    contexts = []
    monkeypatch.setattr(docker_client, "_image_created_epoch", lambda _: None)
    monkeypatch.setattr(
        docker_client,
        "get_settings",
        lambda: SimpleNamespace(docker_cli_config_dir=tmp_path / "cli"),
    )

    def run(argv, **kwargs):
        contexts.append(Path(argv[-1]))
        started.set()
        assert finish.wait(timeout=5)
        assert contexts[0].is_dir()
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(docker_client.subprocess, "run", run)
    task = asyncio.create_task(
        docker_client.ensure_template_image_fresh(
            sparse_collection / "max-miniapp-nextjs",
            "qa-cancel-context",
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert contexts[0].is_dir()
    finally:
        finish.set()
    for _ in range(100):
        if not contexts[0].exists():
            break
        await asyncio.sleep(0.01)
    assert not contexts[0].exists()


async def test_concurrent_provisions_build_shared_context_only_once(
    sparse_collection,
    tmp_path,
    monkeypatch,
):
    source = sparse_collection / "max-miniapp-nextjs"
    created = [None]
    contexts = []
    monkeypatch.setattr(docker_client, "_image_created_epoch", lambda _: created[0])
    monkeypatch.setattr(
        docker_client,
        "get_settings",
        lambda: SimpleNamespace(docker_cli_config_dir=tmp_path / "cli"),
    )

    def run(argv, **kwargs):
        context = Path(argv[-1])
        contexts.append(context)
        assert (context / "public/omnia-inspector.js").is_file()
        created[0] = docker_client._newest_source_mtime(source) + 10
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(docker_client.subprocess, "run", run)
    results = await asyncio.gather(
        *(
            docker_client.ensure_template_image_fresh(source, "qa-concurrent-public")
            for _ in range(5)
        )
    )
    assert results.count(True) == 1
    assert results.count(False) == 4
    assert len(contexts) == 1 and not contexts[0].exists()


@pytest.mark.parametrize("failure", [False, True])
def test_shell_builds_materialized_contexts_and_cleans_after_failure(
    tmp_path, failure
):
    bash = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
    if not bash or not Path(bash).is_file():
        pytest.skip("bash unavailable")
    bundle = tmp_path / "bundle"
    scripts = bundle / "scripts"
    scripts.mkdir(parents=True)
    for name in ["build-template-images.sh", "materialize-template.py"]:
        (scripts / name).write_text(
            (TEMPLATES.parent / "scripts" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
    script = scripts / "build-template-images.sh"
    core = bundle / "src/yleum_orchestrator/core"
    core.mkdir(parents=True)
    shutil.copy2(Path(materialization.__file__), core / "template_materialization.py")
    shutil.copytree(TEMPLATES / "shared-public", bundle / "templates/shared-public")
    for name in GOLDEN["templates"]:
        source = bundle / "templates" / name
        source.mkdir()
        (source / "Dockerfile.dev").write_text("FROM scratch\n")
    executables = tmp_path / "bin"
    executables.mkdir()
    python = executables / "python3"
    arguments = '"$@"' if os.name != "nt" else '"$1" "$2" "$(cygpath -w "$3")"'
    python.write_text(
        f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" {arguments}\n',
        encoding="utf-8",
        newline="\n",
    )
    docker = executables / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        '[ "$1" = build ] || exit 1\n'
        'for arg do context="$arg"; done\n'
        "for asset in omnia-inspector.js omnia-brief-narration.js omnia-remix-cta.js; do\n"
        '  [ -s "$context/public/$asset" ] || exit 23\n'
        "done\n"
        'printf "%s\\n" "$context" >> "$BUILD_LOG"\n'
        '[ "$FAIL_BUILD" = 1 ] && exit 17\n'
        "exit 0\n",
        newline="\n",
    )
    for executable in [python, docker]:
        executable.chmod(0o755)
    log = tmp_path / "builds.txt"
    env = os.environ | {
        "PATH": str(executables) + os.pathsep + os.environ["PATH"],
        "BUILD_LOG": log.as_posix(),
        "FAIL_BUILD": str(int(failure)),
    }
    result = subprocess.run(
        [bash, str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    expected_code = 1 if failure else 0
    assert result.returncode == expected_code, result.stdout + result.stderr
    contexts = log.read_text(encoding="utf-8").splitlines()
    # One image per template dir that ships a Dockerfile.dev — the MAX app and the
    # bare box are what is left of the eight the site builder shipped.
    assert len(contexts) == 1
    # On Windows, Git Bash's /tmp is not the Python temp root. Ask the same shell.
    for context in contexts:
        assert subprocess.run([bash, "-c", 'test ! -e "$1"', "--", context]).returncode == 0
