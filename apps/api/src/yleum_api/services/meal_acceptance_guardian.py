"""Reap the private Meal subprocess and all Chromium descendants."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

from yleum_api.services import max_behavior_browser as browser_tools
from yleum_api.services.generation.notes_acceptance import need


class OwnedObservationProcess(browser_tools._OwnedProcess):
    module = "yleum_api.services.meal_acceptance_worker"

    def __init__(self) -> None:
        # Keep the old named worker and its pinned bytes untouched. This entrypoint
        # reuses its guardian/reaping implementation.
        environment = dict(os.environ)
        environment.update(
            DEBUG="",
            PWDEBUG="0",
            PYTHONINSPECT="",
            PYTHONSTARTUP="",
            PYTHONPATH=browser_tools._API_SOURCE_ROOT,
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", self.module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
            env=environment,
        )
        try:
            self.stdin = browser_tools._PrivatePipe(self.process.stdin)
            self.stdout = browser_tools._PrivatePipe(self.process.stdout)
            self.exited = browser_tools._PrivatePipe(
                os.fdopen(os.pidfd_open(self.process.pid), "rb", buffering=0)
            )
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=1)
            raise
        self.pid = self.process.pid


async def run_owned_observation(
    payload: dict[str, Any], *, process_type: type[OwnedObservationProcess], result_seconds: float,
) -> dict[str, Any]:
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    need(len(raw) <= 131072, "meal private input limit")
    process, group = process_type(), 0

    async def close() -> bool:
        process.stdin.close()
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            for owned in (group, process.pid):
                if owned:
                    try:
                        os.killpg(owned, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            async with asyncio.timeout(1):
                await process.wait()
        async with asyncio.timeout(1):
            tail = await process.stdout.read(131073)
        messages = [json.loads(line) for line in tail.splitlines()]
        return bool(
            process.returncode == 0
            and len(tail) <= 131072
            and messages
            and messages[-1].get("cleanup_closed") is True
        )

    closed = False
    try:
        async with asyncio.timeout(5):
            hello = json.loads(await process.stdout.readline())
            need(hello == {"guardian": process.pid, "ready": True}, "meal guardian unavailable")
            await process.stdin.write(raw)
            process.stdin.close()
            hello = json.loads(await process.stdout.readline())
            need(
                set(hello) == {"guardian", "owned_group"}
                and hello["guardian"] == process.pid
                and type(hello["owned_group"]) is int
                and hello["owned_group"] > 1,
                "meal guardian unavailable",
            )
            group = hello["owned_group"]
        async with asyncio.timeout(result_seconds):
            result = json.loads(await process.stdout.readline())
            await process.wait()
        closed = result.get("cleanup_closed") is True and process.returncode == 0
        need(closed, "browser observation unconfirmed")
        assert isinstance(result, dict)
        return result
    finally:
        if not closed:
            cleanup = asyncio.create_task(close())
            deadline = time.monotonic() + 6
            while not cleanup.done() and time.monotonic() < deadline:
                try:
                    await asyncio.wait({cleanup}, timeout=max(0, deadline - time.monotonic()))
                except asyncio.CancelledError:
                    continue
            if not cleanup.done():
                cleanup.cancel()
            else:
                try:
                    closed = cleanup.result()
                except Exception:
                    pass
        process.close_pipes()
        need(closed, "meal browser cleanup unconfirmed")
