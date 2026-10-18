#!/usr/bin/env python3
"""Privileged complete-reader observer. Registrations can deny, never authorize."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

SLOTS = frozenset(
    {
        "core/api",
        "core/worker",
        "core/generation-worker",
        "core/orchestrator",
        "commerce/orchestrator",
    }
)
API_CAPABILITIES = (
    "acceptance:2",
    "adaptation-cleanup:1",
    "adaptation-recovery:1",
    "context:1",
    "cursor:1",
    "dispatch:1",
    "effect:1",
    "provider-identity:1",
    "publication:1",
    "publication:2",
    "reader-standby:1",
    "recovery-state:1",
    "recovery-state:2",
    "recovery-state:3",
    "response-manifest:1",
    "source-checkpoint:1",
    "tracked-command:1",
    "tracked-source-write:1",
)
CONTROLLER_CAPABILITIES = (
    "adaptation-cleanup:1",
    "adaptation-recovery:1",
    "reader-standby:1",
    "tracked-command:1",
    "tracked-source-write:1",
    "writer-barrier:1",
)
IDENTITY_KEYS = frozenset(
    {
        "host",
        "role",
        "host_boot_id",
        "pid_namespace_inode",
        "pid",
        "start_ticks",
        "startup_nonce",
    }
)
OBSERVED_KEYS = IDENTITY_KEYS | {
    "slot",
    "host_pid",
    "container_id",
    "supervisor_id",
    "release_sha",
    "capabilities",
}
CONTAINERS = {
    "api": "yleum-prod-api",
    "worker": "yleum-prod-worker",
    "generation-worker": "yleum-prod-generation-worker",
}
ENTRYPOINTS = {
    "yleum_api.main:app": "api",
    "yleum_api.workers.run": "worker",
    "yleum_api.workers.generation": "generation-worker",
    "yleum_orchestrator.main:app": "orchestrator",
}


def need(condition: object) -> None:
    if not condition:
        raise ValueError("reader inventory cannot establish complete authority")


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def positive(value: object) -> None:
    need(type(value) is int and value > 0)


def uuid_string(value: object) -> None:
    need(type(value) is str and str(UUID(value)) == value)


def sha(value: object, length: int) -> None:
    need(type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value))


def identity_of(row: dict) -> dict:
    identity = {key: row[key] for key in IDENTITY_KEYS}
    need(f"{identity['host']}/{identity['role']}" in SLOTS)
    for key in ("host_boot_id", "startup_nonce"):
        uuid_string(identity[key])
    for key in ("pid_namespace_inode", "pid", "start_ticks"):
        positive(identity[key])
    return identity


def process_role(argv: list[str]) -> str | None:
    """Recognize actual Python/uvicorn entrypoints, not aggregate heartbeat names."""
    need(type(argv) is list and all(type(v) is str for v in argv))
    tokens = set(argv)
    if "-c" in argv or "-lc" in argv:
        for item in argv:
            if "yleum_" in item:
                tokens.update(shlex.split(item))
    roles = {role for token, role in ENTRYPOINTS.items() if token in tokens}
    need(len(roles) <= 1)
    return next(iter(roles), None)


def retired_dormant(row: dict) -> bool:
    """No automatic restart target; host cgroup absence is checked separately."""
    return (
        row.get("status") == "exited"
        and row.get("running") is False
        and row.get("paused") is False
        and row.get("dead") is False
        and type(row.get("pid")) is int
        and row["pid"] == 0
        and row.get("restart") == "no"
    )


def is_reader_container(row: dict) -> bool:
    return (
        process_role(row["command"]) is not None
        or row["configured_image"].split(":", 1)[0] == "omnia-api"
        or any(
            row["name"].startswith(prefix)
            for prefix in (
                "yleum-prod-api",
                "omnia-prod-api",
                "yleum-prod-worker",
                "omnia-prod-worker",
                "yleum-prod-generation-worker",
                "omnia-prod-generation-worker",
            )
        )
    )


def classify_containers(rows: list[dict], host: str) -> dict[str, dict]:
    """Include parked old containers: their restart policy is not quiescence."""
    need(host in {"core", "commerce"})
    expected = CONTAINERS if host == "core" else {}
    selected = {}
    for row in rows:
        role = process_role(row["command"])
        name = row["name"]
        if not is_reader_container(row):
            continue
        if name not in expected.values() and retired_dormant(row):
            continue
        need(
            role in expected
            and role not in selected
            and row["service"] == role
            and name == expected[role]
            and row["running"] is True
        )
        sha(row["id"], 64)
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", row["image"]))
        positive(row["pid"])
        selected[role] = row
    need(set(selected) == set(expected))
    return selected


def command(argv: list[str], *, stdin: str | None = None, timeout: int = 20) -> str:
    result = subprocess.run(
        argv, input=stdin, capture_output=True, text=True, timeout=timeout
    )
    need(result.returncode == 0 and len(result.stdout) <= 2_000_000)
    return result.stdout.strip()


def _docker_container_roster() -> dict[str, str]:
    """Read every current container identity and name, including stopped ones."""
    template = '{"id":{{json .ID}},"name":{{json .Names}}}'
    raw = command(["docker", "ps", "-a", "--no-trunc", "--format", template])
    rows = [json.loads(line) for line in raw.splitlines()]
    need(
        len(rows) <= 10000
        and all(
            type(row) is dict
            and set(row) == {"id", "name"}
            and type(row["id"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", row["id"])
            and type(row["name"]) is str
            and bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", row["name"]))
            for row in rows
        )
    )
    result = {row["id"]: row["name"] for row in rows}
    need(len(result) == len(rows) and len(set(result.values())) == len(rows))
    return result


def _inspect_docker_containers(ids: list[str]) -> dict[str, dict]:
    """Inspect every requested ID; a disappearing or unobserved ID is unknown."""
    template = (
        '{"id":{{json .Id}},"image":{{json .Image}},"name":{{json .Name}},'
        '"configured_image":{{json .Config.Image}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"running":{{json .State.Running}},"pid":{{json .State.Pid}},'
        '"status":{{json .State.Status}},"paused":{{json .State.Paused}},'
        '"dead":{{json .State.Dead}},"restart":{{json .HostConfig.RestartPolicy.Name}},'
        '"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}}}'
    )
    rows = []
    raw = command(["docker", "inspect", "--format", template, *ids]) if ids else ""
    for line in raw.splitlines():
        row = json.loads(line)
        row["name"] = row["name"].removeprefix("/")
        row["command"] = (row.pop("entrypoint") or []) + (row.pop("cmd") or [])
        rows.append(row)
    need(len(rows) == len(ids) and {row["id"] for row in rows} == set(ids))
    return {row["id"]: row for row in rows}


def _reader_cohort(roster: dict[str, str], observed: dict[str, dict]) -> set[str]:
    """Names are live roster data; only Config/image evidence is reusable by ID."""
    need(set(roster) <= set(observed))
    return {
        container_id
        for container_id, name in roster.items()
        if is_reader_container({**observed[container_id], "name": name})
    }


def docker_inventory() -> list[dict]:
    """Prove a complete census and stable readers while project helpers churn.

    Every new ID is fully inspected. Only positively classified non-readers may
    appear or disappear; the reader cohort, including dormant restart targets,
    must retain fresh metadata. The enclosing timestamp and TTL are unchanged.
    """
    current = _docker_container_roster()
    observed = _inspect_docker_containers(list(current))
    need(all(observed[key]["name"] == name for key, name in current.items()))
    readers = _reader_cohort(current, observed)
    for _attempt in range(3):
        # Inspect IDs from the preceding complete census even if they have
        # already disappeared: absence cannot classify an unknown container.
        unseen = sorted(set(current) - set(observed))
        added = _inspect_docker_containers(unseen)
        need(all(not is_reader_container(row) for row in added.values()))
        need(all(row["name"] == current[key] for key, row in added.items()))
        observed.update(added)
        need(_reader_cohort(current, observed) == readers)
        fresh = _inspect_docker_containers(sorted(readers))
        need(all(fresh[key]["name"] == current[key] for key in readers))
        need(all(is_reader_container(row) for row in fresh.values()))
        observed.update(fresh)
        final = _docker_container_roster()
        need(readers <= set(final))
        if set(final) - set(observed):
            current = final
            continue
        need(_reader_cohort(final, observed) == readers)
        need(all(observed[key]["name"] == final[key] for key in readers))
        # Callers need mutable State/PID/RestartPolicy only for reader candidates.
        # Do not expose cached non-reader state as current physical evidence.
        result = []
        for key, name in final.items():
            row = observed[key]
            if key not in readers:
                row = {field: row[field] for field in (
                    "id", "image", "configured_image", "command"
                )}
                row["name"] = name
            result.append(row)
        return result
    raise ValueError("Docker census contains unclassified containers throughout observation")


def process_inventory(proc_root: Path = Path("/proc")) -> dict[int, dict]:
    """All host processes, including readers outside current named containers."""
    processes = {}
    for proc in proc_root.iterdir():
        if not proc.name.isdecimal():
            continue
        role = None
        try:
            raw = (proc / "cmdline").read_bytes()
            need(len(raw) <= 131072)
            argv = [v.decode("utf-8") for v in raw.split(b"\0") if v]
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            role = process_role(argv)
            if argv and Path(argv[0]).name == "uv":
                role = None  # launcher remains in the verified reader ancestor chain
            values = {
                "host_pid": int(proc.name),
                "parent": int(fields[1]),
                "start_ticks": int(fields[19]),
                "role": role,
                "argv": argv,
                "cgroup": (proc / "cgroup").read_text(),
            }
            if role is not None:
                status = (proc / "status").read_text()
                nspid = [
                    line.split()[1:]
                    for line in status.splitlines()
                    if line.startswith("NSpid:")
                ]
                need(len(nspid) == 1 and bool(nspid[0]))
                values.update(
                    pid=int(nspid[0][-1]),
                    pid_namespace_inode=(proc / "ns/pid").stat().st_ino,
                    argv=argv,
                )
            processes[int(proc.name)] = values
        except (FileNotFoundError, ProcessLookupError):
            # Ordinary short-lived unrelated helpers may exit while enumerating.
            # A recognized reader exiting during collection must invalidate it.
            if role is not None:
                raise ValueError("reader changed during process inventory") from None
            continue
    return processes


def descendant(processes: dict[int, dict], pid: int, parent: int) -> bool:
    seen = set()
    while pid in processes and pid not in seen:
        if pid == parent:
            return True
        seen.add(pid)
        pid = processes[pid]["parent"]
    return False


def require_no_hidden_readers(processes: dict[int, dict], groups: list[str]) -> None:
    """An unregistered interpreter inside a reader supervisor is not ignored."""
    for row in processes.values():
        if row["role"] is not None or not row["argv"]:
            continue
        paths = [line.split(":", 2)[-1] for line in row["cgroup"].splitlines()]
        if not any(
            path == group or path.startswith(group + "/")
            for path in paths
            for group in groups
        ):
            continue
        executable = Path(row["argv"][0]).name
        need(
            not (
                executable.startswith("python")
                or executable in {"uvicorn", "gunicorn", "hypercorn"}
            )
        )
        if executable == "uv":
            need(
                any(
                    other["role"] is not None
                    and descendant(processes, other["host_pid"], row["host_pid"])
                    for other in processes.values()
                )
            )


_INSPECTION_INSTANCE = "yleum-reader-inspection"
_UNIT_PROPERTIES = (
    "Id",
    "Names",
    "LoadState",
    "MainPID",
    "ControlGroup",
    "ExecStart",
    "ActiveState",
    "FragmentPath",
    "DropInPaths",
)


def _require_passive_unit(data: dict[str, str]) -> None:
    need(data["MainPID"] == "0" and data["ActiveState"] == "inactive")
    need(not data["ControlGroup"] and not data["DropInPaths"])
    need(not data.get("ExecStart", ""))


def _require_mask_fragment(fragment: str) -> None:
    if fragment == "/dev/null":
        return
    need(PurePosixPath(fragment).is_absolute())
    path = Path(fragment)
    info = path.lstat()
    need(info.st_uid == 0 and stat.S_ISLNK(info.st_mode))
    need(path.resolve(strict=True) == Path("/dev/null"))


def _require_empty_service_start(unit: str) -> None:
    # systemd v255 unit_dbus_path_from_name / bus_label_escape. Direct property
    # lookup loads only metadata, avoiding a separate GetUnit/manager-GC race.
    label = "".join(
        chr(byte)
        if 65 <= byte <= 90 or 97 <= byte <= 122 or (index > 0 and 48 <= byte <= 57)
        else f"_{byte:02x}"
        for index, byte in enumerate(unit.encode("utf-8"))
    )
    path = "/org/freedesktop/systemd1/unit/" + label
    for interface, prop, expected in (
        ("Unit", "Id", {"type": "s", "data": unit}),
        ("Unit", "LoadState", {"type": "s", "data": "loaded"}),
        ("Service", "ExecStart", {"type": "a(sasbttttuii)", "data": []}),
    ):
        value = json.loads(
            command(
                [
                    "busctl",
                    "--system",
                    "--json=short",
                    "--auto-start=no",
                    "get-property",
                    "org.freedesktop.systemd1",
                    path,
                    "org.freedesktop.systemd1." + interface,
                    prop,
                ]
            )
        )
        need(value == expected)


def _service_properties(
    names: set[str], absent_allowed: set[str]
) -> dict[str, dict[str, str]]:
    """Require a complete effective-property response, including empty values."""
    text = command(
        [
            "systemctl",
            "show",
            *sorted(names),
            "--all",
            "--no-pager",
            "--property=" + ",".join(_UNIT_PROPERTIES),
        ]
    )
    by_name: dict[str, dict[str, str]] = {}
    for block in text.split("\n\n"):
        pairs = [line.split("=", 1) for line in block.splitlines()]
        need(all(len(pair) == 2 for pair in pairs))
        data = {}
        for key, value in pairs:
            if key in data:
                # oneshot services print one ExecStart line per command. Keep
                # every command visible to the reader/restart-target checks.
                need(key == "ExecStart")
                data[key] += "\n" + value
            else:
                data[key] = value
        need(
            set(data) in (set(_UNIT_PROPERTIES), set(_UNIT_PROPERTIES) - {"ExecStart"})
        )
        aliases = set(shlex.split(data["Names"]))
        need(data["Id"] in aliases and aliases & names)
        if data["LoadState"] == "not-found":
            need(aliases & names <= absent_allowed)
            _require_passive_unit(data)
            need(not data["FragmentPath"])
        elif data["LoadState"] == "masked":
            _require_passive_unit(data)
            _require_mask_fragment(data["FragmentPath"])
        else:
            need(data["LoadState"] == "loaded")
            if "ExecStart" not in data:
                _require_empty_service_start(data["Id"])
        # Missing formatted commands require passive or typed-empty proof.
        data.setdefault("ExecStart", "")
        for name in aliases & names:
            need(name not in by_name or by_name[name] == data)
            by_name[name] = data
    need(set(by_name) == names)
    return by_name


def _inspect_template_instance(data: dict[str, str]) -> None:
    """The fixed probe may load metadata, but must not shadow or run a template."""
    need(data["MainPID"] == "0" and data["ActiveState"] == "inactive")
    need(not data["ControlGroup"])
    if data["LoadState"] == "masked":
        _require_mask_fragment(data["FragmentPath"])
    else:
        need(PurePosixPath(data["FragmentPath"]).is_absolute())
        need(PurePosixPath(data["FragmentPath"]).name.endswith("@.service"))
    for path in shlex.split(data["DropInPaths"]):
        need(PurePosixPath(path).is_absolute())
        # Instance-specific overrides could hide the template's actual reader.
        need(
            all(
                "@" not in part or part.endswith("@.service.d")
                for part in PurePosixPath(path).parts
            )
        )


def controller_units() -> dict:
    """Inspect installed definitions and all loaded instances without starting any."""
    units = command(
        ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"]
    ).splitlines()
    found = {}
    loaded = command(
        [
            "systemctl",
            "list-units",
            "--all",
            "--type=service",
            "--plain",
            "--no-legend",
            "--no-pager",
        ]
    ).splitlines()
    installed = {line.split()[0] for line in units if line.strip()}
    names = installed | {line.split()[0] for line in loaded if line.strip()}
    need(names and len(names) < 10000)
    need(all(re.fullmatch(r"[A-Za-z0-9_@.\\:\-]+\.service", name) for name in names))
    templates = {name for name in names if name.endswith("@.service")}
    probes = {
        name[: -len(".service")] + _INSPECTION_INSTANCE + ".service"
        for name in templates
    }
    # Repeated observations may find our inert metadata in list-units; an
    # installed instance file, however, would override the template definition.
    need(not probes & installed)
    rows = _service_properties((names - templates) | probes, names - installed - probes)
    for probe in probes:
        _inspect_template_instance(rows[probe])
    for data in rows.values():
        need(
            not any(
                token in data.get("ExecStart", "")
                for token, role in ENTRYPOINTS.items()
                if role != "orchestrator"
            )
        )
        if "yleum_orchestrator" not in data.get("ExecStart", ""):
            continue
        unit = data["Id"]
        need(unit in {"yleum-orchestrator.service", "omnia-orchestrator.service"})
        need("yleum_orchestrator.main:app" in data["ExecStart"])
        need(
            data["ActiveState"] == "active"
            and data["MainPID"].isdigit()
            and int(data["MainPID"]) > 0
        )
        if unit in found:
            need(found[unit] == data)
        found[unit] = data
    need(len(found) == 1)
    return next(iter(found.values()))


def collect_host(
    *,
    host: str,
    release_sha: str,
    registrations: dict,
    api_image: str | None,
    proc_root: Path = Path("/proc"),
) -> list[dict]:
    need(os.geteuid() == 0 and host in {"core", "commerce"})
    docker_rows = docker_inventory()
    containers = classify_containers(docker_rows, host)
    unit = controller_units()
    processes = process_inventory(proc_root)
    for row in docker_rows:
        if is_reader_container(row) and retired_dormant(row):
            need(
                not any(
                    row["id"] in process["cgroup"] for process in processes.values()
                )
            )
    boot = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
    uuid_string(boot)
    expected_roles = {"orchestrator", *containers}
    readers = [row for row in processes.values() if row["role"] is not None]
    need(
        len(readers) == len(expected_roles)
        and {r["role"] for r in readers} == expected_roles
    )
    groups = [
        line.split(":", 2)[-1] for row in readers for line in row["cgroup"].splitlines()
    ]
    require_no_hidden_readers(processes, groups)
    observed = []
    for row in readers:
        role = row["role"]
        proc = proc_root / str(row["host_pid"])
        release = [
            v.split(b"=", 1)[1].decode("ascii")
            for v in (proc / "environ").read_bytes().split(b"\0")
            if v.startswith(b"OMNIA_RELEASE_SHA=")
        ]
        need(release == [release_sha])
        container_id = None
        if role == "orchestrator":
            need(row["host_pid"] == int(unit["MainPID"]))
            need(
                unit["ControlGroup"]
                and any(
                    line.split(":", 2)[-1] == unit["ControlGroup"]
                    for line in row["cgroup"].splitlines()
                )
            )
            supervisor = unit["Id"]
        else:
            ctr = containers[role]
            need(
                ctr["image"] == api_image
                and ctr["configured_image"] == "omnia-api:" + release_sha
            )
            need(descendant(processes, row["host_pid"], ctr["pid"]))
            need(ctr["id"] in row["cgroup"])
            container_id, supervisor = ctr["id"], "docker:" + ctr["id"]
        # Match nonce-bearing registration only after proving the process from the host.
        actual = {
            "host": host,
            "role": role,
            "host_boot_id": boot,
            **{k: row[k] for k in ("pid_namespace_inode", "pid", "start_ticks")},
        }
        matches = [
            value["payload"]
            for value in registrations.values()
            if all(value["payload"]["identity"].get(k) == v for k, v in actual.items())
        ]
        need(len(matches) == 1)
        registered = matches[0]
        caps = list(
            CONTROLLER_CAPABILITIES if role == "orchestrator" else API_CAPABILITIES
        )
        need(
            registered["release_sha"] == release_sha
            and registered["capabilities"] == caps
        )
        observed.append(
            {
                **registered["identity"],
                "slot": host + "/" + role,
                "host_pid": row["host_pid"],
                "container_id": container_id,
                "supervisor_id": supervisor,
                "release_sha": release_sha,
                "capabilities": caps,
            }
        )
    # A second independently enumerated set prevents a mixed-PID observation.
    final = process_inventory(proc_root)
    require_no_hidden_readers(final, groups)
    need(
        {p: r for p, r in final.items() if r["role"] is not None}
        == {p: r for p, r in processes.items() if r["role"] is not None}
    )
    return observed


def build_snapshot(
    *,
    observations: list[dict],
    registrations: dict,
    release_sha: str,
    epoch: str,
    redis_run_id: str,
    observed_at_ms: int,
) -> dict:
    """Only independently collected observations may be passed here by the root scan."""
    sha(release_sha, 40)
    sha(redis_run_id, 40)
    uuid_string(epoch)
    positive(observed_at_ms)
    need(type(observations) is list and len(observations) == len(SLOTS))
    need(type(registrations) is dict and len(registrations) == len(SLOTS))
    need({row.get("slot") for row in observations} == SLOTS)
    expected = []
    hashes = []
    for row in observations:
        need(type(row) is dict and set(row) == OBSERVED_KEYS)
        identity = identity_of(row)
        need(row["slot"] == f"{identity['host']}/{identity['role']}")
        positive(row["host_pid"])
        need(type(row["supervisor_id"]) is str and 0 < len(row["supervisor_id"]) <= 256)
        caps = (
            CONTROLLER_CAPABILITIES
            if identity["role"] == "orchestrator"
            else API_CAPABILITIES
        )
        need(row["capabilities"] == list(caps) and row["release_sha"] == release_sha)
        if identity["role"] == "orchestrator":
            need(row["container_id"] is None)
        else:
            sha(row["container_id"], 64)
        key = digest(identity)
        record = registrations.get(key)
        need(type(record) is dict and set(record) == {"payload", "ttl"})
        positive(record["ttl"])
        need(record["ttl"] <= 30)
        need(
            record["payload"]
            == {
                "identity": identity,
                "release_sha": release_sha,
                "capabilities": list(caps),
            }
        )
        hashes.append(key)
        expected.append(
            {"slot": row["slot"], "host": identity["host"], "role": identity["role"]}
        )
    need(len(set(hashes)) == len(SLOTS) and set(hashes) == set(registrations))
    value = {
        "version": 1,
        "epoch": epoch,
        "state": "ready",
        "release_sha": release_sha,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": observed_at_ms + 30000,
        "redis_run_id": redis_run_id,
        "expected": sorted(expected, key=lambda row: row["slot"]),
        "observed": sorted(observations, key=lambda row: row["slot"]),
        "registered": sorted(hashes),
    }
    return {**value, "digest": digest(value)}


INHIBIT = "rollout-inhibit.json"


def secure_directory(directory: Path) -> None:
    info = directory.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(directory: Path, name: str, value: dict) -> None:
    secure_directory(directory)
    need(name in {"current.json", INHIBIT})
    target = directory / name
    if target.is_symlink():
        raise ValueError("inventory target is a symlink")
    temporary = directory / (".reader-" + secrets.token_hex(16))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        fsync_directory(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_inhibit(value: dict) -> None:
    need(
        type(value) is dict and set(value) == {"version", "release_sha", "owner_nonce"}
    )
    need(type(value["version"]) is int and value["version"] == 1)
    sha(value["release_sha"], 40)
    uuid_string(value["owner_nonce"])


def read_inhibit(directory: Path) -> dict | None:
    secure_directory(directory)
    try:
        info = (directory / INHIBIT).lstat()
    except FileNotFoundError:
        return None
    need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    need(0 < info.st_size < 4096)
    value = json.loads((directory / INHIBIT).read_bytes())
    validate_inhibit(value)
    return value


def write_inhibit(directory: Path, owner: dict) -> None:
    validate_inhibit(owner)
    previous = read_inhibit(directory)
    need(previous is None or previous == owner)
    atomic_json(directory, INHIBIT, owner)


def remove_inhibit(directory: Path, owner: dict) -> None:
    validate_inhibit(owner)
    need(read_inhibit(directory) == owner)
    (directory / INHIBIT).unlink()
    fsync_directory(directory)


EPOCH_KEY = "omnia:generation-readers:epoch"
PREFIX = "omnia:health:generation-readers:"


def registrations_now(redis: object, *, deadlines: list[float] | None = None) -> dict:
    result = {}
    deadlines = [] if deadlines is None else deadlines
    for key in redis.scan_iter(match=PREFIX + "*", count=100):
        need(type(key) is str and key.startswith(PREFIX))
        identity_hash = key[len(PREFIX) :]
        sha(identity_hash, 64)
        raw = redis.get(key)
        before = time.monotonic()
        ttl = redis.pttl(key)
        need(raw is not None and type(ttl) is int and 0 < ttl <= 30000)
        payload = json.loads(raw)
        need(
            type(payload) is dict
            and set(payload) == {"identity", "release_sha", "capabilities"}
        )
        need(set(payload["identity"]) == IDENTITY_KEYS)
        identity = identity_of(payload["identity"])
        need(digest(identity) == identity_hash and identity_hash not in result)
        # The snapshot observation time precedes collection. Expiry of any
        # registration during the scan still invalidates its final validation.
        deadlines.append(before + ttl / 1000)
        need(time.monotonic() < deadlines[-1])
        result[identity_hash] = {"payload": payload, "ttl": max(1, ttl // 1000)}
    need(len(result) == len(SLOTS) and time.monotonic() < min(deadlines))
    return result


def check_live(snapshot: dict, redis: object) -> None:
    now = time.time_ns() // 1_000_000
    need(snapshot["observed_at_ms"] <= now < snapshot["expires_at_ms"])
    expected = {
        digest(identity_of(row)): {
            "payload": {
                "identity": identity_of(row),
                "release_sha": row["release_sha"],
                "capabilities": row["capabilities"],
            }
        }
        for row in snapshot["observed"]
    }
    deadlines = []
    actual = registrations_now(redis, deadlines=deadlines)
    need(set(actual) == set(expected))
    need(all(actual[key]["payload"] == expected[key]["payload"] for key in expected))
    epoch = redis.get(EPOCH_KEY)
    run_id = redis.info("server").get("run_id")
    need(epoch == snapshot["epoch"] and run_id == snapshot["redis_run_id"])
    need(
        time.time_ns() // 1_000_000 < snapshot["expires_at_ms"]
        and time.monotonic() < min(deadlines)
    )


def distribute(
    snapshot: dict,
    redis: object,
    write_local: object,
    write_remote: object,
    read_remote: object,
) -> None:
    """Inhibit, not a Redis positive commit, protects the release cutover."""
    try:
        check_live(snapshot, redis)
        write_local(snapshot)
        write_remote(snapshot)
        need(read_remote() == snapshot)
        check_live(snapshot, redis)
    except Exception:
        # Invalidating an epoch only removes authority. Never restore a previous
        # epoch or refresh an old scan's timestamps after a partial distribution.
        redis.set(EPOCH_KEY, str(uuid4()))
        denied = {**snapshot, "state": "deny"}
        denied["digest"] = digest({k: v for k, v in denied.items() if k != "digest"})
        for write in (write_local, write_remote):
            try:
                write(denied)
            except Exception:
                pass
        raise


def supervisor_inventory(host: str) -> dict:
    """Capture before replacement; no self-reported capability can omit a process."""
    rows = docker_inventory()
    selected = classify_containers(rows, host)
    processes = process_inventory()
    unit = controller_units()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    uuid_string(boot)
    readers = [p for p in processes.values() if p["role"] is not None]
    require_no_hidden_readers(processes, [
        line.split(":", 2)[-1] for row in readers for line in row["cgroup"].splitlines()
    ])
    need(len(readers) == len(selected) + 1)
    need({p["role"] for p in readers} == {*selected, "orchestrator"})
    for process in readers:
        if process["role"] == "orchestrator":
            need(process["host_pid"] == int(unit["MainPID"]))
        else:
            ctr = selected[process["role"]]
            need(
                ctr["id"] in process["cgroup"]
                and descendant(processes, process["host_pid"], ctr["pid"])
            )
    retired = []
    for row in rows:
        if is_reader_container(row) and retired_dormant(row):
            need(not any(row["id"] in p["cgroup"] for p in processes.values()))
            retired.append(
                {
                    k: row[k]
                    for k in (
                        "id",
                        "image",
                        "name",
                        "status",
                        "running",
                        "paused",
                        "dead",
                        "pid",
                        "restart",
                    )
                }
            )
    return {
        "host": host,
        "boot_id": boot,
        "unit": unit["Id"],
        "processes": [
            {
                k: p[k]
                for k in (
                    "host_pid",
                    "start_ticks",
                    "pid_namespace_inode",
                    "pid",
                    "role",
                )
            }
            for p in readers
        ],
        "containers": [
            {k: row[k] for k in ("id", "image", "name")} for row in selected.values()
        ],
        "retired": sorted(retired, key=lambda row: row["id"]),
    }


def verify_retired(before: dict, after: dict) -> None:
    need(before["host"] == after["host"])
    # An old dormant resource must remain untouched, never deleted or retagged.
    need(before["retired"] == after["retired"])
    if before["boot_id"] == after["boot_id"]:
        old = {(r["host_pid"], r["start_ticks"]) for r in before["processes"]}
        new = {(r["host_pid"], r["start_ticks"]) for r in after["processes"]}
        need(not old & new)
    need(
        not {r["id"] for r in before["containers"]}
        & {r["id"] for r in after["containers"]}
    )


DIRECTORY = Path("/run/yleum-generation-readers")
STATE = Path("/var/lib/yleum-generation-readers")
SCRIPT = "/opt/omnia/infra/release/generation-reader-inventory.py"
PYTHON = "/opt/omnia/apps/orchestrator/.venv/bin/python"


def read_root_json(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        need(
            stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
        )
        need(0 < info.st_size <= 131072)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = json.load(stream)
        need(type(value) is dict)
        return value
    finally:
        os.close(fd)


def save_state(name: str, value: dict) -> None:
    need(re.fullmatch(
        r"(?:config|capture-[0-9a-f]{40}-[0-9a-f]{8}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json", name,
    ))
    secure_directory(STATE)
    temporary = STATE / (".state-" + secrets.token_hex(16))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value).encode())
            stream.flush()
            os.fsync(stream.fileno())
        destination = STATE / name
        need(not destination.is_symlink())
        os.replace(temporary, destination)
        fsync_directory(STATE)
    finally:
        if temporary.exists():
            temporary.unlink()


def redis_connection() -> object:
    from redis import Redis

    unit = controller_units()
    raw = Path("/proc", unit["MainPID"], "environ").read_bytes()
    selected = [
        v.split(b"=", 1)[1].decode()
        for v in raw.split(b"\0")
        if v.startswith(b"REDIS_URL=")
    ]
    need(len(selected) == 1)
    return Redis.from_url(
        selected[0], decode_responses=True, socket_timeout=2, socket_connect_timeout=2
    )


def remote(action: str, payload: dict, ssh_user: str) -> dict:
    need(
        action
        in {"collect", "publish", "read", "capture", "inhibit", "clear", "verify-owner"}
    )
    need(ssh_user == "root")
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "ControlPath=none",
        "-o",
        "ControlMaster=no",
        "-o",
        "ConnectTimeout=5",
        "commerce",
        "sudo",
        "-n",
        PYTHON,
        SCRIPT,
        action,
        "--host",
        "commerce",
    ]
    value = json.loads(command(argv, stdin=canonical(payload), timeout=15))
    need(type(value) is dict)
    return value


def install_host(host: str, release_sha: str, ssh_user: str) -> None:
    """Root release setup only; never changes existing containers or credentials."""
    sha(release_sha, 40)
    need(host in {"core", "commerce"})
    for directory in (DIRECTORY, STATE):
        if not directory.exists():
            directory.mkdir(mode=0o755)
        secure_directory(directory)
    owner = read_inhibit(STATE)
    need(owner is not None and owner["release_sha"] == release_sha)
    image = None
    if host == "core":
        image = command(
            [
                "docker",
                "image",
                "inspect",
                "omnia-api:" + release_sha,
                "--format",
                "{{.Id}}",
            ]
        )
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", image))
        need(ssh_user == "root")
    config = {
        "version": 1,
        "host": host,
        "release_sha": release_sha,
        "api_image": image,
        "ssh_user": ssh_user,
    }
    save_state("config.json", config)
    unit = controller_units()["Id"]
    dropin = Path("/etc/systemd/system") / (unit + ".d")
    dropin.mkdir(mode=0o755, exist_ok=True)
    secure_directory(dropin)
    target = dropin / "generation-readers.conf"
    need(not target.is_symlink())
    content = (
        "[Service]\nEnvironment=GENERATION_RECOVERY_INVENTORY_DIR="
        + str(DIRECTORY)
        + "\nEnvironment=GENERATION_RECOVERY_READER_HOST="
        + host
        + "\nReadOnlyPaths="
        + str(DIRECTORY)
        + "\n"
    )
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(dropin)
    copies = [
        (
            "generation-readers.tmpfiles.conf",
            Path("/etc/tmpfiles.d/yleum-generation-readers.conf"),
        )
    ]
    if host == "core":
        copies.extend(
            (name, Path("/etc/systemd/system") / name)
            for name in (
                "generation-reader-observer.service",
                "generation-reader-observer.timer",
            )
        )
    for source_name, destination in copies:
        content = (Path(SCRIPT).parent / source_name).read_bytes()
        fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(destination.parent)
    command(["systemctl", "daemon-reload"])
    if host == "core":
        command(["systemctl", "enable", "--now", "generation-reader-observer.timer"])


def scan(config: dict) -> dict:
    need(config["host"] == "core")
    redis = None
    collectors = None
    try:
        redis = redis_connection()
        # Timestamp starts BEFORE the complete two-host observation.
        observed_at = time.time_ns() // 1_000_000
        epoch = redis.get(EPOCH_KEY)
        uuid_string(epoch)
        run_id = redis.info("server").get("run_id")
        sha(run_id, 40)
        registrations = registrations_now(redis)
        payload = {"release_sha": config["release_sha"], "registrations": registrations}
        # Independent full observations share the original deadline. Neither
        # host may publish readiness; both results must pass the same final
        # registration, epoch and expiry checks below.
        collectors = ThreadPoolExecutor(max_workers=2)
        futures = {
            collectors.submit(
                collect_host,
                host="core",
                release_sha=config["release_sha"],
                api_image=config["api_image"],
                registrations=registrations,
            ): "core",
            collectors.submit(remote, "collect", payload, config["ssh_user"]): "commerce",
        }
        observations = {}
        for future in as_completed(futures):
            host = futures[future]
            value = future.result()
            if host == "commerce":
                need(set(value) == {"observed"})
                value = value["observed"]
            observations[host] = value
        snapshot = build_snapshot(
            observations=observations["core"] + observations["commerce"],
            registrations=registrations,
            release_sha=config["release_sha"],
            epoch=epoch,
            redis_run_id=run_id,
            observed_at_ms=observed_at,
        )
        distribute(
            snapshot,
            redis,
            lambda value: atomic_json(DIRECTORY, "current.json", value),
            lambda value: remote("publish", value, config["ssh_user"]),
            lambda: remote("read", {}, config["ssh_user"]),
        )
        return snapshot
    except Exception:
        # Revoke immediately, before waiting for any still-running read-only
        # collection. A late result can never publish a partial snapshot.
        deny_snapshot(config, redis)
        raise
    finally:
        if collectors is not None:
            collectors.shutdown(wait=True, cancel_futures=True)


def deny_snapshot(config: dict, redis: object) -> None:
    try:
        redis.set(EPOCH_KEY, str(uuid4()))
    except Exception:
        pass
    try:
        previous = read_root_json(DIRECTORY / "current.json")
        denied = {**previous, "state": "deny"}
        denied["digest"] = digest({k: v for k, v in denied.items() if k != "digest"})
        atomic_json(DIRECTORY, "current.json", denied)
        remote("publish", denied, config["ssh_user"])
    except Exception:
        pass


def restore_inhibit() -> None:
    """A reboot cannot turn a persisted unfinished release into readiness."""
    for directory in (STATE, DIRECTORY):
        if not directory.exists():
            directory.mkdir(mode=0o755)
        secure_directory(directory)
    owner = read_inhibit(STATE)
    if owner is not None:
        write_inhibit(DIRECTORY, owner)


def capture_name(owner: dict) -> str:
    """Each immutable retirement snapshot belongs to one owned rollout cycle."""
    validate_inhibit(owner)
    return "capture-" + owner["release_sha"] + "-" + owner["owner_nonce"] + ".json"


def clear_owned_inhibit(owner: dict) -> None:
    """Caller holds observer flock; never recreate a cleared durable owner."""
    validate_inhibit(owner)
    saved = read_root_json(STATE / capture_name(owner))
    need(saved["owner"] == owner)
    persistent, runtime = read_inhibit(STATE), read_inhibit(DIRECTORY)
    need(persistent is None or persistent == owner)
    need(runtime is None or runtime == owner)
    if persistent is not None:
        remove_inhibit(STATE, owner)
    if runtime is not None:
        remove_inhibit(DIRECTORY, owner)


def capture_owned_release(owner: dict, host: str) -> None:
    """Retries retain the original retirement evidence, not a partial rollout."""
    validate_inhibit(owner)
    for directory in (STATE, DIRECTORY):
        current = read_inhibit(directory)
        need(current is None or current == owner)
    destination = STATE / capture_name(owner)
    if destination.exists() or destination.is_symlink():
        saved = read_root_json(destination)
        need(set(saved) == {"owner", "inventory"} and saved["owner"] == owner)
    else:
        save_state(destination.name, {"owner": owner, "inventory": supervisor_inventory(host)})
    write_inhibit(STATE, owner)
    write_inhibit(DIRECTORY, owner)


def capture_context(owner: dict) -> dict:
    validate_inhibit(owner)
    need(read_inhibit(STATE) == owner and read_inhibit(DIRECTORY) == owner)
    saved = read_root_json(STATE / capture_name(owner))
    need(set(saved) == {"owner", "inventory"} and saved["owner"] == owner)
    return {"owner": owner, "capture_sha256": digest(saved)}


def verify_current(config: dict, release_sha: str) -> dict:
    local = read_root_json(DIRECTORY / "current.json")
    need(local == remote("read", {}, config["ssh_user"]))
    need(local["release_sha"] == release_sha and local["state"] == "ready")
    need(local["digest"] == digest({k: v for k, v in local.items() if k != "digest"}))
    check_live(local, redis_connection())
    return local


READ_ONLY_ACTIONS = frozenset(
    {"read", "collect", "verify-current", "verify-target", "verify-owner", "capture-context"}
)


@contextmanager
def observer_lock(*, readonly: bool):
    """Read commands never initialize or repair an incomplete installation."""
    import fcntl

    need(os.geteuid() == 0)
    if not readonly and not STATE.exists():
        STATE.mkdir(mode=0o755)
    secure_directory(STATE)
    flags = os.O_RDONLY if readonly else os.O_RDWR | os.O_CREAT
    descriptor = os.open(STATE / "observer.lock", flags | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if readonly:
            secure_directory(DIRECTORY)
            # Missing mirrors are a denial, never an invitation to repair state.
            need(read_inhibit(STATE) == read_inhibit(DIRECTORY))
        else:
            restore_inhibit()
        yield
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "install",
            "scan",
            "collect",
            "publish",
            "read",
            "capture",
            "inhibit",
            "clear",
            "verify-owner",
            "verify-current",
            "verify-target",
            "prepare-owner",
            "capture-context",
        ],
    )
    parser.add_argument("--host", choices=["core", "commerce"], default="core")
    parser.add_argument("--release", default="")
    parser.add_argument("--ssh-user", default="")
    parser.add_argument("--owner-nonce", default="")
    args = parser.parse_args()
    with observer_lock(readonly=args.action in READ_ONLY_ACTIONS):
        run_action(args)


def run_action(args: argparse.Namespace) -> None:
    if args.action == "prepare-owner":
        sha(args.release, 40)
        owner = read_inhibit(STATE)
        need(owner is None or owner["release_sha"] == args.release)
        need(read_inhibit(DIRECTORY) == owner)
        print(canonical({"owner": owner}))
        return
    if args.action == "capture-context":
        print(canonical(capture_context({"version": 1, "release_sha": args.release,
                                         "owner_nonce": args.owner_nonce})))
        return
    if args.action == "install":
        install_host(args.host, args.release, args.ssh_user)
        print('{"installed":true}')
        return
    if args.action == "capture":
        if args.release:
            sha(args.release, 40)
            uuid_string(args.owner_nonce)
            owner = {
                "version": 1,
                "release_sha": args.release,
                "owner_nonce": args.owner_nonce,
            }
            capture_owned_release(owner, args.host)
            print('{"captured":true,"inhibited":true}')
        else:
            print(canonical(supervisor_inventory(args.host)))
        return
    if args.action in {"inhibit", "clear", "verify-owner"}:
        owner = (
            {"version": 1, "release_sha": args.release, "owner_nonce": args.owner_nonce}
            if args.release
            else json.load(sys.stdin)
        )
        validate_inhibit(owner)
        if args.action == "inhibit":
            write_inhibit(STATE, owner)
            write_inhibit(DIRECTORY, owner)
        elif args.action == "clear":
            clear_owned_inhibit(owner)
        else:
            need(read_inhibit(DIRECTORY) == owner and read_inhibit(STATE) == owner)
        print('{"owner_confirmed":true}')
        return
    config = read_root_json(STATE / "config.json")
    need(config["host"] == args.host)
    if args.action == "collect":
        payload = json.load(sys.stdin)
        need(payload["release_sha"] == config["release_sha"])
        print(
            canonical(
                {
                    "observed": collect_host(
                        host=args.host,
                        release_sha=config["release_sha"],
                        registrations=payload["registrations"],
                        api_image=config["api_image"],
                    )
                }
            )
        )
    elif args.action == "publish":
        snapshot = json.load(sys.stdin)
        need(snapshot["release_sha"] == config["release_sha"])
        need(snapshot["state"] in {"ready", "deny"})
        need(
            snapshot["digest"]
            == digest({k: v for k, v in snapshot.items() if k != "digest"})
        )
        if snapshot["state"] == "ready":
            check_live(snapshot, redis_connection())
        atomic_json(DIRECTORY, "current.json", snapshot)
        print(canonical({"digest": snapshot["digest"]}))
    elif args.action == "read":
        print(canonical(read_root_json(DIRECTORY / "current.json")))
    elif args.action == "scan":
        snapshot = scan(config)
        print(canonical({"state": "ready", "digest": snapshot["digest"]}))
    elif args.action == "verify-current":
        snapshot = verify_current(config, args.release)
        print(canonical({"verified": True, "digest": snapshot["digest"]}))
    elif args.action == "verify-target":
        sha(args.release, 40)
        uuid_string(args.owner_nonce)
        owner = {
            "version": 1,
            "release_sha": args.release,
            "owner_nonce": args.owner_nonce,
        }
        need(config["release_sha"] == args.release)
        need(read_inhibit(STATE) == owner and read_inhibit(DIRECTORY) == owner)
        saved = read_root_json(STATE / capture_name(owner))
        need(saved["owner"] == owner)
        verify_retired(saved["inventory"], supervisor_inventory(args.host))
        snapshot = read_root_json(DIRECTORY / "current.json")
        need(snapshot["release_sha"] == args.release and snapshot["state"] == "ready")
        check_live(snapshot, redis_connection())
        if args.host == "core":
            need(snapshot == remote("read", {}, config["ssh_user"]))
            check_live(snapshot, redis_connection())
        print(
            canonical(
                {
                    "verified": True,
                    "digest": snapshot["digest"],
                    "epoch": snapshot["epoch"],
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never emit command/env/Redis exception bodies or private host data.
        print(
            canonical({"ready": False, "error_class": type(exc).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
