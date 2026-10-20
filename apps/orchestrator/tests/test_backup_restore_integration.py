"""Opt-in physical acceptance on an explicitly supplied disposable Docker host."""

import json
import os
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
        volume = pool.volume_name()
        assert runner.run(
            ["volume", "create", "--label", cells._SCRATCH_LABEL, volume], timeout=60
        ).ok
        assert backup.prepare_scratch_volume(pool, volume) is None
        server = backup.start_scratch_server(pool, volume)
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
                "CREATE TABLE backup_restore_probe(id integer PRIMARY KEY, payload text); "
                "INSERT INTO backup_restore_probe VALUES (1,'retained-one'),(2,'retained-two'); "
                "CREATE ROLE backup_restore_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; "
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
                    "'public.backup_restore_probe', 'INSERT'))",
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
