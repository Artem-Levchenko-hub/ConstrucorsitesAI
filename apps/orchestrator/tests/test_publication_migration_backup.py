"""Private backups must authenticate their operation and never overwrite evidence."""

import importlib
import os
from uuid import uuid4

import pytest


def implementation():
    name = "yleum_orchestrator.services.publication_migration_backup"
    assert importlib.util.find_spec(name), "encrypted publication backup implementation missing"
    return importlib.import_module(name)


@pytest.mark.parametrize("extra", ["", ' "-c" "listen_addresses=" "-p" "5432"'])
def test_readiness_distinguishes_final_postmaster_from_entrypoint_bootstrap(extra):
    m = implementation()
    arguments = (
        '"/usr/local/bin/postgres" "-c" "listen_addresses=" "-c" '
        '"unix_socket_directories=/tmp,/var/run/postgresql" "-c" "shared_buffers=16MB" '
        '"-c" "max_wal_size=64MB" "-c" "min_wal_size=32MB"'
    )
    assert m.final_postmaster_options(arguments + extra + "\n") is (not extra)


@pytest.mark.parametrize("options", [None, "", '"unterminated', '"/bin/sh" "-c" "postgres"'])
def test_readiness_rejects_unknown_or_malformed_postmaster_options(options):
    assert implementation().final_postmaster_options(options) is False


def test_readiness_rejects_potentially_truncated_options():
    import shlex

    m = implementation()
    final = shlex.join(m.SANDBOX_POSTGRES_COMMAND)
    assert m.final_postmaster_options(final.ljust(4096)) is False
    assert m.final_postmaster_options(final.ljust(4097)) is False


def test_sandbox_does_not_return_during_successful_bootstrap_server(monkeypatch):
    import shlex
    from types import SimpleNamespace

    import docker

    from tests.test_publication_migration_backup_docker import configure_isolated_settings

    configure_isolated_settings(monkeypatch)
    m = implementation()
    image = "postgres@sha256:" + "a" * 64
    final = shlex.join(m.SANDBOX_POSTGRES_COMMAND)
    observed = iter([final + " -c listen_addresses= -p 5432", final])
    calls = []

    def json_probe(statement):
        assert "postmaster.opts" in statement
        calls.append("argv")
        return next(observed)

    def sql_probe(statement):
        assert statement == "SELECT 1;"
        calls.append("select")
        return b"1\n"

    pg = SimpleNamespace(json=json_probe, sql=sql_probe)
    container = SimpleNamespace(start=lambda: None)

    def absent(name):
        raise docker.errors.NotFound("owned test name absent")

    client = SimpleNamespace(
        images=SimpleNamespace(get=lambda _: SimpleNamespace(attrs={"RepoDigests": [image]})),
        containers=SimpleNamespace(get=absent, create=lambda *args, **kwargs: container),
    )
    monkeypatch.setattr(m, "DockerPG", lambda *_: pg)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    assert m.RestoreSandbox(client, uuid4(), uuid4(), image).__enter__() is pg
    assert calls == ["argv", "argv", "select"]


@pytest.mark.parametrize("requested,stored", [
    ("postgres", "postgres"),
    ("docker.io/library/postgres", "postgres"),
    ("postgres", "docker.io/library/postgres"),
    ("index.docker.io/library/postgres", "library/postgres"),
    ("docker.io/postgres", "index.docker.io/postgres"),
    ("registry.example:5000/team/postgres", "registry.example:5000/team/postgres"),
])
def test_sandbox_keeps_bootstrap_and_controller_sockets_inside_owned_tmpfs(
    monkeypatch, requested, stored
):
    from types import SimpleNamespace

    import docker

    from tests.test_publication_migration_backup_docker import configure_isolated_settings

    configure_isolated_settings(monkeypatch)
    image = requested + "@sha256:" + "a" * 64
    stored_image = stored + "@sha256:" + "a" * 64

    class CapturedCreate(Exception):
        pass

    def absent(name):
        raise docker.errors.NotFound("owned test name absent")

    def create(received_image, command, **options):
        assert received_image == image
        # Official entrypoint clears PGHOST for bootstrap psql. It needs the
        # default socket, while controller commands deliberately use /tmp.
        assert "unix_socket_directories=/tmp,/var/run/postgresql" in command
        assert "listen_addresses=" in command
        assert options["network_mode"] == "none" and options["read_only"] is True
        assert options["user"] == "70:70" and options["cap_drop"] == ["ALL"]
        assert options["privileged"] is False
        assert options["security_opt"] == ["no-new-privileges:true"]
        for path in ("/tmp", "/var/run/postgresql"):
            assert "uid=70,gid=70,mode=0700" in options["tmpfs"][path]
        assert options["auto_remove"] is True
        raise CapturedCreate

    client = SimpleNamespace(
        # No pull/tag methods exist: aliases must use the same local immutable image.
        images=SimpleNamespace(
            get=lambda _: SimpleNamespace(attrs={"RepoDigests": [stored_image]})
        ),
        containers=SimpleNamespace(get=absent, create=create),
    )
    with pytest.raises(CapturedCreate):
        implementation().RestoreSandbox(client, uuid4(), uuid4(), image).__enter__()


