"""Read-only, networkless compiler capture from an exact controller-owned volume."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import docker  # type: ignore[import-untyped]

from yleum_orchestrator.core.cell_resources import PINNED_MACHINE_IMAGE
from yleum_orchestrator.core.project_machine import MachineManifest, MachineTask
from yleum_orchestrator.services.next_compilation_receipt import (
    COLLECTOR_VERSION,
    RESTORED_COLLECTOR_VERSION,
)
from yleum_orchestrator.services.project_machine import machine_remaining_seconds

_TEST_PATH = r"(?:\./)?tests/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[cm]?js"
_NODE_TEST = re.compile(rf"node[ \t]+--test(?:[ \t]+{_TEST_PATH})+")


def _supported_direct_node_test(argv: list[str]) -> bool:
    if argv[:4] == ["pnpm", "exec", "node", "--test"]:
        argv = argv[2:]
    if argv[:2] == ["node", "--test"]:
        paths = argv[2:]
    elif len(argv) == 2 and argv[0] == "node":
        paths = argv[1:]
    else:
        return False
    return bool(paths) and all(re.fullmatch(_TEST_PATH, path) is not None for path in paths)


def _supported_test_suffix(argv: list[str], extra_tests: list[list[str]]) -> bool:
    if (
        len(argv) == 4
        and argv[0] == "env"
        and re.fullmatch(r"[A-Z][A-Z0-9_]*_TEST_DATABASE=1", argv[1])
    ):
        argv = argv[2:]
    if not all(_supported_direct_node_test(test) for test in extra_tests):
        return False
    if argv == ["pnpm", "test"]:
        return True
    if extra_tests or len(argv) != 3 or argv[:2] != ["sh", "-lc"]:
        return False
    primary, *extra = (part.strip(" \t") for part in argv[2].split("&&"))
    return (
        re.fullmatch(r"pnpm[ \t]+test", primary) is not None
        and bool(extra)
        and all(_NODE_TEST.fullmatch(command) is not None for command in extra)
    )


def _direct_node_key(argv: list[str]) -> tuple[str, ...]:
    return tuple(argv[2:] if argv[:2] == ["pnpm", "exec"] else argv)


def full_build_tasks(manifest: MachineManifest) -> list[MachineTask]:
    """Execute the standard root Next pipeline even if an author omits its boilerplate."""
    declared = [task for task in manifest.tasks if task.role == "full_build"]
    package_script = getattr(manifest, "root_next_test_script", None)
    if not package_script:
        return declared
    if not declared:
        # Early portable manifests declared these roles separately. The release executor
        # runs their original commands in build/test order with the same cwd and timeout.
        declared = [
            task.model_copy(update={"role": "full_build"})
            for role in ("build", "test") for task in manifest.tasks if task.role == role
        ]
    if any(task.cwd != "." for task in declared):
        return declared
    builds: list[MachineTask] = []
    packages: dict[tuple[str, ...], MachineTask] = {}
    extra: dict[tuple[str, ...], MachineTask] = {}
    for task in declared:
        if task.argv == ["pnpm", "build"]:
            builds.append(task)
        elif _supported_test_suffix(task.argv, []):
            packages.setdefault(tuple(task.argv), task)
        elif _supported_direct_node_test(task.argv):
            extra.setdefault(_direct_node_key(task.argv), task)
        else:
            # Preserve opaque commands, arbitrary shells, and custom pipelines verbatim.
            return declared

    used = {task.name for task in manifest.tasks}

    def default_task(script: str, seconds: int) -> MachineTask:
        name = f"controller-next-{script}"
        index = 0
        while name in used:
            index += 1
            name = f"controller-next-{script}-{index}"
        return MachineTask(
            name=name, role="full_build", argv=["pnpm", script], timeout_seconds=seconds,
        )

    build = builds[0] if builds else default_task("build", 600)
    suites = list(packages.values()) or [default_task("test", 300)]
    # Only fold tests that already ran in the same directory, mode and environment.
    for task in suites:
        if task.argv[:2] == ["sh", "-lc"]:
            for command in task.argv[2].split("&&")[1:]:
                extra.pop(tuple(command.split()), None)
    package_command = re.split(r"[ \t]+", package_script.strip(" \t"))
    if any(task.argv[0] != "env" for task in suites):
        if _supported_direct_node_test(package_command):
            extra.pop(_direct_node_key(package_command), None)
        elif package_command == ["node", "--test", "tests/*.test.mjs"]:
            for path in manifest.root_next_test_files:
                if re.fullmatch(r"tests/[A-Za-z0-9_-][A-Za-z0-9_.-]*\.test\.mjs", path):
                    extra.pop(("node", "--test", path), None)
                    extra.pop(("node", "--test", "./" + path), None)
    return [build, *suites, *extra.values()]


def capture_next_compilation(backend: Any, request: Any, manifest: Any) -> dict[str, Any] | None:
    # Validate the completed build plan before collecting the fixed compiler output.
    tasks = full_build_tasks(manifest)
    if len(tasks) < 2 or any(task.cwd != "." for task in tasks):
        return None
    build, test, *extra_tests = tasks
    if build.argv != ["pnpm", "build"]:
        return None
    if getattr(manifest, "root_next_test_script", None):
        # The managed plan groups all explicit package environments ahead of direct tests.
        if not _supported_test_suffix(test.argv, []) or any(
            not (_supported_test_suffix(task.argv, []) or _supported_direct_node_test(task.argv))
            for task in extra_tests
        ):
            return None
    elif not _supported_test_suffix(test.argv, [task.argv for task in extra_tests]):
        return None
    image = getattr(backend, "base_image", None)
    if type(image) is not str or not PINNED_MACHINE_IMAGE.fullmatch(image):
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
