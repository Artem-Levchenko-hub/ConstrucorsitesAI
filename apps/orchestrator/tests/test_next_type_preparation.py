"""Disposable cache preparation must not weaken source or task evidence."""

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
from yleum_orchestrator.services.next_type_preparation import (
    NEXT_TYPE_PREPARATION_PHASE,
    NEXT_TYPE_PREPARATION_PY,
    prepares_next_types,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="guest uses Linux fd-relative deletion")


def adapter_module():
    pytest.importorskip("fcntl", reason="machine adapter requires the Linux controller runtime")
    return importlib.import_module("yleum_orchestrator.services.machine_adapter")


def max_manifest(protocol=1, cwd="."):
    value = payload()
    value["app_config_protocol"] = protocol
    value["tasks"] = [
        {"name": "install", "role": "bootstrap", "cwd": cwd,
         "argv": ["pnpm", "install", "--frozen-lockfile"]},
        {"name": "types", "role": "fast_check", "cwd": cwd, "argv": ["pnpm", "typecheck"]},
        {"name": "build", "role": "full_build", "cwd": cwd, "argv": ["pnpm", "build"]},
    ]
    return MachineManifest.model_validate(value)


@pytest.mark.parametrize(("protocol", "cwd", "expected"), [
    (1, ".", True), (None, ".", False), (1, "nested", False),
])
def test_only_root_max_contract_adds_next_phase(protocol, cwd, expected):
    manifest = max_manifest(protocol, cwd)
    assert prepares_next_types(manifest) is expected


@pytest.mark.parametrize(("protocol", "cwd", "expected"), [
    (1, ".", True), (None, ".", False), (1, "nested", False),
])
def test_preflight_orders_supported_phase_only(protocol, cwd, expected):
    adapter = adapter_module()
    commands = adapter._node_preflight_commands(max_manifest(protocol, cwd))
    assert [name for name, *_ in commands] == [
        "install", *([NEXT_TYPE_PREPARATION_PHASE] if expected else []),
        adapter.REACT_EFFECT_CHECK_PHASE,
    ]
    if expected:
        assert commands[1][1] == ["python3", "-I", "-S", "-c", NEXT_TYPE_PREPARATION_PY]


def test_unrelated_non_node_tasks_keep_their_existing_preflight():
    manifest = MachineManifest.model_validate(payload())
    assert not prepares_next_types(manifest)
    adapter = adapter_module()
    assert adapter._node_preflight_commands(manifest) == []


@pytest.mark.parametrize(("role", "field", "replacement"), [
    ("bootstrap", "cwd", "nested"), ("bootstrap", "argv", ["sh", "install.sh"]),
    ("fast_check", "argv", ["python", "check.py"]),
    ("full_build", "cwd", "nested"),
])
def test_unsupported_mixed_node_scope_is_not_changed(role, field, replacement):
    value = max_manifest().model_dump(mode="json")
    next(task for task in value["tasks"] if task["role"] == role)[field] = replacement
    assert not prepares_next_types(MachineManifest.model_validate(value))


def test_next_preparation_source_is_part_of_replay_identity(monkeypatch):
    adapter = adapter_module()
    request = SimpleNamespace(cmd="omnia:fast_check", task_role="fast_check",
                              expected_revision="a" * 64, timeout_seconds=120)
    max_value = max_manifest()
    generic = max_manifest(None)
    first = adapter.MachineAdapter._request_digest(max_value, request)
    generic_first = adapter.MachineAdapter._request_digest(generic, request)
    monkeypatch.setattr(adapter, "NEXT_TYPE_PREPARATION_PY", NEXT_TYPE_PREPARATION_PY + "\n# v2")
    assert adapter.MachineAdapter._request_digest(max_value, request) != first
    assert adapter.MachineAdapter._request_digest(generic, request) == generic_first