@pytest.mark.parametrize("digests", [
    None, "postgres@sha256:" + "a" * 64, {"postgres": "a" * 64}, [None], ["invalid"],
    ["postgres@sha256:" + "a" * 64, "invalid"],
    ["registry.example/library/postgres@sha256:" + "a" * 64],
    ["docker.io/other/postgres@sha256:" + "a" * 64],
    ["docker.io/library/other@sha256:" + "a" * 64],
    ["docker.io/library/postgres@sha256:" + "b" * 64],
    ["https://docker.io/library/postgres@sha256:" + "a" * 64],
])
def test_sandbox_image_alias_guard_rejects_untrusted_repo_digests_before_create(digests):
    from types import SimpleNamespace

    m = implementation()
    client = SimpleNamespace(images=SimpleNamespace(
        get=lambda _: SimpleNamespace(attrs={"RepoDigests": digests})))
    with pytest.raises(m.MigrationIOError, match="migration_verifier_image_unavailable"):
        m.RestoreSandbox(client, uuid4(), uuid4(), "postgres@sha256:" + "a" * 64).__enter__()


@pytest.fixture(autouse=True)
def directory_fsync_on_windows(monkeypatch):
    # Production Linux fsync remains mandatory; Windows has no O_DIRECTORY.
    if os.name == "nt":
        monkeypatch.setattr(implementation(), "_sync_directory", lambda path: None)


def test_backup_roundtrip_is_private_bound_and_exclusive(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=4096)
    project, operation = uuid4(), uuid4()
    receipt = store.save(project, operation, b"private order rows", {"binding": "one"})
    assert store.load(project, operation, receipt, {"binding": "one"}) == b"private order rows"
    assert b"private order rows" not in store.path(project, operation).read_bytes()
    with pytest.raises(m.MigrationIOError):
        store.load(project, operation, receipt, {"binding": "other"})
    with pytest.raises(m.MigrationIOError):
        store.save(project, operation, b"replacement", {"binding": "one"})


def test_backup_owner_aad_cannot_be_rebound_even_to_another_allowed_owner(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=4096)
    project, operation = uuid4(), uuid4()
    aad = {"public_schema_owner": "omnia_project_owner"}
    receipt = store.save(project, operation, b"private fixture rows", aad)
    assert store.load(project, operation, receipt, aad) == b"private fixture rows"
    with pytest.raises(m.MigrationIOError):
        store.load(project, operation, receipt, {"public_schema_owner": "postgres"})


def test_backup_corruption_and_oversize_fail_without_plaintext(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=32)
    project, operation = uuid4(), uuid4()
    with pytest.raises(m.MigrationIOError):
        store.save(project, operation, b"x" * 33, {})
    assert not store.path(project, operation).exists()
    receipt = store.save(project, operation, b"a", {})
    path = store.path(project, operation)
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    with pytest.raises(m.MigrationIOError):
        store.load(project, operation, receipt, {})


def test_globals_only_removes_exact_bootstrap_postgres_create():
    m = implementation()
    assert m.restore_globals("CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER;") == (
        "ALTER ROLE postgres WITH SUPERUSER;"
    )
    with pytest.raises(m.MigrationIOError):
        m.restore_globals("CREATE ROLE postgres WITH LOGIN; ALTER ROLE postgres WITH SUPERUSER;")
    with pytest.raises(m.MigrationIOError):
        m.restore_globals("CREATE ROLE postgres; CREATE ROLE postgres;")


def test_backup_bundle_preserves_bytes_and_rejects_trailing_data():
    m = implementation()
    data = m.pack_backup(b"globals", b"\x00\xffPGDMP")
    assert m.unpack_backup(data) == (b"globals", b"\x00\xffPGDMP")
    with pytest.raises(m.MigrationIOError):
        m.unpack_backup(data + b"unexpected")


def test_sandbox_schema_mismatch_blocks_proof():
    m = implementation()
    with pytest.raises(m.MigrationIOError):
        m.require_schema("a" * 64, "b" * 64)


def test_project_backup_budget_rejects_unbounded_retention(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=32)
    project = uuid4()
    for _ in range(4):
        store.save(project, uuid4(), b"x" * 32, {})
    with pytest.raises(m.MigrationIOError):
        store.save(project, uuid4(), b"x", {})


@pytest.mark.parametrize("owner", ["postgres", "pg_database_owner", "omnia_project_owner"])
def test_restore_reinstates_only_public_schema_owner_after_archive(monkeypatch, owner):
    from types import SimpleNamespace

    m = implementation()
    calls = []
    monkeypatch.setattr(m, "restore_globals", lambda _: "GLOBALS;")
    pg = SimpleNamespace(
        sql=lambda statement: calls.append(("sql", statement)),
        command=lambda argv, **kwargs: calls.append(("archive", argv)),
        json=lambda _: owner,
    )
    m.restore_backup(pg, m.pack_backup(b"globals", b"database"), public_schema_owner=owner)
    assert calls == [
        ("sql", "GLOBALS;"),
        ("archive", ["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                     "-h", "/tmp", "-U", "postgres", "-d", "postgres"]),
        ("sql", 'ALTER SCHEMA public OWNER TO "' + owner + '";'),
    ]


@pytest.mark.parametrize("owner", [None, True, "unknown", 'postgres"; DROP SCHEMA public;--'])
def test_restore_owner_is_validated_before_any_sandbox_sql(owner):
    from types import SimpleNamespace

    m = implementation()
    calls = []
    pg = SimpleNamespace(sql=lambda value: calls.append(value))
    with pytest.raises(m.MigrationIOError):
        m.restore_backup(pg, b"invalid archive", public_schema_owner=owner)
    assert calls == []
