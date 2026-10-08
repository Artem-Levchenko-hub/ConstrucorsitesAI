import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from yleum_orchestrator.services.next_compilation_capture import capture_next_compilation


def setup(receipt=None):
    helper = NS(
        start=lambda: None,
        wait=lambda **kw: {"StatusCode": 0},
        logs=lambda **kw: json.dumps(receipt).encode(),
        removed=[],
        remove=lambda **kw: helper.removed.append(kw),
    )
    containers = NS(calls=[], create=lambda *a, **kw: containers.calls.append((a, kw)) or helper)
    backend = NS(
        base_image="image@sha256:" + "a" * 64,
        workspace_volume="owned-volume",
        workspace_id=uuid4(),
        project_id=uuid4(),
        stem="owned",
        labels=lambda kind: {"role": kind},
        client=NS(containers=containers),
    )
    request = NS(
        operation_id=uuid4(), generation_run_id=uuid4(), fencing_epoch=7, expected_revision="b" * 64
    )
    manifest = NS(
        tasks=[NS(role="full_build", argv=["pnpm", role], cwd=".") for role in ["build", "test"]],
        digest=lambda: "c" * 64,
    )
    return backend, request, manifest, helper, containers


def test_capture_uses_only_exact_ro_owned_volume_and_platform_interpreter():
    backend, request, manifest, helper, containers = setup(
        {"collector_version": "next-fixed-data-v1", "assets": []}
    )
    receipt = capture_next_compilation(backend, request, manifest)
    args, kw = containers.calls[0]
    assert args[0] == backend.base_image and args[1][:4] == ["python3", "-I", "-S", "-c"]
    assert kw["volumes"] == {"owned-volume": {"bind": "/proof", "mode": "ro"}}
    assert kw["network_mode"] == "none" and kw["read_only"] is True and kw["cap_drop"] == ["ALL"]
    assert "environment" not in kw and "privileged" in kw and not kw["privileged"]
    assert (
        receipt["operation_id"] == str(request.operation_id)
        and receipt["source_revision"] == request.expected_revision
    )
    assert helper.removed == [{"force": True}]


@pytest.mark.parametrize(
    "mutation", ["unknown-command", "unpin", "invalid-data", "oversize", "failed"]
)
def test_unknown_or_failed_capture_never_supplies_receipt(mutation):
    backend, request, manifest, helper, containers = setup(
        {"collector_version": "next-fixed-data-v1"}
    )
    if mutation == "unknown-command":
        manifest.tasks[0].argv = ["node", "model.js"]
    if mutation == "unpin":
        backend.base_image = "image:latest"
    if mutation == "invalid-data":
        helper.logs = lambda **kw: b"generated raw output"
    if mutation == "oversize":
        helper.logs = lambda **kw: b"x" * 65537
    if mutation == "failed":
        helper.wait = lambda **kw: {"StatusCode": 1}
    assert capture_next_compilation(backend, request, manifest) is None
    assert (
        not containers.calls
        if mutation in {"unknown-command", "unpin"}
        else helper.removed == [{"force": True}]
    )


@pytest.mark.parametrize("version", ["next-restored-runtime-v1", "next-fixed-data-v1"])
def test_restore_capture_has_distinct_receipt_and_allows_core_only_build(version):
    backend, request, manifest, helper, containers = setup({"collector_version": version})
    request.task_role = "restore_runtime"
    receipt = capture_next_compilation(backend, request, manifest)
    script = containers.calls[0][0][1][-1]
    assert "collect_next_compilation(require_product_page=False)" in script
    assert (receipt is not None) is (version == "next-restored-runtime-v1")
    assert helper.removed == [{"force": True}]
