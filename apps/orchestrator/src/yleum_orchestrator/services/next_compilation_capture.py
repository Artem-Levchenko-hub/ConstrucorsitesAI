"""Read-only, networkless compiler capture from an exact controller-owned volume."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import docker  # type: ignore[import-untyped]

from yleum_orchestrator.services.next_compilation_receipt import (
    COLLECTOR_VERSION,
    RESTORED_COLLECTOR_VERSION,
)
from yleum_orchestrator.services.project_machine import machine_remaining_seconds


def capture_next_compilation(backend: Any, request: Any, manifest: Any) -> dict[str, Any] | None:
    # No arbitrary build command, source path, env, project interpreter or network.
    tasks = [t for t in manifest.tasks if t.role == "full_build"]
    if [t.argv for t in tasks] != [["pnpm", "build"], ["pnpm", "test"]] or any(
        t.cwd != "." for t in tasks
    ):
        return None
    from yleum_orchestrator.services.docker_machine_backend import _PIN

    image = getattr(backend, "base_image", None)
    if type(image) is not str or not _PIN.fullmatch(image):
        return None
    source = Path(__file__).with_name("next_compilation_receipt.py").read_text()
    restoring = getattr(request, "task_role", None) == "restore_runtime"
    collect = (
        "collect_next_compilation(require_product_page=False)"
        if restoring
        else "collect_next_compilation()"
    )
    script = source + (
        "\ntry:\n"
        f' print(json.dumps({collect},sort_keys=True,separators=(",",":")))\n'
        'except CompilationUnavailable:\n print("null")\n'
    )
    helper = None
    try:
        helper = backend.client.containers.create(
            backend.base_image,
            ["python3", "-I", "-S", "-c", script],
            name=backend.stem + "-compiled-" + request.operation_id.hex,
            labels=backend.labels("compiled-data"),
            entrypoint=[],
            network_mode="none",
            cap_drop=["ALL"],
            privileged=False,
            read_only=True,
            security_opt=["no-new-privileges:true"],
            user="0:0",
            pids_limit=8,
            mem_limit=128 * 1024**2,
            memswap_limit=128 * 1024**2,
            nano_cpus=100_000_000,
            volumes={backend.workspace_volume: {"bind": "/proof", "mode": "ro"}},
            log_config=docker.types.LogConfig(
                type="json-file", config={"max-size": "128k", "max-file": "1"}
            ),
        )
        helper.start()
        if helper.wait(timeout=machine_remaining_seconds(15)).get("StatusCode") != 0:
            return None
        raw = helper.logs(stdout=True, stderr=False)
        if len(raw) > 65536:
            return None
        data = json.loads(raw)
        version = RESTORED_COLLECTOR_VERSION if restoring else COLLECTOR_VERSION
        if type(data) is not dict or data.get("collector_version") != version:
            return None
        data.update(
            workspace_id=str(backend.workspace_id),
            project_id=str(backend.project_id),
            generation_run_id=str(request.generation_run_id),
            fencing_epoch=request.fencing_epoch,
            source_revision=request.expected_revision,
            operation_id=str(request.operation_id),
            manifest_digest=manifest.digest(),
            collector_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        )
        return data
    except Exception:
        # A generic successful build stays successful. Named promotion requires
        # this receipt separately; no app details or environment in failure text.
        return None
    finally:
        if helper is not None:
            helper.remove(force=True)  # exact helper object, never shared-name/process matching
