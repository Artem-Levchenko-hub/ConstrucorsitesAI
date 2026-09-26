import asyncio
import errno
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.routers import runtime
from yleum_orchestrator.schemas.runtime import HotReloadRequest

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000042")
LEDGER_PREFIX = "__OMNIA_MIGRATION_LEDGER__"
FORBIDDEN_TRANSACTION_CASES = [
    ("BEGIN; SELECT 1; COMMIT;", "BEGIN"),
    ("/* harmless */ START TRANSACTION; SELECT 1;", "START TRANSACTION"),
    ("SAVEPOINT before_change; SELECT 1;", "SAVEPOINT"),
    ("ROLLBACK TO SAVEPOINT before_change;", "ROLLBACK"),
    ("PREPARE TRANSACTION 'escape';", "PREPARE TRANSACTION"),
    ("SELECT 1; END;", "END"),
]
ALLOWED_TRANSACTION_CASES = [
    ("SELECT 'BEGIN; COMMIT; ROLLBACK';", None),
    ("-- BEGIN;\nSELECT 1;", None),
    ("/* COMMIT /* nested ROLLBACK */ */ SELECT 1;", None),
    ("DO $$ BEGIN RAISE NOTICE 'COMMIT'; END $$;", None),
    ("SELECT CASE WHEN true THEN 1 ELSE 0 END;", None),
    (r"SELECT E'quoted \'; COMMIT; still string';", None),
    ('SELECT "SAVEPOINT" FROM example;', None),
    ("SELECT $tag$COMMIT$tag$;", None),
    ("SELECT foo$tag$; COMMIT; SELECT x$tag$;", "COMMIT"),
    ("SELECT foo\u0301$tag$; COMMIT; SELECT x$tag$;", "COMMIT"),
    ("SELECT foo😀$tag$; COMMIT; SELECT x$tag$;", "COMMIT"),
]
TRANSACTION_GRAMMAR_CASES = [
    *FORBIDDEN_TRANSACTION_CASES,
    *ALLOWED_TRANSACTION_CASES,
]


def _ledger_receipt(applied: list[str], *, lock_acquired: bool = True) -> dict[str, str]:
    return {
        "exit_code": "0",
        "stdout": (
            LEDGER_PREFIX
            + json.dumps({"lock_acquired": lock_acquired, "applied": applied})
            + "\n"
        ),
        "stderr": "",
    }


def _payload(files: dict[str, str]) -> HotReloadRequest:
    return HotReloadRequest(project_id=PROJECT_ID, files=files)


def _patch_external_runtime(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    *,
    template: str = "max-miniapp-nextjs",
) -> tuple[AsyncMock, AsyncMock]:
    monkeypatch.setattr(runtime, "_project_workspace_dir", lambda _project_id: workspace)
    writer = AsyncMock(return_value={
        "written": "1", "total_bytes": "8", "dropped": "",
    })
    monkeypatch.setattr(runtime, "write_files", writer)
    monkeypatch.setattr(
        runtime,
        "container_image_template",
        AsyncMock(return_value=template),
    )
    monkeypatch.setattr(runtime.demo_seed_writer, "seed_demo_data", AsyncMock(return_value={}))
    applied: set[str] = set()

    def execute_result(*_args, **kwargs):
        cmd = kwargs["cmd"]
        if cmd == ["node", "scripts/apply-migrations.mjs"]:
            drizzle = workspace / "drizzle"
            if drizzle.is_dir():
                applied.update(path.name for path in drizzle.glob("*.sql"))
            return {"exit_code": "0", "stdout": "ok", "stderr": ""}
        if cmd[:3] == ["node", "--input-type=module", "-e"]:
            requested = set(__import__("json").loads(cmd[-1]))
            receipt = sorted(requested & applied)
            return {
                "exit_code": "0",
                "stdout": LEDGER_PREFIX + __import__("json").dumps(receipt) + "\n",
                "stderr": "",
            }
        return {"exit_code": "0", "stdout": "ok", "stderr": ""}

    execute = AsyncMock(side_effect=execute_result)
    monkeypatch.setattr(runtime, "exec_cmd", execute)
    return writer, execute


