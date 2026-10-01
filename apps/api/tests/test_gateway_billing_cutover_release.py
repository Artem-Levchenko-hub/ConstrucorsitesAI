"""Canonical cutover gate, including real migrated PostgreSQL/pool regression tests."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def pool_program():
    tree = ast.parse(gate_code())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "program" for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


async def migrate_settlements(test_engine):
    """Apply the actual 0074 migration with the real Alembic naming convention."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    from yleum_api.models.base import Base

    migration = SCRIPT.parents[2] / "apps/api/migrations/versions/0074_usage_settlements.py"
    spec = importlib.util.spec_from_file_location("cutover_migration_0074", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def upgrade(connection):
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            module.upgrade()

    async with test_engine.begin() as connection:
        await connection.execute(text("DROP TABLE usage_settlements"))
        await connection.run_sync(upgrade)
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await connection.execute(text("CREATE TABLE alembic_version (version_num varchar(64))"))
        await connection.execute(
            text("INSERT INTO alembic_version VALUES ('0074_usage_settlements')")
        )


def run_real_pool_program(test_engine, *, role=None):
    """No SQL/pool mocks: real gateway module, PostgreSQL and a local HTTP fixture."""
    import os
    import subprocess
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dsn = test_engine.url
    if role:
        dsn = dsn.set(username=role, password=None)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DATABASE_URL": dsn.render_as_string(hide_password=False),
        "PYTHONPATH": str(SCRIPT.parents[2] / "apps/llm-gateway/src"),
    }
    program = pool_program().replace(
        "http://127.0.0.1:8001/health", f"http://127.0.0.1:{server.server_port}/health"
    )
    try:
        return subprocess.run(
            [sys.executable, "-"],
            input=program,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_real_0074_migration_passes_actual_gateway_pool_gate(test_engine):
    await migrate_settlements(test_engine)
    result = run_real_pool_program(test_engine)
    assert result.returncode == 0, result.stderr


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_marker",
        "missing_table",
        "missing_column",
        "missing_unique",
        "wrong_unique_scope",
        "extra_unique_column",
        "weak_status",
        "extra_status",
        "weak_charge",
        "unvalidated_status",
        "unvalidated_charge",
        "permission",
    ],
)
async def test_real_gateway_pool_rejects_incomplete_or_weakened_schema(test_engine, mutation):
    from sqlalchemy import text

    await migrate_settlements(test_engine)
    status_name = "ck_usage_settlements_ck_usage_settlements_status"
    charge_name = "ck_usage_settlements_ck_usage_settlements_charge"
    statements = {
        "missing_marker": ["DELETE FROM alembic_version"],
        "missing_table": ["DROP TABLE usage_settlements"],
        "missing_column": ["ALTER TABLE usage_settlements DROP COLUMN receipt_hash"],
        "missing_unique": [
            "ALTER TABLE usage_settlements DROP CONSTRAINT uq_usage_settlements_provider_receipt"
        ],
        "wrong_unique_scope": [
            "ALTER TABLE usage_settlements DROP CONSTRAINT uq_usage_settlements_provider_receipt",
            "ALTER TABLE usage_settlements ADD CONSTRAINT uq_usage_settlements_provider_receipt "
            "UNIQUE (user_id, provider_request_id)",
        ],
        "extra_unique_column": [
            "ALTER TABLE usage_settlements DROP CONSTRAINT uq_usage_settlements_provider_receipt",
            "ALTER TABLE usage_settlements ADD CONSTRAINT uq_usage_settlements_provider_receipt "
            "UNIQUE (user_id, provider_scope, provider_request_id, receipt_hash)",
        ],
        "weak_status": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {status_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {status_name} "
            "CHECK (status IN ('settled','free','unpaid') OR true)",
        ],
        "extra_status": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {status_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {status_name} "
            "CHECK (status IN ('settled','free','unpaid','pending'))",
        ],
        "weak_charge": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {charge_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {charge_name} "
            "CHECK ((status='settled') = (wallet_charge_id IS NOT NULL) OR true)",
        ],
        "unvalidated_status": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {status_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {status_name} "
            "CHECK (status IN ('settled','free','unpaid')) NOT VALID",
        ],
        "unvalidated_charge": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {charge_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {charge_name} "
            "CHECK ((status='settled') = (wallet_charge_id IS NOT NULL)) NOT VALID",
        ],
        "permission": [
            "DROP ROLE IF EXISTS qa_cutover_reader",
            "CREATE ROLE qa_cutover_reader LOGIN",
            "GRANT SELECT ON alembic_version TO qa_cutover_reader",
        ],
    }
    async with test_engine.begin() as connection:
        for statement in statements[mutation]:
            await connection.execute(text(statement))
    try:
        result = run_real_pool_program(
            test_engine, role="qa_cutover_reader" if mutation == "permission" else None
        )
        assert result.returncode != 0
        assert result.stderr.strip() == (
            "gateway settlement schema unavailable; admission remains fenced"
        )
    finally:
        if mutation == "permission":
            async with test_engine.begin() as connection:
                await connection.execute(text("DROP OWNED BY qa_cutover_reader"))
                await connection.execute(text("DROP ROLE qa_cutover_reader"))


@pytest.mark.asyncio
async def test_real_schema_accepts_renamed_constraints_and_later_head(test_engine):
    from sqlalchemy import text

    await migrate_settlements(test_engine)
    async with test_engine.begin() as connection:
        await connection.execute(text("UPDATE alembic_version SET version_num='later_revision'"))
        await connection.execute(
            text(
                "ALTER TABLE usage_settlements RENAME CONSTRAINT "
                "ck_usage_settlements_ck_usage_settlements_status TO future_status_name"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE usage_settlements RENAME CONSTRAINT "
                "ck_usage_settlements_ck_usage_settlements_charge TO future_charge_name"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE usage_settlements RENAME CONSTRAINT "
                "uq_usage_settlements_provider_receipt TO future_receipt_name"
            )
        )
    result = run_real_pool_program(test_engine)
    assert result.returncode == 0, result.stderr


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
