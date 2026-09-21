from __future__ import annotations

import json
import os
import stat
import tarfile
from collections.abc import Mapping, Sequence
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
        self.holders: dict[str, list[str]] = {}
        self.table_count = 7
        self.dump_payload = b"PGDMP fake dump payload\n"
        self.dump_exit = 0
        self.dump_stderr = b""
        self.fail_tokens: set[str] = set()

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
        if tuple(args[:2]) == ("volume", "ls"):
            return CommandResult(argv, 0, ("\n".join(self.volumes) + "\n").encode(), b"")
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
        f"omnia-cell-{'f' * 32}-postgres",
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