@pytest.mark.parametrize("failure", [None, "install", NEXT_TYPE_PREPARATION_PHASE])
async def test_phase_is_fenced_journalled_and_precedes_unchanged_tsc(tmp_path, failure):
    adapter = adapter_module()
    commands = []
    stored = None

    async def start(argv, cwd, mutation):
        commands.append((argv, cwd, mutation))
        return str(mutation.operation_id)

    async def status(operation, mutation):
        argv = commands[-1][0]
        failed = (failure == "install" and argv[0:2] == ["pnpm", "install"]) or (
            failure == NEXT_TYPE_PREPARATION_PHASE and argv[0] == "python3"
        )
        return SimpleNamespace(state="completed", exit_code=1 if failed else 0, output="result")

    async def finish(mutation, result):
        nonlocal stored
        stored = result
        return result

    machine = SimpleNamespace(
        ensure=AsyncMock(), request_start=AsyncMock(side_effect=lambda *a, **kw: stored),
        request_heartbeat=AsyncMock(), request_finish=finish, exec_start=start, exec_status=status,
    )
    runtime = adapter.MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state")), SimpleNamespace(),
    )
    runtime.parts = lambda _: (machine, object())
    runtime._migration_dependency_gap = AsyncMock(return_value=None)
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(), fencing_epoch=7, expected_revision="a" * 64,
        cmd="omnia:fast_check", task_role="fast_check",
    )
    state = SimpleNamespace(workspace_id=uuid4())
    result = await runtime.execute(state, max_manifest(), request)
    assert result.exit_code == (1 if failure else 0)
    expected = [*adapter._node_preflight_commands(max_manifest()),
        ("types", ["pnpm", "typecheck"], ".", 600),
    ]
    stop = {"install": 1, NEXT_TYPE_PREPARATION_PHASE: 2, None: 4}[failure]
    assert [argv for argv, _, _ in commands] == [argv for _, argv, _, _ in expected[:stop]]
    for (_argv, cwd, mutation), (phase, _, _, _) in zip(commands, expected, strict=False):
        assert cwd == "."
        assert mutation.operation_id == uuid5(request.operation_id, phase)
        assert mutation.fencing_epoch == request.fencing_epoch
    replayed = await runtime.execute(state, max_manifest(), request)
    assert replayed == result
    assert len(commands) == stop


async def test_request_timeout_terminates_the_new_journalled_child(tmp_path):
    adapter = adapter_module()
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(), fencing_epoch=7, expected_revision="a" * 64,
        cmd="omnia:fast_check", task_role="fast_check",
    )
    machine = SimpleNamespace(
        inspect_request_status=AsyncMock(return_value=SimpleNamespace(
            result=None, state="running", phase=NEXT_TYPE_PREPARATION_PHASE,
        )), exec_terminate=AsyncMock(),
        request_finish=AsyncMock(side_effect=lambda _, result: result),
    )
    runtime = adapter.MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state")), SimpleNamespace(),
    )
    runtime.parts = lambda _: (machine, object())
    runtime._execute = AsyncMock(side_effect=TimeoutError)
    result = await runtime.execute(SimpleNamespace(workspace_id=uuid4()), max_manifest(), request)
    assert result.exit_code == 124 and result.timed_out
    call = machine.exec_terminate.await_args
    assert call.args[0] == str(uuid5(request.operation_id, NEXT_TYPE_PREPARATION_PHASE))
    assert call.args[1].fencing_epoch == 7


def guest_program():
    namespace = {"__name__": "controller_test"}
    exec(compile(NEXT_TYPE_PREPARATION_PY, "<controller-next-types>", "exec"), namespace)
    return SimpleNamespace(**namespace)