async def test_max_hot_reload_applies_drizzle_sql_with_platform_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    _, execute = _patch_external_runtime(monkeypatch, tmp_path)

    result = await runtime._hot_reload_locked(
        _payload({"drizzle/0004_add_private_note.sql": "ALTER TABLE tasks ADD COLUMN x text;"}),
        "max-preview",
    )

    assert result["drizzle_exit_code"] == "0"
    assert any(
        call.kwargs["cmd"] == ["node", "scripts/apply-migrations.mjs"]
        for call in execute.await_args_list
    )


@pytest.mark.parametrize(
    "path",
    [
        "migrations/0001.sql",
        "src/lib/db/migrations/0001.sql",
        "scripts/migrate.mjs",
        "drizzle/nested/0001.sql",
    ],
)
async def test_max_hot_reload_rejects_alternative_migration_paths_before_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError, match="drizzle/\\*\\.sql"):
        await runtime._hot_reload_locked(_payload({path: "SELECT 1;"}), "max-preview")

    writer.assert_not_awaited()
    assert not (tmp_path / path).exists()


async def test_max_hot_reload_rejects_platform_runner_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError, match="platform-owned"):
        await runtime._hot_reload_locked(
            _payload({"scripts/apply-migrations.mjs": "// replacement"}),
            "max-preview",
        )

    assert runner.read_text(encoding="utf-8") == "// platform-owned"
    writer.assert_not_awaited()


async def test_max_metadata_requires_platform_runner_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError, match="platform-owned migration runner is missing"):
        await runtime._hot_reload_locked(
            _payload({"src/app/page.tsx": "export default function Page(){}"}),
            "max-preview",
        )

    writer.assert_not_awaited()


async def test_hot_reload_normalizes_one_alias_for_validation_write_and_trigger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)

    await runtime._hot_reload_locked(
        _payload({"./drizzle/0004.sql": "SELECT 1;"}),
        "max-preview",
    )

    assert writer.await_args.args[1] == {"drizzle/0004.sql": "SELECT 1;"}
    assert any(
        call.kwargs["cmd"] == ["node", "scripts/apply-migrations.mjs"]
        for call in execute.await_args_list
    )
    assert (tmp_path / "drizzle" / "0004.sql").read_text(encoding="utf-8") == "SELECT 1;"


@pytest.mark.parametrize(
    "files,empty_files",
    [
        ({"./drizzle/0004.sql": "SELECT 1;", "drizzle/0004.sql": "SELECT 2;"}, ()),
        ({"src/../drizzle/0004.sql": "SELECT 1;"}, ()),
        ({"./scripts/apply-migrations.mjs": ""}, ()),
        ({"scripts/apply-migrations.mjs": ""}, ("scripts/apply-migrations.mjs",)),
    ],
)
async def test_max_alias_traversal_and_runner_deletion_fail_before_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: dict[str, str],
    empty_files: tuple[str, ...],
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError):
        await runtime._hot_reload_locked(
            HotReloadRequest(project_id=PROJECT_ID, files=files, empty_files=list(empty_files)),
            "max-preview",
        )

    writer.assert_not_awaited()


@pytest.mark.parametrize("replacement,empty_files", [("SELECT 2;", ()), ("", ())])
async def test_existing_canonical_migration_is_immutable_before_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    replacement: str,
    empty_files: tuple[str, ...],
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    migration = tmp_path / "drizzle" / "0003.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("SELECT 1;", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError, match="immutable"):
        await runtime._hot_reload_locked(
            HotReloadRequest(
                project_id=PROJECT_ID,
                files={"drizzle/0003.sql": replacement},
                empty_files=list(empty_files),
            ),
            "max-preview",
        )

    writer.assert_not_awaited()
    assert migration.read_text(encoding="utf-8") == "SELECT 1;"


async def test_max_rejects_non_append_migration_before_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    migration = tmp_path / "drizzle" / "0003.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("SELECT 3;", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError, match="append after"):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0002.sql": "SELECT 2;"}),
            "max-preview",
        )

    writer.assert_not_awaited()


@pytest.mark.parametrize("legacy_path", ["migrations/0001.sql", "scripts/migrate.mjs"])
async def test_max_allows_deleting_legacy_migration_but_rejects_modifying_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, legacy_path: str,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    legacy = tmp_path / legacy_path
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("SELECT 1;", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)

    await runtime._hot_reload_locked(_payload({legacy_path: ""}), "max-preview")
    assert not legacy.exists()
    writer.assert_awaited_once()

    legacy.write_text("SELECT 1;", encoding="utf-8")
    writer.reset_mock()
    with pytest.raises(OrchestratorError, match="cannot be introduced or modified"):
        await runtime._hot_reload_locked(
            _payload({legacy_path: "SELECT 2;"}),
            "max-preview",
        )
    writer.assert_not_awaited()


async def test_max_runner_failure_is_typed_after_source_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)
    execute.side_effect = [
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "1", "stdout": "", "stderr": "database down"},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
    ]

    with pytest.raises(OrchestratorError) as raised:
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "SELECT 4;"}),
            "max-preview",
        )

    assert raised.value.code == "migration_apply_failed"
    assert raised.value.details == {
        "source_files_written": True,
        "database_changes_confirmed": False,
        "exit_code": "1",
        "stderr_tail": "database down",
    }
    writer.assert_awaited_once()
    assert (tmp_path / "drizzle" / "0004.sql").exists()


async def test_exact_canonical_replay_is_immutable_and_idempotently_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    migration = tmp_path / "drizzle" / "0003.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("SELECT 3;", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)

    await runtime._hot_reload_locked(
        _payload({"drizzle/0003.sql": "SELECT 3;"}),
        "max-preview",
    )

    writer.assert_awaited_once()
    assert any(
        call.kwargs["cmd"] == ["node", "scripts/apply-migrations.mjs"]
        for call in execute.await_args_list
    )


async def test_failed_unapplied_migration_can_be_corrected_and_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)
    execute.side_effect = [
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "1", "stdout": "", "stderr": "bad sql"},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "0", "stdout": "applied", "stderr": ""},
        {
            "exit_code": "0",
            "stdout": f'{LEDGER_PREFIX}["0004.sql"]\n',
            "stderr": "",
        },
    ]

    with pytest.raises(OrchestratorError) as failed:
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "BROKEN SQL"}),
            "max-preview",
        )
    assert failed.value.code == "migration_apply_failed"

    result = await runtime._hot_reload_locked(
        _payload({"drizzle/0004.sql": "SELECT 4;"}),
        "max-preview",
    )

    assert result["drizzle_exit_code"] == "0"
    assert writer.await_count == 2
    assert (tmp_path / "drizzle" / "0004.sql").read_text(encoding="utf-8") == "SELECT 4;"


@pytest.mark.parametrize("replacement", ["SELECT 5;", ""])
async def test_ledger_applied_migration_cannot_be_rewritten_or_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    replacement: str,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)
    execute.side_effect = [
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "1", "stdout": "", "stderr": "bad sql"},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {
            "exit_code": "0",
            "stdout": f'{LEDGER_PREFIX}["0004.sql"]\n',
            "stderr": "",
        },
    ]
    with pytest.raises(OrchestratorError):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "BROKEN SQL"}),
            "max-preview",
        )
    writer.reset_mock()

    with pytest.raises(OrchestratorError, match=r"(?:applied|accepted).*immutable"):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": replacement}),
            "max-preview",
        )

    writer.assert_not_awaited()


async def test_unknown_runner_outcome_requires_reconciliation_before_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)
    execute.side_effect = [
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        OrchestratorError(
            code="container_failure",
            message="runner transport lost",
            status_code=503,
        ),
    ]

    with pytest.raises(OrchestratorError) as unknown:
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "BROKEN SQL"}),
            "max-preview",
        )
    assert unknown.value.code == "migration_reconciliation_required"
    writer.reset_mock()
    execute.reset_mock()
    execute.side_effect = [_ledger_receipt([], lock_acquired=False)]

    with pytest.raises(OrchestratorError) as retry:
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "BROKEN SQL"}),
            "max-preview",
        )
    assert retry.value.code == "migration_reconciliation_required"
    writer.assert_not_awaited()
    assert not any(
        call.kwargs["cmd"] == ["node", "scripts/apply-migrations.mjs"]
        for call in execute.await_args_list
    )


async def test_newer_migration_cannot_bypass_failed_unapplied_predecessor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)
    execute.side_effect = [
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "1", "stdout": "", "stderr": "bad sql"},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
        {"exit_code": "0", "stdout": f"{LEDGER_PREFIX}[]\n", "stderr": ""},
    ]
    with pytest.raises(OrchestratorError):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "BROKEN SQL"}),
            "max-preview",
        )
    writer.reset_mock()

    with pytest.raises(OrchestratorError, match=r"resolve pending.*0004"):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0005.sql": "SELECT 5;"}),
            "max-preview",
        )

    writer.assert_not_awaited()


@pytest.mark.parametrize(
    "sql,expected",
    FORBIDDEN_TRANSACTION_CASES,
)
async def test_transaction_control_is_rejected_before_any_workspace_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str,
    expected: str,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, execute = _patch_external_runtime(monkeypatch, tmp_path)

    with pytest.raises(OrchestratorError, match=expected):
        await runtime._hot_reload_locked(
            _payload(
                {
                    "src/app/page.tsx": "export default function Page(){}",
                    "drizzle/0004.sql": sql,
                }
            ),
            "max-preview",
        )

    writer.assert_not_awaited()
    execute.assert_not_awaited()
    assert not (tmp_path / "src" / "app" / "page.tsx").exists()
    assert not (tmp_path / "drizzle" / "0004.sql").exists()


@pytest.mark.parametrize("sql,expected", TRANSACTION_GRAMMAR_CASES)
def test_python_transaction_control_grammar(
    sql: str, expected: str | None,
) -> None:
    assert runtime._transaction_control_statement(sql) == expected


def test_platform_runner_enforces_same_sql_guard_and_session_lock() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is unavailable")
    runner = (
        Path(__file__).parents[1]
        / "templates"
        / "max-miniapp-nextjs"
        / "scripts"
        / "apply-migrations.mjs"
    )
    source = runner.read_text(encoding="utf-8")
    scanner = source[
        source.index("const TRANSACTION_WORDS") : source.index("\ntry {")
    ]
    cases = [sql for sql, _expected in TRANSACTION_GRAMMAR_CASES]
    program = (
        scanner
        + "\nconsole.log(JSON.stringify(process.argv.slice(1).map(transactionControl)));"
    )
    checked = subprocess.run(
        ["node", "--input-type=module", "-e", program, *cases],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(checked.stdout) == [
        expected for _sql, expected in TRANSACTION_GRAMMAR_CASES
    ]
    assert "pg_advisory_lock" in source
    assert "pg_advisory_unlock" in source
    assert source.index("const forbidden = transactionControl(sql)") < source.index(
        'client.query("BEGIN")'
    )


@pytest.mark.parametrize(
    "failure_point",
    ["file_fsync", "rename", "directory_open", "directory_fsync"],
)
async def test_durable_intent_io_error_stops_before_source_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)
    original_fsync = runtime.os.fsync
    original_open = runtime.os.open
    fsync_calls = 0

    def fail_fsync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if failure_point == "file_fsync" and fsync_calls == 1:
            raise OSError(errno.EIO, "file fsync failed")
        if failure_point == "directory_fsync" and fsync_calls == 2:
            raise OSError(errno.EIO, "directory fsync failed")
        original_fsync(file_descriptor)

    monkeypatch.setattr(runtime.os, "fsync", fail_fsync)
    if failure_point == "rename":
        monkeypatch.setattr(
            runtime.os,
            "replace",
            lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "rename failed")),
        )
    elif failure_point == "directory_open":
        monkeypatch.setattr(
            runtime.os,
            "open",
            lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "directory open failed")),
        )
    elif failure_point == "directory_fsync":
        directory_handle = tmp_path / "directory-handle"
        directory_handle.write_text("handle", encoding="utf-8")
        monkeypatch.setattr(
            runtime.os,
            "open",
            lambda *_args: original_open(directory_handle, runtime.os.O_RDONLY),
        )

    with pytest.raises(OSError) as raised:
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "SELECT 4;"}),
            "max-preview",
        )

    assert raised.value.errno == errno.EIO
    writer.assert_not_awaited()
    assert not (tmp_path / "drizzle" / "0004.sql").exists()


async def test_verified_unsupported_directory_fsync_error_is_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime, "_DIRECTORY_FSYNC_PLATFORM", "nt")
    monkeypatch.setattr(
        runtime.os,
        "open",
        lambda *_args: (_ for _ in ()).throw(
            PermissionError(errno.EACCES, "directory fsync is unsupported")
        ),
    )

    result = await runtime._hot_reload_locked(
        _payload({"drizzle/0004.sql": "SELECT 4;"}),
        "max-preview",
    )

    assert result["drizzle_exit_code"] == "0"
    writer.assert_awaited_once()


async def test_cancellation_before_source_write_leaves_recoverable_intent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    writer, _ = _patch_external_runtime(monkeypatch, tmp_path)
    writer.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "SELECT 4;"}),
            "max-preview",
        )

    receipt = runtime._load_max_migration_receipts(tmp_path)["drizzle/0004.sql"]
    assert receipt["state"] == "intent"
    assert receipt["execution_id"]
    assert not (tmp_path / "drizzle" / "0004.sql").exists()

    writer.side_effect = None
    writer.return_value = {"written": "1", "total_bytes": "8", "dropped": ""}
    result = await runtime._hot_reload_locked(
        _payload({"drizzle/0004.sql": "SELECT 4;"}),
        "max-preview",
    )
    assert result["drizzle_exit_code"] == "0"
    assert runtime._load_max_migration_receipts(tmp_path) == {}


async def test_cancellation_after_source_write_restarts_from_staged_intent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    _patch_external_runtime(monkeypatch, tmp_path)
    seed = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(runtime.demo_seed_writer, "seed_demo_data", seed)

    with pytest.raises(asyncio.CancelledError):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": "SELECT 4;"}),
            "max-preview",
        )

    receipt = runtime._load_max_migration_receipts(tmp_path)["drizzle/0004.sql"]
    assert receipt["state"] == "intent"
    assert (tmp_path / "drizzle" / "0004.sql").read_text(encoding="utf-8") == "SELECT 4;"

    seed.side_effect = None
    seed.return_value = {}
    result = await runtime._hot_reload_locked(
        _payload({"drizzle/0004.sql": "SELECT 4;"}),
        "max-preview",
    )
    assert result["drizzle_exit_code"] == "0"
    assert runtime._load_max_migration_receipts(tmp_path) == {}


async def test_cancellation_after_staged_migration_deletion_reconciles_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    migration = tmp_path / "drizzle" / "0004.sql"
    migration.parent.mkdir()
    migration.write_text("BROKEN SQL", encoding="utf-8")
    runtime._save_max_migration_receipts(
        tmp_path,
        {
            "drizzle/0004.sql": {
                "state": "staged_unapplied",
                "digest": runtime._migration_digest("BROKEN SQL"),
                "execution_id": "failed-execution",
            }
        },
    )
    _patch_external_runtime(monkeypatch, tmp_path)
    apply_workspace_files = runtime._apply_workspace_files

    def cancel_after_delete(*args, **kwargs):
        migration.unlink()
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime, "_apply_workspace_files", cancel_after_delete)

    with pytest.raises(asyncio.CancelledError):
        await runtime._hot_reload_locked(
            _payload({"drizzle/0004.sql": ""}),
            "max-preview",
        )
    assert not migration.exists()
    assert runtime._load_max_migration_receipts(tmp_path)["drizzle/0004.sql"][
        "operation"
    ] == "delete"

    monkeypatch.setattr(runtime, "_apply_workspace_files", apply_workspace_files)
    await runtime._hot_reload_locked(
        _payload({"drizzle/0004.sql": ""}),
        "max-preview",
    )
    assert runtime._load_max_migration_receipts(tmp_path) == {}


async def test_cancellation_after_runner_is_reconciled_from_ledger_on_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    _patch_external_runtime(monkeypatch, tmp_path)
    path = "drizzle/0004.sql"
    ledger = AsyncMock(side_effect=[set(), asyncio.CancelledError(), {path}, {path}])
    monkeypatch.setattr(runtime, "_read_max_migration_ledger", ledger)

    with pytest.raises(asyncio.CancelledError):
        await runtime._hot_reload_locked(_payload({path: "SELECT 4;"}), "max-preview")
    assert runtime._load_max_migration_receipts(tmp_path)[path]["state"] == "intent"

    result = await runtime._hot_reload_locked(_payload({path: "SELECT 4;"}), "max-preview")
    assert result["drizzle_exit_code"] == "0"
    assert runtime._load_max_migration_receipts(tmp_path) == {}


async def test_every_pending_runner_input_gets_durable_execution_intent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runner = tmp_path / "scripts" / "apply-migrations.mjs"
    runner.parent.mkdir(parents=True)
    runner.write_text("// platform-owned", encoding="utf-8")
    drizzle = tmp_path / "drizzle"
    drizzle.mkdir()
    (drizzle / "0003.sql").write_text("SELECT 3;", encoding="utf-8")
    (drizzle / "0004.sql").write_text("SELECT 4;", encoding="utf-8")
    runtime._save_max_migration_receipts(
        tmp_path,
        {
            "drizzle/0004.sql": {
                "state": "staged_unapplied",
                "digest": runtime._migration_digest("SELECT 4;"),
                "execution_id": "previous-execution",
            }
        },
    )
    _, execute = _patch_external_runtime(monkeypatch, tmp_path)
    applied: set[str] = set()

    def execute_result(*_args, **kwargs):
        command = kwargs["cmd"]
        if command == ["node", "scripts/apply-migrations.mjs"]:
            receipts = runtime._load_max_migration_receipts(tmp_path)
            assert receipts["drizzle/0003.sql"]["state"] == "intent"
            assert receipts["drizzle/0004.sql"]["state"] == "intent"
            assert (
                receipts["drizzle/0003.sql"]["execution_id"]
                == receipts["drizzle/0004.sql"]["execution_id"]
            )
            applied.update({"0003.sql", "0004.sql"})
            return {"exit_code": "0", "stdout": "ok", "stderr": ""}
        requested = set(json.loads(command[-1]))
        return _ledger_receipt(sorted(requested & applied))

    execute.side_effect = execute_result
    result = await runtime._hot_reload_locked(
        _payload({"drizzle/0003.sql": "SELECT 3;"}),
        "max-preview",
    )

    assert result["drizzle_exit_code"] == "0"
    assert runtime._load_max_migration_receipts(tmp_path) == {}
