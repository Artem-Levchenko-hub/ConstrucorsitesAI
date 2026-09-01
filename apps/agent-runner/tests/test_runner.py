from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

import pytest

from omnia_agent_runner import (
    HS256JWTSigner,
    ProjectCellJWTMessagesAuth,
)
from omnia_agent_runner.runner import (
    RunnerEvent,
    RunnerIdentity,
    StaticBearerMessagesAuth,
    TrustedRunner,
)


@dataclass(frozen=True, slots=True)
class FakeLoopResult:
    done: bool
    summary: str
    files: dict[str, str]
    steps: int
    stop_reason: str


@dataclass(frozen=True, slots=True)
class FakeAction:
    name: str
    args: dict[str, Any]


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[FakeAction, RunnerIdentity]] = []

    async def execute(self, action: Any, identity: RunnerIdentity) -> dict[str, Any]:
        assert isinstance(action, FakeAction)
        self.calls.append((action, identity))
        return {"ok": True, "content": "ok"}


class SequenceControl:
    def __init__(self, values: list[bool]) -> None:
        self._values = list(values)

    async def cancel_requested(self, identity: RunnerIdentity) -> bool:
        _ = identity
        return self._values.pop(0) if self._values else False


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[RunnerEvent] = []

    async def emit(self, event: RunnerEvent) -> None:
        self.events.append(event)


def _identity() -> RunnerIdentity:
    return RunnerIdentity(
        project_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id=UUID("33333333-3333-3333-3333-333333333333"),
        workspace_id=UUID("44444444-4444-4444-4444-444444444444"),
        fencing_epoch=3,
        cancel_epoch=0,
    )


@pytest.mark.asyncio
async def test_runner_forwards_ordered_events_and_injected_gateway_headers() -> None:
    sink = RecordingSink()
    control = SequenceControl([False, False, False])
    executor = RecordingExecutor()
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> FakeLoopResult:
        captured.update(kwargs)
        await kwargs["emit"]("agent.text", {"step": 0, "text": "planning"})
        await kwargs["execute"](FakeAction(name="read_file", args={"path": "src/app.tsx"}))
        await kwargs["emit"]("agent.step", {"step": 0, "action": "read_file"})
        return FakeLoopResult(
            done=True,
            summary="ok",
            files={"src/app.tsx": "export default function App() {}"},
            steps=2,
            stop_reason="done",
        )

    runner = TrustedRunner(
        identity=_identity(),
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_auth=StaticBearerMessagesAuth("runner-token"),
        control_client=control,
        executor_client=executor,
        event_sink=sink,
        native_loop=fake_loop,
    )

    outcome = await runner.run(system="s", task="t", max_steps=5)

    assert outcome.done is True
    assert outcome.stop_reason == "done"
    assert captured["messages_url"] == "http://gateway.internal/v1/project-cell/messages"
    assert captured["messages_headers"] == {"Authorization": "Bearer runner-token"}
    assert [event.seq for event in sink.events] == [1, 2, 3]
    assert [event.event_type for event in sink.events] == [
        "agent.text",
        "agent.tool_result",
        "agent.step",
    ]
    assert sink.events[1].payload == {"action": "read_file", "ok": True, "path": "src/app.tsx"}
    assert executor.calls == [
        (
            FakeAction(name="read_file", args={"path": "src/app.tsx"}),
            _identity(),
        )
    ]
    assert outcome.tool_evidence[0].action == "read_file"


