"""Fixed-path compiler metadata: synthetic bytes, no Docker or provider calls."""

import hashlib
import json
import os

import pytest

from yleum_orchestrator.services.next_compilation_receipt import (
    CompilationUnavailable,
    collect_next_compilation,
)


def fixture(root):
    files = {
        "package.json": json.dumps(
            {"scripts": {"build": "next build"}, "dependencies": {"next": "15.5.24"}}
        ),
        ".next/BUILD_ID": "build-a",
        ".next/build-manifest.json": json.dumps(
            {
                "rootMainFiles": ["static/chunks/main-app.js"],
                "pages": {},
                "polyfillFiles": [],
                "lowPriorityFiles": ["static/build-a/_buildManifest.js"],
            }
        ),
        ".next/app-build-manifest.json": json.dumps(
            {
                "pages": {
                    "/page": [
                        "static/chunks/main-app.js",
                        "static/chunks/app/page.js",
                        "static/css/app.css",
                    ]
                }
            }
        ),
        ".next/static/chunks/main-app.js": "window.main=1;",
        ".next/static/chunks/app/page.js": "window.page=1;",
        ".next/static/css/app.css": "body{color:#000}",
        ".next/static/build-a/_buildManifest.js": "self.__BUILD_MANIFEST={};",
        ".next/static/media/font.woff2": "synthetic-font",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    return files


def test_valid_next_data_and_exact_asset_hashes(tmp_path):
    fixture(tmp_path)
    receipt = collect_next_compilation(str(tmp_path))
    assert receipt["collector_version"] == "next-fixed-data-v1"
    assert receipt["declared_next_version"] == "15.5.24"
    assert len(receipt["assets"]) == 5
    asset = next(x for x in receipt["assets"] if x["path"].endswith("/app/page.js"))
    assert asset["sha256"] == hashlib.sha256(b"window.page=1;").hexdigest()
    assert asset["bytes"] == 14
    # JavaScript manifests are hashed assets, never interpreted as metadata.
    assert receipt["build_id_sha256"] == hashlib.sha256(b"build-a").hexdigest()


def test_core_only_restoration_has_separate_receipt_without_product_page(tmp_path):
    fixture(tmp_path)
    (tmp_path / ".next/app-build-manifest.json").write_text(
        '{"pages":{"/_not-found/page":["static/chunks/main-app.js"]}}'
    )
    receipt = collect_next_compilation(str(tmp_path), require_product_page=False)
    assert receipt["collector_version"] == "next-restored-runtime-v1"
    assert receipt["assets"]
    with pytest.raises(CompilationUnavailable):
        collect_next_compilation(str(tmp_path))


@pytest.mark.parametrize(
    "path,value",
    [
        ("package.json", '{"scripts":{"build":"node evil.js"},"dependencies":{"next":"15.5.24"}}'),
        ("package.json", '{"scripts":{"build":"next build"},"dependencies":{"next":"^15.5.24"}}'),
        (".next/BUILD_ID", "../escape"),
        (
            ".next/build-manifest.json",
            '{"rootMainFiles":["../secret"],"pages":{},"polyfillFiles":[],"lowPriorityFiles":[]}',
        ),
        (".next/app-build-manifest.json", '{"pages":{"/other":["static/chunks/main-app.js"]}}'),
        (".next/app-build-manifest.json", '{"pages":{"/page":["static/chunks/../../secret.js"]}}'),
        (".next/app-build-manifest.json", '{"pages":{"/page":["static/export.zip"]}}'),
        (
            ".next/build-manifest.json",
            '{"rootMainFiles":[],"pages":{},"polyfillFiles":[],"lowPriorityFiles":[],"rootMainFiles":[]}',
        ),
    ],
)
def test_unsupported_or_unsafe_data_is_unavailable(tmp_path, path, value):
    fixture(tmp_path)
    (tmp_path / path).write_text(value)
    with pytest.raises(CompilationUnavailable, match="COMPILATION_UNAVAILABLE"):
        collect_next_compilation(str(tmp_path))


@pytest.mark.parametrize(
    "path",
    [".next/app-build-manifest.json", ".next/static/chunks/app/page.js", ".next/static/chunks"],
)
def test_symlinks_are_never_read(tmp_path, path):
    fixture(tmp_path)
    target = tmp_path / path
    if target.is_dir():
        target.rename(tmp_path / "hidden")
        target.symlink_to(tmp_path / "hidden", target_is_directory=True)
    else:
        target.unlink()
        target.symlink_to(tmp_path / "package.json")
    with pytest.raises(CompilationUnavailable):
        collect_next_compilation(str(tmp_path))


def test_asset_budget_checked_before_hashing(tmp_path):
    fixture(tmp_path)
    with (tmp_path / ".next/static/chunks/app/page.js").open("wb") as stream:
        stream.truncate(8 * 1024**2 + 1)
    with pytest.raises(CompilationUnavailable):
        collect_next_compilation(str(tmp_path))


def test_changed_file_during_read_is_unavailable(tmp_path, monkeypatch):
    fixture(tmp_path)
    original = os.read
    changed = False

    def race(fd, length):
        nonlocal changed
        result = original(fd, length)
        if not changed:
            changed = True
            (tmp_path / "package.json").write_text("{}")
        return result

    monkeypatch.setattr(os, "read", race)
    with pytest.raises(CompilationUnavailable):
        collect_next_compilation(str(tmp_path))


def test_metadata_reopen_cannot_replace_original_manifest_stamp(tmp_path, monkeypatch):
    from yleum_orchestrator.services.next_compilation_receipt import _Reader

    fixture(tmp_path)
    original = _Reader.read
    reads = {}

    def replace_before_reopen(reader, path, limit):
        reads[path] = reads.get(path, 0) + 1
        if path == ".next/app-build-manifest.json" and reads[path] == 2:
            (tmp_path / path).write_text('{"pages":{"/page":[]}}')
        return original(reader, path, limit)

    monkeypatch.setattr(_Reader, "read", replace_before_reopen)
    with pytest.raises(CompilationUnavailable):
        collect_next_compilation(str(tmp_path))
