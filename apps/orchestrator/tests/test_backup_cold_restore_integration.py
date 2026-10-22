"""Opt-in synthetic cold PVC recovery on the operator's disposable Docker sandbox."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest
from scripts import backup_cells as cells

SOURCE = Path(__file__).resolve().parents[3] / "infra/backup/cold-retained.py"
SPEC = importlib.util.spec_from_file_location("cold_physical_contract", SOURCE)
cold = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cold)
pytestmark = [
    pytest.mark.skipif(
        os.environ.get("BACKUP_DISPOSABLE_DOCKER_TEST") != "1",
        reason="requires an explicitly selected disposable Docker sandbox",
    ),
    pytest.mark.usefixtures("standalone_verifier_cells"),
]
PROJECT = "11111111-1111-4111-8111-111111111111"
OWNER = "22222222-2222-4222-8222-222222222222"
NS_UID = "33333333-3333-4333-8333-333333333333"
PVC_UID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def standalone_verifier_cells(monkeypatch):
    """Fresh standalone source, not a controller-initialized package instance."""
    import sys

    name = "cold_physical_standalone_cells"
    path = SOURCE.parents[2] / "apps/orchestrator/scripts/backup_cells.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys.modules[__name__], "cells", module)
    return module


def fixture_entry(path, kind):
    sha, size = cells._file_digest(path)
    return {
        "project_id": PROJECT,
        "owner_id": OWNER,
        "namespace_uid": NS_UID,
        "authority": {"project_id": PROJECT, "owner_id": OWNER},
        "proof": {
            "cleanup_confirmed": True,
            "watch_start": "synthetic-offline-fixture",
            "barrier": "synthetic-offline-fixture",
            "helper_uid": PVC_UID,
        },
        "volumes": [
            {
                "uid": PVC_UID,
                "kind": kind,
                "file": path.name,
                "sha256": sha,
                "bytes": size,
                **cold.filesystem_manifest(path),
            }
        ],
    }


def tracked_pools(monkeypatch):
    original, containers, volumes = cells.ScratchPool, set(), set()

    def factory(runner):
        pool = original(runner)
        release = pool.release

        def record_release():
            containers.update(pool.containers)
            volumes.update(pool.volumes)
            return release()

        pool.release = record_release
        return pool

    monkeypatch.setattr(cells, "ScratchPool", factory)
    return containers, volumes


def assert_absent(runner, containers, volumes):
    actual = runner.run(["container", "ls", "--all", "--format", "{{.Names}}"], timeout=60)
    actual_volumes = runner.run(["volume", "ls", "--format", "{{.Name}}"], timeout=60)
    assert actual.ok and actual_volumes.ok
    assert not containers.intersection(actual.text().splitlines())
    assert not volumes.intersection(actual_volumes.text().splitlines())


@pytest.mark.parametrize("kind", ["core", "project"])
def test_real_cold_pgdata_recovers_rows_and_grants_from_both_layouts(tmp_path, monkeypatch, kind):
    image = os.environ["CELL_POSTGRES_IMAGE"]
    assert "@sha256:" in image
    containers, volumes = tracked_pools(monkeypatch)
    runner = cells.DockerRunner()
    backup = cells.CellBackup(
        runner=runner,
        out_dir=tmp_path,
        postgres_image=image,
        helper_image=image,
        redactor=cells.SecretRedactor(),
    )
    source = cells.ScratchPool(runner)
    recovered = []
    try:
        volume = source.volume_name()
        assert runner.run(
            ["volume", "create", "--label", cells._SCRATCH_LABEL, volume], timeout=60
        ).ok
        assert backup.prepare_scratch_volume(source, volume) is None
        server = backup.start_scratch_server(source, volume)
        assert isinstance(server, str)
        assert runner.run(
            [
                "exec",
                server,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                *backup.scratch_connection(),
                "-c",
                "CREATE TABLE cold_backup_probe(id integer PRIMARY KEY, payload text); "
                "INSERT INTO cold_backup_probe VALUES (1,'cold-one'),(2,'cold-two'); "
                "CREATE ROLE cold_backup_reader NOLOGIN; "
                "GRANT SELECT ON cold_backup_probe TO cold_backup_reader; "
                "ALTER ROLE postgres SET search_path=pg_catalog;",
            ],
            timeout=60,
        ).ok
        assert runner.run(["stop", "--time", "5", server], timeout=60).ok
        pvc = volume
        if kind == "core":
            pvc = source.volume_name()
            assert runner.run(
                ["volume", "create", "--label", cells._SCRATCH_LABEL, pvc], timeout=60
            ).ok
            exported, extracted = runner.pipe(
                [
                    "run",
                    *backup.helper_flags(source.container_name(), read_caps=True),
                    "--volume",
                    f"{volume}:/source:ro",
                    image,
                    "tar",
                    "-cf",
                    "-",
                    "-C",
                    "/source",
                    ".",
                ],
                [
                    "run",
                    "--interactive",
                    *backup.helper_flags(source.container_name(), write_caps=True),
                    "--volume",
                    f"{pvc}:/target",
                    image,
                    "sh",
                    "-ec",
                    "mkdir /target/pgdata; tar --numeric-owner -xf - -C /target/pgdata",
                ],
                timeout=1800,
            )
            assert exported.ok and extracted.ok
        archive = tmp_path / cold.cold_file(NS_UID, PVC_UID)
        assert runner.run_to_file(
            [
                "run",
                *backup.helper_flags(source.container_name(), read_caps=True),
                "--volume",
                f"{pvc}:/source:ro",
                image,
                "tar",
                "-cf",
                "-",
                "-C",
                "/source",
                ".",
            ],
            path=archive,
            timeout=1800,
        ).ok
        original_count = cells.CellBackup.count_tables

        def check_data(self, prefix, connection, *, env=None):
            count = original_count(self, prefix, connection, env=env)
            checked = runner.run(
                [
                    *prefix,
                    "psql",
                    "-X",
                    "-qAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                    *connection,
                    "-c",
                    "SELECT jsonb_build_object("
                    "'rows',(SELECT jsonb_agg(jsonb_build_array(id,payload) ORDER BY id) "
                    "FROM cold_backup_probe),"
                    "'select',has_table_privilege('cold_backup_reader','cold_backup_probe','SELECT'),"
                    "'insert',has_table_privilege('cold_backup_reader','cold_backup_probe','INSERT'),"
                    "'admin_login',(SELECT rolcanlogin FROM pg_catalog.pg_roles "
                    "WHERE rolname='postgres'),"
                    "'admin_super',(SELECT rolsuper FROM pg_catalog.pg_roles "
                    "WHERE rolname='postgres'),"
                    "'reader_login',(SELECT rolcanlogin FROM pg_catalog.pg_roles "
                    "WHERE rolname='cold_backup_reader'),"
                    "'retained_role_default',(SELECT rolconfig @> "
                    "ARRAY['search_path=pg_catalog']::text[] "
                    "FROM pg_catalog.pg_roles WHERE rolname='postgres'))",
                ],
                timeout=60,
                env=env,
            )
            assert checked.ok
            evidence = json.loads(checked.text())
            assert evidence == {
                "rows": [[1, "cold-one"], [2, "cold-two"]],
                "select": True,
                "insert": False,
                "admin_login": True,
                "admin_super": True,
                "reader_login": False,
                "retained_role_default": True,
            }
            recovered.append(evidence)
            return count

        monkeypatch.setattr(cells.CellBackup, "count_tables", check_data)
        import builtins

        original_import = builtins.__import__
        controller_imports = []

        def no_controller_import(name, *args, **kwargs):
            if name.startswith("yleum_orchestrator"):
                controller_imports.append(name)
                raise ImportError("controller package absent in standalone physical verifier")
            return original_import(name, *args, **kwargs)

        with monkeypatch.context() as guard:
            guard.setattr(builtins, "__import__", no_controller_import)
            cold.verify(cells, tmp_path, [fixture_entry(archive, kind)], image, runner)
        assert controller_imports == []
        assert len(recovered) == 2, "check both cold physical recovery and fresh logical restore"
    finally:
        try:
            source.release()
        finally:
            assert_absent(runner, containers, volumes)


def test_real_cold_code_SGID_tree_is_extracted_and_verified_without_database_start(
    tmp_path, monkeypatch
):
    image = os.environ["CELL_POSTGRES_IMAGE"]
    assert "@sha256:" in image
    containers, volumes = tracked_pools(monkeypatch)
    archive = tmp_path / cold.cold_file(NS_UID, PVC_UID)
    with tarfile.open(archive, "w") as output:
        directory = tarfile.TarInfo(".")
        directory.type, directory.mode = tarfile.DIRTYPE, 0o755
        output.addfile(directory)
        directory = tarfile.TarInfo("retained")
        directory.type, directory.uid, directory.gid, directory.mode = (
            tarfile.DIRTYPE,
            70,
            70,
            0o2770,
        )
        output.addfile(directory)
        item = tarfile.TarInfo("retained/saved-code.txt")
        payload = b"preserved code bytes\n"
        item.uid, item.gid, item.mode, item.size = 70, 70, 0o660, len(payload)
        output.addfile(item, io.BytesIO(payload))
    runner = cells.DockerRunner()
    original_manifest = cold.filesystem_manifest
    roundtrips = []

    def assert_SGID_metadata(path):
        result = original_manifest(path)
        if path.name == "restored.tar":
            with tarfile.open(path, "r:") as tar:
                members = {member.name.removeprefix("./"): member for member in tar}
                directory, item = members["retained"], members["retained/saved-code.txt"]
                assert (directory.uid, directory.gid, directory.mode) == (70, 70, 0o2770)
                assert (item.uid, item.gid, item.mode) == (70, 70, 0o660)
                assert tar.extractfile(item).read() == payload
            roundtrips.append(result)
        return result

    monkeypatch.setattr(cold, "filesystem_manifest", assert_SGID_metadata)

    def no_database_start(*_, **__):
        raise AssertionError("code verification must not initialize or start PostgreSQL")

    monkeypatch.setattr(cells.CellBackup, "start_scratch_server", no_database_start)
    try:
        cold.verify(cells, tmp_path, [fixture_entry(archive, "files")], image, runner)
        assert len(roundtrips) == 1, "actual extracted SGID/owner/file bytes must be checked"
    finally:
        assert_absent(runner, containers, volumes)
