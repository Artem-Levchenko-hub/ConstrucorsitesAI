#!/usr/bin/env python3
"""Read-only publication quiescence gate using the live controller's settings."""

from __future__ import annotations

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
        "development", "project-postgres", "namespace-guard", "egress-proxy",
        "managed-max-core", "max-gateway",
    }
)


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
                        development = [
                            labels
                            for _attrs, labels in by_id.values()
                            if labels["omnia.workspace_id"] == path.parent.name
                            and labels.get("omnia.resource_kind") == "development"
                        ]
                        if execution is None:
                            if development:
                                raise PhysicalDrainError(
                                    "missing command exec has a remaining development container"
                                )
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
