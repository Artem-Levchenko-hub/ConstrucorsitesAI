import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tests.test_cell_publication import request
from yleum_orchestrator.core.cell_resources import CellIdentityConflict, WorkspaceLockTimeout
from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock


class IO:
    def __init__(self):
        self.prepares = self.applies = self.observations = 0
        self.outcome = committed_outcome()

    async def prepare(self, request, operation_id):
        self.prepares += 1
        return {
            "operation_id": str(operation_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
            "snapshot_id": str(request.snapshot_id),
            "plan_sha256": "a" * 64,
            "public_fields": [{"table": "orders", "name": "note", "type": "text"}],
            "backup_verified": True,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "private_test_canary": "never-public",
        }

    async def apply(self, request, prepared, persist):
        self.applies += 1
        persist({"intent_xid": "91234", "effect_possible": True})
        persist({"precommit_verified": True, "precommit_plan_sha256": prepared["plan_sha256"]})
        return self.outcome

    async def reconcile(self, prepared):
        self.observations += 1
        return self.outcome


def committed_outcome():
    return {
        "state": "applied",
        "effect_possible": False,
        "outcome_proof": {"kind": "xid_committed_verified", "xid": "91234"},
    }


def setup(tmp_path):
    from yleum_orchestrator.services.publication_migration import PublicationMigrationService

    saved = {
        "project_id": str(request().project_id),
        "owner_id": str(request().owner_id),
        "history": [],
        "active_release": {"release_id": "existing"},
    }
    publication = SimpleNamespace(
        root=tmp_path, _submission_lock=WorkspaceOperationLock(tmp_path), _read=lambda _: saved
    )
    io = IO()
    return PublicationMigrationService(publication, io=io), publication, io


async def prepared(service):
    status = await service.prepare(request(), str(uuid4()))
    assert status.state == "preparing"
    await service.drain()
    return service.get(request().project_id, request().owner_id)


async def test_prepare_is_durable_idempotent_and_public_projection_is_safe(tmp_path):
    service, _, io = setup(tmp_path)
    key = str(uuid4())
    first = await service.prepare(request(), key)
    assert service.get(request().project_id, request().owner_id).operation_id == first.operation_id
    second = await service.prepare(request(), key)
    assert second.operation_id == first.operation_id
    await service.drain()
    status = service.get(request().project_id, request().owner_id)
    assert status.state == "prepared" and status.backup_verified
    assert "never-public" not in status.model_dump_json()
    assert io.prepares == 1


@pytest.mark.parametrize("code", [
    "migration_verifier_image_untrusted", "migration_verifier_image_unavailable",
    "migration_full_schema_mismatch", "migration_docker_sql_failed",
])
async def test_preparation_records_exact_owned_failure_code_privately(tmp_path, code):
    from yleum_orchestrator.services.publication_migration_pg import MigrationIOError

    service, _, io = setup(tmp_path)

    async def failed(*args):
        raise MigrationIOError(code)

    io.prepare = failed
    status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    assert record["failure_diagnostic"] == {
        "phase": "preparation", "type": "MigrationIOError",
        "code": code,
    }
    assert status.state == "failed" and not status.effect_possible
    assert status.error_code == "migration_preparation_failed"
    assert "failure_diagnostic" not in status.model_dump_json()
    assert code not in status.model_dump_json()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
async def test_schema_evidence_is_private_and_never_releases_unknown_effect(
    tmp_path, apply, malformed,
):
    from yleum_orchestrator.services.publication_migration_schema import require_schema_dump

    service, _, io = setup(tmp_path)

    async def failed(*args):
        if apply:
            args[2]({"intent_xid": "12345", "effect_possible": True})
        observed = "SELECT 'private_schema_canary" + ("" if malformed else "';")
        require_schema_dump(observed, "SELECT 1;", stage="backup_restore")

    if apply:
        status = await prepared(service)
        io.apply = failed
        await service.confirm(request(), status.operation_id, status.plan_sha256, True)
        await service.drain()
        status = service.get(request().project_id, request().owner_id)
    else:
        io.prepare = failed
        status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    diagnostic = record["failure_diagnostic"]
    assert diagnostic["type"] == "SchemaMismatchError"
    assert diagnostic["code"] == "migration_full_schema_mismatch"
    assert diagnostic["schema"]["stage"] == "backup_restore"
    assert diagnostic["schema"]["first_difference"]["observed_kind"] == "sql_other"
    assert diagnostic["schema"]["same_nonblank_lines"] is False
    assert diagnostic["schema"]["same_statement_stream"] is (None if malformed else False)
    assert record["state"] == ("reconciling" if apply else "failed")
    assert record["effect_possible"] is apply
    expected_code = "migration_outcome_unknown" if apply else "migration_preparation_failed"
    assert status.error_code == expected_code
    public = status.model_dump_json()
    for hidden in (
        "SchemaMismatchError", "backup_restore", "observed_sha256", "failure_diagnostic",
        "same_nonblank_lines", "same_statement_stream",
    ):
        assert hidden not in public
    assert "private_schema_canary" not in service._path(
        request().project_id, status.operation_id,
    ).read_text()


@pytest.mark.parametrize("damage", [
    "extra", "kind", "args", "subclass", "same_nonblank_lines", "same_statement_stream",
])
def test_schema_diagnostic_boundary_rejects_tampered_owned_attributes(damage):
    from yleum_orchestrator.services.publication_migration import _failure_diagnostic
    from yleum_orchestrator.services.publication_migration_schema import (
        SchemaMismatchError,
        require_schema_dump,
    )

    with pytest.raises(SchemaMismatchError) as caught:
        require_schema_dump("SELECT 1;", "SELECT 2;", stage="source_recheck")
    error = caught.value
    if damage == "extra":
        error.evidence["raw_sql"] = "private_schema_canary"
    elif damage == "kind":
        error.evidence["first_difference"]["observed_kind"] = "private_schema_canary"
    elif damage == "args":
        error.args = ("private_schema_canary",)
    elif damage.startswith("same_"):
        error.evidence[damage] = "private_schema_canary"
    else:
        error = type("private_schema_canary", (SchemaMismatchError,), {})(error.evidence)
    result = _failure_diagnostic(error, apply=False)
    assert "schema" not in result
    assert "private_schema_canary" not in repr(result)
    if damage == "subclass":
        assert result["type"] == "OtherError"


@pytest.mark.parametrize(
    "kind", ["owned_unknown", "multi_args", "foreign_owned_name", "private_type", "cancelled"]
)
async def test_failure_diagnostic_rejects_untrusted_messages_and_types(tmp_path, kind):
    from yleum_orchestrator.services.publication_migration_pg import MigrationIOError

    service, _, io = setup(tmp_path)
    if kind == "owned_unknown":
        error = MigrationIOError("migration_secret_canary_lowercase")
        expected = "MigrationIOError"
    elif kind == "multi_args":
        error = MigrationIOError("migration_verifier_image_untrusted", "secret_canary")
        expected = "MigrationIOError"
    elif kind == "cancelled":
        error = asyncio.CancelledError("secret_canary")
        expected = "CancelledError"
    else:
        name = "MigrationIOError" if kind == "foreign_owned_name" else "secret_canary_type"
        error = type(name, (Exception,), {"__str__": lambda _: pytest.fail("must not stringify")})(
            "migration_verifier_image_untrusted", "secret_canary")
        expected = "OtherError"

    async def failed(*args):
        raise error

    io.prepare = failed
    status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    assert record["failure_diagnostic"] == {"phase": "preparation", "type": expected}
    journal = service._path(request().project_id, status.operation_id).read_text()
    assert "secret_canary" not in journal
    assert status.state == "failed" and not status.effect_possible
    assert status.error_code == "migration_preparation_failed"


async def test_apply_exception_diagnostic_preserves_unknown_effect_exclusion(tmp_path):
    service, _, io = setup(tmp_path)
    status = await prepared(service)

    async def failed(request, prepared, persist):
        persist({"intent_xid": "12345", "effect_possible": True})
        raise TimeoutError("private SQL URL secret_canary")

    io.apply = failed
    await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    await service.drain()
    record = service._load(request().project_id, status.operation_id)
    assert record["failure_diagnostic"] == {"phase": "apply", "type": "TimeoutError"}
    assert record["state"] == "reconciling" and record["effect_possible"] is True
    assert record["prepared"]["intent_xid"] == "12345"
    assert record["error_code"] == "migration_outcome_unknown"
    journal = service._path(request().project_id, status.operation_id).read_text()
    assert "secret_canary" not in journal


async def test_explicit_intent_exact_hash_and_request_binding_required(tmp_path):
    service, _, io = setup(tmp_path)
    status = await prepared(service)
    for value, digest, intent in [
        (request(), "a" * 64, False),
        (request(), "b" * 64, True),
        (request(snapshot_id=uuid4()), "a" * 64, True),
        (request(owner_id=uuid4()), "a" * 64, True),
    ]:
        with pytest.raises(CellIdentityConflict):
            await service.confirm(value, status.operation_id, digest, intent)
    assert io.applies == 0


async def test_confirm_repeats_do_not_replay_effect(tmp_path):
    service, _, io = setup(tmp_path)
    status = await prepared(service)
    for _ in range(2):
        await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    await service.drain()
    result = await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    assert result.state == "applied" and not result.effect_possible
    assert io.applies == 1


async def test_unknown_outcome_survives_restart_and_only_observes(tmp_path):
    from yleum_orchestrator.services.publication_migration import PublicationMigrationService

    service, publication, io = setup(tmp_path)
    status = await prepared(service)
    io.outcome = {"state": "blocked", "effect_possible": True}
    await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    await service.drain()
    restarted = PublicationMigrationService(publication, io=io)
    with pytest.raises(CellIdentityConflict):
        restarted.require_no_active(request().project_id)
    before = restarted.get(request().project_id, request().owner_id).model_dump_json()
    assert restarted.get(request().project_id, request().owner_id).model_dump_json() == before
    assert io.observations == 0
    io.outcome = committed_outcome()
    await restarted.reconcile()
    restarted.require_no_active(request().project_id)
    assert io.observations == 1 and io.applies == 1


async def test_expired_confirmation_stales_without_effect(tmp_path):
    service, _, io = setup(tmp_path)
    status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    record["prepared"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    service._save(record)
    with pytest.raises(CellIdentityConflict):
        await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    assert service.get(request().project_id, request().owner_id).state == "stale"
    assert io.applies == 0


@pytest.mark.skipif(sys.platform == "win32", reason="existing publication imports POSIX fcntl")
async def test_publication_submit_rejects_durable_active_migration(tmp_path):
    from yleum_orchestrator.services.cell_publication import CellPublicationService

    service, _publication, _ = setup(tmp_path)
    await service.prepare(request(), str(uuid4()))
    with pytest.raises(CellIdentityConflict, match="migration"):
        await CellPublicationService(SimpleNamespace(), root=tmp_path).submit(request())
    await service.drain()


async def test_corrupt_journal_fails_closed_and_owner_is_checked(tmp_path):
    service, _, _ = setup(tmp_path)
    status = await prepared(service)
    with pytest.raises(CellIdentityConflict):
        service.get(request().project_id, UUID(int=99))
    service._path(request().project_id, status.operation_id).write_text("{}")
    with pytest.raises(CellIdentityConflict):
        service.require_no_active(request().project_id)


async def test_private_configuration_is_never_journaled(tmp_path):
    service, _, io = setup(tmp_path)
    with pytest.raises(CellIdentityConflict):
        await service.prepare(request(runtime_env={"MAX_BOT_TOKEN": "secret-canary"}), str(uuid4()))
    assert io.prepares == 0
    assert not list(tmp_path.glob("*/migrations/*.json"))


async def test_failed_durable_outcome_write_keeps_exclusion(tmp_path, monkeypatch):
    service, _, _ = setup(tmp_path)
    status = await prepared(service)
    original = service._save

    def fail_applied(record):
        if record["state"] == "applied":
            raise OSError("disk unavailable")
        original(record)

    monkeypatch.setattr(service, "_save", fail_applied)
    await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    await service.drain()
    assert service.get(request().project_id, request().owner_id).effect_possible
    with pytest.raises(CellIdentityConflict):
        service.require_no_active(request().project_id)


async def test_interrupted_preparation_is_not_restarted_after_recovery(tmp_path, monkeypatch):
    service, _, io = setup(tmp_path)
    monkeypatch.setattr(service, "_launch", lambda *args, **kwargs: None)
    status = await service.prepare(request(), str(uuid4()))
    await service.reconcile()
    await service._execute(request().project_id, status.operation_id, False)
    assert io.prepares == 0
    assert service.get(request().project_id, request().owner_id).state == "failed"


async def test_active_publication_prevents_prepare(tmp_path):
    service, publication, io = setup(tmp_path)
    publication._read(None)["history"] = [{"response": {"phase": "building"}}]
    with pytest.raises(CellIdentityConflict):
        await service.prepare(request(), str(uuid4()))
    assert io.prepares == 0


async def test_background_prepare_holds_same_publication_lock(tmp_path):
    service, publication, io = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = io.prepare

    async def paused(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    io.prepare = paused
    await service.prepare(request(), str(uuid4()))
    await entered.wait()
    acquired = asyncio.Event()

    async def competing():
        async with publication._submission_lock.hold(request().project_id):
            acquired.set()

    task = asyncio.create_task(competing())
    await asyncio.sleep(0)
    assert not acquired.is_set()
    release.set()
    await service.drain()
    await task
    assert acquired.is_set()


async def test_lost_commit_ack_preserves_exact_xid_for_observation(tmp_path):
    service, _, io = setup(tmp_path)
    status = await prepared(service)

    async def uncertain(request, prepared, persist):
        persist({"intent_xid": "4294967321", "effect_possible": True})
        raise TimeoutError("private server command and credentials")

    io.apply = uncertain
    await service.confirm(request(), status.operation_id, status.plan_sha256, True)
    await service.drain()
    record = service._load(request().project_id, status.operation_id)
    assert record["prepared"]["intent_xid"] == "4294967321"
    assert record["state"] == "reconciling" and record["effect_possible"]
    assert "credentials" not in service._path(request().project_id, status.operation_id).read_text()


async def test_prepare_rejects_reused_key_bound_to_another_snapshot(tmp_path):
    service, _, _ = setup(tmp_path)
    key = str(uuid4())
    await service.prepare(request(), key)
    with pytest.raises(CellIdentityConflict):
        await service.prepare(request(snapshot_id=uuid4()), key)
    await service.drain()


async def test_unverified_backup_cannot_reach_confirmation(tmp_path):
    service, _, io = setup(tmp_path)
    original = io.prepare

    async def unverified(*args):
        return {**await original(*args), "backup_verified": False}

    io.prepare = unverified
    status = await prepared(service)
    assert status.state == "failed" and not status.backup_verified
    with pytest.raises(CellIdentityConflict):
        await service.confirm(request(), status.operation_id, "a" * 64, True)
    assert io.applies == 0


async def test_prepared_receipt_is_bound_to_exact_operation_and_owner(tmp_path):
    service, _, io = setup(tmp_path)
    original = io.prepare

    async def foreign(*args):
        return {**await original(*args), "owner_id": str(uuid4())}

    io.prepare = foreign
    assert (await prepared(service)).state == "failed"


@pytest.mark.parametrize("failure", ["lock", "corrupt"])
async def test_recovery_project_failure_does_not_block_other_projects(
    tmp_path, monkeypatch, failure
):
    service, publication, _ = setup(tmp_path)
    monkeypatch.setattr(service, "_launch", lambda *args, **kwargs: None)
    first = await service.prepare(request(), str(uuid4()))
    other = request(project_id=UUID(int=9))
    await service.prepare(other, str(uuid4()))
    if failure == "corrupt":
        service._path(request().project_id, first.operation_id).write_text("{}")
    else:
        hold = publication._submission_lock.hold

        @asynccontextmanager
        async def denied(project_id):
            if project_id == request().project_id:
                raise WorkspaceLockTimeout("workspace_lock_timeout")
            async with hold(project_id):
                yield

        monkeypatch.setattr(publication._submission_lock, "hold", denied)
    outcomes = await service.reconcile()
    assert {"project_id": str(request().project_id), "state": "blocked"} in outcomes
    assert service.get(other.project_id, other.owner_id).state == "failed"
    with pytest.raises(CellIdentityConflict):
        service.require_no_active(request().project_id)


async def test_recovery_skips_local_running_project_without_waiting_on_its_lock(
    tmp_path, monkeypatch
):
    service, _, io = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = io.prepare

    async def paused(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    io.prepare = paused
    await service.prepare(request(), str(uuid4()))
    await entered.wait()
    monkeypatch.setattr(service, "_launch", lambda *args, **kwargs: None)
    other = request(project_id=UUID(int=9))
    await service.prepare(other, str(uuid4()))
    try:
        await asyncio.wait_for(service.reconcile(), timeout=0.5)
        assert service.get(other.project_id, other.owner_id).state == "failed"
        with pytest.raises(CellIdentityConflict):
            service.require_no_active(request().project_id)
    finally:
        release.set()
        await service.drain()


@pytest.mark.parametrize(
    ("proof", "state"),
    [
        (None, "failed"),
        ({"kind": "preflight_no_dispatch"}, "failed"),
        ({"kind": "xid_aborted", "xid": "91235"}, "failed"),
        ({"kind": "xid_committed_verified", "xid": "91234"}, "applied"),
    ],
)
async def test_unproven_false_patch_never_clears_durable_intent(tmp_path, proof, state):
    service, _, _ = setup(tmp_path)
    status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    record.update(state="applying", effect_possible=True)
    record["prepared"].update(intent_xid="91234", effect_possible=True)
    service._save(record)
    service._patch(record, {"state": state, "effect_possible": False, "outcome_proof": proof})
    persisted = service._load(request().project_id, status.operation_id)
    assert persisted["state"] == "reconciling" and persisted["effect_possible"] is True
    assert persisted["prepared"]["effect_possible"] is True
    assert persisted["prepared"]["intent_xid"] == "91234"


@pytest.mark.parametrize("kind", ["preflight_no_dispatch", "xid_aborted", "xid_committed_verified"])
async def test_only_bound_proven_outcome_can_release_exclusion(tmp_path, kind):
    service, _, _ = setup(tmp_path)
    status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    record.update(state="applying", effect_possible=True)
    proof = {"kind": kind}
    if kind != "preflight_no_dispatch":
        record["prepared"].update(intent_xid="91234", effect_possible=True)
        proof["xid"] = "91234"
    if kind == "xid_committed_verified":
        record["prepared"].update(precommit_verified=True, precommit_plan_sha256="a" * 64)
    service._save(record)
    state = "applied" if kind == "xid_committed_verified" else "failed"
    service._patch(record, {"state": state, "effect_possible": False, "outcome_proof": proof})
    service.require_no_active(request().project_id)
    assert service.get(request().project_id, request().owner_id).state == state


async def test_later_patch_cannot_replace_or_erase_durable_xid(tmp_path):
    service, _, _ = setup(tmp_path)
    status = await prepared(service)
    record = service._load(request().project_id, status.operation_id)
    record.update(state="applying", effect_possible=True)
    record["prepared"].update(intent_xid="91234", effect_possible=True)
    service._save(record)
    for replacement in (None, "91235"):
        with pytest.raises(CellIdentityConflict):
            service._patch(record, {"intent_xid": replacement, "effect_possible": True})
    assert (
        service._load(request().project_id, status.operation_id)["prepared"]["intent_xid"]
        == "91234"
    )
