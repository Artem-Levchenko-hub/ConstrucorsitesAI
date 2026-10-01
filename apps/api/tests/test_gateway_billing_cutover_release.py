"""Canonical cutover gate: no Docker, SSH, DB or providers are used by tests."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "infra/release/deploy-prod.sh"
OLD, NEW = "a" * 64, "b" * 64
OLD_IMAGE, NEW_IMAGE = "sha256:" + "c" * 64, "sha256:" + "d" * 64


def gate_code():
    script = SCRIPT.read_text()
    delimiter = "<<'GATEWAY_CUTOVER_PY'\n"
    assert delimiter in script, "Canonical release must contain an executable cutover gate"
    return script.split(delimiter, 1)[1].split("\nGATEWAY_CUTOVER_PY", 1)[0]


def metadata(container=NEW, image=NEW_IMAGE, running=True):
    return dict(
        id=container,
        image=image,
        running=running,
        service="gateway",
        configured_image="omnia-gateway:prod",
        name="/yleum-prod-gw",
    )


def execute_gate(
    monkeypatch,
    *,
    running=None,
    image=NEW_IMAGE,
    db_result=0,
    degraded=False,
    target_running=True,
    ancestor=None,
    stderr_degraded=False,
):
    import subprocess

    target = metadata(image=image, running=target_running)
    running = [target] if running is None else running
    inspected = {entry["id"]: entry for entry in running}
    inspected[NEW], inspected[OLD] = target, metadata(OLD, OLD_IMAGE, False)
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        if command[:3] == ["docker", "image", "inspect"]:
            output = NEW_IMAGE
        elif command[:2] == ["docker", "inspect"]:
            key = command[2]
            output = json.dumps(target if key == "yleum-prod-gw" else inspected[key])
        elif command[:2] == ["docker", "ps"]:
            output = (
                "\n".join(ancestor or [])
                if "--filter" in command
                else "\n".join(e["id"] for e in running)
            )
        elif command[:2] == ["docker", "logs"]:
            output = "startup.postgres_unavailable" if degraded else ""
        elif command[:2] == ["docker", "exec"]:
            return SimpleNamespace(returncode=db_result, stdout="", stderr="PRIVATE_DB_SECRET")
        else:
            raise AssertionError(command)
        return SimpleNamespace(
            returncode=0,
            stdout=output,
            stderr=(
                "startup.postgres_unavailable"
                if stderr_degraded and command[:2] == ["docker", "logs"]
                else ""
            ),
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("sys.argv", ["-", OLD, OLD_IMAGE])
    exec(compile(gate_code(), str(SCRIPT), "exec"), {})
    return commands


def test_gate_runs_after_replacement_before_both_admission_reopens():
    script = SCRIPT.read_text()
    assert "gateway_capture_identity" in script
    assert "--force-recreate gateway" in script
    assert script.index("gateway_capture_identity\n") < script.index("build gateway")
    gate = script.rindex("gateway_cutover_gate\n")
    assert script.index("build gateway") < gate < script.index("  ingress_drain end")
    assert gate < script.index("generation_deployment_drain end $SHA")


def test_green_gate_checks_actual_container_pool_without_secret_output(monkeypatch, capsys):
    commands = execute_gate(monkeypatch)
    execs = [(cmd, opts) for cmd, opts in commands if cmd[:2] == ["docker", "exec"]]
    assert len(execs) == 1 and NEW in execs[0][0]
    program = execs[0][1]["input"]
    ast.parse(program)
    assert "init_pool" in program and "get_pool" in program and "readonly=True" in program
    assert "usage_settlements" in program and "alembic_version" in program
    assert "0074_usage_settlements" not in program
    assert "PRIVATE_DB_SECRET" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "case",
    [
        "old_writer",
        "wrong_image",
        "db_schema",
        "degraded",
        "stopped",
        "ancestor",
        "stderr_degraded",
    ],
)
def test_failed_cutover_refuses_reopen(monkeypatch, capsys, case):
    options = {}
    if case == "old_writer":
        options["running"] = [metadata(), metadata(OLD, OLD_IMAGE)]
    elif case == "wrong_image":
        options["image"] = OLD_IMAGE
    elif case == "db_schema":
        options["db_result"] = 1
    elif case == "degraded":
        options["degraded"] = True
    elif case == "stopped":
        options["target_running"] = False
    elif case == "ancestor":
        options["ancestor"] = ["e" * 64]
    elif case == "stderr_degraded":
        options["stderr_degraded"] = True
    with pytest.raises((RuntimeError, SystemExit)):
        execute_gate(monkeypatch, **options)
    assert "PRIVATE_DB_SECRET" not in capsys.readouterr().out


def test_gateway_build_failure_is_not_hidden_by_log_filter():
    build = next(line for line in SCRIPT.read_text().splitlines() if "build gateway 2>&1" in line)
    assert "set -euo pipefail;" in build
    assert "{ grep -E 'Built|ERROR|error' || true; }" in build


@pytest.mark.parametrize("missing", [None, "migration", "table", "columns", "constraints"])
def test_actual_gateway_pool_program_is_readonly_and_fail_closed(monkeypatch, missing):
    import io
    import sys
    import types
    import urllib.request

    commands = execute_gate(monkeypatch)
    program = next(opts["input"] for cmd, opts in commands if cmd[:2] == ["docker", "exec"])
    calls = []

    class Context:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return False

    class Connection:
        def transaction(self, *, readonly):
            assert readonly is True
            calls.append("readonly")
            return Context()

        async def fetchval(self, query):
            if "alembic_version" in query:
                return missing != "migration"
            if "to_regclass" in query:
                return missing != "table"
            if "pg_constraint" in query:
                return 2 if missing == "constraints" else 3
            raise AssertionError(query)

        async def fetch(self, query):
            assert "usage_settlements LIMIT 0" in query
            if missing == "columns":
                raise RuntimeError("PRIVATE_DB_SECRET")
            return []

    connection = Connection()
    pool = SimpleNamespace(acquire=lambda: Context())

    async def init_pool():
        calls.append("init_pool")
        return pool

    async def close_pool():
        calls.append("close_pool")

    module = types.ModuleType("yleum_gateway.core.db")
    module.init_pool, module.get_pool, module.close_pool = init_pool, lambda: pool, close_pool
    for name in ("yleum_gateway", "yleum_gateway.core"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "yleum_gateway.core.db", module)

    def health(url, timeout):
        assert url == "http://127.0.0.1:8001/health" and timeout == 10
        response = io.StringIO('{"status":"ok"}')
        response.status = 200
        return response

    monkeypatch.setattr(urllib.request, "urlopen", health)
    if missing:
        with pytest.raises(SystemExit, match="gateway settlement schema unavailable"):
            exec(compile(program, "<gateway-pool-probe>", "exec"), {})
    else:
        exec(compile(program, "<gateway-pool-probe>", "exec"), {})
    assert calls[:2] == ["init_pool", "readonly"]
    assert calls[-1] == "close_pool"


def test_failed_real_shell_build_pipeline_never_reaches_gateway_replacement(tmp_path):
    import shlex
    import subprocess

    line = next(line for line in SCRIPT.read_text().splitlines() if "build gateway 2>&1" in line)
    capture = tmp_path / "remote-command"
    capture_ssh = 'ssh() { printf "%s" "$2" > ' + shlex.quote(str(capture)) + "; }; "
    captured = subprocess.run(["bash", "-c", capture_ssh + line], capture_output=True, text=True)
    assert captured.returncode == 0
    remote = capture.read_text()
    remote = remote.replace("/opt/omnia/apps/llm-gateway/deploy/full", str(tmp_path))
    marker = tmp_path / "replacement-attempted"
    fake_docker = (
        'docker() { if [[ "$*" == *"build gateway"* ]]; then return 23; '
        f"else touch {shlex.quote(str(marker))}; return 0; fi; }}; "
    )
    result = subprocess.run(["bash", "-c", fake_docker + remote], capture_output=True, text=True)
    assert result.returncode == 23
    assert not marker.exists()
