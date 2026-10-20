#!/usr/bin/env python3
"""Run release checks in the live API's filesystem/network, outside its cgroup.

The host launcher imports no application code. It pins the serving incarnation,
joins its mount, network and private PID namespaces and uses its Python and env.
The serving cgroup is never joined. Reader inventory remains a
live read-only mount, not a copied snapshot or an exemption for hidden readers.
"""

from __future__ import annotations

import base64
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

API = "yleum-prod-api"
MAX_ARGUMENTS = 256 * 1024


def require(condition: object) -> None:
    if not condition:
        raise ValueError("release maintenance runtime is unconfirmed")


def decode_arguments(encoded: str) -> tuple[str | None, list[str]]:
    require(len(encoded) <= MAX_ARGUMENTS)
    data = json.loads(base64.b64decode(encoded, validate=True))
    require(type(data) is list and 2 <= len(data) <= 64)
    require(all(type(value) is str and "\0" not in value for value in data))
    if data == ["", "--describe-runtime"]:
        return None, []
    revision, *arguments = data
    require(re.fullmatch(r"[0-9a-f]{40}", revision))
    require(arguments[0] in {"-", "-c", "-m"})
    return revision, arguments


@dataclass(frozen=True)
class Runtime:
    container_id: str
    image_id: str
    pid: int
    start_ticks: int
    mount_inode: int
    network_inode: int
    pid_inode: int
    root_identity: tuple[int, int]
    environment: dict[str, str]


def process_start(pid: int) -> int:
    # comm can contain spaces and ')'; the remaining fields start at state (3).
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def cgroups(pid: int | str) -> tuple[str, ...]:
    lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    require(lines)
    paths = tuple(line.split(":", 2)[2] for line in lines)
    require(all(path.startswith("/") for path in paths))
    return paths


def outside_supervisor(groups: tuple[str, ...], supervisor: tuple[str, ...]) -> bool:
    return not any(
        current == parent or current.startswith(parent.rstrip("/") + "/")
        for current in groups for parent in supervisor
    )


def inspect_runtime(revision: str | None) -> Runtime:
    result = subprocess.run(
        ["docker", "inspect", API], capture_output=True, text=True, timeout=15,
    )
    require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024)
    rows = json.loads(result.stdout)
    require(type(rows) is list and len(rows) == 1)
    info = rows[0]
    state, config = info["State"], info["Config"]
    require(info["Name"] == "/" + API)
    require(re.fullmatch(r"[0-9a-f]{64}", info["Id"]))
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", info["Image"]))
    require(state["Running"] is True and state["Paused"] is False and state["Dead"] is False)
    require(type(state["Pid"]) is int and state["Pid"] > 1)
    require((config.get("Labels") or {}).get("com.docker.compose.service") == "api")
    environment = {}
    for value in config["Env"]:
        key, item = value.split("=", 1)
        require(key not in environment)
        environment[key] = item
    actual_revision = environment.get("OMNIA_RELEASE_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", actual_revision))
    require(revision is None or actual_revision == revision)
    pid = state["Pid"]
    start = process_start(pid)
    actual = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    require([value for value in actual if value.startswith(b"OMNIA_RELEASE_SHA=")]
            == [b"OMNIA_RELEASE_SHA=" + actual_revision.encode()])
    namespace_pid = [line.split()[1:] for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                     if line.startswith("NSpid:")]
    require(len(namespace_pid) == 1 and namespace_pid[0][-1] == "1")
    pid_inode = Path(f"/proc/{pid}/ns/pid").stat().st_ino
    require(pid_inode != Path("/proc/self/ns/pid").stat().st_ino)
    supervisor = cgroups(pid)
    require(any(info["Id"] in group for group in supervisor))
    require(outside_supervisor(cgroups("self"), supervisor))
    root = Path(f"/proc/{pid}/root").stat()
    return Runtime(info["Id"], info["Image"], pid, start,
                   Path(f"/proc/{pid}/ns/mnt").stat().st_ino,
                   Path(f"/proc/{pid}/ns/net").stat().st_ino,
                   pid_inode, (root.st_dev, root.st_ino), environment)


