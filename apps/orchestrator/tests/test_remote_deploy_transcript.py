"""Golden SSH transcript of the BYO deployment service.

Everything ``remote_deploy`` does to a customer's server is a sequence of SSH
commands, so the transcript — connection arguments, every command with its stdin
and timeout, the outcome, whether the session was closed — pins the part of its
behaviour that the refactoring touched: connecting and routing. Image transfer,
health polling and the database path are stubbed here and covered elsewhere.
The golden was written from the code BEFORE its repeated blocks got single owners
and must hold unchanged AFTER.

Regenerate deliberately by running this file with ``OMNIA_WRITE_GOLDEN=1``; that
run rewrites the fixture and then skips, so it can never pass vacuously.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from omnia_orchestrator.core.shell import CmdResult
from omnia_orchestrator.services import remote_deploy

GOLDEN = Path(__file__).parent / "fixtures" / "remote_deploy_transcript.json"
PROJECT = "11111111-2222-3333-4444-555555555555"
RUN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PREVIOUS_ROUTE = base64.b64encode(b"old.example {\n\treverse_proxy 127.0.0.1:1\n}\n").decode()
CREDS: dict[str, object] = {
    "host": "vps.example",
    "resolved_ip": "203.0.113.9",
    "port": "2222",
    "user": "deploy",
    "auth_type": "key",
    "secret": "PRIVATE-KEY",
    "known_host_key": "203.0.113.9 ssh-ed25519 AAAA",
}


class Session:
    def __init__(
        self, *, previous: str, fail: str | None, container: bool, restore_fails: bool
    ) -> None:
        self.previous = previous
        self.fail = fail
        self.container = container
        self.restore_fails = restore_fails
        self.reloads = 0
        self.log: list[dict[str, Any]] = []

    async def run(
        self,
        command: str,
        *,
        timeout: float = 30,  # noqa: ASYNC109 - mirrors production facade
        input_data: Any = None,
    ) -> CmdResult:
        self.log.append({"run": command, "timeout": timeout, "input": input_data})
        if command.startswith("base64 -w0 "):
            return CmdResult(0, self.previous, "")
        if self.restore_fails and input_data == PREVIOUS_ROUTE:
            return CmdResult(1, "", "disk full")
        if "caddy validate" in command:
            return CmdResult(1 if self.fail == "validate" else 0, "", "bad route")
        if "caddy reload" in command and "|| true" not in command:
            self.reloads += 1
            failed = self.fail == "reload" and self.reloads == 1
            return CmdResult(1, "", "reload refused") if failed else CmdResult(0, "", "")
        if "image inspect" in command:
            return CmdResult(0, "amd64\n", "")
        if command == "uname -m":
            return CmdResult(0, "x86_64\n", "")
        if "docker run -d --name omnia-app-" in command:
            return CmdResult(0, "candidate-id\n", "")
        if "docker port" in command:
            return CmdResult(0, "34568\n", "")
        if "docker inspect" in command and "omnia-edge" in command:
            return CmdResult(0, "true\n", "")
        if "docker ps -aq" in command:
            return CmdResult(0, "old-id\ncandidate-id\n", "")
        if command.startswith("docker ps -q "):
            return CmdResult(0, "app-id\n" if self.container else "", "")
        if "docker logs" in command:
            return CmdResult(0, "line 1\nline 2\n", "warn\n")
        return CmdResult(0, "", "")

    async def close(self) -> None:
        self.log.append({"close": True})


async def _scenario(monkeypatch: pytest.MonkeyPatch, name: str) -> dict[str, Any]:
    kind, _, variant = name.partition(":")
    fail = next((mode for mode in ("validate", "reload") if mode in variant), None)
    previous = ""
    if "blank-previous" in variant:
        previous = "\n  \n"
    elif "broken-previous" in variant:
        previous = "abc\n"
    elif "previous" in variant:
        previous = PREVIOUS_ROUTE + "\n"
    session = Session(
        previous=previous,
        fail=fail,
        container="no-container" not in variant,
        restore_fails="restore-fails" in variant,
    )
    connections: list[dict[str, Any]] = []

    async def connect(**kwargs: Any) -> Session:
        connections.append(kwargs)
        if "connect-fails" in variant:
            raise OSError("no route to host")
        return session

    async def save_load(_tag: str, _session: Any, _progress: Any = None) -> tuple[bool, str]:
        return True, "loaded"

    async def healthy(_session: Any, _port: int) -> bool:
        return True

    monkeypatch.setattr(remote_deploy.ssh, "connect", connect)
    monkeypatch.setattr(remote_deploy, "_save_load", save_load)
    monkeypatch.setattr(remote_deploy, "_health", healthy)

    outcome: Any
    try:
        if kind == "deploy":
            outcome = await remote_deploy.deploy_to_target(
                creds=CREDS,
                image_tag="omnia-app-shop:1",
                project_id=PROJECT,
                run_id=RUN,
                slug="shop",
                host_port=34567,
                env={"AUTH_SECRET": "APP-SUPER-SECRET"},
                needs_database=False,
                domains=["shop.example"] if "domain" in variant else None,
            )
        elif kind == "sync":
            outcome = await remote_deploy.sync_routes(
                creds=CREDS, project_id=PROJECT, domains=["shop.example"], host_port=34567
            )
        elif kind == "teardown":
            outcome = await remote_deploy.teardown_target(creds=CREDS, project_id=PROJECT)
        else:
            outcome = await remote_deploy.target_logs(creds=CREDS, project_id=PROJECT, tail=5000)
    except Exception as exc:
        outcome = {"raised": type(exc).__name__, "message": str(exc)}
    return {"connect": connections, "outcome": outcome, "transcript": session.log}


SCENARIOS = [
    "deploy:new",
    "deploy:domain-previous",
    "deploy:validate",
    "deploy:validate-previous",
    "deploy:reload",
    "deploy:reload-previous",
    "sync:new",
    "sync:previous",
    "sync:no-container",
    "sync:validate",
    "sync:validate-previous",
    "sync:reload",
    "sync:reload-previous",
    "teardown:",
    "logs:",
    # what the single owners must not change: where a connection error surfaces,
    # and what a failing or unreadable "put the previous route back" does
    "deploy:connect-fails",
    "sync:connect-fails",
    "teardown:connect-fails",
    "logs:connect-fails",
    "deploy:validate-previous-restore-fails",
    "sync:validate-previous-restore-fails",
    "sync:validate-blank-previous",
    "sync:validate-broken-previous",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SCENARIOS)
async def test_transcript_matches_the_golden(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    actual = json.loads(json.dumps(await _scenario(monkeypatch, name), ensure_ascii=False))
    if os.environ.get("OMNIA_WRITE_GOLDEN") == "1":
        golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        golden[name] = actual
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(
            json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip("golden rewritten — run again without OMNIA_WRITE_GOLDEN to compare")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))[name]
    assert actual == expected


def test_golden_covers_every_scenario_and_the_failure_branches() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(SCENARIOS)
    restored = [
        run["input"] for run in golden["sync:validate-previous"]["transcript"] if "input" in run
    ]
    assert PREVIOUS_ROUTE in restored, "a rejected route must put the previous one back"
    removed = [run.get("run", "") for run in golden["deploy:validate"]["transcript"]]
    assert any(command.startswith("rm -f ") for command in removed)
    assert golden["deploy:reload"]["outcome"]["ok"] is False
    assert golden["sync:reload"]["outcome"]["raised"] == "RuntimeError"
    # a deployment reports a connection error, the three tools let it propagate;
    # nobody closes a session that was never opened
    assert golden["deploy:connect-fails"]["outcome"]["ok"] is False
    for tool in ("sync", "teardown", "logs"):
        assert golden[f"{tool}:connect-fails"]["outcome"]["raised"] == "OSError"
    for case in (c for name, c in golden.items() if name.endswith("connect-fails")):
        assert case["transcript"] == []
    assert "disk full" in golden["sync:validate-previous-restore-fails"]["outcome"]["message"]
    blank = [run.get("run", "") for run in golden["sync:validate-blank-previous"]["transcript"]]
    assert any(command.startswith("rm -f ") for command in blank)
    assert golden["sync:validate-broken-previous"]["outcome"]["raised"] == "Error"
    commands = [
        run["run"] for case in golden.values() for run in case["transcript"] if "run" in run
    ]
    assert not [c for c in commands if "PRIVATE-KEY" in c or "APP-SUPER-SECRET" in c], (
        "credentials travel in the connection and in stdin, never in a command line"
    )