@pytest.mark.asyncio
async def test_runner_emits_cancel_event_after_successful_write() -> None:
    sink = RecordingSink()
    control = SequenceControl([False, True])
    executor = RecordingExecutor()
    write_content = "API_TOKEN=super-secret\n"

    async def fake_loop(**kwargs: Any) -> FakeLoopResult:
        await kwargs["execute"](FakeAction(name="write_file", args={"path": ".env", "content": write_content}))
        await kwargs["emit"]("agent.step", {"step": 0, "action": "write_file"})
        raise AssertionError("loop should have been cancelled before this line")

    runner = TrustedRunner(
        identity=_identity(),
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_auth=StaticBearerMessagesAuth("runner-token"),
        control_client=control,
        executor_client=executor,
        event_sink=sink,
        native_loop=fake_loop,
    )

    outcome = await runner.run(system="s", task="t")

    assert outcome.done is False
    assert outcome.stop_reason == "cancelled"
    assert outcome.events_emitted == 2
    assert outcome.steps == 1
    assert [event.event_type for event in sink.events] == ["agent.tool_result", "runner.cancelled"]
    assert sink.events[0].payload == {
        "action": "write_file",
        "bytes_written": len(write_content.encode("utf-8")),
        "changed_paths": [".env"],
        "content_sha256": hashlib.sha256(write_content.encode("utf-8")).hexdigest(),
        "ok": True,
        "path": ".env",
    }
    assert "content" not in sink.events[0].payload
    assert sink.events[1].payload == {
        "cancel_epoch": 0,
        "changed_paths": [".env"],
        "completed_tools": 1,
    }
    assert outcome.files == {}
    assert outcome.tool_evidence[0].content_sha256 == hashlib.sha256(
        write_content.encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_runner_emits_cancel_event_after_loop_returns_success() -> None:
    sink = RecordingSink()
    control = SequenceControl([False, True])
    executor = RecordingExecutor()
    write_content = "API_TOKEN=super-secret\n"

    async def fake_loop(**kwargs: Any) -> FakeLoopResult:
        await kwargs["execute"](FakeAction(name="write_file", args={"path": ".env", "content": write_content}))
        return FakeLoopResult(
            done=True,
            summary="ok",
            files={".env": write_content},
            steps=1,
            stop_reason="done",
        )

    runner = TrustedRunner(
        identity=_identity(),
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_auth=StaticBearerMessagesAuth("runner-token"),
        control_client=control,
        executor_client=executor,
        event_sink=sink,
        native_loop=fake_loop,
    )

    outcome = await runner.run(system="s", task="t")

    assert outcome.done is False
    assert outcome.stop_reason == "cancelled"
    assert outcome.events_emitted == 2
    assert outcome.steps == 1
    assert outcome.files == {}
    assert [event.event_type for event in sink.events] == ["agent.tool_result", "runner.cancelled"]
    assert sink.events[1].payload == {
        "cancel_epoch": 0,
        "changed_paths": [".env"],
        "completed_tools": 1,
    }


def test_runner_identity_validates_epochs_and_auth_provider_builds_headers() -> None:
    identity = _identity()
    auth = StaticBearerMessagesAuth("runner-token", extra_headers={"X-Trace": "abc"})

    assert auth.headers(identity) == {
        "Authorization": "Bearer runner-token",
        "X-Trace": "abc",
    }
    assert auth.auth_factory(identity) is None

    with pytest.raises(ValueError):
        RunnerIdentity(
            project_id=identity.project_id,
            run_id=identity.run_id,
            session_id=identity.session_id,
            workspace_id=identity.workspace_id,
            fencing_epoch=0,
            cancel_epoch=0,
        )

    with pytest.raises(ValueError):
        RunnerIdentity(
            project_id=identity.project_id,
            run_id=identity.run_id,
            session_id=identity.session_id,
            workspace_id=identity.workspace_id,
            fencing_epoch=1,
            cancel_epoch=-1,
        )


@pytest.mark.asyncio
async def test_project_cell_auth_provider_emits_fresh_jwt_and_uuid_per_attempt() -> None:
    issued = iter(
        [
            UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ]
    )
    auth = ProjectCellJWTMessagesAuth(
        signer=HS256JWTSigner("runner-secret"),
        issuer="omnia-agent-runner",
        audience="omnia-project-cell-runner",
        ttl_seconds=90,
        clock=lambda: 1_726_000_000,
        jti_factory=lambda: next(issued),
        extra_headers={"Authorization": "Bearer stale", "X-Trace": "trace-1"},
    )

    factory = auth.auth_factory(_identity())
    first = await factory(0)
    second = await factory(1)

    assert auth.headers(_identity()) == {"X-Trace": "trace-1"}
    assert first.message_id == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert second.message_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert first.headers["Authorization"] != second.headers["Authorization"]
    assert first.headers["Authorization"] != "Bearer stale"
    assert first.headers["X-Trace"] == "trace-1"
    assert first.project_id == str(_identity().project_id)
    assert first.run_id == str(_identity().run_id)
    assert first.session_id == str(_identity().session_id)
    assert first.workspace_id == str(_identity().workspace_id)
    assert first.fencing_epoch == _identity().fencing_epoch
    assert first.cancel_epoch == _identity().cancel_epoch


@pytest.mark.asyncio
async def test_project_cell_auth_provider_emits_fresh_jwt_between_turns() -> None:
    issued = iter(
        [
            UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ]
    )
    sink = RecordingSink()
    control = SequenceControl([False])
    executor = RecordingExecutor()
    captured: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any) -> FakeLoopResult:
        captured["first_turn_auth"] = await kwargs["messages_auth_factory"](0)
        captured["second_turn_auth"] = await kwargs["messages_auth_factory"](0)
        return FakeLoopResult(
            done=True,
            summary="ok",
            files={},
            steps=0,
            stop_reason="done",
        )

    runner = TrustedRunner(
        identity=_identity(),
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_auth=ProjectCellJWTMessagesAuth(
            signer=HS256JWTSigner("runner-secret"),
            issuer="omnia-agent-runner",
            audience="omnia-project-cell-runner",
            clock=lambda: 1_726_000_000,
            jti_factory=lambda: next(issued),
            extra_headers={"X-Trace": "trace-1"},
        ),
        control_client=control,
        executor_client=executor,
        event_sink=sink,
        native_loop=fake_loop,
    )

    outcome = await runner.run(system="s", task="t", max_steps=5)

    assert outcome.done is True
    first = captured["first_turn_auth"]
    second = captured["second_turn_auth"]
    assert first.message_id == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert second.message_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert first.headers["Authorization"] != second.headers["Authorization"]
    assert first.headers["X-Trace"] == "trace-1"
    assert second.headers["X-Trace"] == "trace-1"


