"""Opt-in physical acceptance on an explicitly supplied disposable Docker host."""

import gzip
import importlib.util
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from scripts import backup_cells as cells


class RecordingDockerRunner(cells.DockerRunner):
    """Real Engine operations, recording only this fixture's fresh resources."""

    def __init__(self):
        super().__init__()
        self.created_containers = set()
        self.created_volumes = set()

    def run(self, args, **kwargs):
        if args[:2] == ["volume", "create"]:
            assert args[-1].startswith(cells._SCRATCH_PREFIX)
            assert cells._SCRATCH_LABEL in args
            self.created_volumes.add(args[-1])
        if args[0] == "run" and "--name" in args:
            name = args[args.index("--name") + 1]
            assert name.startswith(cells._SCRATCH_PREFIX)
            assert cells._SCRATCH_LABEL in args
            self.created_containers.add(name)
        return super().run(args, **kwargs)

    def assert_owned_resources_absent(self):
        containers = self.run(
            ["container", "ls", "--all", "--format", "{{.Names}}"],
            timeout=60,
        )
        volumes = self.run(["volume", "ls", "--format", "{{.Name}}"], timeout=60)
        assert containers.ok and volumes.ok
        assert not self.created_containers.intersection(containers.text().splitlines())
        assert not self.created_volumes.intersection(volumes.text().splitlines())


def fresh_scratch_server(backup, pool):
    volume = pool.volume_name()
    assert backup.runner.run(
        ["volume", "create", "--label", cells._SCRATCH_LABEL, volume], timeout=60,
    ).ok
    assert backup.prepare_scratch_volume(pool, volume) is None
    server = backup.start_scratch_server(pool, volume)
    assert isinstance(server, str)
    return server


@pytest.mark.skipif(
    os.environ.get("BACKUP_DISPOSABLE_DOCKER_TEST") != "1",
    reason="requires an explicitly selected disposable Docker sandbox",
)
def test_full_cluster_restore_rejects_sql_failure_even_with_matching_table_count(tmp_path):
    image = os.environ["CELL_POSTGRES_IMAGE"]
    assert "@sha256:" in image
    runner = RecordingDockerRunner()
    backup = cells.CellBackup(
        runner=runner,
        out_dir=tmp_path,
        postgres_image=image,
        helper_image=image,
        redactor=cells.SecretRedactor(),
    )
    pool = cells.ScratchPool(runner)
    try:
        server = fresh_scratch_server(backup, pool)
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
                "CREATE TABLE backup_restore_probe(id integer PRIMARY KEY, payload text); "
                "INSERT INTO backup_restore_probe VALUES (1,'retained-one'),(2,'retained-two'); "
                "CREATE ROLE backup_restore_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; "
                 "CREATE ROLE backup_restore_member NOLOGIN; "
                 "GRANT backup_restore_reader TO backup_restore_member "
                 "WITH INHERIT TRUE GRANTED BY postgres; "
                "GRANT SELECT ON backup_restore_probe TO backup_restore_reader;",
            ],
            timeout=60,
        ).ok
        # This server is our freshly initialized scratch cluster, not a live
        # project with controller credential/HBA authority. Use its actual peer
        # socket rather than pretending to configure a production protocol.
        path = tmp_path / f"{uuid4()}-project.sql"
        dumped = runner.run_to_file(
            [
                "exec",
                server,
                "pg_dumpall",
                "-U",
                "postgres",
                "-h",
                "/tmp",
                "--lock-wait-timeout=30s",
            ],
            path=path,
            timeout=1800,
        )
        assert dumped.ok and dumped.size_bytes > 0
        # The input intentionally disables postgres after replaying its grants;
        # maintenance must use the separate scratch-only login, not rename OID10.
        with path.open("ab") as stream:
            stream.write(b"\nALTER ROLE postgres NOSUPERUSER NOLOGIN;\n")
        tables = backup.count_tables(["exec", server], backup.scratch_connection())
        assert tables == 1
        entry = {"kind": "project", "format": "plain", "tables": tables}
        original_count_tables = backup.count_tables
        recovered = []

        def inspect_recovered_data(prefix, connection, *, env=None):
            count = original_count_tables(prefix, connection, env=env)
            # _restore_entry calls this before its own scratch pool is removed.
            # Query the restored server through its actual new administrator.
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
                    "'rows', (SELECT jsonb_agg(jsonb_build_array(id,payload) ORDER BY id) "
                    "FROM public.backup_restore_probe), "
                    "'role_flags', (SELECT jsonb_build_array(rolsuper,rolcreatedb,rolcreaterole,"
                    "rolcanlogin) FROM pg_catalog.pg_roles WHERE rolname='backup_restore_reader'), "
                    "'can_select', has_table_privilege('backup_restore_reader', "
                    "'public.backup_restore_probe', 'SELECT'), "
                    "'can_insert', has_table_privilege('backup_restore_reader', "
                     "'public.backup_restore_probe', 'INSERT'), "
                     "'bootstrap', (SELECT jsonb_build_array(oid,rolsuper,rolcanlogin) "
                     "FROM pg_catalog.pg_roles WHERE rolname='postgres'), "
                     "'grantor', (SELECT grantor FROM pg_catalog.pg_auth_members "
                     "WHERE roleid=(SELECT oid FROM pg_catalog.pg_roles "
                     "WHERE rolname='backup_restore_reader') AND member=(SELECT oid "
                     "FROM pg_catalog.pg_roles WHERE rolname='backup_restore_member')))",
                ],
                timeout=60,
                env=env,
            )
            assert checked.ok
            proof = json.loads(checked.text())
            assert proof == {
                "rows": [[1, "retained-one"], [2, "retained-two"]],
                "role_flags": [False, False, False, False],
                "can_select": True,
                "can_insert": False,
                "bootstrap": [10, False, False],
                "grantor": 10,
            }
            recovered.append(proof)
            return count

        backup.count_tables = inspect_recovered_data
        valid_restore = cells._restore_entry(backup, path, "project", entry)
        assert valid_restore["status"] == "ok", valid_restore
        # The table remains present; table-count-only verification would pass.
        with path.open("ab") as stream:
            stream.write(
                b"\n\\connect postgres\nINSERT INTO nonexistent_backup_probe VALUES (1);\n"
            )
        assert cells._restore_entry(backup, path, "project", entry)["status"] == "failed"
        assert len(recovered) == 1, "invalid SQL must fail before successful verification"
    finally:
        try:
            pool.release()
        finally:
            runner.assert_owned_resources_absent()
    assert len(runner.created_volumes) == 3


