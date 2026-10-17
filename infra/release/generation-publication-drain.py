#!/usr/bin/env python3
"""Read-only publication quiescence gate using the live controller's settings."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID


class PhysicalDrainError(RuntimeError):
    """Only fixed structural reasons, never Docker exception bodies."""


_MACHINE_HELPERS = frozenset(
    {
        "project-postgres-prepare",
        "project-postgres-init",
        "project-postgres-restore",
        "retained-proof",
        "restore-check",
        "archive",
        "core-database-bootstrap",
    }
)
_MACHINE_SERVICES = frozenset(
    {
        "development",
        "project-postgres",
        "namespace-guard",
        "egress-proxy",
        "managed-max-core",
        "max-gateway",
    }
)


_PROC_ROOT = Path("/proc")
_CGROUP_ROOT = Path("/sys/fs/cgroup")


def _kernel_need(condition, reason):
    if not condition:
        raise PhysicalDrainError(reason)


def _kernel_read(path: Path) -> bytes:
    with path.open("rb") as handle:
        value = handle.read(1048577)
    _kernel_need(len(value) <= 1048576, "kernel_read_bound")
    return value


def _kernel_ids(path: Path) -> set[int]:
    values = _kernel_read(path).decode("ascii").split()
    _kernel_need(all(v.isdecimal() and int(v) > 0 for v in values), "kernel_id_shape")
    return {int(v) for v in values}


def _kernel_status(path: Path) -> dict[str, str]:
    rows = _kernel_read(path).decode("ascii").splitlines()
    return dict(row.split(":", 1) for row in rows if ":" in row)


def _kernel_boot_id() -> str:
    value = (
        _kernel_read(_PROC_ROOT / "sys/kernel/random/boot_id").decode("ascii").strip()
    )
    _kernel_need(str(UUID(value)) == value, "host_boot_identity")
    return value


def _kernel_row(proc: Path, tid: int, group: str, namespace: int, allowed_caps: int):
    root = proc / str(tid)
    raw = _kernel_read(root / "stat").decode("ascii")
    fields = raw.rsplit(")", 1)[1].split()
    info = _kernel_status(root / "status")
    _kernel_need(fields[0] not in {"Z", "X", "x"}, "process_not_live")
    _kernel_need(int(info["Pid"]) == tid and int(fields[19]) > 0, "process_identity")
    _kernel_need(
        _kernel_read(root / "cgroup").decode("ascii").strip() == "0::" + group,
        "process_cgroup",
    )
    _kernel_need((root / "ns/pid").stat().st_ino == namespace, "process_namespace")
    _kernel_need(int(info["NoNewPrivs"]) == 1, "process_privileges")
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        _kernel_need(
            int(info[key].strip(), 16) & ~allowed_caps == 0, "process_capabilities"
        )
    nspid = tuple(map(int, info["NSpid"].split()))
    _kernel_need(nspid and nspid[0] == tid and nspid[-1] > 0, "process_namespace_pid")
    return {
        "pid": tid,
        "parent": int(fields[1]),
        "start": int(fields[19]),
        "tgid": int(info["Tgid"]),
        "nspid": nspid,
        "cmd": hashlib.sha256(_kernel_read(root / "cmdline")).hexdigest(),
        "exe": str((root / "exe").readlink()),
        "namespace": namespace,
        "group": group,
    }


def _kernel_snapshot(pid: int, *, proc=Path("/proc"), cgroups=Path("/sys/fs/cgroup")):
    group_line = _kernel_read(proc / str(pid) / "cgroup").decode("ascii").strip()
    _kernel_need(
        group_line.startswith("0::/") and "\n" not in group_line, "cgroup_v2_required"
    )
    group = group_line[3:]
    _kernel_need(
        group != "/" and ".." not in Path(group).parts, "container_cgroup_required"
    )
    root = cgroups / group.lstrip("/")
    _kernel_need(root.resolve() == root and root.is_dir(), "cgroup_path")
    # Unknown nested delegation is not treated as an empty container.
    _kernel_need(not any(p.is_dir() for p in root.iterdir()), "nested_cgroup_unknown")
    processes, threads = (
        _kernel_ids(root / "cgroup.procs"),
        _kernel_ids(root / "cgroup.threads"),
    )
    _kernel_need(pid in processes and processes <= threads, "init_cgroup_membership")
    namespace = (proc / str(pid) / "ns/pid").stat().st_ino
    _kernel_need(
        namespace != (proc / "1/ns/pid").stat().st_ino, "host_namespace_denied"
    )
    # Exact capabilities from DockerMachineBackend._USERLAND_CAPS.
    allowed_caps = sum(1 << bit for bit in (0, 1, 3, 4, 6, 7, 31, 5, 10, 18))
    rows = {}
    for process in sorted(processes):
        task_ids = {int(p.name) for p in (proc / str(process) / "task").iterdir()}
        _kernel_need(process in task_ids and task_ids <= threads, "task_inventory")
        _kernel_need(process != pid or task_ids == {pid}, "idle_init_tasks")
        for tid in sorted(task_ids):
            _kernel_need(tid not in rows, "duplicate_task")
            item = _kernel_row(proc, tid, group, namespace, allowed_caps)
            _kernel_need(item["tgid"] == process, "task_group")
            rows[tid] = item
    _kernel_need(set(rows) == threads, "unaccounted_thread")
    _kernel_need(rows[pid]["nspid"][-1] == 1, "container_init_pid")
    _kernel_need(
        processes == _kernel_ids(root / "cgroup.procs")
        and threads == _kernel_ids(root / "cgroup.threads"),
        "kernel_inventory_changed",
    )
    return {
        "boot": _kernel_boot_id(),
        "group": group,
        "namespace": namespace,
        "processes": sorted(processes),
        "rows": rows,
    }


def _service_forest(snapshot, roots: set[int], init: int):
    processes = set(snapshot["processes"])
    _kernel_need(roots <= processes and init not in roots, "service_root_missing")
    for pid in processes - {init}:
        seen, parent = set(), pid
        while parent not in roots:
            _kernel_need(
                parent in processes and parent != init and parent not in seen,
                "unknown_process",
            )
            seen.add(parent)
            parent = snapshot["rows"][parent]["parent"]


def _stopped_kernel_absence(container_id: str):
    """Default Docker cgroup namespace only; any matching residual denies.

    Read every cgroup directory and process membership, including nested groups.
    Failed or racing reads cannot establish absence. No process is signalled.
    """
    boot = _kernel_boot_id()
    names = {container_id, "docker-" + container_id + ".scope"}
    _kernel_need(_CGROUP_ROOT.is_dir() and not _CGROUP_ROOT.is_symlink(), "cgroup_root")
    pending = [_CGROUP_ROOT]
    while pending:
        directory = pending.pop()
        _kernel_need(directory.name not in names, "stopped_cgroup_present")
        for entry in directory.iterdir():
            _kernel_need(not entry.is_symlink(), "cgroup_symlink")
            if entry.is_dir():
                pending.append(entry)
    for process in _PROC_ROOT.iterdir():
        if not process.name.isdecimal():
            continue
        _kernel_need(not process.is_symlink(), "process_path")
        tasks = list((process / "task").iterdir())
        _kernel_need(
            any(task.name == process.name for task in tasks), "stopped_task_inventory"
        )
        for member in [process, *tasks]:
            _kernel_need(
                member.name.isdecimal() and not member.is_symlink(), "process_path"
            )
            membership = _kernel_read(member / "cgroup").decode("ascii").strip()
            _kernel_need(
                membership.startswith("0::/") and "\n" not in membership,
                "cgroup_v2_required",
            )
            _kernel_need(
                not (set(Path(membership[3:]).parts) & names), "stopped_process_present"
            )
    _kernel_need(_kernel_boot_id() == boot, "host_boot_changed")
    return {"boot": boot, "stopped_absent": True}


def _command_absence_sample(client, state_parent, container):
    from yleum_orchestrator.services.docker_machine_backend import (
        _SERVICE_WRAPPER,
        _USERLAND_CAPS,
    )

    container.reload()
    attrs = copy.deepcopy(container.attrs)
    labels = attrs.get("Config", {}).get("Labels") or {}
    _kernel_need(
        labels.get("omnia.managed") == "true"
        and labels.get("omnia.project_machine") == "true"
        and labels.get("omnia.resource_kind") == "development",
        "container_owner_labels",
    )
    for key in ("workspace_id", "project_id", "owner_id"):
        value = labels.get("omnia." + key)
        _kernel_need(
            type(value) is str and str(UUID(value)) == value, "container_owner_identity"
        )
    config, host, state = attrs["Config"], attrs["HostConfig"], attrs["State"]
    _kernel_need(
        re.fullmatch(r"[0-9a-f]{64}", container.id) and attrs["Id"] == container.id,
        "container_id",
    )
    _kernel_need(
        state.get("Paused") is False
        and state.get("Dead") is False
        and state.get("Restarting") is False
        and type(state.get("Pid")) is int
        and bool(state.get("StartedAt")),
        "container_state",
    )
    _kernel_need(
        host.get("Privileged") is False
        and host.get("PidMode") in ("", None)
        and host.get("UsernsMode") in ("", None)
        and host.get("CgroupnsMode") == "private"
        and set(host.get("CapDrop") or []) == {"ALL"}
        and set(host.get("CapAdd") or []) == set(_USERLAND_CAPS)
        and host.get("SecurityOpt") == ["no-new-privileges:true"],
        "container_security",
    )
    idle = "import signal,sys; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); signal.pause()"
    _kernel_need(
        config.get("Entrypoint") == ["python3", "-c"]
        and config.get("Cmd") == [idle]
        and config.get("User") == "0:0"
        and host.get("Init") in (None, False),
        "idle_init_config",
    )
    common = {
        "id": container.id,
        "state": state,
        "exec_ids": attrs.get("ExecIDs"),
        "config": hashlib.sha256(
            json.dumps({"config": config, "host": host}, sort_keys=True).encode()
        ).hexdigest(),
    }
    services, _ = _machine_execs(state_parent, labels)
    fresh_exec_ids = attrs.get("ExecIDs")
    _kernel_need(type(fresh_exec_ids) is list, "fresh_exec_inventory")
    fresh_execs = {}
    for exec_id in fresh_exec_ids:
        _kernel_need(
            type(exec_id) is str and re.fullmatch(r"[0-9a-f]{64}", exec_id),
            "fresh_exec_identity",
        )
        execution = client.api.exec_inspect(exec_id)
        terminal = (
            execution.get("Running") is False and type(execution.get("ExitCode")) is int
        )
        live_service = (
            state.get("Status") == "running"
            and exec_id in services
            and execution.get("Running") is True
            and execution.get("ExitCode") is None
        )
        _kernel_need(
            execution.get("ContainerID") == container.id and (terminal or live_service),
            "fresh_exec_unresolved",
        )
        fresh_execs[exec_id] = {
            key: execution.get(key)
            for key in ("ContainerID", "Running", "ExitCode", "Pid")
        }
    common["fresh_execs"] = fresh_execs
    if state.get("Status") == "exited":
        _kernel_need(
            state.get("Running") is False
            and state["Pid"] == 0
            and host.get("RestartPolicy", {}).get("Name") == "no"
            and host.get("CgroupParent") == "",
            "stopped_container_state",
        )
        return {**common, "kernel": _stopped_kernel_absence(container.id)}
    _kernel_need(
        state.get("Status") == "running"
        and state.get("Running") is True
        and state["Pid"] > 0,
        "container_state",
    )
    pid = state["Pid"]
    _kernel_need(
        _kernel_read(_PROC_ROOT / str(pid) / "cmdline").split(b"\0")[:-1]
        == [b"python3", b"-c", idle.encode()],
        "idle_init_process",
    )
    metadata = _journal(
        state_parent / "project-machines" / labels["omnia.workspace_id"] / "docker.json"
    )
    declared = {item["name"]: item for item in metadata["manifest"]["services"]}
    expected_arguments = {
        record["exec_id"]: [
            declared[name]["argv"],
            "/workspace/" + declared[name]["cwd"],
            record["log"],
            declared[name]["restart"],
        ]
        for name, record in metadata["services"].items()
    }
    execs, roots = {}, set()
    for exec_id in services:
        _kernel_need(re.fullmatch(r"[0-9a-f]{64}", exec_id), "service_exec_id")
        execution = client.api.exec_inspect(exec_id)
        process = execution.get("ProcessConfig") or {}
        arguments = process.get("arguments")
        _kernel_need(
            execution.get("ContainerID") == container.id
            and execution.get("Running") is True
            and execution.get("ExitCode") is None
            and type(execution.get("Pid")) is int
            and execution["Pid"] > 0
            and process.get("entrypoint") == "python3"
            and isinstance(arguments, list)
            and len(arguments) == 3
            and arguments[:2] == ["-c", _SERVICE_WRAPPER],
            "live_service_binding",
        )
        _kernel_need(
            json.loads(arguments[2]) == expected_arguments[exec_id],
            "service_manifest_binding",
        )
        root = execution["Pid"]
        _kernel_need(root not in roots, "duplicate_service_root")
        roots.add(root)
        _kernel_need(
            _kernel_read(_PROC_ROOT / str(root) / "cmdline").split(b"\0")[:-1]
            == [b"python3", *[v.encode() for v in arguments]],
            "live_service_process",
        )
        execs[exec_id] = {
            k: execution.get(k)
            for k in ("ContainerID", "Running", "ExitCode", "Pid", "ProcessConfig")
        }
    snapshot = _kernel_snapshot(pid, proc=_PROC_ROOT, cgroups=_CGROUP_ROOT)
    _kernel_need(
        bool(
            set(Path(snapshot["group"]).parts)
            & {container.id, "docker-" + container.id + ".scope"}
        ),
        "container_cgroup_identity",
    )
    _service_forest(snapshot, roots, pid)
    return {**common, "metadata": metadata, "execs": execs, "kernel": snapshot}


def _require_current_command_absence(
    state_parent: Path, client: object, containers: list
) -> None:
    """Current sampled absence only, never evidence of an old exec outcome.

    Called solely after strict terminal journal validation and exact Docker404.
    External admission/ingress closure must span this read-only sample.
    """
    try:
        first = [_command_absence_sample(client, state_parent, c) for c in containers]
        second = [_command_absence_sample(client, state_parent, c) for c in containers]
        _kernel_need(first == second, "current command absence sample changed")
    except PhysicalDrainError:
        raise
    except Exception as exc:
        raise PhysicalDrainError("current command absence is unknown") from exc


def _journal(path: Path) -> dict | None:
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise PhysicalDrainError("physical journal path is unsafe")
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise PhysicalDrainError("physical journal shape is unknown")
    return value


def _machine_execs(state_parent: Path, labels: dict) -> tuple[set[str], set[str]]:
    """Only persisted declared runtime services are outside generation drain."""
    root = state_parent / "project-machines" / labels["omnia.workspace_id"]
    machine = _journal(root / "machine.json") or {}
    commands = {
        record["exec_id"]
        for record in machine.get("operations", {}).values()
        if isinstance(record, dict)
        and record.get("kind") == "command"
        and record.get("exec_id")
    }
    if labels.get("omnia.resource_kind") != "development":
        return set(), commands
    metadata = _journal(root / "docker.json")
    if metadata is None:
        return set(), commands
    epoch = metadata.get("epoch")
    if type(epoch) is not int or labels.get("omnia.fencing_epoch") != str(epoch):
        raise PhysicalDrainError("physical service epoch is unknown")
    declared = {
        item["name"] for item in metadata.get("manifest", {}).get("services", [])
    }
    services = set()
    for name, record in metadata.get("services", {}).items():
        if name not in declared or record.get("epoch") != epoch:
            raise PhysicalDrainError("physical service binding is unknown")
        services.add(record["exec_id"])
    if services & (commands | set(metadata.get("exec_logs", {}))):
        raise PhysicalDrainError("physical service conflicts with command journal")
    return services, commands


def _terminal_record(value: dict, family: str, path: Path) -> None:
    """Reuse real typed journal contracts, without their mutating load/fsync paths."""
    try:
        if not isinstance(value.get("receipt"), dict):
            raise ValueError("pending")
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if family == "source-write-operations":
            from yleum_orchestrator.services.source_write_journal import (
                SourceWriteRecord,
            )

            record = SourceWriteRecord.model_validate_json(raw, strict=True)
            binding = record
            if (
                record.receipt is None
                or record.receipt.model_dump(
                    exclude={"after_revision", "written", "deleted"}
                )
                != record.binding()
            ):
                raise ValueError("binding")
        else:
            from yleum_orchestrator.services.command_receipts import (
                CommandJournal,
                CommandRecord,
            )

            record = CommandRecord.model_validate_json(raw, strict=True)
            binding = record.binding
            response = record.response
            if (
                response is None
                or record.receipt != CommandJournal._receipt(binding, response)
                or response.operation_id != binding.operation_id
                or response.before_identity.workspace_revision
                != binding.before_revision
                or response.after_identity.workspace_revision
                != response.workspace_revision
            ):
                raise ValueError("binding")
        if (
            str(binding.workspace_id) != path.parent.name
            or str(binding.operation_id) != path.stem
            or json.dumps(
                record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            )
            != raw
        ):
            raise ValueError("canonical identity")
    except Exception as exc:
        raise PhysicalDrainError(
            "typed physical terminal receipt is unresolved"
        ) from exc


def _command_terminal(record: dict, operation_id: str) -> str:
    try:
        from yleum_orchestrator.services.project_machine import MachineOperationResult

        result = MachineOperationResult.model_validate_json(
            json.dumps(record["result"]), strict=True
        )
        if (
            str(UUID(operation_id)) != operation_id
            or result.operation_id != operation_id
            or result.state != record["state"]
            or type(result.exit_code) is not int
            or not re.fullmatch(r"[0-9a-f]{64}", record["exec_id"])
        ):
            raise ValueError("command")
        return record["exec_id"]
    except Exception as exc:
        raise PhysicalDrainError(
            "typed machine command terminal is unresolved"
        ) from exc


def require_physical_writers_drained(
    state_parent: Path, client: object
) -> dict[str, int]:
    """Read actual helper/exec state in addition to historical terminal SQL proof."""
    containers = client.containers.list(all=True)
    by_name = {}
    by_id = {}
    inspected = {}

    def inspect(exec_id: str):
        if exec_id not in inspected:
            try:
                inspected[exec_id] = client.api.exec_inspect(exec_id)
            except Exception as exc:
                from docker.errors import NotFound

                if type(exc) is NotFound and exc.status_code == 404:
                    inspected[exec_id] = None
                else:
                    raise PhysicalDrainError(
                        "physical exec inspection is unknown"
                    ) from exc
        return inspected[exec_id]

    for container in containers:
        container.reload()
        attrs = container.attrs
        labels = attrs.get("Config", {}).get("Labels") or {}
        kind = labels.get("omnia.resource_kind")
        affected = (
            labels.get("omnia.helper") == "true"
            or labels.get("omnia.project_machine") == "true"
            or "omnia.source_operation_id" in labels
            # The trusted core bootstrap inherits project-cell identity labels,
            # unlike helpers created by DockerMachineBackend.
            or labels.get("omnia.project_cell") == "true"
            and kind == "core-database-bootstrap"
        )
        if not affected:
            continue
        for key in ("workspace_id", "project_id", "owner_id"):
            value = labels.get("omnia." + key)
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise PhysicalDrainError("affected physical writer identity is unknown")
        by_name[container.name] = (attrs, labels)
        by_id[container.id] = (attrs, labels)
        helper = (
            labels.get("omnia.helper") == "true"
            or kind in _MACHINE_HELPERS
            or "omnia.source_operation_id" in labels
        )
        if not helper and kind not in _MACHINE_SERVICES:
            raise PhysicalDrainError("affected physical resource kind is unknown")
        state = attrs.get("State", {})
        if helper and not (
            state.get("Status") == "exited"
            and state.get("Running") is False
            and state.get("Paused") is False
            and state.get("Dead") is False
            and type(state.get("Pid")) is int
            and state["Pid"] == 0
            and attrs.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") == "no"
        ):
            raise PhysicalDrainError("affected physical helper is active or unknown")
        exec_ids = attrs.get("ExecIDs")
        if exec_ids is not None and not isinstance(exec_ids, list):
            raise PhysicalDrainError("affected physical exec inventory is unknown")
        services, _commands = _machine_execs(state_parent, labels)
        for exec_id in set(exec_ids or []):
            if not isinstance(exec_id, str) or not re.fullmatch(
                r"[0-9a-f]{64}", exec_id
            ):
                raise PhysicalDrainError("affected physical exec identity is unknown")
            execution = inspect(exec_id)
            if (
                execution is None
                or execution.get("ContainerID") != attrs.get("Id")
                or not (
                    execution.get("Running") is False
                    and type(execution.get("ExitCode")) is int
                    or exec_id in services
                    and execution.get("Running") is True
                    and execution.get("ExitCode") is None
                )
            ):
                raise PhysicalDrainError("affected physical exec is active or unknown")

    journals = 0
    missing_exec_workspaces = set()
    missing_exec_ids = set()
    for family, filename in (
        ("project-machines", "machine.json"),
        ("source-write-operations", "*.json"),
        ("command-operations", "*.json"),
    ):
        root = state_parent / family
        if root.is_symlink():
            raise PhysicalDrainError("physical journal root is unsafe")
        for path in root.glob("*/" + filename):
            if path.is_symlink() or path.parent.is_symlink():
                raise PhysicalDrainError("physical journal path is unsafe")
            value = json.loads(path.read_bytes())
            identity = (
                value.get("binding", {}) if family == "command-operations" else value
            )
            if (
                str(UUID(path.parent.name)) != path.parent.name
                or identity.get("workspace_id") != path.parent.name
            ):
                raise PhysicalDrainError("physical journal identity is unknown")
            if family == "command-operations":
                _terminal_record(value, family, path)
            elif family == "source-write-operations":
                _terminal_record(value, family, path)
                helpers = value.get("helpers")
                if not isinstance(helpers, dict):
                    raise PhysicalDrainError("source helper journal is unknown")
                for name, expected in helpers.items():
                    if name in by_name:
                        _attrs, actual = by_name[name]
                        if not isinstance(expected, dict) or any(
                            actual.get(k) != v for k, v in expected.items()
                        ):
                            raise PhysicalDrainError(
                                "source helper receipt binding changed"
                            )
            else:
                operations = value.get("operations")
                if not isinstance(operations, dict):
                    raise PhysicalDrainError("physical command journal is unknown")
                for operation_id, record in operations.items():
                    if (
                        not isinstance(record, dict)
                        or record.get("kind")
                        not in {
                            "request",
                            "command",
                            "adaptation_recovery",
                            "adaptation_cleanup",
                        }
                        or record.get("state")
                        not in {"completed", "failed", "timed_out"}
                    ):
                        raise PhysicalDrainError(
                            "physical command outcome is unresolved"
                        )
                    if record["kind"] == "command":
                        exec_id = _command_terminal(record, operation_id)
                        execution = inspect(exec_id)
                        if execution is None:
                            missing_exec_workspaces.add(path.parent.name)
                            missing_exec_ids.add(exec_id)
                        else:
                            owner = by_id.get(execution.get("ContainerID"))
                            if (
                                owner is None
                                or owner[1]["omnia.workspace_id"] != path.parent.name
                                or owner[1].get("omnia.resource_kind") != "development"
                                or execution.get("Running") is not False
                                or type(execution.get("ExitCode")) is not int
                            ):
                                raise PhysicalDrainError(
                                    "command physical owner or outcome is unknown"
                                )
            journals += 1
    # One complete repeated sample per workspace, not per historical command.
    for workspace_id in sorted(missing_exec_workspaces):
        development = [
            container
            for container in containers
            if container.id in by_id
            and by_id[container.id][1]["omnia.workspace_id"] == workspace_id
            and by_id[container.id][1].get("omnia.resource_kind") == "development"
        ]
        if development:
            _require_current_command_absence(state_parent, client, development)
    for exec_id in missing_exec_ids:
        inspected.pop(exec_id, None)
        if inspect(exec_id) is not None:
            raise PhysicalDrainError("missing command inspection changed")
    # A helper appearing after enumeration invalidates the sample; there is no
    # expiry-as-termination inference and no mutation of foreign resources.
    if {c.id for c in client.containers.list(all=True)} != {c.id for c in containers}:
        raise PhysicalDrainError(
            "physical container inventory changed during inspection"
        )
    return {"affected_containers": len(by_name), "physical_journals": journals}


def require_publications_drained(root: Path) -> int:
    if not root.parent.is_dir() or root.is_symlink():
        raise RuntimeError("publication storage authority is unavailable")
    count = 0
    for path in root.glob("*/publication.json"):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("unsafe journal path")
            value = json.loads(path.read_text(encoding="utf-8"))
            if value["project_id"] != path.parent.name:
                raise ValueError("journal identity mismatch")
            history = value["history"]
            if not isinstance(history, list):
                raise ValueError("invalid journal history")
            if history and history[-1]["response"]["phase"] not in {"done", "failed"}:
                raise ValueError("unfinished or unknown publication")
            count += 1
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            # No log bodies/project data are emitted, including malformed JSON.
            raise RuntimeError("publication journals are not quiescent") from exc
    return count


def live_publication_root() -> Path:
    processes = set()
    for service in ("yleum-orchestrator", "omnia-orchestrator"):
        result = subprocess.run(
            ["systemctl", "show", service, "--property=MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            pid = int(result.stdout.strip())
            if pid > 0:
                processes.add(pid)
    if len(processes) != 1:
        raise RuntimeError("expected exactly one live controller process")
    proc = Path("/proc") / str(processes.pop())
    if b"yleum_orchestrator" not in (proc / "cmdline").read_bytes():
        raise RuntimeError("controller process identity is unconfirmed")
    environment = dict(
        item.decode().split("=", 1)
        for item in (proc / "environ").read_bytes().split(b"\0")
        if b"=" in item
    )
    # Changes affect this read-only helper only, never the live process. Settings
    # resolve the same environment/.env defaults as that controller; no secrets print.
    os.environ.clear()
    os.environ.update(environment)
    os.chdir((proc / "cwd").resolve())
    sys.path.insert(0, str(Path.cwd() / "src"))
    from yleum_orchestrator.core.config import get_settings

    return Path(get_settings().cell_state_path).parent / "cell-publications"


if __name__ == "__main__":
    try:
        root = live_publication_root()
        count = require_publications_drained(root)
        import docker
        from yleum_orchestrator.core.config import get_settings

        client = docker.DockerClient(base_url=get_settings().docker_host, timeout=10)
        try:
            physical = require_physical_writers_drained(root.parent, client)
        finally:
            client.close()
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, PhysicalDrainError)
            else "publication_or_physical_unknown"
        )
        print(json.dumps({"publication_drained": False, "reason": reason}))
        raise SystemExit(75) from None
    print(json.dumps({"publication_drained": True, "journals": count, **physical}))
