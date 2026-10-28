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
        def __init__(self, dumps):
            self.dumps = iter(dumps)
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
            return "00000001-1"

    source_pg = PG([source_dump, b"\n" + source_dump if stage == "source_recheck" else source_dump])
    target = PG([live_dump, live_dump, b"\n" + live_dump if stage == "live_recheck" else live_dump])
    restored = PG([
        b"\n" + live_dump if stage == "backup_restore" else live_dump,
        b"\n" + source_dump if stage == "trial_migration" else source_dump,
    ])

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
    monkeypatch.setattr(m, "PrivateBackup", lambda *args: SimpleNamespace(
        save=lambda *args: {"sha256": "b" * 64}, load=lambda *args: b"authenticated-unit-archive",
    ))
    monkeypatch.setattr(m, "restore_backup", lambda *args: None)
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
        io._prepare(*arguments)
    else:
        with pytest.raises(SchemaMismatchError) as caught:
            io._prepare(*arguments)
        assert caught.value.evidence["stage"] == stage
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
