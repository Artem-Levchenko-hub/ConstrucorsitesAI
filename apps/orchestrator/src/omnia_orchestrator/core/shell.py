"""Async subprocess runner with a mandatory timeout.

R-01 (deep module): callers get one `await run([...], timeout=...)` that
hides asyncio subprocess plumbing, decoding, and timeout handling.
R-10/R-32 (stability): every external command has an explicit timeout — a
hung `nginx`, `certbot`, or `systemctl` must never block the event loop
forever. On timeout the child is killed and a non-zero result is returned.

This module shells out to host tools (nginx, certbot, systemctl) that the
orchestrator user runs via passwordless sudo. It deliberately does NOT run a
shell — args are passed as a list to avoid injection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

log = structlog.get_logger("omnia_orchestrator.shell")


@dataclass(frozen=True, slots=True)
class CmdResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


async def run(
    cmd: list[str], *, timeout: float = 30.0, env: dict[str, str] | None = None
) -> CmdResult:
    """Run `cmd` (no shell), capture output, enforce `timeout` seconds.

    Returns a CmdResult even on failure/timeout (rc != 0); never raises for a
    non-zero exit — callers decide how to react. Only raises if the binary is
    missing (FileNotFoundError), which is a real misconfiguration. `env`, when
    given, replaces the child's environment (default: inherit the parent's).
    """
    log.info("shell.run", cmd=cmd, timeout=timeout)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("shell.timeout", cmd=cmd, timeout=timeout)
        return CmdResult(rc=124, stdout="", stderr=f"timed out after {timeout}s")

    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    rc = proc.returncode if proc.returncode is not None else -1
    if rc != 0:
        log.warning("shell.nonzero", cmd=cmd, rc=rc, stderr=err[-400:])
    return CmdResult(rc=rc, stdout=out, stderr=err)
