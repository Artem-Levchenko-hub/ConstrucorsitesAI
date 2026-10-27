"""Real PG16 transaction/backup tests; only fresh, nonce-owned network-none Docker.

Enable with QA_PUBLICATION_MIGRATION_DOCKER=1 and provision the immutable official
PG16-alpine image in QA_PUBLICATION_MIGRATION_PG_IMAGE first. An enabled gate never skips a
missing image/daemon. The only substituted boundaries are accepted source context
and Kubernetes placement: SQL, protocol framing, dumps, restore, fingerprints,
encrypted backup, transaction postconditions and exact-XID recovery are production.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import struct
import time
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("QA_PUBLICATION_MIGRATION_DOCKER") != "1",
    reason="explicit disposable publication migration Docker gate is disabled",
)

BASE_SQL = """
CREATE TABLE public.orders(id bigint PRIMARY KEY, amount integer NOT NULL, note text);
INSERT INTO public.orders VALUES(1,630,NULL),(2,650,'keep'),(3,650,NULL),(4,270,'pickup');
CREATE TABLE public.__omnia_project_migrations(
 name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now());
"""
ADDITIONS = """
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS pickup text;
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS comment text;
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS pickup_at timestamptz;
"""


class DockerExecChannel:
    """A Docker socket replacing Kubernetes websocket, retaining real SqlSession."""

    def __init__(self, client, container):
        from yleum_orchestrator.services.publication_migration_pg import psql_command

        self.client = client
        execution = client.api.exec_create(
            container.id,
            psql_command(seconds=60),
            stdin=True,
            stdout=True,
            stderr=True,
            tty=False,
            user="postgres",
        )
        self.execution_id = execution["Id"]
        self.connection = client.api.exec_start(self.execution_id, socket=True)
        self.sock = self.connection._sock
        self.sock.settimeout(0.2)
        self.pending = bytearray()
        self.closed = False

    def write(self, data):
        self.sock.sendall(data)

    def read(self, deadline):
        assert time.monotonic() < deadline, "owned Docker SQL channel deadline"
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError:
            return b"", b"", True
        self.pending.extend(chunk)
        stdout, stderr = bytearray(), bytearray()
        while len(self.pending) >= 8:
            stream, size = self.pending[0], struct.unpack("!I", self.pending[4:8])[0]
            assert stream in (1, 2) and self.pending[1:4] == b"\0\0\0"
            assert size <= 8 * 1024**2, "owned Docker SQL frame exceeded budget"
            if len(self.pending) < size + 8:
                break
            (stdout if stream == 1 else stderr).extend(self.pending[8 : 8 + size])
            del self.pending[: 8 + size]
        return bytes(stdout), bytes(stderr), bool(chunk)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()


class FaultSession:
    """Fault injection changes delivery or real uncommitted rows, never SQL results."""

    def __init__(self, target):
        from yleum_orchestrator.services.publication_migration_pg import SqlSession

        self.target = target
        self.pod_uid = target.container.id
        self.session = SqlSession(DockerExecChannel(target.client, target.container), seconds=65)
        self.migration = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()

    def sql(self, sql, **kwargs):
        if "omnia:project:migrations:v1" in sql:
            self.migration = True
        result = self.session.sql(sql, **kwargs)
        if self.migration and sql.startswith("ALTER TABLE") and self.target.fault == "rows":
            self.target.fault = None
            self.session.sql("UPDATE public.orders SET amount=1 WHERE id=1;")
        if self.migration and sql == "COMMIT;" and self.target.fault == "commit_ack":
            self.target.fault = None
            self.target.lost_acks += 1
            raise TimeoutError("injected lost acknowledgement after actual owned COMMIT")
        return result

    def json(self, sql):
        return json.loads(self.sql(sql))


class OwnedTarget:
    """Real owned PG transport behind the Kubernetes placement boundary."""

    def __init__(self, pg, image):
        self.pg, self.client, self.container = pg, pg.client, pg.container
        self.storage = {
            "image": image,
            "pvc_uid": str(uuid4()),
            "test_owned_container_id": pg.container.id,
        }
        self.fault = None
        self.lost_acks = 0

    def session(self):
        return FaultSession(self)

    def identify(self):
        from yleum_orchestrator.services.publication_migration_pg import pg_identity

        with self.session() as session:
            return {**self.storage, **pg_identity(session)}

    def command(self, argv, **kwargs):
        return self.pg.command(argv, **kwargs)

    def full_schema(self):
        return self.pg.full_schema()

    def globals(self):
        return self.pg.command(
            [
                "pg_dumpall",
                "--globals-only",
                "--no-role-passwords",
                "-h",
                "/tmp",
                "-U",
                "postgres",
            ]
        )


def assert_isolated(sandbox):
    sandbox.container.reload()
    attrs = sandbox.container.attrs
    host = attrs["HostConfig"]
    assert attrs["Config"]["Labels"] == sandbox.labels
    assert attrs["Config"]["User"] == "70:70"
    assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
    assert host["Privileged"] is False and host["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in host["SecurityOpt"]
    assert 0 < host["Memory"] <= 768 * 1024**2
    assert 0 < host["PidsLimit"] <= 64 and 0 < host["NanoCpus"] <= 1_000_000_000
    assert not host.get("Binds") and not host.get("PortBindings")
    assert all(mount["Type"] == "tmpfs" for mount in attrs.get("Mounts", []))
    assert set(host["Tmpfs"]) == {"/var/lib/postgresql/data", "/tmp", "/var/run/postgresql"}


@pytest.fixture
def real_migration(tmp_path, monkeypatch):
    # Validate explicit local-only authority before opening Docker or importing POSIX code.
    image = os.environ.get("QA_PUBLICATION_MIGRATION_PG_IMAGE", "")
    assert re.fullmatch(r"(?:(?:docker\.io/)?library/)?postgres@sha256:[0-9a-f]{64}", image), (
        "QA_PUBLICATION_MIGRATION_PG_IMAGE must be a provisioned official immutable digest"
    )
    assert os.name == "posix", "enabled Docker migration gate requires Linux"
    assert os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock") == (
        "unix:///var/run/docker.sock"
    ), "remote Docker endpoints are forbidden in this test"
    import docker

    from tests.test_cell_publication import request
    from tests.test_project_machine_manifest import payload
    from tests.test_publication_migration_backup_docker import (
        configure_isolated_settings,
        diagnose_first_empty_sandbox,
    )
    from yleum_orchestrator.core.project_machine import MachineManifest
    from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock
    from yleum_orchestrator.services.project_machine import write_controller_json
    from yleum_orchestrator.services.publication_migration import PublicationMigrationService
    from yleum_orchestrator.services.publication_migration_backup import RestoreSandbox
    from yleum_orchestrator.services.publication_migration_io import PublicationMigrationIO
    from yleum_orchestrator.services.published_machine_backend import data_contract_digest

    configure_isolated_settings(monkeypatch)
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=20)
    diagnose_first_empty_sandbox(monkeypatch, client)
    source_sandbox = target_sandbox = None
    try:
        assert client.ping() and client.info()["OSType"] == "linux"
        assert image in client.images.get(image).attrs["RepoDigests"]
        value = request(project_id=uuid4(), owner_id=uuid4(), snapshot_id=uuid4())
        source_sandbox = RestoreSandbox(client, value.project_id, uuid4(), image)
        target_sandbox = RestoreSandbox(client, value.project_id, uuid4(), image)
        with source_sandbox as source_pg, target_sandbox as target_pg:
            assert_isolated(source_sandbox)
            assert_isolated(target_sandbox)
            source_pg.sql(BASE_SQL)
            target_pg.sql(BASE_SQL)
            source_pg.sql(ADDITIONS)
            target = OwnedTarget(target_pg, image)
            assert target.identify()["pg_version"] == "16"
            manifest = MachineManifest.model_validate(payload())
            files = {"drizzle/0020_pickup.sql": ADDITIONS}
            source = SimpleNamespace(client=client, _project_postgres=lambda: source_pg.container)
            active = {
                "release_id": str(uuid4()),
                "epoch": 1,
                "data_contract_digest": data_contract_digest(manifest),
                "schema_digest": target.full_schema(),
            }
            manager = SimpleNamespace(operation_lock=WorkspaceOperationLock(tmp_path / "locks"))
            service = SimpleNamespace(
                root=tmp_path / "controller",
                _manager=lambda _: manager,
                _accepted_schema_digest=lambda release: release["schema_digest"],
                settings=SimpleNamespace(cell_postgres_image=image),
                _submission_lock=WorkspaceOperationLock(tmp_path / "controller"),
                _read=lambda _: {
                    "owner_id": str(value.owner_id),
                    "history": [],
                    "active_release": active,
                },
            )

            class IO(PublicationMigrationIO):
                async def _source(self, *_):
                    return source, manifest, files, {"schema_digest": source_pg.full_schema()}

                def _target(self, request=None, prepared=None):
                    # The real Kubernetes identity adapter is separately unit tested.
                    # Real server identity/XID/catalog still come from this owned PG.
                    return target, active

            saved = {}
            journal = tmp_path / "controller" / "integration-intent.json"

            def persist(patch):
                saved.update(deepcopy(patch))
                write_controller_json(journal, saved)

            yield SimpleNamespace(
                io=IO(service),
                controller=PublicationMigrationService(service, io=IO(service)),
                new_io=lambda: IO(service),
                request=value,
                source=source_pg,
                live=target_pg,
                target=target,
                saved=saved,
                persist=persist,
                journal=journal,
                active=active,
                image=image,
            )
        assert source_sandbox.cleaned and target_sandbox.cleaned
        # Include verifier containers created by the production prepare path.
        assert (
            client.containers.list(
                all=True,
                filters={
                    "label": "omnia.project_id=" + str(value.project_id),
                },
            )
            == []
        ), "owned migration resources remain after cleanup"
    finally:
        client.close()


async def prepare(case):
    prepared = await case.io.prepare(case.request, uuid4())
    case.saved.update(deepcopy(prepared))
    assert prepared["backup_verified"] and prepared["sandbox_cleanup_verified"]
    assert prepared["sandbox_before_sha256"] == case.live.full_schema()
    assert prepared["sandbox_after_sha256"] == case.source.full_schema()
    assert len(prepared["public_fields"]) == 3
    return prepared


async def test_real_prepare_apply_preserves_old_rows_and_concurrent_insert(real_migration):
    case = real_migration
    from yleum_orchestrator.services.publication_migration_pg import read_catalog, row_proofs

    original_tables = read_catalog(case.live)
    original = row_proofs(case.live, original_tables)
    status = await case.controller.prepare(case.request, str(uuid4()))
    await case.controller.drain()
    status = case.controller.get(case.request.project_id, case.request.owner_id)
    assert status.state == "prepared" and status.backup_verified and len(status.fields) == 3
    prepared = case.controller._load(case.request.project_id, status.operation_id)["prepared"]
    assert prepared["sandbox_cleanup_verified"]
    assert prepared["sandbox_after_sha256"] == case.source.full_schema()
    assert case.live.full_schema() == prepared["binding"]["live_schema_sha256"]
    assert row_proofs(case.live, original_tables) == original
    # A live insert after backup is part of the execution proof, never overwritten by restore.
    case.live.sql("INSERT INTO public.orders VALUES(5,250,'concurrent-after-backup');")
    before_apply = row_proofs(case.live, original_tables)
    await case.controller.confirm(case.request, status.operation_id, status.plan_sha256, True)
    await case.controller.drain()
    outcome = case.controller.get(case.request.project_id, case.request.owner_id)
    assert outcome.state == "applied" and outcome.effect_possible is False
    case.controller.require_no_active(case.request.project_id)
    assert case.live.full_schema() == case.source.full_schema()
    assert row_proofs(case.live, original_tables) == before_apply
    assert case.live.json("SELECT to_json(array_agg(amount ORDER BY id)) FROM public.orders;") == [
        630,
        650,
        650,
        270,
        250,
    ]
    assert case.live.json("SELECT count(*) FROM public.orders WHERE pickup IS NOT NULL;") == 0
    case.live.sql("INSERT INTO public.orders(id,amount,pickup) VALUES(6,100,'scheduled');")
    assert case.live.json("SELECT to_json(pickup) FROM public.orders WHERE id=6;") == "scheduled"


@pytest.mark.parametrize(
    "unsupported",
    [
        "ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;"
        "CREATE POLICY visible ON public.orders USING(amount>0);",
        "CREATE FUNCTION public.unrelated() RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT 1';",
    ],
    ids=["policy-drift", "function-drift"],
)
async def test_real_unsupported_schema_drift_blocks_before_live_ddl(real_migration, unsupported):
    from yleum_orchestrator.services.publication_migration_pg import read_catalog, row_proofs

    case = real_migration
    before_schema = case.live.full_schema()
    tables = read_catalog(case.live)
    before_rows = row_proofs(case.live, tables)
    case.source.sql(unsupported)
    with pytest.raises(ValueError):
        await case.io.prepare(case.request, uuid4())
    assert case.live.full_schema() == before_schema
    assert row_proofs(case.live, tables) == before_rows
    assert not case.saved


async def test_real_postcondition_failure_rolls_back_columns_and_changed_rows(real_migration):
    from yleum_orchestrator.services.publication_migration_pg import read_catalog, row_proofs

    case = real_migration
    prepared = await prepare(case)
    tables = read_catalog(case.live)
    original = row_proofs(case.live, tables)
    case.target.fault = "rows"
    outcome = await case.io.apply(case.request, prepared, case.persist)
    deadline = time.monotonic() + 8
    while outcome["effect_possible"] and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        outcome = await case.new_io().reconcile(case.saved)
    assert outcome["state"] == "failed" and outcome["effect_possible"] is False
    assert case.saved["intent_xid"] and not case.saved["precommit_verified"]
    assert case.live.full_schema() == prepared["binding"]["live_schema_sha256"]
    assert row_proofs(case.live, tables) == original


async def test_real_lost_commit_ack_recovers_exact_xid_and_rejects_incomplete_proof(real_migration):
    case = real_migration
    prepared = await prepare(case)
    case.target.fault = "commit_ack"
    outcome = await case.io.apply(case.request, prepared, case.persist)
    assert case.target.lost_acks == 1 and outcome["state"] == "applied"
    persisted = json.loads(case.journal.read_text())
    xid = persisted["intent_xid"]
    assert re.fullmatch(r"[0-9]{1,20}", xid)
    with case.target.session() as session:
        assert session.json("SELECT to_json(pg_xact_status('" + xid + "'::xid8));") == "committed"
    recovered = await case.new_io().reconcile(persisted)
    assert recovered["state"] == "applied" and recovered["effect_possible"] is False
    assert recovered["outcome_proof"] == {"kind": "xid_committed_verified", "xid": xid}
    for invalid in (
        {**persisted, "precommit_verified": False},
        {**persisted, "intent_xid": None},
        {**persisted, "storage": {**persisted["storage"], "pvc_uid": str(uuid4())}},
    ):
        rejected = await case.new_io().reconcile(invalid)
        assert rejected["state"] == "reconciling" and rejected["effect_possible"] is True
    assert case.target.lost_acks == 1
    assert case.live.full_schema() == case.source.full_schema()
