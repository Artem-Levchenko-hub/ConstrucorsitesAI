"""No migration replay or column-based inference of ambiguous commit success."""

import importlib
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_orchestrator.services.publication_additive_plan import MigrationBinding, plan_additive


def implementation():
    name = "yleum_orchestrator.services.publication_migration_io"
    assert importlib.util.find_spec(name), "publication IO implementation missing"
    return importlib.import_module(name)


def plan():
    binding = MigrationBinding(
        uuid4(),
        uuid4(),
        uuid4(),
        "a" * 40,
        "b" * 64,
        uuid4(),
        1,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
    )
    return plan_additive(
        binding, "ALTER TABLE public.orders ADD COLUMN pickup text", {"orders": ("id",)}
    )


@pytest.mark.parametrize("stage,counts", [
    ("backup_restore", (1, 2, 1)),
    ("trial_migration", (1, 2, 2)),
    ("live_recheck", (1, 3, 2)),
    ("source_recheck", (2, 3, 2)),
    (None, (2, 3, 2)),
    ("owner_unsupported", (0, 0, 0)),
    ("owner_live_mismatch", (1, 1, 0)),
    ("owner_source_recheck", (2, 3, 2)),
    ("owner_live_recheck", (2, 3, 2)),
])
def test_prepare_schema_stages_keep_existing_rpc_count_and_exact_rejection(
    monkeypatch, tmp_path, stage, counts,
):
    """Exercise real preparation control flow; transports/catalog/backup are unit boundaries."""
    from tests.test_cell_publication import request
    from yleum_orchestrator.services.project_database_schema_proof import legacy_schema_digest
    from yleum_orchestrator.services.publication_migration_pg import schema_command
    from yleum_orchestrator.services.publication_migration_schema import SchemaMismatchError

    m = implementation()
    image = "postgres@sha256:" + "a" * 64
    storage = {"image": image, "pvc_uid": str(uuid4())}
    live_dump, source_dump = b"SELECT 1;", b"SELECT 2;"

    class PG:
        def __init__(self, dumps, owners=("omnia_project_owner", "omnia_project_owner")):
            self.dumps = iter(dumps)
            self.owners = iter(owners)
            self.calls = 0

        def command(self, argv, **kwargs):
            if argv == schema_command():
                self.calls += 1
                return next(self.dumps)
            assert argv[0] == "pg_dump"
            return b"archive"

        def full_schema(self):
            return legacy_schema_digest(self.command(schema_command()).decode())

        def identify(self):
            return storage

        def globals(self):
            return b"globals"

        def session(self):
            return nullcontext(self)

        def sql(self, sql):
            return b""

        def json(self, sql):
            if "pg_advisory_unlock" in sql:
                return True
            if "pg_catalog.pg_namespace" in sql:
                return next(self.owners)
            return "00000001-1"

    source_pg = PG([source_dump, b"\n" + source_dump if stage == "source_recheck" else source_dump])
    target = PG([live_dump, live_dump, b"\n" + live_dump if stage == "live_recheck" else live_dump])
    restored = PG([
        b"\n" + live_dump if stage == "backup_restore" else live_dump,
        b"\n" + source_dump if stage == "trial_migration" else source_dump,
    ])
    if stage == "owner_unsupported":
        source_pg.owners = iter(["unknown"])
    elif stage == "owner_live_mismatch":
        target.owners = iter(["postgres"])
    elif stage == "owner_source_recheck":
        source_pg.owners = iter(["omnia_project_owner", "postgres"])
    elif stage == "owner_live_recheck":
        target.owners = iter(["omnia_project_owner", "postgres"])

    class Sandbox:
        cleaned = False

        def __enter__(self):
            return restored

        def __exit__(self, *_):
            self.cleaned = True

    sandbox = Sandbox()
    monkeypatch.setattr(m, "DockerPG", lambda *args: source_pg)
    monkeypatch.setattr(m, "RestoreSandbox", lambda *args: sandbox)
    monkeypatch.setattr(m, "fingerprint_key", lambda _: b"x" * 32)
    backup_writes = []

    def save_backup(*args):
        backup_writes.append(args)
        return {"sha256": "b" * 64}

    monkeypatch.setattr(m, "PrivateBackup", lambda *args: SimpleNamespace(
        save=save_backup, load=lambda *args: b"authenticated-unit-archive",
    ))
    monkeypatch.setattr(m, "restore_backup", lambda *args, **kwargs: None)
    monkeypatch.setattr(m, "read_catalog", lambda _: ())
    monkeypatch.setattr(m, "_receipt", lambda *args: None)
    monkeypatch.setattr(m, "row_proofs", lambda *args: {})
    monkeypatch.setattr(m, "_ledger", lambda _: {})
    monkeypatch.setattr(m, "verify_pending", lambda *args: {})
    monkeypatch.setattr(m, "resolve_additive", lambda *args, **kwargs: SimpleNamespace(
        statements=("ALTER TABLE public.orders ADD COLUMN pickup text",),
        catalog=(), sha256="c" * 64,
    ))
    # Isolate the contract collaborator, including its Linux-only Docker imports.
    monkeypatch.setitem(
        sys.modules, "yleum_orchestrator.services.published_machine_backend",
        SimpleNamespace(data_contract_digest=lambda _: "d" * 64),
    )
    service = SimpleNamespace(
        root=tmp_path, settings=SimpleNamespace(cell_postgres_image=image),
        _accepted_schema_digest=lambda _: legacy_schema_digest(live_dump.decode()),
    )
    io = m.PublicationMigrationIO(service)
    monkeypatch.setattr(io, "_target", lambda _: (
        target, {"data_contract_digest": "d" * 64, "release_id": str(uuid4()), "epoch": 1},
    ))
    source = SimpleNamespace(client=None, _project_postgres=lambda: object())
    arguments = (request(), uuid4(), source, None, {},
                 {"schema_digest": legacy_schema_digest(source_dump.decode())})
    if stage is None:
        prepared = io._prepare(*arguments)
        assert prepared["public_schema_owner"] == "omnia_project_owner"
        assert prepared["backup_aad"]["public_schema_owner"] == "omnia_project_owner"
    elif stage.startswith("owner_"):
        with pytest.raises(m.MigrationIOError, match="migration_public_schema_owner_"):
            io._prepare(*arguments)
    else:
        with pytest.raises(SchemaMismatchError) as caught:
            io._prepare(*arguments)
        assert caught.value.evidence["stage"] == stage
    if stage in {"owner_unsupported", "owner_live_mismatch"}:
        assert not backup_writes and not sandbox.cleaned
    else:
        assert sandbox.cleaned
    assert (source_pg.calls, target.calls, restored.calls) == counts


