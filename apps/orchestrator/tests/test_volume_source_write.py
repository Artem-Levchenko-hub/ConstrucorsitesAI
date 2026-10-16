"""Trusted source writer must not follow guest-controlled links, including races."""
import importlib
import importlib.util
import io
import os
import subprocess
import sys
import tarfile

import pytest


def program():
    module = "yleum_orchestrator.services.volume_source_write"
    assert importlib.util.find_spec(module), "missing atomic no-follow source writer"
    return importlib.import_module(module).SOURCE_WRITE_SCRIPT


def payload(path, files):
    with tarfile.open(path, "w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def run(root, archive):
    return subprocess.run([sys.executable, "-I", "-S", "-c", program(), str(root), str(archive)],
                          capture_output=True, check=False)


@pytest.mark.skipif(os.name == "nt", reason="Linux dirfd/no-follow boundary")
@pytest.mark.parametrize("kind", ["ordinary", "leaf", "ancestor", "traversal", "hardlink"])
def test_actual_writer_containment(tmp_path, kind):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside")
    inside = root / "sentinel"
    inside.write_bytes(b"inside")
    target = "src/file.txt"
    if kind == "leaf":
        (root / "src").mkdir()
        (root / target).symlink_to(sentinel)
    elif kind == "ancestor":
        (root / "src").symlink_to(outside, target_is_directory=True)
        target = "src/sentinel"
    elif kind == "traversal":
        target = "../outside/sentinel"
    elif kind == "hardlink":
        (root / "src").mkdir()
        os.link(sentinel, root / target)
    archive = tmp_path / "payload.tar"
    payload(archive, {target: b"changed"})
    result = run(root, archive)
    assert sentinel.read_bytes() == b"outside"
    assert inside.read_bytes() == b"inside"
    if kind in {"ordinary", "hardlink"}:
        assert result.returncode == 0, result.stderr
        assert (root / target).read_bytes() == b"changed"
    else:
        assert result.returncode != 0


@pytest.mark.skipif(os.name == "nt", reason="Linux dirfd/no-follow boundary")
def test_complete_payload_validated_before_any_source_write(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "source.txt"
    source.write_bytes(b"original")
    archive = tmp_path / "payload.tar"
    payload(archive, {"source.txt": b"changed", "../escape": b"bad"})
    assert run(root, archive).returncode != 0
    assert source.read_bytes() == b"original"


@pytest.mark.skipif(os.name == "nt", reason="Linux resource/dirfd boundary")
def test_allowed_file_count_with_production_descriptor_limit(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    archive = tmp_path / "payload.tar"
    files = {f"directory-{i}/file.txt": b"complete" for i in range(5000)}
    payload(archive, files)
    limits = "import resource; resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))\n"
    result = subprocess.run([
        sys.executable, "-I", "-S", "-c", limits + program(), str(root), str(archive),
    ], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.txt")} == files


@pytest.mark.parametrize("value", ["a\\b", "a\x00b", "/absolute", "../escape"])
def test_helper_path_validation_is_independent_of_outer_normalization(value):
    namespace = {"__name__": "test"}
    # Load only definitions; test the exact helper validator including NUL (tar names
    # truncate NUL, so a tar roundtrip would not exercise that branch).
    exec(program().split("\ntry:\n    archive_path", 1)[0], namespace)
    with pytest.raises(ValueError):
        namespace["parts"](value)


@pytest.mark.skipif(os.name == "nt", reason="Linux dirfd/no-follow boundary")
@pytest.mark.parametrize("kind", ["per_file", "file_count", "truncated_upload"])
def test_payload_bounds_and_incomplete_transfer_precede_source_effects(tmp_path, kind):
    root = tmp_path / "workspace"
    root.mkdir()
    archive = tmp_path / "payload.tar"
    if kind == "per_file":
        payload(archive, {"too-large": b"x" * (2 * 1024 * 1024 + 1)})
        result = run(root, archive)
    elif kind == "file_count":
        payload(archive, {str(i): b"x" for i in range(5001)})
        result = run(root, archive)
    else:
        result = subprocess.run([
            sys.executable, "-I", "-S", "-c", program(), str(root), str(archive), "100",
        ], input=b"incomplete", capture_output=True, check=False)
    assert result.returncode != 0
    assert list(root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Linux dirfd/no-follow boundary")
@pytest.mark.parametrize("kind", ["leaf", "ancestor", "hardlink"])
def test_substitution_at_atomic_rename_cannot_modify_outside_inode(tmp_path, kind):
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "file.txt"
    sentinel.write_bytes(b"outside")
    archive = tmp_path / "payload.tar"
    payload(archive, {"src/file.txt": b"changed"})
    # Deterministically make the guest substitution AFTER the last target stat,
    # at the real rename syscall boundary. The writer's descriptors stay real.
    injection = f'''
import os
original_rename = os.rename
def raced(source, target, **kwargs):
    root, outside, kind = {str(root)!r}, {str(outside)!r}, {kind!r}
    if kind == 'ancestor':
        original_rename(root + '/src', root + '/pinned')
        os.symlink(outside, root + '/src')
    elif kind == 'leaf':
        os.symlink(outside + '/file.txt', target, dir_fd=kwargs['dst_dir_fd'])
    else:
        os.link(outside + '/file.txt', target, dst_dir_fd=kwargs['dst_dir_fd'])
    return original_rename(source, target, **kwargs)
os.rename = raced
'''
    result = subprocess.run([
        sys.executable, "-I", "-S", "-c", injection + program(), str(root), str(archive),
    ], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert sentinel.read_bytes() == b"outside"
    written = root / ("pinned" if kind == "ancestor" else "src") / "file.txt"
    assert not written.is_symlink() and written.read_bytes() == b"changed"
