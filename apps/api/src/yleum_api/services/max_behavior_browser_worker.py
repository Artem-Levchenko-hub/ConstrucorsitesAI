"""Private browser guardian. Input is private IPC; stdout is bounded safe evidence.

The guardian is a Linux subreaper and owns its executor's new process group.
Pending generated JS cannot prevent deadline/cancellation termination and reaping.
This module is not a public tool or an application-provided executable.
"""

from __future__ import annotations

import ctypes
import json
import os
import select
import signal
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

LIMIT = 131072


def _input(payload: dict[str, Any]) -> tuple[Any, Any, Any]:
    from yleum_api.services.max_behavior_browser import (
        CoffeeSummaryAdapter,
        DensityAdapter,
        DensityView,
        PlatformBrowserAdapter,
        ThemeAdapter,
    )
    from yleum_api.services.max_behavior_proof import (
        BehaviorDriverInput,
        CandidateBehaviorBinding,
        CompiledAssetWitness,
        ObservedAsset,
        PrivatePreviewCapability,
        contract_from_json,
    )

    binding = dict(payload["binding"])
    for key in ("candidate_id", "project_id", "workspace_id", "generation_run_id"):
        binding[key] = UUID(binding[key])
    bound = CandidateBehaviorBinding(**binding)
    # Authorization was validated by the parent before dispatch. The credential
    # goes only to _authenticate; this measurement request exports no capability.
    request = BehaviorDriverInput(
        bound, contract_from_json(payload["contract"]), PrivatePreviewCapability(None)
    )
    raw = payload["adapter"]
    density = raw["density"]
    if density:
        density = dict(density)
        density["list_view"] = DensityView(**density["list_view"])
        density["board_view"] = DensityView(**density["board_view"])
    adapter = PlatformBrowserAdapter(
        density=DensityAdapter(**density) if density else None,
        theme=ThemeAdapter(**raw["theme"]) if raw["theme"] else None,
        coffee_summary=CoffeeSummaryAdapter(**raw["coffee_summary"])
        if raw["coffee_summary"]
        else None,
        read_paths=tuple(raw["read_paths"]),
    )
    witness = CompiledAssetWitness(
        bound,
        tuple(ObservedAsset(**item) for item in payload["assets"]),
        payload["compilation_receipt_sha256"],
        "served_candidate_compilation_v1",
        tuple(ObservedAsset(**item) for item in payload["platform_assets"]),
    )
    return request, witness, adapter


def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    from yleum_api.services.max_behavior_browser import _execute_browser
    from yleum_api.services.max_behavior_proof import BehaviorProofError, CompiledAssetWitness

    try:
        request, witness, adapter = _input(payload)
        result = _execute_browser(
            request,
            witness,
            payload["observe"],
            origin=payload["origin"],
            bootstrap=payload["bootstrap"],
            adapter=adapter,
            executable_path=payload["executable_path"],
            launch_args=tuple(payload["launch_args"]),
        )
        return {
            "status": "PASS",
            "measurements": (
                None if isinstance(result, CompiledAssetWitness) else result.measurements
            ),
        }
    except BehaviorProofError as error:
        return {"status": "ERROR", "code": error.code, "proof_status": error.status}
    except BaseException:
        return {"status": "ERROR", "code": "BEHAVIOR_DRIVER_FAILED", "proof_status": "NEEDS_REVIEW"}


def _kill_group(owned: int) -> None:
    try:
        os.killpg(owned, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reap_owned(executor: int) -> bool:
    _kill_group(executor)
    # Only this guardian's own/adopted children can be targeted here. This is
    # no process-name/command matching or generic Chrome/pkill operation.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return True
        # Some Linux kernels omit task/children. Read only PID/PPID metadata;
        # target a PID only while its parent is this exact live guardian. A
        # detached Chromium helper is adopted by the subreaper and still owned.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == os.getpid():
                    os.kill(int(entry.name), signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
                pass
        time.sleep(0.01)
    return False


def main() -> None:
    stopped = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # A fresh single-threaded guardian forks before importing Playwright. Its
    # subreaper boundary adopts/reaps Node and Chromium descendants on exit.
    if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise RuntimeError("private_worker_guardian_unavailable")
    print(json.dumps({"guardian": os.getpid(), "ready": True}), flush=True)
    # No executor exists before complete private input. Independently bound
    # orphaned/partial stdin even if the parent disappears before dispatch.
    signal.alarm(5)
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise RuntimeError("private_worker_input_limit")
    payload = json.loads(raw)
    signal.alarm(0)
    read_fd, write_fd = os.pipe()
    executor = os.fork()
    if executor == 0:
        os.close(read_fd)
        os.setsid()
        # Library/browser diagnostics never reach the guardian's stdout or logs.
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.close(null)
        result = json.dumps(_execute(payload), separators=(",", ":"), allow_nan=False).encode()
        if len(result) <= LIMIT:
            position = 0
            while position < len(result):
                position += os.write(write_fd, result[position:])
        os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    print(json.dumps({"guardian": os.getpid(), "owned_group": executor}), flush=True)
    deadline = time.monotonic() + payload["timeout_seconds"]
    data = bytearray()
    outcome: dict[str, Any] = {
        "status": "ERROR",
        "code": "BEHAVIOR_BROWSER_DEADLINE",
        "proof_status": "NEEDS_REVIEW",
    }
    try:
        while not stopped and time.monotonic() < deadline:
            if select.select([read_fd], [], [], 0.02)[0]:
                chunk = os.read(read_fd, min(4096, LIMIT + 1 - len(data)))
                data.extend(chunk)
                if len(data) > LIMIT:
                    break
                if not chunk:
                    outcome = (
                        json.loads(data)
                        if data
                        else {
                            "status": "ERROR",
                            "code": "BEHAVIOR_DRIVER_FAILED",
                            "proof_status": "NEEDS_REVIEW",
                        }
                    )
                    break
    finally:
        os.close(read_fd)
        closed = _reap_owned(executor)
    if stopped:
        outcome = {
            "status": "ERROR",
            "code": "BEHAVIOR_BROWSER_CANCELLED",
            "proof_status": "NEEDS_REVIEW",
        }
    outcome["cleanup_closed"] = closed
    print(json.dumps(outcome, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # No request/URL/cookie/exception details on either output stream.
        print(
            '{"status":"ERROR","code":"BEHAVIOR_DRIVER_FAILED",'
            '"proof_status":"NEEDS_REVIEW","cleanup_closed":false}',
            flush=True,
        )
        raise SystemExit(1) from None