def test_replayed_migration_must_be_only_exact_idempotent_additions():
    m = implementation()
    p = plan()
    result = m.verify_pending(
        {"drizzle/0004.sql": "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS pickup text;"},
        {},
        p,
    )
    assert list(result) == ["drizzle/0004.sql"]
    for sql in (
        "ALTER TABLE public.orders ADD COLUMN pickup text;",
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS pickup text; DELETE FROM orders;",
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS other text;",
    ):
        with pytest.raises(m.MigrationIOError):
            m.verify_pending({"drizzle/0004.sql": sql}, {}, p)


@pytest.mark.parametrize(
    "status,precommit,match,state,effect",
    [
        ("committed", True, True, "applied", False),
        ("committed", False, True, "reconciling", True),
        ("committed", True, False, "reconciling", True),
        ("aborted", False, False, "failed", False),
        ("in progress", True, True, "reconciling", True),
        (None, True, True, "reconciling", True),
    ],
)
def test_only_exact_xid_outcome_clears_exclusion(status, precommit, match, state, effect):
    m = implementation()
    result = m.recovery_patch(status, precommit=precommit, schema_matches=match, xid="42")
    assert result["state"] == state
    assert result["effect_possible"] is effect
    if not effect:
        assert result["outcome_proof"]["xid"] == "42"


