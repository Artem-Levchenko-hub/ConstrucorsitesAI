"""Canonical cutover gate, including real migrated PostgreSQL/pool regression tests."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

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
    """Apply the actual 0074/0075 migrations with the real Alembic naming convention."""
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
            next_path = migration.with_name("0075_generation_billing.py")
            next_spec = importlib.util.spec_from_file_location("cutover_migration_0075", next_path)
            next_module = importlib.util.module_from_spec(next_spec)
            next_spec.loader.exec_module(next_module)
            next_module.upgrade()

    async with test_engine.begin() as connection:
        for table in [
            "generation_billing_outbox",
            "generation_billing_intents",
            "generation_billing_policies",
            "usage_settlements",
        ]:
            await connection.execute(text("DROP TABLE " + table))
        await connection.run_sync(upgrade)
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await connection.execute(text("CREATE TABLE alembic_version (version_num varchar(64))"))
        await connection.execute(
            text("INSERT INTO alembic_version VALUES ('0075_generation_billing')")
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
    # Windows socket/asyncio DLL loading needs its OS root even in this
    # deliberately minimal environment. Never inherit provider credentials.
    for name in ("SYSTEMROOT", "WINDIR", "PYTHONPYCACHEPREFIX"):
        if name in os.environ:
            environment[name] = os.environ[name]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["generation_credit_grants", "generation_credit_reservations"])
async def test_gateway_rejects_missing_unit_billing_table(test_engine, table):
    from sqlalchemy import text

    await migrate_settlements(test_engine)
    async with test_engine.begin() as connection:
        await connection.execute(text("DROP TABLE " + table))
    result = run_real_pool_program(test_engine)
    assert result.returncode != 0, "Admission must remain closed without unit billing storage"
    assert "gateway settlement schema unavailable" in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("constraint", ["unique-grant", "validated-positive-reservation"])
async def test_gateway_rejects_missing_unit_financial_constraint(test_engine, constraint):
    from sqlalchemy import text

    await migrate_settlements(test_engine)
    async with test_engine.begin() as connection:
        if constraint == "unique-grant":
            await connection.execute(text("ALTER TABLE generation_credit_grants DROP CONSTRAINT uq_generation_credit_grants_external_ref"))
        else:
            await connection.execute(text("ALTER TABLE generation_credit_reservations DROP CONSTRAINT ck_generation_credit_reservations_positive_units"))
            await connection.execute(text("ALTER TABLE generation_credit_reservations ADD CONSTRAINT unvalidated_units CHECK (units > 0) NOT VALID"))
    result = run_real_pool_program(test_engine)
    assert result.returncode != 0, "Admission must remain closed without validated financial constraints"
    assert "gateway settlement schema unavailable" in result.stderr


SCRIPT = Path(__file__).resolve().parents[3] / "infra/release/deploy-prod.sh"
OLD, NEW = "a" * 64, "b" * 64
OLD_IMAGE, NEW_IMAGE = "sha256:" + "c" * 64, "sha256:" + "d" * 64


def gate_code():
    script = SCRIPT.read_text(encoding="utf-8")
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


@pytest.mark.parametrize("gate_exit", [0, 75], ids=["green", "failed-cutover"])
def test_gate_runs_after_replacement_before_both_admission_reopens(tmp_path, gate_exit):
    import re
    import shutil
    import subprocess

    source = SCRIPT.read_text(encoding="utf-8")
    # Definitions may precede rollout; compare the actual normal call sites.
    normal = source[source.index('say "core: ff-merge') :]
    replacement_start = normal.index("if [ $GW = 1 ]; then")
    replacement = normal[replacement_start : normal.index("\nfi", replacement_start) + 3]
    closing = normal.index("\n  finish_owned_release\n")
    assert replacement_start < normal.index("build gateway") < closing
    call_start = normal.rfind('if [ "$API" = 1 ]; then', 0, closing)
    assert call_start >= 0
    closing_call = normal[call_start : normal.index("\nfi", closing) + 3]

    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        pytest.skip("requires local Bash for the canonical gateway ending contract")

    def function(name):
        body = source.split(name + "() {", 1)[1]
        # A Python dictionary inside the gate's heredoc also closes with `}`.
        # The shell function ends before the next shell definition or main.
        ending = re.search(r"\n}\n(?=\n?(?:[a-z_]+\(\) \{|say ))", body)
        assert ending is not None, name
        return name + "() {" + body[: ending.end()]

    # Keep identity capture, the gate launcher and the shared end real. Only
    # host/process boundaries are inert; no SSH, service or fence is touched.
    program = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
API=1 GW=1
READER_NONCE=11111111-1111-4111-8111-111111111111
record() { printf '%s\\n' "$1" >> events; }
say() { :; }
verify_billing_runtime() { record billing; }
api_maintenance() {
 if [ "$1" = - ]; then cat >/dev/null; record runtime-health
 elif [ "$*" = "-m yleum_api.services.generation_deployment_drain end $SHA" ]; then
  record sql-end
 else return 99; fi
}
ssh() {
 [ "$1" = max-core ] || return 99
 case "$2" in
  "docker inspect yleum-prod-gw --format"*)
   record capture; printf '%s %s\\n' '__OLD__' '__OLD_IMAGE__' ;;
  *"--force-recreate gateway"*) record replacement ;;
  "python3 - '__OLD__' '__OLD_IMAGE__'")
   cat >/dev/null; record gateway-cutover; return __GATE_EXIT__ ;;
  "set -euo pipefail; curl -fsS"*) record gateway-health ;;
  *) return 99 ;;
 esac
}
reader_target() { record target; }
seq() { printf '1\\n'; }
ingress_drain() { record "ingress-$1"; }
quiescence_gate() { record physical; }
reader_control() { record "$2-$1"; }
'''
    program = program.replace("__OLD__", OLD).replace("__OLD_IMAGE__", OLD_IMAGE)
    program = program.replace("__GATE_EXIT__", str(gate_exit))
    for name in ("gateway_capture_identity", "gateway_cutover_gate", "finish_owned_release"):
        program += function(name)
    program += replacement + "\n" + closing_call
    # The real gate includes a long Python heredoc; stdin avoids command-line
    # truncation in the bundled Windows Bash while preserving that payload.
    result = subprocess.run(
        [shell, "-s"], input=program, cwd=tmp_path, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == gate_exit, result.stderr
    events = (tmp_path / "events").read_text(encoding="utf-8").splitlines()
    assert events[:2] == ["capture", "replacement"]
    assert "gateway-cutover" in events
    assert events.index("replacement") < events.index("gateway-cutover")
    reopening = ("ingress-end", "sql-end", "clear-core", "clear-commerce")
    if gate_exit:
        assert events[-1] == "gateway-cutover"
        assert all(event not in events for event in reopening)
    else:
        assert events.index("gateway-cutover") < events.index("physical")
        assert events.index("physical") < events.index("ingress-end") < events.index("sql-end")
        assert events.index("sql-end") < events.index("clear-core") < events.index("clear-commerce")


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
    build = next(line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if "build gateway 2>&1" in line)
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
        "missing_policy",
        "missing_outbox_binding",
        "weak_intent_amount",
        "wallet_not_nullable",
    ],
)
async def test_real_gateway_pool_rejects_incomplete_or_weakened_schema(test_engine, mutation):
    from sqlalchemy import text

    await migrate_settlements(test_engine)
    status_name = "ck_usage_settlements_ck_usage_settlements_status"
    charge_name = "ck_usage_settlements_ck_usage_settlements_charge"
    role = "qa_cutover_" + uuid4().hex
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
            "CHECK (status IN ('settled','free','unpaid','deferred') OR true)",
        ],
        "extra_status": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {status_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {status_name} "
            "CHECK (status IN ('settled','free','unpaid','deferred','pending'))",
        ],
        "weak_charge": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {charge_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {charge_name} "
            "CHECK ((status='settled') = (wallet_charge_id IS NOT NULL) OR true)",
        ],
        "unvalidated_status": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {status_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {status_name} "
            "CHECK (status IN ('settled','free','unpaid','deferred')) NOT VALID",
        ],
        "unvalidated_charge": [
            f"ALTER TABLE usage_settlements DROP CONSTRAINT {charge_name}",
            f"ALTER TABLE usage_settlements ADD CONSTRAINT {charge_name} "
            "CHECK ((status='settled') = (wallet_charge_id IS NOT NULL)) NOT VALID",
        ],
        "missing_policy": ["DROP TABLE generation_billing_policies"],
        "missing_outbox_binding": [
            "ALTER TABLE generation_billing_outbox DROP CONSTRAINT "
            "fk_generation_billing_outbox_run_id_generation_billing_intents"
        ],
        "weak_intent_amount": [
            "ALTER TABLE generation_billing_intents DROP CONSTRAINT "
            "ck_generation_billing_intents_amount"
        ],
        "wallet_not_nullable": [
            "ALTER TABLE generation_billing_intents ALTER COLUMN billing_account_id SET NOT NULL"
        ],
        "permission": [
            f"CREATE ROLE {role} LOGIN",
            f"GRANT SELECT ON alembic_version TO {role}",
        ],
    }
    async with test_engine.begin() as connection:
        for statement in statements[mutation]:
            await connection.execute(text(statement))
    try:
        result = run_real_pool_program(test_engine, role=role if mutation == "permission" else None)
        assert result.returncode != 0
        assert result.stderr.strip() == (
            "gateway settlement schema unavailable; admission remains fenced"
        )
    finally:
        if mutation == "permission":
            async with test_engine.begin() as connection:
                await connection.execute(text(f"DROP OWNED BY {role}"))
                await connection.execute(text(f"DROP ROLE {role}"))


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

    line = next(line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if "build gateway 2>&1" in line)
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
