"""Explicit CI-only, new network-none tmpfs PG16; never attaches existing data."""

import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from yleum_orchestrator.services.publication_migration_backup import (
    RestoreSandbox,
    pack_backup,
    restore_backup,
)
from yleum_orchestrator.services.publication_migration_pg import (
    MAX_DUMP,
    read_catalog,
    row_proofs,
)


def configure_isolated_settings(monkeypatch):
    """Configure the real dispatch guard for an isolated, inventory-free CI process."""
    from yleum_orchestrator.core.config import Settings
    from yleum_orchestrator.services import generation_reader_inventory

    settings = Settings(
        _env_file=None,
        _env_prefix="QA_MIGRATION_FIXTURE_",
        database_url="postgresql://migration-ci@127.0.0.1:1/migration_fixture_unused",
        internal_token="nonsecret-isolated-migration-ci-token",
        generation_recovery_inventory_dir="",
    )
    # The DSN is configuration only: all actual SQL uses owned Docker exec.
    endpoint = urlsplit(settings.database_url)
    assert endpoint.hostname == "127.0.0.1" and endpoint.port == 1
    assert endpoint.path == "/migration_fixture_unused"
    monkeypatch.setattr(generation_reader_inventory, "get_settings", lambda: settings)
    return settings


def test_isolated_dispatch_settings_ignore_dotenv_and_keep_real_guard(monkeypatch, tmp_path):
    from yleum_orchestrator.services import generation_reader_inventory

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=invalid\nINTERNAL_TOKEN=invalid\n")
    before = generation_reader_inventory.require_if_configured
    settings = configure_isolated_settings(monkeypatch)
    assert generation_reader_inventory.get_settings() is settings
    assert generation_reader_inventory.require_if_configured is before
    assert generation_reader_inventory.reader_dispatch(lambda: "isolated") == "isolated"
    settings.generation_recovery_inventory_dir = str(tmp_path / "unregistered-inventory")
    monkeypatch.setattr(generation_reader_inventory, "_OWN", None)
    with pytest.raises(generation_reader_inventory.ReaderInventoryUnavailable):
        generation_reader_inventory.reader_dispatch(lambda: pytest.fail("guard was bypassed"))


@pytest.mark.skipif(
    os.environ.get("QA_MIGRATION_DOCKER_IMAGE") is None,
    reason="requires explicit disposable Docker CI image",
)
def test_network_none_restore_proves_full_schema_and_original_rows_before_and_after_plan(
    monkeypatch,
):
    import docker

    assert os.environ.get("CI") == "true"
    assert (
        os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        == "unix:///var/run/docker.sock"
    )
    image = os.environ["QA_MIGRATION_DOCKER_IMAGE"]
    assert re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image)
    configure_isolated_settings(monkeypatch)
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=20)
    project = uuid4()
    source = RestoreSandbox(client, project, uuid4(), image)
    restored = RestoreSandbox(client, project, uuid4(), image)
    try:
        with source as live:
            live.sql(
                "CREATE TABLE public.orders(id bigint PRIMARY KEY, amount integer NOT NULL);"
                "INSERT INTO public.orders VALUES(1,630),(2,650);"
                "CREATE TABLE public.__omnia_project_migrations(name text PRIMARY KEY,"
                "sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now());"
            )
            before = live.full_schema()
            tables = read_catalog(live)
            rows = row_proofs(live, tables)
            packed = pack_backup(
                live.command(
                    [
                        "pg_dumpall",
                        "--globals-only",
                        "--no-role-passwords",
                        "-h",
                        "/tmp",
                        "-U",
                        "postgres",
                    ]
                ),
                live.command(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        "-h",
                        "/tmp",
                        "-U",
                        "postgres",
                        "-d",
                        "postgres",
                    ],
                    max_bytes=MAX_DUMP,
                ),
            )
            plan = (
                "ALTER TABLE public.orders ADD COLUMN pickup text;"
                "ALTER TABLE public.orders ADD COLUMN comment text;"
                "ALTER TABLE public.orders ADD COLUMN pickup_at timestamptz;"
            )
            live.sql(plan)
            after = live.full_schema()
            with restored as verify:
                restore_backup(verify, packed)
                assert verify.full_schema() == before
                assert row_proofs(verify, tables) == rows
                verify.sql(plan)
                assert verify.full_schema() == after
                assert row_proofs(verify, tables) == rows
                verify.sql("INSERT INTO orders(id,amount,pickup) VALUES(3,270,'now');")
                assert verify.json("SELECT count(*) FROM orders;") == 3
        assert source.cleaned and restored.cleaned
    finally:
        client.close()
