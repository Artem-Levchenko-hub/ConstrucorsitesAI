"""Reject unsafe historical bundles before creating any candidate resources."""

from base64 import b64encode
from uuid import UUID

import pytest
from pydantic import ValidationError


def envelope(files):
    return {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222",
        "project_id": "33333333-3333-4333-8333-333333333333",
        "owner_id": "44444444-4444-4444-8444-444444444444",
        "expected_source_head": "a" * 40,
        "target_commit_sha": "b" * 40,
        "planned_commit_sha": "c" * 40,
        "fencing_epoch": 7,
        "files": files,
    }


def file(path, content=b"export default 1", mode="100644"):
    return {"path": path, "content_base64": b64encode(content).decode(), "mode": mode}


def test_current_source_manifest_is_immutable_and_path_validated():
    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare

    payload = envelope([file("src/page.ts")])
    payload["current_files"] = [
        {"path": "public/icon.bin", "sha256": "a" * 64, "mode": "100755"},
        {"path": "src/page.ts", "sha256": "b" * 64, "mode": "100644"},
    ]
    first = CodeRestorationPrepare.model_validate(payload)
    payload["current_files"].reverse()
    assert CodeRestorationPrepare.model_validate(payload).digest() == first.digest()
    payload["current_files"][0]["sha256"] = "c" * 64
    assert CodeRestorationPrepare.model_validate(payload).digest() != first.digest()
    payload["current_files"][0]["path"] = "../untracked.txt"
    with pytest.raises(ValidationError):
        CodeRestorationPrepare.model_validate(payload)


def test_bundle_preserves_binary_empty_and_executable_source():
    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare

    request = CodeRestorationPrepare.model_validate(
        envelope(
            [
                file("public/icon.png", b"\x89PNG\x00\xff"),
                file("src/empty.ts", b""),
                file("scripts/start.sh", b"#!/bin/sh\nexec node server.js\n", "100755"),
            ]
        )
    )
    assert request.operation_id == UUID("11111111-1111-4111-8111-111111111111")
    assert [(f.path, f.decoded(), f.mode) for f in request.files] == [
        ("public/icon.png", b"\x89PNG\x00\xff", "100644"),
        ("src/empty.ts", b"", "100644"),
        ("scripts/start.sh", b"#!/bin/sh\nexec node server.js\n", "100755"),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "../host",
        "/root/key",
        "C:/secret",
        "src\\outside.ts",
        "src/../outside.ts",
        "src//page.tsx",
        "./src/page.tsx",
        "src/./page.tsx",
        "src/page.tsx\x00",
        ".git/config",
        ".env",
        ".env.local",
        "src/.env.production",
        "node_modules/x.js",
        ".next/server/app.js",
        "src/\npage.tsx",
    ],
)
def test_unsafe_paths_never_reach_candidate(path):
    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare

    with pytest.raises(ValidationError):
        CodeRestorationPrepare.model_validate(envelope([file(path)]))


@pytest.mark.parametrize(
    "entries",
    [
        [file("src/page.tsx"), file("src/page.tsx")],
        [file("src"), file("src/page.tsx")],
        [file("src/page.tsx"), file("src")],
        [{"path": "src/page.tsx", "content_base64": "%%%", "mode": "100644"}],
        [file("src/page.tsx", mode="120000")],
    ],
)
def test_conflicting_or_unverifiable_entries_are_rejected(entries):
    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare

    with pytest.raises(ValidationError):
        CodeRestorationPrepare.model_validate(envelope(entries))


def test_canonical_digest_binds_identity_files_modes_and_fence_not_entry_order():
    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare

    wire = envelope([file("a.ts", b"a"), file("b.ts", b"b")])
    original = CodeRestorationPrepare.model_validate(wire)
    reordered = CodeRestorationPrepare.model_validate(
        {**wire, "files": list(reversed(wire["files"]))}
    )
    assert original.digest() == reordered.digest()
    for changes in (
        {"owner_id": "55555555-5555-4555-8555-555555555555"},
        {"fencing_epoch": 8},
        {"files": [file("a.ts", b"changed"), file("b.ts", b"b")]},
        {"files": [file("a.ts", b"a", "100755"), file("b.ts", b"b")]},
    ):
        assert (
            CodeRestorationPrepare.model_validate({**wire, **changes}).digest() != original.digest()
        )