@pytest.mark.asyncio
async def test_runner_prefers_per_attempt_auth_factory_over_static_bearer_headers() -> None:
    sink = RecordingSink()
    control = SequenceControl([False])
    executor = RecordingExecutor()
    captured: dict[str, Any] = {}
    jtis = iter([UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")])

    async def fake_loop(**kwargs: Any) -> FakeLoopResult:
        captured.update(kwargs)
        auth_factory = kwargs["messages_auth_factory"]
        assert auth_factory is not None
        attempt_auth = await auth_factory(0)
        captured["attempt_auth"] = attempt_auth
        return FakeLoopResult(
            done=True,
            summary="ok",
            files={},
            steps=0,
            stop_reason="done",
        )

    runner = TrustedRunner(
        identity=_identity(),
        messages_url="http://gateway.internal/v1/project-cell/messages",
        messages_auth=ProjectCellJWTMessagesAuth(
            signer=HS256JWTSigner("runner-secret"),
            issuer="omnia-agent-runner",
            audience="omnia-project-cell-runner",
            clock=lambda: 1_726_000_000,
            jti_factory=lambda: next(jtis),
            extra_headers={"Authorization": "Bearer stale", "X-Trace": "trace-1"},
        ),
        control_client=control,
        executor_client=executor,
        event_sink=sink,
        native_loop=fake_loop,
    )

    outcome = await runner.run(system="s", task="t", max_steps=5)

    assert outcome.done is True
    assert captured["messages_headers"] == {"X-Trace": "trace-1"}
    attempt_auth = captured["attempt_auth"]
    assert attempt_auth.message_id == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert attempt_auth.headers["X-Trace"] == "trace-1"
    assert attempt_auth.headers["Authorization"].startswith("Bearer ")
    assert attempt_auth.headers["Authorization"] != "Bearer stale"


def test_runner_sources_never_load_generated_code_or_shells_out() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "omnia_agent_runner"
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in source_root.glob("*.py")
    )

    for forbidden in (
        "subprocess",
        "importlib",
        "runpy",
        "os.system",
        "exec(",
        "eval(",
        "sys.path",
        "shell=",
        "docker.sock",
        "psycopg",
    ):
        assert forbidden not in combined


def test_runner_image_is_non_root() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "USER runner" in dockerfile
    assert "useradd --create-home --uid 10001 runner" in dockerfile
    assert 'CMD ["python", "-m", "omnia_agent_runner"]' in dockerfile


def test_runner_module_boots_as_resident_fail_closed_service() -> None:
    project_root = Path(__file__).resolve().parents[1]
    port = _reserve_port()
    env = {
        **os.environ,
        "OMNIA_RUNNER_HOST": "127.0.0.1",
        "OMNIA_RUNNER_PORT": str(port),
        "PYTHONPATH": str(project_root / "src"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "omnia_agent_runner"],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        health = _wait_for_json(f"http://127.0.0.1:{port}/healthz")
        assert health == {"ok": True, "ready": False, "service": "omnia-agent-runner"}
        assert proc.poll() is None

        ready_error = _request_json(
            f"http://127.0.0.1:{port}/readyz",
            expected_status=503,
        )
        assert ready_error == {"ok": False, "reason": "adapters_unconfigured"}

        reject = _request_json(
            f"http://127.0.0.1:{port}/runs",
            method="POST",
            body=b"{}",
            expected_status=503,
        )
        assert reject == {"ok": False, "reason": "adapters_unconfigured"}
        assert proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_json(url: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _request_json(url)
        except Exception as exc:  # pragma: no cover - bounded retry helper
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"service did not become healthy: {last_error}")


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    expected_status: int = 200,
) -> dict[str, Any]:
    request = Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=2) as response:
            status = response.status
            payload = response.read()
    except HTTPError as exc:
        status = exc.code
        payload = exc.read()
    assert status == expected_status
    decoded = json.loads(payload.decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded
