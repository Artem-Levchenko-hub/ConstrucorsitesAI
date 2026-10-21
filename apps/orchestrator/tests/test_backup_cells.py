from __future__ import annotations

import json
import os
import stat
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from scripts import backup_cells
from scripts.backup_cells import CommandResult, StreamResult, _create_private_file, main


@pytest.fixture(autouse=True)
def _fast_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real readiness loop, drop only its (production) waiting."""
    monkeypatch.setattr(backup_cells, "_READY_TIMEOUT", 0.0)
    monkeypatch.setattr(backup_cells, "_READY_POLL_SECONDS", 0.0)

EDITOR = "11111111-1111-4111-8111-111111111111"
PRODUCTION = "22222222-2222-4222-8222-222222222222"
RESTORED = "33333333-3333-4333-8333-333333333333"
GENERATING = "44444444-4444-4444-8444-444444444444"
PROJECT = "99999999-9999-4999-8999-999999999999"
OWNER = "88888888-8888-4888-8888-888888888888"
RETIRED_PROJECT = "77777777-7777-4777-8777-777777777777"

CORE_PASSWORD = "core-secret-do-not-leak-0123456789"
PROJECT_PASSWORD = "project-secret-do-not-leak-98765"

POSTGRES_IMAGE = "postgres@sha256:" + "a" * 64
HELPER_IMAGE = "alpine@sha256:" + "b" * 64


def _hex(workspace_id: str) -> str:
    return UUID(workspace_id).hex


def core_volume(workspace_id: str) -> str:
    return f"omnia-cell-{_hex(workspace_id)}-postgres"


def project_volume(workspace_id: str) -> str:
    return f"omnia-machine-{_hex(workspace_id)}-app-postgres-data"


# --------------------------------------------------------------------------- #
# fake docker
# --------------------------------------------------------------------------- #


class FakeRunner:
    """Replaces DockerRunner: records every argv/env and scripts the replies."""

    def __init__(self) -> None:
        self.binary = "docker"
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[dict[str, str]] = []
        self.stdins: list[Path | None] = []
        self.volumes: list[str] = []
        self.containers: set[str] = set()
        self.holders: dict[str, list[str]] = {}
        self.table_count = 7
        self.dump_payload = b"PGDMP fake dump payload\n"
        self.dump_exit = 0
        self.dump_stderr = b""
        self.fail_tokens: set[str] = set()
        self.volume_records: dict[str, dict[str, Any]] = {}
        self.container_records: dict[str, dict[str, Any]] = {}

    # -- helpers used by the assertions ------------------------------------ #

    def matching(self, *tokens: str) -> list[tuple[str, ...]]:
        return [call for call in self.calls if _contains(call, tokens)]

    def _next_holders(self, volume: str) -> str:
        queue = self.holders.get(volume)
        if not queue:
            return ""
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def _failed(self, argv: tuple[str, ...]) -> bool:
        return any(token in argv for token in self.fail_tokens)

    # -- DockerRunner surface ---------------------------------------------- #

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        stdin_path: Path | None = None,
    ) -> CommandResult:
        argv = (self.binary, *args)
        self.calls.append(argv)
        self.envs.append(dict(env) if env else {})
        self.stdins.append(stdin_path)
        assert timeout > 0, "every docker call must carry an explicit timeout"
        if self._failed(argv):
            return CommandResult(argv, 1, b"", b"scripted failure")
        if args[0] == "run" and "--name" in args:
            self.containers.add(args[args.index("--name") + 1])
        if args[0] == "rm":
            self.containers.discard(args[-1])
        if tuple(args[:2]) == ("volume", "create"):
            self.volumes.append(args[-1])
        if tuple(args[:2]) == ("volume", "rm") and args[-1] in self.volumes:
            self.volumes.remove(args[-1])
        if tuple(args[:2]) == ("container", "ls"):
            return CommandResult(argv, 0, "\n".join(sorted(self.containers)).encode(), b"")
        if tuple(args[:2]) == ("volume", "ls"):
            return CommandResult(argv, 0, ("\n".join(self.volumes) + "\n").encode(), b"")
        if tuple(args[:2]) == ("volume", "inspect"):
            names = args[args.index("--format") + 2 :]
            rows = [
                self.volume_records.get(
                    name,
                    {
                        "name": name,
                        "labels": {},
                        "mountpoint": str(Path(__file__).parent.resolve()),
                    },
                )
                for name in names
            ]
            return CommandResult(argv, 0, "\n".join(json.dumps(row) for row in rows).encode(), b"")
        if tuple(args[:2]) == ("ps", "--all"):
            return CommandResult(argv, 0, "\n".join(self.container_records).encode(), b"")
        if tuple(args[:3]) == ("inspect", "--type", "container"):
            ids = args[args.index("--format") + 2 :]
            return CommandResult(
                argv,
                0,
                "\n".join(json.dumps(self.container_records[item]) for item in ids).encode(),
                b"",
            )
        if args[0] == "ps":
            volume = next(item for item in args if item.startswith("volume=")).removeprefix(
                "volume="
            )
            line = self._next_holders(volume)
            return CommandResult(argv, 0, f"{line}\n".encode() if line else b"", b"")
        if "psql" in argv and "-c" in argv:
            return CommandResult(argv, 0, f"{self.table_count}\n".encode(), b"")
        return CommandResult(argv, 0, b"", b"")

    def run_to_file(
        self,
        args: Sequence[str],
        *,
        path: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> StreamResult:
        argv = (self.binary, *args)
        self.calls.append(argv)
        self.envs.append(dict(env) if env else {})
        self.stdins.append(None)
        assert timeout > 0, "every docker call must carry an explicit timeout"
        if self._failed(argv) or self.dump_exit:
            return StreamResult(argv, self.dump_exit or 1, 0, "", self.dump_stderr)
        with os.fdopen(_create_private_file(path), "wb") as handle:
            handle.write(self.dump_payload)
        import hashlib

        return StreamResult(
            argv,
            0,
            len(self.dump_payload),
            hashlib.sha256(self.dump_payload).hexdigest(),
            b"",
        )

    def pipe(
        self,
        producer: Sequence[str],
        consumer: Sequence[str],
        *,
        timeout: float,
    ) -> tuple[CommandResult, CommandResult]:
        first = (self.binary, *producer)
        second = (self.binary, *consumer)
        self.calls.extend((first, second))
        self.envs.extend(({}, {}))
        self.stdins.extend((None, None))
        assert timeout > 0, "every docker call must carry an explicit timeout"
        code = 1 if self._failed(first) or self._failed(second) else 0
        return (
            CommandResult(first, code, b"", b""),
            CommandResult(second, code, b"", b""),
        )


def _contains(call: Sequence[str], tokens: Sequence[str]) -> bool:
    return all(token in call for token in tokens)


# --------------------------------------------------------------------------- #
# state-root fixture
# --------------------------------------------------------------------------- #


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


def _workspace_payload(workspace_id: str, *, generation: str | None = None) -> dict[str, Any]:
    stem = _hex(workspace_id)
    return {
        "version": 1,
        "workspace": {
            "workspace_id": workspace_id,
            "project_id": PROJECT,
            "owner_id": OWNER,
            "profile_version": "docker-owner-cell-resources-v2",
            "phase": "completed",
            "bundle_state": "retained",
            "fencing_epoch": 4,
            "active_generation_run_id": generation,
            "active_generation_fencing_epoch": None,
            "last_operation_id": None,
            "provider_ref": "docker-owner-canary:live",
            "resource_names": {
                "workspace_id": workspace_id,
                "namespace": "prod",
                "internal_network": f"omnia-cell-{stem}-internal",
                "egress_network": f"omnia-cell-{stem}-egress",
                "workspace_volume": f"omnia-cell-{stem}-workspace",
                "agent_home_volume": f"omnia-cell-{stem}-agent-home",
                "postgres_volume": f"omnia-cell-{stem}-postgres",
                "redis_volume": f"omnia-cell-{stem}-redis",
                "checkpoint_volume": f"omnia-cell-{stem}-checkpoints",
                "postgres_container": f"omnia-cell-{stem}-postgres",
                "redis_container": f"omnia-cell-{stem}-redis",
            },
            "operations": [],
        },
    }


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    for workspace_id in (EDITOR, PRODUCTION, RESTORED):
        _write_json(
            root / "project-cells" / f"{workspace_id}.json", _workspace_payload(workspace_id)
        )
    _write_json(
        root / "project-cells" / f"{GENERATING}.json",
        _workspace_payload(GENERATING, generation="55555555-5555-4555-8555-555555555555"),
    )
    for workspace_id in (EDITOR, PRODUCTION, RESTORED, GENERATING):
        _write_json(
            root / "project-cells-credentials" / f"{workspace_id}.json",
            {"postgres_password": CORE_PASSWORD},
        )
        _write_json(
            root / "project-machines" / "project-postgres-secrets" / f"{workspace_id}.json",
            {"postgres_password": PROJECT_PASSWORD},
        )
    _write_json(
        root / "project-machines" / RESTORED / "docker.json",
        {
            "services": {},
            "active_database_volume": f"omnia-machine-{_hex(RESTORED)}-db-{'c' * 32}",
            "active_code_volume": f"omnia-machine-{_hex(RESTORED)}-code-{'d' * 32}",
        },
    )
    _write_json(
        root / "cell-publications" / PROJECT / "publication.json",
        {
            "project_id": PROJECT,
            "history": [],
            "active_release": None,
            "source_workspace_id": EDITOR,
            "production_workspace_id": PRODUCTION,
        },
    )
    # Payloads the archive must leave out, and one it must keep.
    _write_json(root / "project-machines" / "artifacts" / EDITOR / "meta.json", {"huge": True})
    (root / "project-machines" / "artifacts" / EDITOR / f"{'a' * 32}.tar").write_bytes(b"x" * 64)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "locks" / "workspace.lock").write_bytes(b"")
    (root / "project-cells-capacity-reservations").mkdir(parents=True, exist_ok=True)
    (root / "project-cells-capacity-reservations" / "r.json").write_text("{}", encoding="utf-8")
    _write_json(root / "deploy-runs.json", {"runs": []})
    os.chmod(root, 0o700)
    return root


def _all_volumes() -> list[str]:
    return [
        core_volume(EDITOR),
        project_volume(EDITOR),
        core_volume(PRODUCTION),
        project_volume(PRODUCTION),
        core_volume(RESTORED),
        project_volume(RESTORED),
        f"omnia-machine-{_hex(RESTORED)}-db-{'c' * 32}",
        core_volume(GENERATING),
        f"omnia-machine-{_hex(EDITOR)}-data-uploads",
        f"omnia-machine-{_hex(EDITOR)}-data-omnia-pnpm-store",
        f"omnia-machine-{_hex(EDITOR)}-data-omnia-corepack",
        f"omnia-machine-{_hex(EDITOR)}-data-omnia-next-{'e' * 24}",
    ]


def _runner(*, running: Sequence[str] = ()) -> FakeRunner:
    runner = FakeRunner()
    runner.volumes = _all_volumes()
    for volume in running:
        container = volume if volume.startswith("omnia-cell-") else _project_container(volume)
        runner.holders[volume] = [f"abc123def456 {container}"]
    return runner


def _project_container(volume: str) -> str:
    return volume.split("-app-postgres-data")[0].split("-db-")[0] + "-project-postgres"


def _backup(state_root: Path, out: Path, runner: FakeRunner) -> int:
    return main(
        [
            "backup",
            "--state-root",
            str(state_root),
            "--out",
            str(out),
            "--postgres-image",
            POSTGRES_IMAGE,
            "--helper-image",
            HELPER_IMAGE,
        ],
        runner=runner,  # type: ignore[arg-type]
    )


def _settle_generating_cell(state_root: Path) -> None:
    """Drop the in-flight generation so a whole run can legitimately be green."""
    _write_json(
        state_root / "project-cells" / f"{GENERATING}.json", _workspace_payload(GENERATING)
    )


def _manifest(out: Path) -> dict[str, Any]:
    return json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))


def _workspace(manifest: Mapping[str, Any], workspace_id: str) -> dict[str, Any]:
    return next(
        item for item in manifest["workspaces"] if item["workspace_id"] == workspace_id
    )


def _database(manifest: Mapping[str, Any], workspace_id: str, kind: str) -> dict[str, Any]:
    return next(
        item for item in _workspace(manifest, workspace_id)["databases"] if item["kind"] == kind
    )


def _removal_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    removals = []
    for call in runner.calls:
        verb = call[1:3]
        if verb[:1] in (("stop",), ("rm",), ("kill",)) or verb == ("volume", "rm"):
            removals.append(call)
    return removals


def _assert_only_scratch_touched(runner: FakeRunner) -> None:
    for call in _removal_calls(runner):
        target = call[-1]
        assert target.startswith("omnia-backup-scratch-"), f"removal touched {target!r}: {call}"


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #


def test_enumeration_covers_published_production_cells_and_reports_orphans(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    runner.volumes.append(f"omnia-cell-{'f' * 32}-postgres")
    _backup(state_root, tmp_path / "out", runner)
    manifest = _manifest(tmp_path / "out")

    roles = {item["workspace_id"]: item["role"] for item in manifest["workspaces"]}
    assert roles[PRODUCTION] == "production"
    assert roles[EDITOR] == "editor"
    assert _workspace(manifest, EDITOR)["project_id"] == PROJECT
    orphans = {item.get("volume"): item["reason"] for item in manifest["orphans"]}
    assert orphans[f"omnia-cell-{'f' * 32}-postgres"] == "volume_without_state"
    # The superseded default volume of a restored cell is real data we never touch.
    assert orphans[project_volume(RESTORED)] == "inactive_database_volume"
    caches = [item for item in manifest["orphans"] if "corepack" in str(item.get("volume"))]
    assert caches == [], "rebuildable caches must not be reported or archived"
    _assert_only_scratch_touched(runner)


def test_unknown_retained_volume_prevents_complete_backup(state_root: Path, tmp_path: Path) -> None:
    _settle_generating_cell(state_root)
    runner = _runner()
    runner.volumes.append(f"omnia-cell-{'f' * 32}-postgres")
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert _verify(tmp_path / "out", FakeRunner()) == 2


def test_missing_active_database_is_not_hidden_as_an_inactive_copy(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    runner = _runner()
    runner.volumes.remove(f"omnia-machine-{_hex(RESTORED)}-db-{'c' * 32}")
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert any(
        item["reason"] == "active_database_volume_missing"
        for item in _manifest(tmp_path / "out")["orphans"]
    )


def test_corrupt_active_database_registry_cannot_fall_back_to_legacy_volume(
    state_root: Path, tmp_path: Path
) -> None:
    metadata = state_root / "project-machines" / RESTORED / "docker.json"
    metadata.write_text('{"active_database_volume":"first","active_database_volume":"second"}')
    assert _backup(state_root, tmp_path / "out", _runner()) == 1


def test_declared_retained_core_volume_missing_prevents_complete_backup(
    state_root: Path, tmp_path: Path,
) -> None:
    _settle_generating_cell(state_root)
    runner = _runner()
    runner.volumes.remove(core_volume(EDITOR))
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert {
        "workspace_id": EDITOR, "volume": core_volume(EDITOR),
        "reason": "declared_core_volume_missing",
    } in _manifest(tmp_path / "out")["coverage"]["issues"]


@pytest.mark.parametrize("metadata", [
    {"project_database_role_protocol": 1}, {"manifest": {"version": 1}},
])
def test_materialized_project_database_missing_prevents_complete_backup(
    state_root: Path, tmp_path: Path, metadata: dict[str, Any],
) -> None:
    _settle_generating_cell(state_root)
    _write_json(state_root / "project-machines" / EDITOR / "docker.json", metadata)
    runner = _runner()
    runner.volumes.remove(project_volume(EDITOR))
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert {
        "workspace_id": EDITOR, "volume": project_volume(EDITOR),
        "reason": "active_database_volume_missing",
    } in _manifest(tmp_path / "out")["coverage"]["issues"]


def test_pre_materialization_core_only_cell_remains_complete(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    runner = _runner()
    # GENERATING has only its declared core database and no machine materialization record.
    assert project_volume(GENERATING) not in runner.volumes
    assert _backup(state_root, tmp_path / "out", runner) == 0
    assert [entry["kind"] for entry in _workspace(
        _manifest(tmp_path / "out"), GENERATING,
    )["databases"]] == ["core"]


def _terminal_destroy(root: Path, workspace_id: str, *, stale_lease: bool = False) -> Path:
    payload = _workspace_payload(workspace_id)
    workspace = payload["workspace"]
    workspace["project_id"] = RETIRED_PROJECT
    operation = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    generation = "55555555-5555-4555-8555-555555555555"
    workspace["last_operation_id"] = operation
    workspace["operations"] = [
        {
            "operation_id": operation,
            "kind": "destroy",
            "status": "completed",
            "phase": "completed",
            "fencing_epoch": 4,
            "generation_run_id": None,
        }
    ]
    if stale_lease:
        workspace["active_generation_run_id"] = generation
        workspace["active_generation_fencing_epoch"] = 3
        workspace["operations"].insert(
            0,
            {
                "operation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "kind": "reconcile",
                "status": "completed",
                "phase": "completed",
                "fencing_epoch": 3,
                "generation_run_id": generation,
            },
        )
    path = root / "project-cells" / f"{workspace_id}.json"
    _write_json(path, payload)
    return path


def _census(*, present: bool = False) -> str:
    return json.dumps(
        {
            "version": 1,
            "scope": "complete_platform_project_workspace_census",
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "projects": [PROJECT, RETIRED_PROJECT] if present else [PROJECT],
            "workspaces": [{"id": GENERATING, "project_id": RETIRED_PROJECT}] if present else [],
        }
    )


def test_completed_destroy_supersedes_only_older_matched_lease_and_keeps_real_dumps(
    state_root: Path,
    tmp_path: Path,
) -> None:
    journal = _terminal_destroy(state_root, GENERATING, stale_lease=True)
    original = journal.read_bytes()
    runner = _runner()
    assert _backup(state_root, tmp_path / "out", runner) == 0
    assert _database(_manifest(tmp_path / "out"), GENERATING, "core")["status"] == "ok"
    assert journal.read_bytes() == original


def test_retired_storage_absence_requires_census_and_has_verifiable_separate_receipt(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _terminal_destroy(state_root, GENERATING)
    original = journal.read_bytes()
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    assert _backup(state_root, tmp_path / "out", runner) == 0
    manifest = _manifest(tmp_path / "out")
    receipts = manifest["coverage"]["retired_no_local_storage"]
    assert [row["workspace_id"] for row in receipts] == [GENERATING]
    assert receipts[0]["operation_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert all(row["workspace_id"] != GENERATING for row in manifest["workspaces"])
    assert _verify(tmp_path / "out", FakeRunner()) == 0
    assert journal.read_bytes() == original
    receipts[0]["fencing_epoch"] = 9
    _write_json(tmp_path / "out" / "MANIFEST.json", manifest)
    assert _verify(tmp_path / "out", FakeRunner()) == 1


@pytest.mark.parametrize("problem", ["missing", "current_project", "stale", "malformed"])
def test_terminal_destroy_never_hides_missing_storage_without_current_absence_authority(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    _terminal_destroy(state_root, GENERATING)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    encoded = _census(present=problem == "current_project")
    if problem == "stale":
        payload = json.loads(encoded)
        payload["observed_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        encoded = json.dumps(payload)
    elif problem == "malformed":
        encoded = '{"version":1}'
    if problem != "missing":
        monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", encoded)
    assert _backup(state_root, tmp_path / "out", runner) != 0
    if problem in {"missing", "current_project"}:
        assert any(
            row.get("reason") == "declared_core_volume_missing"
            for row in _manifest(tmp_path / "out")["coverage"]["issues"]
        )


@pytest.mark.parametrize(
    "problem",
    [
        "equal_lease",
        "newer_lease",
        "unmatched_run",
        "unmatched_epoch",
        "pending_destroy",
        "indeterminate_destroy",
        "wrong_last_operation",
        "old_destroy",
        "conflicting_epoch",
        "boolean_epoch",
        "duplicate_operation",
        "invalid_resource_identity",
    ],
)
def test_terminal_lease_exception_is_exact_and_fail_closed(
    state_root: Path,
    tmp_path: Path,
    problem: str,
) -> None:
    path = _terminal_destroy(state_root, GENERATING, stale_lease=True)
    payload = json.loads(path.read_bytes())
    workspace = payload["workspace"]
    operation = workspace["operations"][-1]
    if problem == "equal_lease":
        workspace["active_generation_fencing_epoch"] = 4
    elif problem == "newer_lease":
        workspace["active_generation_fencing_epoch"] = 5
    elif problem == "unmatched_run":
        workspace["active_generation_run_id"] = OWNER
    elif problem == "unmatched_epoch":
        workspace["operations"][0]["fencing_epoch"] = 2
    elif problem == "pending_destroy":
        operation["status"] = "running"
    elif problem == "indeterminate_destroy":
        operation["status"] = "indeterminate"
    elif problem == "wrong_last_operation":
        workspace["last_operation_id"] = OWNER
    elif problem == "old_destroy":
        operation["fencing_epoch"] = 2
    elif problem == "conflicting_epoch":
        workspace["operations"][0]["fencing_epoch"] = 4
    elif problem == "boolean_epoch":
        operation["fencing_epoch"] = True
    elif problem == "duplicate_operation":
        workspace["operations"].append(dict(operation))
    elif problem == "invalid_resource_identity":
        workspace["resource_names"]["workspace_id"] = EDITOR
    _write_json(path, payload)
    assert _backup(state_root, tmp_path / "out", _runner()) == 2
    assert (
        _database(_manifest(tmp_path / "out"), GENERATING, "core")["status"]
        == "deferred_active_generation"
    )


def _docker_container(
    *,
    labels: dict[str, str] | None = None,
    mounts: list[dict[str, Any]] | None = None,
    state: str = "running",
) -> dict[str, Any]:
    return {
        "id": "a" * 64,
        "name": "/opaque-container",
        "state": state,
        "labels": labels or {},
        "mounts": mounts or [],
    }


@pytest.mark.parametrize(
    "remaining", ["workspace", "project", "restoration", "data", "labeled", "container"]
)
def test_retired_absence_excludes_no_owned_resources(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remaining: str,
) -> None:
    _terminal_destroy(state_root, GENERATING)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    stem = _hex(GENERATING)
    if remaining == "container":
        row = _docker_container(labels={"omnia.workspace_id": GENERATING}, state="exited")
        runner.container_records[row["id"]] = row
    else:
        name = {
            "workspace": f"omnia-cell-{stem}-workspace",
            "project": project_volume(GENERATING),
            "restoration": f"omnia-machine-{stem}-db-{'d' * 32}",
            "data": f"omnia-machine-{stem}-data-uploads",
            "labeled": "opaque-retained-volume",
        }[remaining]
        runner.volumes.append(name)
        if remaining == "labeled":
            runner.volume_records[name] = {
                "name": name,
                "labels": {"omnia.project_id": RETIRED_PROJECT},
                "mountpoint": str(tmp_path),
            }
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert _manifest(tmp_path / "out")["coverage"]["retired_no_local_storage"] == []


@pytest.mark.parametrize(
    "mount_type", ["volume", "bind", "bind_parent", "bind_child", "bind_alias"]
)
def test_foreign_mounted_writer_prevents_stale_lease_override(
    state_root: Path,
    tmp_path: Path,
    mount_type: str,
) -> None:
    _terminal_destroy(state_root, GENERATING, stale_lease=True)
    runner = _runner()
    data = tmp_path / "owned-pg" / "data"
    data.mkdir(parents=True)
    source = data
    if mount_type == "bind_parent":
        source = data.parent
    elif mount_type == "bind_child":
        source = data / "base"
        source.mkdir()
    elif mount_type == "bind_alias":
        alias = tmp_path / "alias"
        try:
            alias.symlink_to(data, target_is_directory=True)
        except OSError:
            pytest.skip("symlink privilege unavailable")
        source = alias
    runner.volume_records[core_volume(GENERATING)] = {
        "name": core_volume(GENERATING),
        "labels": {},
        "mountpoint": str(data),
    }
    mount = {"Type": "volume" if mount_type == "volume" else "bind", "Source": str(source)}
    if mount_type == "volume":
        mount["Name"] = core_volume(GENERATING)
    row = _docker_container(mounts=[mount])
    runner.container_records[row["id"]] = row
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert (
        _database(_manifest(tmp_path / "out"), GENERATING, "core")["status"]
        == "deferred_active_generation"
    )


@pytest.mark.parametrize(
    "problem", ["query_failure", "inspect_failure", "partial_inspect", "unknown_mount"]
)
def test_terminal_decision_never_uses_partial_docker_observation(
    state_root: Path,
    tmp_path: Path,
    problem: str,
) -> None:
    _terminal_destroy(state_root, GENERATING, stale_lease=True)
    runner = _runner()
    if problem == "query_failure":
        runner.fail_tokens.add("--all")
    elif problem == "inspect_failure":
        runner.fail_tokens.add("inspect")
    elif problem == "partial_inspect":
        runner.volume_records[core_volume(GENERATING)] = {
            "name": "unexpected-volume",
            "labels": {},
            "mountpoint": str(tmp_path),
        }
    else:
        row = _docker_container(mounts=[{"Type": "unknown"}])
        runner.container_records[row["id"]] = row
    assert _backup(state_root, tmp_path / "out", runner) == 1


@pytest.mark.parametrize("change", ["journal", "docker", "publication"])
def test_terminal_exclusion_changed_authority_never_promotes_complete_backup(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    path = _terminal_destroy(state_root, GENERATING)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    archive = backup_cells.archive_state

    def changed(root: Path, destination: Path) -> dict[str, Any]:
        if change == "journal":
            payload = json.loads(path.read_bytes())
            payload["workspace"]["phase"] = "indeterminate"
            _write_json(path, payload)
        elif change == "docker":
            runner.volumes.append(core_volume(GENERATING))
        else:
            _write_json(
                root / "cell-publications" / RETIRED_PROJECT / "publication.json",
                {
                    "project_id": RETIRED_PROJECT,
                    "production_workspace_id": GENERATING,
                },
            )
        return archive(root, destination)

    monkeypatch.setattr(backup_cells, "archive_state", changed)
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert {"reason": "inventory_changed_during_backup"} in _manifest(tmp_path / "out")["coverage"][
        "issues"
    ]


@pytest.mark.parametrize(
    "problem", ["duplicates", "orphan_workspace", "future", "boolean", "wrong_scope", "offset"]
)
def test_platform_census_is_complete_typed_and_fresh(problem: str) -> None:
    payload = json.loads(_census())
    if problem == "duplicates":
        payload["projects"] *= 2
    elif problem == "orphan_workspace":
        payload["workspaces"] = [{"id": GENERATING, "project_id": RETIRED_PROJECT}]
    elif problem == "future":
        payload["observed_at"] = (
            (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        )
    elif problem == "boolean":
        payload["version"] = True
    elif problem == "wrong_scope":
        payload["scope"] = "owner_filtered_projects"
    else:
        payload["observed_at"] = datetime.now(UTC).isoformat()
    with pytest.raises(backup_cells.BackupError, match="census"):
        backup_cells.parse_platform_census(json.dumps(payload))


@pytest.mark.parametrize(
    "field", ["journal_sha256", "platform_census_sha256", "docker_observation", "operation_id"]
)
def test_retired_receipt_tampering_is_rejected_before_any_restore(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    _terminal_destroy(state_root, GENERATING)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    out = tmp_path / "out"
    assert _backup(state_root, out, runner) == 0
    manifest = _manifest(out)
    manifest["coverage"]["retired_no_local_storage"][0][field] = "forged"
    _write_json(out / "MANIFEST.json", manifest)
    verify_runner = FakeRunner()
    assert _verify(out, verify_runner) == 1
    assert verify_runner.calls == []


@pytest.mark.parametrize(
    "authority",
    [
        "source_publication",
        "external_publication",
        "bad_metadata",
        "foreign_metadata",
        "wrong_workspace_census",
    ],
)
def test_retired_exclusion_requires_no_other_storage_authority(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
) -> None:
    _terminal_destroy(state_root, GENERATING)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    encoded = _census()
    if authority in {"source_publication", "external_publication"}:
        payload: dict[str, Any] = {"project_id": RETIRED_PROJECT, "source_workspace_id": GENERATING}
        if authority == "external_publication":
            payload.update(
                {
                    "production_workspace_id": GENERATING,
                    "active_release": {
                        "placement": {
                            "backend": "kubernetes",
                            "namespace": f"app-{RETIRED_PROJECT}",
                        },
                    },
                }
            )
        _write_json(
            state_root / "cell-publications" / RETIRED_PROJECT / "publication.json", payload
        )
    elif authority == "wrong_workspace_census":
        census = json.loads(encoded)
        census["workspaces"].append({"id": GENERATING, "project_id": PROJECT})
        encoded = json.dumps(census)
    else:
        payload = (
            {"project_database_role_protocol": True}
            if authority == "bad_metadata"
            else {
                "active_code_volume": f"omnia-machine-{_hex(EDITOR)}-code-{'a' * 32}",
            }
        )
        _write_json(state_root / "project-machines" / GENERATING / "docker.json", payload)
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", encoded)
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert _manifest(tmp_path / "out")["coverage"]["retired_no_local_storage"] == []


def _checkpoint_reference(workspace_id: str) -> dict[str, Any]:
    stem = _hex(workspace_id)
    return {
        "workspace_id": workspace_id,
        "image_id": "sha256:" + "a" * 64,
        "artifact_ref": "a" * 32 + ".tar",
        "sha256": "b" * 64,
        "size": 123,
        "base_image": HELPER_IMAGE,
        "manifest_digest": "c" * 64,
        "volumes": [
            {"name": name, "artifact_ref": "d" * 32 + ".tar", "sha256": "e" * 64, "size": 123}
            for name in (
                f"omnia-cell-{stem}-workspace",
                f"omnia-machine-{stem}-home",
                project_volume(workspace_id),
            )
        ],
    }


def test_retired_materialized_checkpoint_accepts_exact_own_cell_workspace_volume(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_orchestrator.services.machine_environment import MachineEnvironmentRef

    _terminal_destroy(state_root, GENERATING)
    reference = _checkpoint_reference(GENERATING)
    assert MachineEnvironmentRef.model_validate(reference).workspace_id == UUID(GENERATING)
    _write_json(
        state_root / "project-machines" / GENERATING / "docker.json",
        {
            "project_database_role_protocol": 1,
            "active_database_volume": project_volume(GENERATING),
            "environment_ref": reference,
        },
    )
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    out = tmp_path / "out"
    assert _backup(state_root, out, runner) == 0
    assert [
        row["workspace_id"] for row in _manifest(out)["coverage"]["retired_no_local_storage"]
    ] == [GENERATING]
    assert _verify(out, FakeRunner()) == 0


@pytest.mark.parametrize(
    "problem", ["foreign_cell_workspace", "own_cell_postgres", "foreign_reference_workspace"]
)
def test_checkpoint_cannot_expand_retired_storage_identity(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    _terminal_destroy(state_root, GENERATING)
    reference = _checkpoint_reference(GENERATING)
    if problem == "foreign_cell_workspace":
        reference["volumes"][0]["name"] = f"omnia-cell-{_hex(EDITOR)}-workspace"
    elif problem == "own_cell_postgres":
        reference["volumes"][0]["name"] = core_volume(GENERATING)
    else:
        reference["workspace_id"] = EDITOR
    _write_json(
        state_root / "project-machines" / GENERATING / "docker.json", {"environment_ref": reference}
    )
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    assert _backup(state_root, tmp_path / "out", runner) == 2


def test_recorded_lease_receipt_can_be_verified_offline(
    state_root: Path,
    tmp_path: Path,
) -> None:
    _terminal_destroy(state_root, GENERATING, stale_lease=True)
    out = tmp_path / "out"
    assert _backup(state_root, out, _runner()) == 0
    assert _verify(out, FakeRunner()) == 0


def test_unknown_journal_version_cannot_authorize_terminal_exclusion(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _terminal_destroy(state_root, GENERATING)
    payload = json.loads(journal.read_bytes())
    payload["version"] = True
    _write_json(journal, payload)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    assert _backup(state_root, tmp_path / "out", runner) == 1


def test_old_valid_retired_receipt_verifies_against_capture_time_not_wall_clock(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _terminal_destroy(state_root, GENERATING)
    runner = _runner()
    runner.volumes.remove(core_volume(GENERATING))
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", _census())
    out = tmp_path / "out"
    assert _backup(state_root, out, runner) == 0

    class FutureDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime.now(tz) + timedelta(days=30)

    monkeypatch.setattr(backup_cells, "datetime", FutureDatetime)
    assert _verify(out, FakeRunner()) == 0


def test_explicit_privileged_resolver_uses_fixed_metadata_only_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def execute(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, (str(tmp_path) + "\n").encode(), b"")

    monkeypatch.setattr(backup_cells.subprocess, "run", execute)
    assert backup_cells._resolve_docker_source(str(tmp_path), with_sudo=True) == str(tmp_path)
    assert calls[0][0] == [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/realpath",
        "-e",
        "--",
        str(tmp_path),
    ]
    assert calls[0][1]["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert calls[0][1]["timeout"] > 0
    assert "shell" not in calls[0][1]


@pytest.mark.parametrize(
    "problem", ["denied", "multiline", "relative", "missing_newline", "timeout"]
)
def test_privileged_resolver_failure_never_leaks_or_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    secret = "SECRET-DO-NOT-LOG"

    def execute(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if problem == "timeout":
            raise subprocess.TimeoutExpired(argv, 60, output=secret.encode())
        output = (str(tmp_path) + "\n").encode()
        if problem == "multiline":
            output += secret.encode() + b"\n"
        elif problem == "relative":
            output = b"relative\n"
        elif problem == "missing_newline":
            output = str(tmp_path).encode()
        return subprocess.CompletedProcess(
            argv, 1 if problem == "denied" else 0, output, secret.encode()
        )

    monkeypatch.setattr(backup_cells.subprocess, "run", execute)
    with pytest.raises(backup_cells.BackupError, match="resolution is unconfirmed") as failure:
        backup_cells._resolve_docker_source(str(tmp_path), with_sudo=True)
    assert secret not in str(failure.value)


def test_native_path_failure_does_not_implicitly_enable_privilege(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("denied")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("native mode must not invoke sudo")

    monkeypatch.setattr(Path, "resolve", denied)
    monkeypatch.setattr(backup_cells.subprocess, "run", forbidden)
    with pytest.raises(backup_cells.BackupError, match="inaccessible"):
        backup_cells._resolve_docker_source(str(tmp_path), with_sudo=False)


def test_publication_identity_directory_is_archived_without_becoming_a_fake_project(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    _write_json(
        state_root / "cell-publications" / "identities" / f"{PRODUCTION}.json",
        {"project_id": PROJECT},
    )
    assert _backup(state_root, tmp_path / "out", _runner()) == 0


@pytest.mark.skipif(os.name == "nt", reason="canonical private file modes require POSIX")
def test_publication_service_artifacts_are_validated_and_archived_not_journals(
    state_root: Path, tmp_path: Path,
) -> None:
    _settle_generating_cell(state_root)
    root = state_root / "cell-publications"
    (root / "locks").mkdir(mode=0o700)
    (root / "identities").mkdir(mode=0o700)
    key = root / "fingerprint.key"
    key.write_bytes(b"x" * 32)
    key.chmod(0o600)
    assert backup_cells.external_publications(state_root) == []
    assert _backup(state_root, tmp_path / "out", _runner()) == 0
    with tarfile.open(tmp_path / "out" / "state.tar.gz") as archive:
        assert "state/cell-publications/fingerprint.key" in archive.getnames()


@pytest.mark.parametrize(
    "fault", ["unknown", "wrong_directory_type", "key_directory", "key_length"],
)
def test_publication_shared_entry_names_cannot_hide_unknown_or_invalid_paths(
    state_root: Path, fault: str,
) -> None:
    root = state_root / "cell-publications"
    if fault == "unknown":
        (root / "unrecognized").mkdir()
    elif fault == "wrong_directory_type":
        (root / "locks").write_text("not a directory")
    elif fault == "key_directory":
        (root / "fingerprint.key").mkdir()
    else:
        key = root / "fingerprint.key"
        key.write_bytes(b"not a 32-byte key")
        key.chmod(0o600)
    with pytest.raises(backup_cells.BackupError, match="publication"):
        backup_cells.external_publications(state_root)


@pytest.mark.skipif(os.name == "nt", reason="canonical private file modes require POSIX")
def test_publication_fingerprint_key_must_remain_private(state_root: Path) -> None:
    key = state_root / "cell-publications" / "fingerprint.key"
    key.write_bytes(b"x" * 32)
    key.chmod(0o644)
    with pytest.raises(backup_cells.BackupError, match="publication fingerprint"):
        backup_cells.external_publications(state_root)


def test_full_cluster_restore_keeps_standard_databases_with_strict_sql(tmp_path: Path) -> None:
    path = tmp_path / f"{EDITOR}-project.sql"
    path.write_bytes(b"-- synthetic pg_dumpall\n\\connect postgres\n")
    runner = FakeRunner()
    runner.fail_tokens.add("DROP DATABASE postgres")
    backup = backup_cells.CellBackup(
        runner=runner, out_dir=tmp_path, postgres_image=POSTGRES_IMAGE,
        helper_image=HELPER_IMAGE, redactor=backup_cells.SecretRedactor(),
    )
    result = backup_cells._restore_entry(
        backup, path, "project", {"format": "plain", "tables": runner.table_count},
    )
    assert result["status"] == "ok", result
    restores = runner.matching("exec", "psql", "-f", "ON_ERROR_STOP=1")
    assert len(restores) == 1
    assert restores[0][restores[0].index("-U") + 1].startswith("omnia_backup_")
    starts = runner.matching("run", "--detach", "--entrypoint", "sh")
    assert len(starts) == 1 and "initdb --username=postgres " in starts[0][-1]
    assert not any("DROP DATABASE postgres" in call for call in runner.calls)


def test_registry_change_while_dumping_prevents_complete_coverage(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    runner = _runner()
    original = runner.run_to_file

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        _write_json(
            state_root / "project-machines" / EDITOR / "docker.json",
            {"changed_during_backup": True},
        )
        return result

    runner.run_to_file = changed
    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert {"reason": "inventory_changed_during_backup"} in _manifest(tmp_path / "out")["coverage"][
        "issues"
    ]


def test_build_caches_are_skipped_but_business_data_volumes_are_archived(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    _backup(state_root, tmp_path / "out", runner)
    manifest = _manifest(tmp_path / "out")

    archived = [item["volume"] for item in _workspace(manifest, EDITOR)["data_volumes"]]
    assert archived == [f"omnia-machine-{_hex(EDITOR)}-data-uploads"]
    entry = _workspace(manifest, EDITOR)["data_volumes"][0]
    assert entry["status"] == "ok"
    assert entry["file"] == f"{EDITOR}-data-uploads.tar.gz"
    assert (tmp_path / "out" / entry["file"]).is_file()


def test_restoration_activated_database_volume_is_chosen_from_docker_json(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    _backup(state_root, tmp_path / "out", runner)
    manifest = _manifest(tmp_path / "out")

    active = _database(manifest, RESTORED, "project")["volume"]
    assert active == f"omnia-machine-{_hex(RESTORED)}-db-{'c' * 32}"
    assert active in " ".join(" ".join(call) for call in runner.calls)


def test_active_database_volume_of_another_workspace_is_refused(
    state_root: Path, tmp_path: Path
) -> None:
    _write_json(
        state_root / "project-machines" / RESTORED / "docker.json",
        {"active_database_volume": f"omnia-machine-{_hex(EDITOR)}-db-{'c' * 32}"},
    )
    runner = _runner()
    assert _backup(state_root, tmp_path / "out", runner) == 1


# --------------------------------------------------------------------------- #
# dump methods
# --------------------------------------------------------------------------- #


def test_running_cell_is_dumped_with_exec_and_halted_cell_with_a_scratch_copy(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    runner = _runner(running=[core_volume(EDITOR), project_volume(EDITOR)])
    assert _backup(state_root, tmp_path / "out", runner) == 0
    manifest = _manifest(tmp_path / "out")

    assert manifest["totals"]["failed"] == 0
    assert _database(manifest, EDITOR, "core")["method"] == "exec"
    assert _database(manifest, EDITOR, "project")["method"] == "exec"
    assert _database(manifest, PRODUCTION, "core")["method"] == "scratch_copy"

    core_dump = runner.matching("exec", "pg_dump")[0]
    assert "-Fc" in core_dump and "--lock-wait-timeout=30s" in core_dump
    project_dump = runner.matching("exec", "pg_dumpall", "127.0.0.1")[0]
    assert project_dump[project_dump.index("-U") + 1] == "postgres"
    # The user's own volume is only ever mounted read-only on the copy path.
    copy = runner.matching("run", f"{core_volume(PRODUCTION)}:/source:ro")
    assert copy, "the halted cell must be copied through a read-only mount"
    mounts = [item for call in runner.calls for item in call if ":" in item]
    assert not [
        item
        for item in mounts
        if item.startswith(f"{core_volume(PRODUCTION)}:") and not item.endswith(":ro")
    ]
    _assert_only_scratch_touched(runner)


def test_scratch_server_is_networkless_and_serves_a_trust_hba_of_our_own(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    _backup(state_root, tmp_path / "out", runner)

    server = runner.matching("run", "--detach")[0]
    assert server[server.index("--network") + 1] == "none"
    assert server[server.index("--user") + 1] == "postgres"
    assert "--read-only" in server and "ALL" in server
    assert "--cpus" in server and "--pids-limit" in server
    script = server[-1]
    assert "hba_file=/tmp/omnia-backup-hba.conf" in script
    assert "local all all trust" in script
    assert "listen_addresses=" in script and "unix_socket_directories=/tmp" in script
    assert runner.matching("exec", "pg_isready"), "readiness must be proven before dumping"


def test_password_never_reaches_argv_logs_or_the_manifest(
    state_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _runner(running=[core_volume(EDITOR), project_volume(EDITOR)])
    runner.dump_exit = 1
    runner.dump_stderr = f'FATAL: password "{CORE_PASSWORD}" authentication failed'.encode()
    _backup(state_root, tmp_path / "out", runner)
    captured = capsys.readouterr()
    manifest_text = (tmp_path / "out" / "MANIFEST.json").read_text(encoding="utf-8")

    for call in runner.calls:
        for item in call:
            assert CORE_PASSWORD not in item
            assert PROJECT_PASSWORD not in item
    for secret in (CORE_PASSWORD, PROJECT_PASSWORD):
        assert secret not in captured.out
        assert secret not in captured.err
        assert secret not in manifest_text
    assert "***" in manifest_text, "a leaked secret must be redacted, not merely dropped"
    # It travels as an inherited environment variable of that one child process.
    exec_calls = [
        (call, env) for call, env in zip(runner.calls, runner.envs, strict=True) if "-e" in call
    ]
    passwords = [env.get("PGPASSWORD") for _call, env in exec_calls if env]
    assert CORE_PASSWORD in passwords
    for call, _env in exec_calls:
        assert call[call.index("-e") + 1] == "PGPASSWORD"


def test_busy_volume_is_retried_once_then_reported_partial(
    state_root: Path, tmp_path: Path
) -> None:
    volume = core_volume(EDITOR)
    runner = _runner()
    # discovery empty, then empty before each copy and occupied after each copy.
    runner.holders[volume] = ["", "", "abc123def456", "", "abc123def456"]

    assert _backup(state_root, tmp_path / "out", runner) == 2
    manifest = _manifest(tmp_path / "out")
    entry = _database(manifest, EDITOR, "core")
    assert entry["status"] == "busy"
    assert entry["method"] == "scratch_copy"
    assert len(runner.matching("run", f"{core_volume(EDITOR)}:/source:ro")) == 2, "copied twice"
    # A busy volume never reaches a scratch server: only the five other halted
    # databases of the fixture do.
    assert len(runner.matching("run", "--detach")) == 5
    assert not (tmp_path / "out" / f"{EDITOR}-core.dump").exists()
    _assert_only_scratch_touched(runner)


def test_active_generation_defers_the_cell_without_calling_docker(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    assert _backup(state_root, tmp_path / "out", runner) == 2
    manifest = _manifest(tmp_path / "out")

    entry = _database(manifest, GENERATING, "core")
    assert entry["status"] == "deferred_active_generation"
    assert entry["file"] is None
    assert not runner.matching(core_volume(GENERATING))
    assert not [call for call in runner.calls if core_volume(GENERATING) in " ".join(call)]


def test_foreign_container_on_the_volume_is_reported_busy(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    runner.holders[core_volume(EDITOR)] = ["abc123def456 some-other-container"]

    assert _backup(state_root, tmp_path / "out", runner) == 2
    assert _database(_manifest(tmp_path / "out"), EDITOR, "core")["status"] == "busy"


# --------------------------------------------------------------------------- #
# scratch lifecycle
# --------------------------------------------------------------------------- #


def test_scratch_resources_are_removed_even_when_the_dump_fails(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner()
    runner.fail_tokens = {"pg_dumpall"}

    assert _backup(state_root, tmp_path / "out", runner) == 1
    manifest = _manifest(tmp_path / "out")
    assert _database(manifest, EDITOR, "core")["status"] == "failed"

    created = [call[-1] for call in runner.matching("volume", "create")]
    removed = [call[-1] for call in runner.matching("volume", "rm")]
    assert created and sorted(created) == sorted(removed)
    started = {call[call.index("--name") + 1] for call in runner.calls if "--name" in call}
    stopped = {call[-1] for call in runner.matching("rm", "--force")}
    assert started <= stopped, "every named scratch container is removed in finally"
    _assert_only_scratch_touched(runner)


def test_nothing_outside_the_scratch_prefix_is_ever_stopped_or_removed(
    state_root: Path, tmp_path: Path
) -> None:
    runner = _runner(running=[core_volume(EDITOR)])
    runner.fail_tokens = {"pg_isready"}
    _backup(state_root, tmp_path / "out", runner)

    removals = _removal_calls(runner)
    assert removals, "the test is meaningless without removals to inspect"
    for call in removals:
        assert call[0] == "docker"
        assert call[-1].startswith("omnia-backup-scratch-")
    cell_names = {
        core_volume(EDITOR),
        project_volume(EDITOR),
        f"omnia-cell-{_hex(EDITOR)}-postgres",
    }
    for call in removals:
        assert not cell_names & set(call)


# --------------------------------------------------------------------------- #
# state archive and manifest
# --------------------------------------------------------------------------- #


def test_state_archive_excludes_artifacts_locks_and_capacity_reservations(
    state_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    _backup(state_root, out, _runner())

    with tarfile.open(out / "state.tar.gz", "r:gz") as archive:
        names = archive.getnames()
    assert "state/project-cells" in names
    assert f"state/project-cells/{EDITOR}.json" in names
    assert "state/deploy-runs.json" in names
    for excluded in (
        "state/project-machines/artifacts",
        "state/locks",
        "state/project-cells-capacity-reservations",
    ):
        assert not [name for name in names if name == excluded or name.startswith(f"{excluded}/")]
    assert f"state/project-machines/project-postgres-secrets/{EDITOR}.json" in names


def test_manifest_schema_and_private_file_modes(state_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _backup(state_root, out, _runner(running=[core_volume(EDITOR)]))
    manifest = _manifest(out)

    assert manifest["schema_version"] == 1
    assert manifest["created_at"].endswith("Z")
    assert manifest["postgres_image"] == POSTGRES_IMAGE
    assert set(manifest) >= {"state_archive", "workspaces", "orphans", "totals"}
    assert manifest["totals"]["databases"] == sum(
        len(item["databases"]) for item in manifest["workspaces"]
    )
    entry = _database(manifest, EDITOR, "core")
    assert set(entry) == {
        "kind",
        "volume",
        "status",
        "method",
        "format",
        "file",
        "bytes",
        "sha256",
        "tables",
        "detail",
    }
    assert entry["tables"] == 7
    assert entry["bytes"] > 0 and len(entry["sha256"]) == 64
    assert manifest["state_archive"]["excluded"] == [
        "project-machines/artifacts",
        "locks",
        "project-cells-capacity-reservations",
    ]

    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for name in ("MANIFEST.json", "state.tar.gz", entry["file"]):
        assert stat.S_IMODE((out / name).stat().st_mode) == 0o600


def test_missing_image_configuration_fails_with_a_clear_error(
    state_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CELL_POSTGRES_IMAGE", raising=False)
    monkeypatch.delenv("CELL_BACKUP_IMAGE", raising=False)
    code = main(
        ["backup", "--state-root", str(state_root), "--out", str(tmp_path / "out")],
        runner=_runner(),  # type: ignore[arg-type]
    )
    assert code == 1
    assert "CELL_POSTGRES_IMAGE" in capsys.readouterr().out


def test_images_default_to_the_pinned_environment_variables(
    state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CELL_POSTGRES_IMAGE", POSTGRES_IMAGE)
    monkeypatch.setenv("CELL_BACKUP_IMAGE", HELPER_IMAGE)
    runner = _runner()
    main(
        ["backup", "--state-root", str(state_root), "--out", str(tmp_path / "out")],
        runner=runner,  # type: ignore[arg-type]
    )
    assert _manifest(tmp_path / "out")["helper_image"] == HELPER_IMAGE


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


def _verify(out: Path, runner: FakeRunner) -> int:
    return main(
        ["verify", "--backup", str(out), "--postgres-image", POSTGRES_IMAGE],
        runner=runner,  # type: ignore[arg-type]
    )


def test_verify_refuses_a_manifest_without_any_coverage_or_state_archive(tmp_path: Path) -> None:
    _write_json(tmp_path / "MANIFEST.json", {"schema_version": backup_cells.SCHEMA_VERSION})
    verifier = FakeRunner()
    assert _verify(tmp_path, verifier) == 1
    assert verifier.calls == [], "unconfirmed coverage must stop before creating scratch resources"


def test_backup_cannot_claim_complete_when_published_runtime_databases_are_uncovered(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    _write_json(root / "cell-publications" / PROJECT / "publication.json", {
        "project_id": PROJECT,
        "production_workspace_id": PRODUCTION,
        "active_release": {"placement": {"backend": "kubernetes", "namespace": f"app-{PROJECT}"}},
    })
    assert _backup(root, tmp_path / "out", FakeRunner()) != 0


def test_verify_never_treats_an_unknown_database_kind_as_checksum_only(tmp_path: Path) -> None:
    payload = tmp_path / "state.tar.gz"
    payload.write_bytes(b"not a database dump")
    digest, size = backup_cells._file_digest(payload)
    backup = backup_cells.CellBackup(
        runner=FakeRunner(), out_dir=tmp_path, postgres_image=POSTGRES_IMAGE,
        helper_image=HELPER_IMAGE, redactor=backup_cells.SecretRedactor(),
    )
    result = backup_cells._verify_entry(backup, tmp_path, {
        "kind": "published-project", "status": "ok", "file": payload.name,
        "sha256": digest, "bytes": size,
    })
    assert result["status"] == "failed"


@pytest.mark.parametrize("kind", ["core", "project", "data", "state"])
def test_verify_rejects_validly_hashed_artifact_with_wrong_identity(
    state_root: Path, tmp_path: Path, kind: str,
) -> None:
    _settle_generating_cell(state_root)
    out = tmp_path / "out"
    assert _backup(state_root, out, _runner()) == 0
    manifest = _manifest(out)
    if kind == "state":
        entry = manifest["state_archive"]
        replacement = f"{EDITOR}-core.dump"
    elif kind == "data":
        entry = _workspace(manifest, EDITOR)["data_volumes"][0]
        replacement = f"{PRODUCTION}-data-uploads.tar.gz"
    else:
        entry = _database(manifest, EDITOR, kind)
        replacement = f"{PRODUCTION}-" + ("core.dump" if kind == "core" else "project.sql")
    original = out / entry["file"]
    wrong = out / replacement
    if not wrong.exists():
        wrong.write_bytes(original.read_bytes())
    original.unlink()
    entry.update(file=replacement)
    entry["sha256"], entry["bytes"] = backup_cells._file_digest(wrong)
    _write_json(out / "MANIFEST.json", manifest)
    verifier = FakeRunner()
    assert _verify(out, verifier) == 1
    assert verifier.calls == [], "identity mismatch must stop before scratch allocation"


def test_direct_verifier_rejects_wrong_workspace_dump_before_restore(tmp_path: Path) -> None:
    wrong = tmp_path / f"{PRODUCTION}-core.dump"
    wrong.write_bytes(b"PGDMP validly hashed wrong workspace")
    digest, size = backup_cells._file_digest(wrong)
    runner = FakeRunner()
    backup = backup_cells.CellBackup(
        runner=runner, out_dir=tmp_path, postgres_image=POSTGRES_IMAGE,
        helper_image=HELPER_IMAGE, redactor=backup_cells.SecretRedactor(),
    )
    result = backup_cells._verify_entry(backup, tmp_path, {
        "workspace_id": EDITOR, "volume": core_volume(EDITOR), "kind": "core",
        "status": "ok", "file": wrong.name, "sha256": digest, "bytes": size, "tables": 7,
    })
    assert result["status"] == "failed"
    assert runner.calls == []


def test_data_manifest_cannot_relabel_a_state_archive_as_business_data(
    state_root: Path, tmp_path: Path,
) -> None:
    _settle_generating_cell(state_root)
    out = tmp_path / "out"
    assert _backup(state_root, out, _runner()) == 0
    manifest = _manifest(out)
    data = _workspace(manifest, EDITOR)["data_volumes"][0]
    data.update({**manifest["state_archive"], "kind": "state"})
    manifest["coverage"]["expected"] = backup_cells._artifact_identities(manifest["workspaces"])
    _write_json(out / "MANIFEST.json", manifest)
    runner = FakeRunner()
    assert _verify(out, runner) == 1
    assert runner.calls == []


def test_verify_restores_every_dump_into_its_own_scratch_server(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    out = tmp_path / "out"
    assert _backup(state_root, out, _runner(running=[core_volume(EDITOR)])) == 0

    verifier = FakeRunner()
    assert _verify(out, verifier) == 0
    assert verifier.matching("exec", "pg_restore"), "custom-format dumps restore with pg_restore"
    assert verifier.matching("exec", "psql", "-f"), "pg_dumpall output restores with psql -f"
    roles = [call for call in verifier.calls if any(
        "CREATE ROLE omnia_core_runtime" in argument for argument in call)]
    assert len(roles) == 1  # only the one running core has a custom-format dump
    assert "NOLOGIN NOSUPERUSER NOBYPASSRLS" in roles[0][-1]
    target = roles[0][2]
    restored = next(call for call in verifier.matching("exec", "pg_restore") if target in call)
    assert verifier.calls.index(roles[0]) < verifier.calls.index(restored)
    assert "--no-acl" not in restored and "--no-owner" not in restored
    created = [call[-1] for call in verifier.matching("volume", "create")]
    removed = [call[-1] for call in verifier.matching("volume", "rm")]
    assert created and sorted(created) == sorted(removed)
    _assert_only_scratch_touched(verifier)


def test_verify_detects_a_corrupted_dump_file(
    state_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    _backup(state_root, out, _runner(running=[core_volume(EDITOR)]))
    corrupted = out / f"{EDITOR}-core.dump"
    corrupted.write_bytes(corrupted.read_bytes() + b"bit rot")

    verifier = FakeRunner()
    assert _verify(out, verifier) == 1
    assert corrupted not in verifier.stdins, "a bad checksum must stop before restoring"
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if corrupted.name in line and "checksum" in line]
    # One damaged artifact must not hide the healthy ones.
    assert [line for line in lines if f"{PRODUCTION}-core.dump: ok" in line]


def test_verify_detects_a_table_count_mismatch(state_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _backup(state_root, out, _runner(running=[core_volume(EDITOR)]))

    verifier = FakeRunner()
    verifier.table_count = 3
    assert _verify(out, verifier) == 1
    _assert_only_scratch_touched(verifier)


def test_verify_reports_partial_when_the_backup_itself_was_partial(
    state_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    runner = _runner()
    runner.holders[core_volume(EDITOR)] = ["", "", "abc123def456", "", "abc123def456"]
    assert _backup(state_root, out, runner) == 2

    verifier = FakeRunner()
    assert _verify(out, verifier) == 2


def test_successful_restore_cannot_hide_unconfirmed_scratch_cleanup(
    state_root: Path, tmp_path: Path
) -> None:
    _settle_generating_cell(state_root)
    out = tmp_path / "out"
    assert _backup(state_root, out, _runner()) == 0
    verifier = FakeRunner()
    verifier.fail_tokens.add("rm")
    assert _verify(out, verifier) == 1
    _assert_only_scratch_touched(verifier)


def test_the_restore_tool_follows_the_format_not_the_file_name(tmp_path: Path) -> None:
    """A halted cell's core dump is a plain script in a `-core.dump` file.

    On production every cell is halted between builds, so `pg_restore` was tried on
    15 of 15 core dumps and every one of them failed with "input file appears to be
    a text format dump" — the backups were fine, their verification was not.
    """
    from scripts.backup_cells import _dump_format, _restore_command

    plain = tmp_path / "ws-core.dump"
    plain.write_bytes(b"--\n-- PostgreSQL database cluster dump\n")
    custom = tmp_path / "ws2-core.dump"
    custom.write_bytes(b"PGDMP\x00\x01")

    # The format recorded by the backup wins.
    assert _dump_format(plain, {"format": "plain"}) == "plain"
    assert _dump_format(custom, {"format": "custom"}) == "custom"
    # A backup written before the field existed is sniffed, not guessed from the name.
    assert _dump_format(plain, {}) == "plain"
    assert _dump_format(custom, {}) == "custom"
    assert _dump_format(plain, {"format": "nonsense"}) == "plain"

    assert _restore_command("custom")[0] == "pg_restore"
    assert _restore_command("plain")[0] == "psql"
    assert "ON_ERROR_STOP=1" in _restore_command("plain")


def test_every_dump_records_the_format_it_actually_wrote(
    state_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    # One cell is running (exec path), the rest are halted (scratch path).
    _backup(state_root, out, _runner(running=[core_volume(EDITOR)]))
    manifest = _manifest(out)

    formats = {
        (db["method"], db["kind"]): db["format"]
        for item in manifest["workspaces"]
        for db in item["databases"]
        if db["status"] == "ok"
    }
    assert formats, "the fixture produced no successful dump"
    for (method, kind), value in formats.items():
        # exec + core is the only custom-format pg_dump; everything else is pg_dumpall.
        assert value == ("custom" if (method, kind) == ("exec", "core") else "plain"), (
            method, kind, value,
        )


ADMIN_PASSWORD = "admin-independent-secret-" + "a" * 32


def _protocol_one_authority(state_root: Path) -> Path:
    _write_json(
        state_root / "project-machines" / EDITOR / "docker.json",
        {"project_database_role_protocol": 1, "services": {}},
    )
    authority = state_root / "project-machines" / "project-db-credentials" / f"{EDITOR}.json"
    _write_json(authority, {
        "version": 1, "workspace_id": EDITOR,
        "runtime_password": "r" * 43, "migrator_password": "m" * 43,
        "admin_password": ADMIN_PASSWORD,
    })
    authority.parent.chmod(0o700)
    authority.chmod(0o600)
    return authority


def test_protocol_one_backup_uses_bound_existing_admin_authority(state_root: Path) -> None:
    authority = _protocol_one_authority(state_root)
    original = authority.read_bytes()
    redactor = backup_cells.SecretRedactor()
    inventory = backup_cells.build_inventory(state_root, [project_volume(EDITOR)], redactor)
    assert inventory.plans[0].databases[0].password == ADMIN_PASSWORD
    assert authority.read_bytes() == original
    assert ADMIN_PASSWORD not in redactor.scrub(ADMIN_PASSWORD)


@pytest.mark.parametrize("problem", [
    "missing", "corrupt", "wrong_workspace", "wrong_version", "duplicate_key",
    "unsafe_mode", "symlink", "shared_password", "missing_admin",
])
def test_protocol_one_backup_never_falls_back_to_legacy_authority(
    state_root: Path, problem: str,
) -> None:
    authority = _protocol_one_authority(state_root)
    payload = json.loads(authority.read_bytes())
    if problem == "missing":
        authority.unlink()
    elif problem == "corrupt":
        authority.write_text("{broken", encoding="utf-8")
    elif problem == "wrong_workspace":
        payload["workspace_id"] = PRODUCTION
    elif problem == "wrong_version":
        payload["version"] = True
    elif problem == "duplicate_key":
        authority.write_text(
            authority.read_text().replace('{', '{"version":1,', 1), encoding="utf-8",
        )
    elif problem == "unsafe_mode":
        authority.chmod(0o644)
    elif problem == "symlink":
        other = authority.with_suffix(".real")
        authority.rename(other)
        authority.symlink_to(other)
    elif problem == "shared_password":
        payload["admin_password"] = payload["runtime_password"]
    elif problem == "missing_admin":
        del payload["admin_password"]
    if problem in {"wrong_workspace", "wrong_version", "shared_password", "missing_admin"}:
        authority.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(backup_cells.BackupError, match="project database authority") as failure:
        backup_cells.build_inventory(
            state_root, [project_volume(EDITOR)], backup_cells.SecretRedactor(),
        )
    assert ADMIN_PASSWORD not in str(failure.value)
    assert PROJECT_PASSWORD not in str(failure.value)


@pytest.mark.parametrize("encoded", [
    '{broken', 'null', '{"project_database_role_protocol":null}',
    '{"project_database_role_protocol":true}', '{"project_database_role_protocol":2}',
    '{"project_database_role_protocol":1,"project_database_role_protocol":0}',
])
def test_invalid_project_protocol_metadata_cannot_downgrade_backup(
    state_root: Path, encoded: str,
) -> None:
    _protocol_one_authority(state_root)
    metadata = state_root / "project-machines" / EDITOR / "docker.json"
    metadata.write_text(encoded, encoding="utf-8")
    expected = (
        "active database registry is unreadable"
        if encoded in {"{broken", "null"} or encoded.count("project_database_role_protocol") > 1
        else "project database authority"
    )
    with pytest.raises(backup_cells.BackupError, match=expected):
        backup_cells.build_inventory(
            state_root, [project_volume(EDITOR)], backup_cells.SecretRedactor(),
        )


def test_explicit_legacy_protocol_keeps_existing_password(state_root: Path) -> None:
    _write_json(
        state_root / "project-machines" / EDITOR / "docker.json",
        {"project_database_role_protocol": 0},
    )
    inventory = backup_cells.build_inventory(
        state_root, [project_volume(EDITOR)], backup_cells.SecretRedactor(),
    )
    assert inventory.plans[0].databases[0].password == PROJECT_PASSWORD


def test_protocol_one_admin_password_only_reaches_dump_child_environment(
    state_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _protocol_one_authority(state_root)
    runner = _runner(running=[project_volume(EDITOR)])
    runner.volumes = [project_volume(EDITOR)]
    runner.dump_exit = 1
    runner.dump_stderr = f'FATAL: password "{ADMIN_PASSWORD}" authentication failed'.encode()
    assert _backup(state_root, tmp_path / "out", runner) == 1
    captured = capsys.readouterr()
    manifest = (tmp_path / "out" / "MANIFEST.json").read_text(encoding="utf-8")
    assert all("PGPASSWORD" not in env for env in runner.envs)
    assert all(env.get("PGPASSWORD") != PROJECT_PASSWORD for env in runner.envs)
    for secret in (ADMIN_PASSWORD, PROJECT_PASSWORD):
        assert secret not in captured.out + captured.err + manifest
        assert all(secret not in argument for call in runner.calls for argument in call)
    assert "***" in manifest


@pytest.mark.parametrize("running", [True, False])
def test_protocol_one_dump_and_table_count_use_admin_peer_with_trusted_options(
    state_root: Path, tmp_path: Path, running: bool,
) -> None:
    from yleum_orchestrator.services.restoration_database import TRUSTED_ADMIN_PGOPTIONS
    _protocol_one_authority(state_root)
    _settle_generating_cell(state_root)
    runner = _runner(running=[project_volume(EDITOR)] if running else [])
    assert _backup(state_root, tmp_path / "out", runner) == 0
    calls = [(call, env) for call, env in zip(runner.calls, runner.envs, strict=True)
             if ("pg_dumpall" in call or "psql" in call) and "PGOPTIONS" in call]
    assert len(calls) == 2
    for call, env in calls:
        assert call[1:4] == ("exec", "--user", "postgres")
        assert call[call.index("-h") + 1] == "/tmp"
        assert "127.0.0.1" not in call
        assert "PGPASSWORD" not in call and "PGPASSWORD" not in env
        assert env == {"PGOPTIONS": TRUSTED_ADMIN_PGOPTIONS}
        assert call[call.index("-e") + 1] == "PGOPTIONS"
    if not running:
        scripts = [a for call in runner.calls for a in call if "local all all trust" in a]
        assert scripts and all("hba_file=/tmp/" in a for a in scripts)
        assert any(f"{project_volume(EDITOR)}:/source:ro" in call for call in runner.calls)



@pytest.mark.parametrize("prefix", [
    b"CREATE ROLE postgres;",
    b'-- CREATE ROLE postgres;\n/* nested /* quote \' */ comment */\nCREATE ROLE "postgres";',
    b"\\restrict Abc123\nSET client_encoding = 'UTF8';\nCREATE /* x */ ROLE postgres;",
    b"SELECT 'CREATE ROLE postgres;';\nCREATE ROLE postgres;",
    b"SELECT $tag$CREATE ROLE postgres;$tag$;\nCREATE ROLE postgres;",
    (b'CREATE ROLE "someone; else";\nALTER ROLE "someone; else" PASSWORD \'a\'\'b\';\n'
     b'CREATE ROLE POSTGRES;'),
])
def test_plain_restore_skips_only_top_level_bootstrap_create(tmp_path: Path, prefix: bytes) -> None:
    source = tmp_path / "dump.sql"
    tail = (b"\nALTER ROLE postgres NOSUPERUSER NOLOGIN;\n"
            b"CREATE ROLE reader;\nCREATE ROLE member;\n"
            b"GRANT reader TO member WITH INHERIT TRUE GRANTED BY postgres;\n"
            b"\\connect postgres\nCOPY public.t FROM stdin;\nCREATE ROLE postgres;\n\\.\n"
            b"\\unrestrict Abc123\n")
    original = prefix + tail
    source.write_bytes(original)
    span = backup_cells._bootstrap_role_span(original)
    assert span is not None
    with backup_cells.plain_restore_source(source, tmp_path) as filtered:
        assert filtered != source
        copied = filtered.read_bytes()
        start, end = span
        assert copied[:start] == original[:start] and copied[end:] == original[end:]
        assert all(byte in b" \r\n" for byte in copied[start:end])
        assert copied.endswith(tail)
        assert stat.S_IMODE(filtered.stat().st_mode) == 0o600 if os.name == "posix" else True
    assert not filtered.exists()
    assert source.read_bytes() == original


@pytest.mark.parametrize("source", [
    b'CREATE ROLE "Postgres";',
    b"CREATE ROLE postgres LOGIN;",
    b"COPY t FROM stdin;\nCREATE ROLE postgres;\n\\.\n",
    b"\\connect postgres\nCREATE ROLE postgres;",
    b"CREATE FUNCTION f() RETURNS void AS $$ BEGIN CREATE ROLE postgres; END; $$ LANGUAGE plpgsql;",
    b"SELECT 'unterminated; CREATE ROLE postgres;",
    b"/* unclosed CREATE ROLE postgres;",
    b"SET something = E'escaped\\\\';\nCREATE ROLE postgres;",
    b"\\unknown\nCREATE ROLE postgres;",
])
def test_plain_restore_ambiguous_or_database_sql_is_never_rewritten(
    tmp_path: Path, source: bytes,
) -> None:
    path = tmp_path / "dump.sql"
    path.write_bytes(source)
    assert backup_cells._bootstrap_role_span(source) is None
    with backup_cells.plain_restore_source(path, tmp_path) as selected:
        assert selected == path and selected.read_bytes() == source


def test_plain_restore_keeps_second_duplicate_and_cleans_temp_on_error(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    original = b"CREATE ROLE postgres;\nCREATE ROLE postgres;\nINVALID SQL;\n"
    path.write_bytes(original)
    with pytest.raises(RuntimeError, match="synthetic restore failure"):
        with backup_cells.plain_restore_source(path, tmp_path) as filtered:
            assert filtered.read_bytes().count(b"CREATE ROLE postgres;") == 1
            assert b"INVALID SQL;" in filtered.read_bytes()
            raise RuntimeError("synthetic restore failure")
    assert not filtered.exists() and path.read_bytes() == original
    assert not list(tmp_path.glob("omnia-restore-*"))


def test_plain_restore_bounded_prefix_does_not_guess_past_limit(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    original = b"--" + b"x" * (4 * 1024 * 1024) + b"\nCREATE ROLE postgres;\n"
    path.write_bytes(original)
    with backup_cells.plain_restore_source(path, tmp_path) as filtered:
        assert filtered == path
    assert path.read_bytes() == original


@pytest.mark.parametrize("invalid_sql", [False, True])
def test_actual_plain_restore_preserves_bootstrap_and_strict_sql(
    tmp_path: Path, invalid_sql: bool,
) -> None:
    class InspectingRunner(FakeRunner):
        payloads: list[bytes]

        def __init__(self) -> None:
            super().__init__()
            self.payloads = []

        def run(self, args, *, timeout, env=None, stdin_path=None):
            result = super().run(args, timeout=timeout, env=env, stdin_path=stdin_path)
            if stdin_path is not None:
                payload = stdin_path.read_bytes()
                self.payloads.append(payload)
                assert b"CREATE ROLE postgres;" not in payload
                assert b"GRANTED BY postgres" in payload
                if b"INVALID SQL;" in payload:
                    return CommandResult(tuple(args), 1, b"", b"unrelated SQL failure")
            return result

    runner = InspectingRunner()
    path = tmp_path / "dump.sql"
    original = (b"CREATE ROLE postgres;\nALTER ROLE postgres NOSUPERUSER NOLOGIN;\n"
                b"CREATE ROLE reader;\nCREATE ROLE member;\n"
                b"GRANT reader TO member GRANTED BY postgres;\n")
    if invalid_sql:
        original += b"INVALID SQL;\n"
    path.write_bytes(original)
    backup = backup_cells.CellBackup(
        runner=runner, out_dir=tmp_path, postgres_image=POSTGRES_IMAGE,
        helper_image=HELPER_IMAGE, redactor=backup_cells.SecretRedactor(),
    )
    actual = backup_cells._restore_entry(backup, path, "project", {"format": "plain", "tables": 7})
    assert actual["status"] == ("failed" if invalid_sql else "ok")
    assert len(runner.payloads) == 1
    started = runner.matching("run", "--detach")[0]
    assert "initdb --username=postgres " in started[-1]
    admin = next(call[-1] for call in runner.matching("psql", "-c")
                 if call[-1].startswith("CREATE ROLE omnia_backup_"))
    assert admin.endswith(" LOGIN SUPERUSER")
    restores = runner.matching("psql", "-f")
    assert len(restores) == 1 and "ON_ERROR_STOP=1" in restores[0]
    assert restores[0][restores[0].index("-U") + 1].startswith("omnia_backup_")
    assert path.read_bytes() == original and not list(tmp_path.glob("omnia-restore-*"))
    assert not runner.containers and not runner.volumes