def alive(pidfd: int) -> None:
    poll = select.poll()
    poll.register(pidfd, select.POLLIN)
    require(not poll.poll(0))


def open_pinned(stack: ExitStack, path: str, flags: int = os.O_RDONLY) -> int:
    fd = os.open(path, flags | os.O_CLOEXEC)
    stack.callback(os.close, fd)
    return fd


@contextmanager
def pinned_runtime(revision: str | None) -> Iterator[tuple[Runtime, tuple[int, ...]]]:
    require(os.geteuid() == 0 and sys.platform == "linux")
    runtime = inspect_runtime(revision)
    with ExitStack() as stack:
        pidfd = os.pidfd_open(runtime.pid)
        stack.callback(os.close, pidfd)
        mount = open_pinned(stack, f"/proc/{runtime.pid}/ns/mnt")
        network = open_pinned(stack, f"/proc/{runtime.pid}/ns/net")
        pid = open_pinned(stack, f"/proc/{runtime.pid}/ns/pid")
        root = open_pinned(stack, f"/proc/{runtime.pid}/root", os.O_PATH | os.O_DIRECTORY)
        require(os.fstat(mount).st_ino == runtime.mount_inode)
        require(os.fstat(network).st_ino == runtime.network_inode)
        require(os.fstat(pid).st_ino == runtime.pid_inode)
        root_info = os.fstat(root)
        require((root_info.st_dev, root_info.st_ino) == runtime.root_identity)
        alive(pidfd)
        require(inspect_runtime(revision) == runtime)
        yield runtime, (mount, network, pid, root)
        alive(pidfd)
        require(inspect_runtime(revision) == runtime)


def namespace_command(descriptors: tuple[int, ...], arguments: list[str]) -> list[str]:
    mount, network, pid, root = descriptors
    # Default nsenter fork is essential: the command belongs to the pinned API
    # PID namespace and is kernel-killed when its validated PID1 exits. Cgroup
    # membership stays with our host launcher, never with a serving reader.
    return ["/usr/bin/nsenter", f"--mount=/proc/self/fd/{mount}",
            f"--net=/proc/self/fd/{network}", f"--pid=/proc/self/fd/{pid}",
            f"--root=/proc/self/fd/{root}", f"--wd=/proc/self/fd/{root}/app",
            "--", "/app/.venv/bin/python", *arguments]


def stop_command(child: subprocess.Popen) -> None:
    """Stop only the new session created for this command, including its fork."""
    # Do not reap the leader before signalling its entire group: a fork may
    # ignore TERM even after nsenter exits. These short maintenance commands
    # have no graceful shutdown protocol; closing DB connections rolls back an
    # uncommitted transaction, while an unknown committed outcome stays unknown.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def execute_command(command: list[str], environment: dict[str, str], descriptors: tuple[int, ...],
                    *, timeout: float = 180) -> int:
    with subprocess.Popen(command, env=environment, pass_fds=descriptors,
                          start_new_session=True) as child:
        try:
            return child.wait(timeout=timeout)
        except BaseException:
            stop_command(child)
            raise


def run(revision: str, arguments: list[str]) -> int:
    metadata = Path("/usr/bin/nsenter").stat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
            and metadata.st_mode & 0o022 == 0)
    with pinned_runtime(revision) as (runtime, descriptors):
        return execute_command(namespace_command(descriptors, arguments), runtime.environment, descriptors)


def main() -> int:
    require(len(sys.argv) == 2)
    revision, arguments = decode_arguments(sys.argv[1])
    if revision is None:
        # Runtime discovery is independent of HTTP readiness, so an interrupted
        # release can still repair a stale/mixed registry under its saved fence.
        with pinned_runtime(None) as (runtime, _):
            print(runtime.environment["OMNIA_RELEASE_SHA"])
        return 0
    return run(revision, arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Docker/Python diagnostics may contain live environment or DB secrets.
        print(json.dumps({"maintenance_failed": True, "error_class": type(exc).__name__}),
              file=sys.stderr)
        raise SystemExit(75) from None