@pytest.mark.skipif(
    os.environ.get("BACKUP_DISPOSABLE_DOCKER_TEST") != "1",
    reason="requires an explicitly selected disposable Docker sandbox",
)
def test_offline_globals_restore_preserves_bootstrap_grantor_and_data(tmp_path):
    script = Path(__file__).resolve().parents[3] / "infra/backup/offline-restore.py"
    spec = importlib.util.spec_from_file_location("offline_bootstrap_physical", script)
    offline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(offline)
    image = os.environ["CELL_POSTGRES_IMAGE"]
    assert "@sha256:" in image
    recovered = []

    class InspectGlobalsRunner(RecordingDockerRunner):
        def run(self, args, **kwargs):
            result = super().run(args, **kwargs)
            source = kwargs.get("stdin_path")
            if source is not None and source.name.endswith("-counts.sql") and result.ok:
                sql = (
                    "SELECT jsonb_build_object('bootstrap',(SELECT jsonb_build_array("
                    "oid,rolsuper,rolcanlogin) FROM pg_roles WHERE rolname='postgres'),"
                    "'grantor',(SELECT grantor FROM pg_auth_members WHERE roleid=(SELECT "
                    "oid FROM pg_roles WHERE rolname='globals_reader') AND member=(SELECT "
                    "oid FROM pg_roles WHERE rolname='globals_member')),"
                    "'rows',(SELECT jsonb_agg(n ORDER BY n) FROM public.globals_probe),"
                    "'member_select',has_table_privilege('globals_member',"
                    "'public.globals_probe','SELECT'))"
                )
                checked = super().run([*args[:args.index("-f")], "-c", sql], timeout=60)
                assert checked.ok
                proof = json.loads(checked.text())
                assert proof == {"bootstrap": [10, False, False], "grantor": 10,
                                 "rows": [1, 2], "member_select": True}
                recovered.append(proof)
            return result

    runner = InspectGlobalsRunner()
    backup = cells.CellBackup(runner=runner, out_dir=tmp_path, postgres_image=image,
                              helper_image=image, redactor=cells.SecretRedactor())
    pool = cells.ScratchPool(runner)
    try:
        server = fresh_scratch_server(backup, pool)
        setup = (
            "CREATE ROLE globals_reader NOLOGIN; CREATE ROLE globals_member NOLOGIN; "
            "GRANT globals_reader TO globals_member WITH INHERIT TRUE GRANTED BY postgres; "
            "CREATE TABLE globals_probe(n int); INSERT INTO globals_probe VALUES(1),(2); "
            "GRANT SELECT ON globals_probe TO globals_reader;"
        )
        assert runner.run(["exec", server, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                           *backup.scratch_connection(), "-c", setup], timeout=60).ok
        paths = {}
        for name, command in (
            ("globals", ["pg_dumpall", "--globals-only", "-h", "/tmp", "-U", "postgres"]),
            ("data", ["pg_dump", "-h", "/tmp", "-U", "postgres", "-d", "postgres"]),
        ):
            plain = tmp_path / (name + ".sql")
            assert runner.run_to_file(["exec", server, *command], path=plain, timeout=1800).ok
            if name == "globals":
                with plain.open("ab") as output:
                    output.write(b"\nALTER ROLE postgres NOSUPERUSER NOLOGIN;\n")
            compressed = tmp_path / (name + ".sql.gz")
            with plain.open("rb") as source, gzip.open(compressed, "wb") as output:
                shutil.copyfileobj(source, output)
            paths[name] = compressed
        result = offline.restore_sql(cells, runner, tmp_path, image, "omnia",
                                     paths["data"], paths["globals"])
        assert result == {"status": "passed", "tables": 1, "rows": 2,
                          "write_restart": True, "globals_restored": True}
        assert len(recovered) == 1
    finally:
        try:
            pool.release()
        finally:
            runner.assert_owned_resources_absent()
    assert len(runner.created_volumes) == 2