@posix_only
@pytest.mark.parametrize("target", [".next", ".next/types"])
def test_cache_symlink_is_rejected_without_following_it(tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"business data")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / target).symlink_to(outside, target_is_directory=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises((OSError, RuntimeError)):
            guest_program().prune_types(fd)
    finally:
        os.close(fd)
    assert (outside / "keep").read_bytes() == b"business data"


@posix_only
def test_rename_race_keeps_the_original_next_descriptor(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    old = root / ".next"
    (old / "types").mkdir(parents=True)
    (old / "types/stale.ts").write_text("stale", encoding="utf-8")
    (old / "BUILD_ID").write_text("accepted", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "types").mkdir(parents=True)
    (outside / "types/keep").write_bytes(b"business data")
    original = shutil.rmtree

    def race(name, **kwargs):
        old.rename(root / "old-next")
        old.symlink_to(outside, target_is_directory=True)
        return original(name, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", race)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        guest_program().prune_types(fd)
    finally:
        os.close(fd)
    assert (outside / "types/keep").read_bytes() == b"business data"
    assert (root / "old-next/BUILD_ID").read_text() == "accepted"
    assert not (root / "old-next/types").exists()


@posix_only
def test_type_cache_swap_to_symlink_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    types = root / ".next/types"
    types.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"business data")
    original = shutil.rmtree

    def race(name, **kwargs):
        types.rename(types.with_name("old-types"))
        types.symlink_to(outside, target_is_directory=True)
        return original(name, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", race)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            guest_program().prune_types(fd)
    finally:
        os.close(fd)
    assert (outside / "keep").read_bytes() == b"business data"


@posix_only
def test_nested_cache_symlink_removes_only_the_link(tmp_path):
    root = tmp_path / "workspace"
    types = root / ".next/types"
    types.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"business data")
    (types / "nested").symlink_to(outside, target_is_directory=True)
    (root / ".next/BUILD_ID").write_bytes(b"accepted")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        guest_program().prune_types(fd)
    finally:
        os.close(fd)
    assert not types.exists()
    assert (root / ".next/BUILD_ID").read_bytes() == b"accepted"
    assert (outside / "keep").read_bytes() == b"business data"


@posix_only
@pytest.mark.parametrize("name", ["tsconfig.json", "next-env.d.ts"])
def test_configuration_symlink_is_rejected_before_cache_deletion(tmp_path, monkeypatch, name):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.5.24"}}), encoding="utf-8",
    )
    for config in ("tsconfig.json", "next-env.d.ts"):
        (tmp_path / config).write_text("source", encoding="utf-8")
    outside = tmp_path / "private-config"
    outside.write_bytes(b"do not follow")
    (tmp_path / name).unlink()
    (tmp_path / name).symlink_to(outside)
    cache = tmp_path / ".next/types/keep.ts"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale")
    called = []

    def resolve_only(argv, **kwargs):
        called.append(argv)
        assert argv[1] == "-e"
        return SimpleNamespace(stdout="/installed/next/dist/bin/next")

    monkeypatch.setattr(subprocess, "run", resolve_only)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(OSError):
        guest_program().main()
    assert len(called) == 0
    assert cache.read_bytes() == b"stale"
    assert outside.read_bytes() == b"do not follow"


@posix_only
def test_typegen_source_mutation_is_rejected_and_never_normalized(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.5.24"}}), encoding="utf-8",
    )
    for config in ("tsconfig.json", "next-env.d.ts"):
        (tmp_path / config).write_text("original", encoding="utf-8")

    def typegen(argv, **kwargs):
        if argv[1] == "-e":
            return SimpleNamespace(stdout="/installed/next/dist/bin/next")
        assert argv == ["node", "/installed/next/dist/bin/next", "typegen"]
        (tmp_path / "tsconfig.json").write_text("changed", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", typegen)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="changed source configuration"):
        guest_program().main()
    assert (tmp_path / "tsconfig.json").read_text() == "changed"


@posix_only
def test_missing_installed_pin_rejects_before_cache_deletion(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.5.24"}}), encoding="utf-8",
    )
    for config in ("tsconfig.json", "next-env.d.ts"):
        (tmp_path / config).write_text("source", encoding="utf-8")
    cache = tmp_path / ".next/types/keep.ts"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale")

    def missing(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", missing)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        guest_program().main()
    assert cache.read_bytes() == b"stale"


@posix_only
@pytest.mark.parametrize("name", [
    "package.json", "tsconfig.json", "next-env.d.ts", "next.config.ts",
])
def test_source_hardlink_rejects_before_children_or_cache_deletion(tmp_path, monkeypatch, name):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.5.24"}}), encoding="utf-8",
    )
    for config in ("tsconfig.json", "next-env.d.ts"):
        (workspace / config).write_text("source", encoding="utf-8")
    template = Path(__file__).resolve().parents[1] / "templates/max-miniapp-nextjs/next.config.ts"
    shutil.copyfile(template, workspace / "next.config.ts")
    sentinel = tmp_path / "outside-sentinel"
    os.link(workspace / name, sentinel)
    original = sentinel.read_bytes()
    cache = workspace / ".next/types/keep.ts"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale")
    run = AsyncMock()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(workspace)
    with pytest.raises(RuntimeError, match="linked"):
        guest_program().main()
    run.assert_not_called()
    assert cache.read_bytes() == b"stale"
    assert sentinel.read_bytes() == original
    assert (workspace / name).read_bytes() == original


@posix_only
@pytest.mark.parametrize("name", [
    "next.config.ts", "next.config.js", "next.config.mjs", "next.config.cjs",
])
def test_custom_config_is_never_executed_and_cache_is_untouched(tmp_path, monkeypatch, name):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.5.24"}}), encoding="utf-8",
    )
    (tmp_path / name).write_text("throw new Error('must never execute');", encoding="utf-8")
    cache = tmp_path / ".next/types/keep.ts"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale")
    run = AsyncMock()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(tmp_path)
    assert guest_program().main() == 0
    run.assert_not_called()
    assert cache.read_bytes() == b"stale"


def test_default_config_allowlist_matches_the_locked_template():
    template = Path(__file__).resolve().parents[1] / "templates/max-miniapp-nextjs/next.config.ts"
    source = template.read_text(encoding="utf-8")
    constants = guest_program().DEFAULT_CONFIG_HASHES
    assert hashlib.sha256(source.encode("utf-8")).hexdigest() in constants
    assert hashlib.sha256(source.replace("\n", "\r\n").encode("utf-8")).hexdigest() in constants


@posix_only
@pytest.mark.parametrize("package", [{}, {"dependencies": {"next": "16.0.0"}}])
def test_non_next_or_unsupported_version_is_an_unchanged_noop(tmp_path, monkeypatch, package):
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (tmp_path / ".next/types").mkdir(parents=True)
    stale = tmp_path / ".next/types/stale.ts"
    stale.write_bytes(b"keep")
    monkeypatch.chdir(tmp_path)
    assert guest_program().main() == 0
    assert stale.read_bytes() == b"keep"


@posix_only
def test_actual_next_15_5_route_rename_prunes_only_generated_types(tmp_path):
    """Mandatory CI fixture: real pinned Next and real unchanged tsc, no fake CLI."""
    modules_setting = os.environ.get("QA_NEXT_TYPEGEN_NODE_MODULES")
    modules = Path(modules_setting) if modules_setting else (
        Path(__file__).resolve().parents[2] / "web/node_modules"
    )
    if not (modules / "next/package.json").is_file():
        if os.environ.get("QA_NEXT_TYPEGEN_REQUIRED") == "1":
            pytest.fail("Mandatory native Next dependency fixture is missing")
        pytest.skip("Native Next fixture dependencies unavailable")
    assert json.loads((modules / "next/package.json").read_text())["version"] == "15.5.24"
    node = shutil.which("node")
    assert node is not None
    (tmp_path / "node_modules").symlink_to(modules.resolve(), target_is_directory=True)
    template = Path(__file__).resolve().parents[1] / "templates/max-miniapp-nextjs"
    for name in ("tsconfig.json", "next-env.d.ts", "next.config.ts"):
        shutil.copyfile(template / name, tmp_path / name)
    (tmp_path / "package.json").write_text(
        json.dumps({"private": True, "dependencies": {"next": "15.5.24"}}), encoding="utf-8",
    )
    app = tmp_path / "src/app"
    app.mkdir(parents=True)
    (app / "page.tsx").write_text("export default function Page(){return <main>Hello</main>}\n")
    (app / "layout.tsx").write_text(
        "export default function Layout({children}:{children:React.ReactNode})"
        "{return <html><body>{children}</body></html>}\n",
    )

    def run(argv):
        return subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=90)

    next_cli = str(modules / "next/dist/bin/next")
    tsc = [node, str(modules / "typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.json"]
    assert run([node, next_cli, "typegen"]).returncode == 0
    assert run(tsc).returncode == 0
    stale = tmp_path / ".next/types/app/page.ts"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        "import * as page from '../../../src/app/page.js'; export type Old=typeof page;\n",
    )
    (app / "home").mkdir()
    (app / "page.tsx").rename(app / "home/page.tsx")
    assert "TS2307" in run(tsc).stdout
    assert run([node, next_cli, "typegen"]).returncode == 0
    assert stale.is_file(), "Next typegen alone must reproduce the stale per-route file"
    assert run(tsc).returncode != 0
    protected = {
        ".next/BUILD_ID": b"accepted-build", ".next/server/app/page.js": b"accepted server",
        ".next/static/chunks/keep.js": b"accepted assets", "data/orders.json": b'[{"id":7}]',
    }
    for name, content in protected.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    paths = [*protected, "tsconfig.json", "next-env.d.ts", "next.config.ts", "package.json",
             "src/app/layout.tsx", "src/app/home/page.tsx"]

    def hashes():
        return {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in paths}

    before = hashes()
    prepared = run([sys.executable, "-I", "-S", "-c", NEXT_TYPE_PREPARATION_PY])
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert not stale.exists()
    assert (tmp_path / ".next/types/routes.d.ts").is_file()
    assert (tmp_path / ".next/types/validator.ts").is_file()
    checked = run(tsc)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert hashes() == before
    (app / "home/page.tsx").write_text(
        'const bad: number = "real source error"; export default function Page(){return <main/>}\n',
    )
    assert run([sys.executable, "-I", "-S", "-c", NEXT_TYPE_PREPARATION_PY]).returncode == 0
    actual_source_error = run(tsc)
    assert actual_source_error.returncode != 0
    assert "TS2322" in actual_source_error.stdout
    environment = tmp_path / "next-env.d.ts"
    environment.write_text("// stale source configuration\n")
    mutation = run([sys.executable, "-I", "-S", "-c", NEXT_TYPE_PREPARATION_PY])
    assert mutation.returncode != 0
    assert "changed source configuration" in mutation.stderr
    assert environment.read_text() != "// stale source configuration\n"