def test_status_without_recorded_xid_never_clears_exclusion():
    m = implementation()
    for status in ("aborted", "committed"):
        result = m.recovery_patch(status, precommit=True, schema_matches=True)
        assert result["effect_possible"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unlock", "worker_deadline"])
async def test_post_effect_transport_or_unlock_failure_never_clears_exclusion(monkeypatch, failure):
    m = implementation()

    class Lock:
        def hold(self, _):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            if failure == "unlock":
                raise OSError("unlock failed")

    service = SimpleNamespace(_manager=lambda _: SimpleNamespace(operation_lock=Lock()))
    io = m.PublicationMigrationIO(service)

    async def source(*_):
        return (None, None, None, None)

    def effect(request, prepared, persist, *_):
        persist({"intent_xid": "42", "effect_possible": True})
        return {"state": "reconciling", "effect_possible": True}

    async def run(call, *args):
        result = call(*args)
        if failure == "worker_deadline":
            raise TimeoutError("worker already ran")
        return result

    monkeypatch.setattr(io, "_source", source)
    monkeypatch.setattr(io, "_apply", effect)
    monkeypatch.setattr(m, "machine_effect", run)
    patches = []
    result = await io.apply(SimpleNamespace(workspace_id=uuid4()), {}, patches.append)
    assert patches and patches[0]["intent_xid"] == "42"
    assert result["effect_possible"] is True
    assert result["state"] == "reconciling"


@pytest.mark.parametrize("owner,aad", [
    (None, {}), (True, {}), ("unknown", {}), ("postgres", None),
    ("postgres", {}), ("postgres", {"public_schema_owner": True}),
    ("postgres", {"public_schema_owner": "omnia_project_owner"}),
])
def test_invalid_private_owner_binding_fails_before_intent(monkeypatch, owner, aad):
    m = implementation()
    monkeypatch.setitem(sys.modules, "yleum_orchestrator.services.published_machine_backend",
                        SimpleNamespace(data_contract_digest=lambda _: "unused"))
    prepared = {"effect_possible": False, "public_schema_owner": owner, "backup_aad": aad}
    writes = []
    result = m.PublicationMigrationIO(None)._apply(
        None, prepared, writes.append, None, None, {}, {},
    )
    assert not writes
    assert result["effect_possible"] is False
    assert result["outcome_proof"] == {"kind": "preflight_no_dispatch"}


@pytest.mark.parametrize("drift", ["source", "preflight", "transaction", "precommit", None])
def test_apply_owner_drift_is_checked_before_intent_and_before_commit(monkeypatch, drift):
    from datetime import UTC, datetime, timedelta

    m = implementation()
    owner, other = "omnia_project_owner", "postgres"
    p = plan()
    statements, writes, recovery = [], [], []
    source_pg = SimpleNamespace(
        json=lambda _: other if drift == "source" else owner,
        full_schema=lambda: p.binding.target_schema_sha256,
    )
    owners = iter(other if drift == stage else owner
                  for stage in ("preflight", "transaction", "precommit"))

    def query(sql):
        if "pg_catalog.pg_namespace" in sql:
            return next(owners)
        return {"xid": "42", "pid": 1, "start": "start"}

    session = SimpleNamespace(sql=statements.append, json=query, pod_uid="pod")
    target = SimpleNamespace(
        session=lambda: nullcontext(session), identify=lambda: {}, globals=lambda: b"globals",
        full_schema=lambda: p.binding.live_schema_sha256,
    )
    monkeypatch.setitem(
        sys.modules, "yleum_orchestrator.services.published_machine_backend",
        SimpleNamespace(data_contract_digest=lambda _: p.binding.data_contract_sha256),
    )
    monkeypatch.setattr(m, "_source_binding", lambda _: {})
    monkeypatch.setattr(m, "_unpack_plan", lambda _: p)
    monkeypatch.setattr(m, "authorize_plan", lambda *args, **kwargs: p.statements)
    monkeypatch.setattr(m, "fingerprint_key", lambda _: b"x" * 32)
    monkeypatch.setattr(m, "PrivateBackup", lambda *args: SimpleNamespace(load=lambda *args: b"ok"))
    monkeypatch.setattr(m, "DockerPG", lambda *args: source_pg)
    monkeypatch.setattr(m, "read_catalog", lambda _: ())
    monkeypatch.setattr(m, "row_proofs", lambda *args: {})
    monkeypatch.setattr(m, "_ledger", lambda _: {})
    monkeypatch.setattr(m, "verify_pending", lambda *args: {})
    monkeypatch.setattr(m, "pg_identity", lambda _: {})
    io = m.PublicationMigrationIO(SimpleNamespace(root=None))
    monkeypatch.setattr(io, "_target", lambda *args: (target, {}))
    monkeypatch.setattr(io, "_reconcile", lambda context: recovery.append(context) or {
        "state": "reconciling", "effect_possible": True,
    })
    prepared = {
        "effect_possible": False, "source_binding": {},
        "public_schema_owner": owner, "backup_aad": {"public_schema_owner": owner},
        "expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "plan_sha256": p.sha256, "backup_verified": True, "sandbox_cleanup_verified": True,
        "sandbox_before_sha256": p.binding.live_schema_sha256,
        "sandbox_after_sha256": p.binding.target_schema_sha256,
        "operation_id": str(uuid4()), "backup": {}, "storage": {},
        "globals_sha256": m.legacy_globals_hash(b"globals"),
        "live_catalog": [], "target_catalog": [], "pending_migrations": {},
    }
    request = SimpleNamespace(owner_id=p.binding.owner_id, project_id=p.binding.project_id,
                              snapshot_id=p.binding.snapshot_id)
    source = SimpleNamespace(client=None, _project_postgres=lambda: None)
    result = io._apply(request, prepared, writes.append, source, None, {},
                       {"schema_digest": p.binding.target_schema_sha256})
    if drift in {"source", "preflight", "transaction"}:
        assert not writes and not recovery
        assert result["effect_possible"] is False
        assert not any(sql.startswith("ALTER TABLE") for sql in statements)
    else:
        assert writes[0]["intent_xid"] == "42" and recovery
        assert result["effect_possible"] is True
        assert ("COMMIT;" in statements) is (drift is None)
        assert any(patch.get("precommit_verified") is True for patch in writes) is (drift is None)


@pytest.mark.parametrize("actual,metadata", [
    ("omnia_project_owner", True), ("postgres", True), ("omnia_project_owner", False),
])
def test_committed_xid_and_schema_need_exact_owner_before_clearing_effect(
    monkeypatch, actual, metadata,
):
    from datetime import UTC, datetime

    m = implementation()
    prepared = {
        "intent_xid": "42", "intent_at": datetime.now(UTC).isoformat(), "storage": {},
        "plan_sha256": "a" * 64, "precommit_plan_sha256": "a" * 64,
        "precommit_verified": True, "binding": {"target_schema_sha256": "b" * 64},
    }
    if metadata:
        prepared.update(public_schema_owner="omnia_project_owner",
                        backup_aad={"public_schema_owner": "omnia_project_owner"})
    statements = []
    session = SimpleNamespace(
        json=lambda sql: actual if "pg_namespace" in sql else "committed", sql=statements.append,
    )
    target = SimpleNamespace(identify=lambda: {}, session=lambda: nullcontext(session),
                             full_schema=lambda: "b" * 64)
    io = m.PublicationMigrationIO(None)
    monkeypatch.setattr(io, "_target", lambda **kwargs: (target, {}))
    result = io._reconcile(prepared)
    accepted = metadata and actual == "omnia_project_owner"
    assert result["state"] == ("applied" if accepted else "reconciling")
    assert result["effect_possible"] is (not accepted)
    assert statements[0].startswith("BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY;")
    if accepted:
        assert statements[-1] == "ROLLBACK;"


@pytest.mark.parametrize("failed,unlock", [(False, True), (True, True), (False, False)])
def test_backup_snapshot_acquires_lock_before_rr_and_explicitly_hands_it_off(failed, unlock):
    m = implementation()
    calls = []

    def sql(text):
        calls.append(text)
        if failed and text.startswith("BEGIN"):
            raise RuntimeError("aborted transaction")

    def query(text):
        calls.append(text)
        return unlock

    session = SimpleNamespace(sql=sql, json=query)
    if failed or not unlock:
        with pytest.raises((RuntimeError, m.MigrationIOError)):
            m._begin_backup_snapshot(session)
    else:
        m._begin_backup_snapshot(session)
    assert calls[0].startswith("SELECT pg_catalog.pg_advisory_lock(")
    assert calls[1].startswith("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
    assert "pg_advisory_xact_lock" in calls[1]
    assert "pg_advisory_unlock" in calls[-1]
    assert ("ROLLBACK;" in calls) is failed
