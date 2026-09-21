from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest


def _binding(**changes):
    from omnia_orchestrator.schemas.code_restoration import RestorationSourceBindingV2

    value = {
        "version": 2,
        "serving_route_digest": "1" * 64,
        "serving_release_digest": "2" * 64,
        "controller_resource_digest": "3" * 64,
        "controller_incarnation_digest": "4" * 64,
        "controller_generation_digest": "5" * 64,
        "provider_digest": "6" * 64,
        "source_artifact_digest": "7" * 64,
        "database_identity_digest": "8" * 64,
        "database_schema_digest": "9" * 64,
        "database_role_binding_digest": "a" * 64,
        "database_system_identifier": "7612345678901234567",
        "database_export_digest": "b" * 64,
        "source_business_inventory_digest": "c" * 64,
        "candidate_business_inventory_digest": "c" * 64,
        "source_technical_inventory_digest": "d" * 64,
        "candidate_technical_inventory_digest": "d" * 64,
        "candidate_artifact_digest": "e" * 64,
    }
    value.update(changes)
    return RestorationSourceBindingV2.model_validate(value)


def test_binding_digest_is_canonical_and_contains_no_secret_values():
    from omnia_orchestrator.services.restoration_binding import canonical_digest

    binding = _binding()
    first = binding.digest()
    second = canonical_digest(binding.model_dump(mode="json"))

    assert first == second
    assert len(first) == 64
    assert "password" not in binding.model_dump_json().lower()
    assert "database_url" not in binding.model_dump_json().lower()


def test_v3_empty_database_binding_requires_both_artifact_and_witness():
    from pydantic import ValidationError

    from omnia_orchestrator.schemas.code_restoration import RestorationSourceBindingV3

    value = _binding().model_dump(mode="json")
    value.update(
        version=3,
        database_strategy="replace_verified_empty",
        witness_digest="f" * 64,
        target_database_artifact_digest="0" * 64,
    )
    binding = RestorationSourceBindingV3.model_validate(value)

    assert binding.digest() != _binding().digest()
    for missing in ("witness_digest", "target_database_artifact_digest"):
        invalid = dict(value)
        invalid[missing] = None
        with pytest.raises(ValidationError):
            RestorationSourceBindingV3.model_validate(invalid)


def test_v3_preserved_database_cannot_carry_empty_replacement_evidence():
    from pydantic import ValidationError

    from omnia_orchestrator.schemas.code_restoration import RestorationSourceBindingV3

    value = _binding().model_dump(mode="json")
    value.update(
        version=3,
        database_strategy="preserve_current",
        witness_digest="f" * 64,
        target_database_artifact_digest=None,
    )
    with pytest.raises(ValidationError):
        RestorationSourceBindingV3.model_validate(value)


def test_live_identity_ignores_normal_row_changes_and_export_receipts():
    baseline = _binding()
    after_insert = _binding(
        database_export_digest="f" * 64,
        source_business_inventory_digest="0" * 64,
        candidate_business_inventory_digest="0" * 64,
    )

    assert baseline.live_identity_digest() == after_insert.live_identity_digest()


def test_live_identity_rejects_same_cluster_different_database():
    baseline = _binding(database_system_identifier="same-cluster")
    detached = _binding(
        database_system_identifier="same-cluster",
        database_identity_digest="f" * 64,
    )

    assert baseline.live_identity_digest() != detached.live_identity_digest()


def test_live_identity_rejects_recreated_resource_with_same_name():
    baseline = _binding()
    recreated = _binding(controller_incarnation_digest="f" * 64)

    assert baseline.live_identity_digest() != recreated.live_identity_digest()


def test_database_oid_query_casts_pg_oid_to_canonical_json_number(monkeypatch):
    import json

    from omnia_orchestrator.services import restoration_binding as module

    backend = SimpleNamespace(project_database_env=lambda: {"PGDATABASE": "app", "PGUSER": "app"})

    def execute(_backend, sql, **_kwargs):
        # PostgreSQL's oid type is emitted as a JSON string in production unless
        # the query first converts it to an ordinary numeric type.
        database_oid = 42 if "d.oid::bigint" in sql else "42"
        return json.dumps(
            {
                "database_name": "app",
                "database_oid": database_oid,
                "role_name": "app",
                "role_login": True,
                "role_superuser": False,
                "role_create_db": False,
                "role_create_role": False,
                "database_acl": "",
                "system_identifier": "7612345678901234567",
            }
        ).encode()

    monkeypatch.setattr(module, "admin_sql", execute)

    observed = module._database_observation(backend)

    assert observed["database_oid"] == 42


def test_inventory_partitions_business_and_technical_rows():
    from omnia_orchestrator.services.restoration_binding import inventory_partition_digests
    from omnia_orchestrator.services.versioning.contracts import InventoryReport

    source = InventoryReport.model_validate(
        {
            "presence": "present",
            "coverage": "complete",
            "schema_analysis": "complete",
            "observed_on": "source",
            "objects": [
                {
                    "object": "public.clients",
                    "kind": "table",
                    "classification": "business",
                    "presence": "present",
                    "row_count": 2,
                    "count_kind": "exact",
                },
                {
                    "object": "drizzle.__drizzle_migrations",
                    "kind": "table",
                    "classification": "technical",
                    "presence": "present",
                    "row_count": 7,
                    "count_kind": "exact",
                },
            ],
        }
    )
    candidate = source.model_copy(update={"observed_on": "candidate_copy"})
    business, technical = inventory_partition_digests(source)

    assert (business, technical) == inventory_partition_digests(candidate)
    changed = deepcopy(candidate.model_dump(mode="json"))
    changed["objects"][0]["object"] = "public.detached_clients"
    changed_candidate = InventoryReport.model_validate(changed)
    assert business != inventory_partition_digests(changed_candidate)[0]
    assert technical == inventory_partition_digests(changed_candidate)[1]


def _live_source_fixture(monkeypatch):
    from omnia_orchestrator.services import restoration_binding as module

    expected = {
        "DATABASE_URL": "postgresql://app:secret@db/app",
        "PGHOST": "db",
        "PGPORT": "5432",
        "PGUSER": "app",
        "PGPASSWORD": "secret",
        "PGDATABASE": "app",
    }
    app = SimpleNamespace(
        attrs={"Config": {"Env": [f"{key}={value}" for key, value in expected.items()]}}
    )
    postgres, gateway = object(), object()
    core = SimpleNamespace(
        attrs={"NetworkSettings": {"Networks": {"internal": {"IPAddress": "10.0.0.8"}}}}
    )
    volume = SimpleNamespace(
        attrs={"Name": "db-volume", "CreatedAt": "now", "Labels": {"owned": "yes"}}
    )
    containers, volumes = object(), object()
    backend = SimpleNamespace(
        client=SimpleNamespace(containers=containers, volumes=volumes),
        stem="owned",
        namespace="prod",
        internal_network="internal",
        project_postgres_volume="db-volume",
        _container=lambda: app,
        _project_postgres=lambda: postgres,
        _lookup=lambda collection, _name, kind: (
            (core if kind == "managed-max-core" else gateway)
            if collection is containers
            else volume
        ),
        project_database_env=lambda: expected,
        address=lambda: "10.0.0.7",
    )
    monkeypatch.setattr(
        module,
        "_trusted_identity",
        lambda _backend, resource, kind: {"id": kind + "-" + str(id(resource))},
    )
    monkeypatch.setattr(
        module,
        "_gateway_config",
        lambda _gateway: {
            "project_id": str(UUID(int=3)),
            "epoch": 7,
            "core_host": "10.0.0.8",
            "machine_host": "10.0.0.7",
            "routes": [{"path": "/", "service": "web", "port": 3000}],
        },
    )
    monkeypatch.setattr(
        module,
        "_database_observation",
        lambda _backend: {
            "database_name": "app",
            "database_oid": 42,
            "role_name": "app",
            "role_login": True,
            "role_superuser": False,
            "role_create_db": False,
            "role_create_role": False,
            "database_acl": "",
            "system_identifier": "7612345678901234567",
        },
    )
    state = SimpleNamespace(
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        owner_id=UUID(int=4),
        profile_version="v2",
        resource_names=None,
        fencing_epoch=7,
        active_generation_run_id=None,
        active_generation_fencing_epoch=None,
        last_operation_id=UUID(int=5),
        provider_ref="docker://owned",
        operation=lambda _operation_id: None,
    )
    machine = SimpleNamespace(
        state=lambda: {"manifest": {"routes": ["/"]}, "ready_epoch": 7, "epoch": 7}
    )
    return module, backend, machine, state, app


def _legacy_release_state(
    state,
    *,
    predecessor_kind="ensure",
    predecessor_status="completed",
    predecessor_bundle="resources_ready",
    same_run=True,
    immediate=True,
    release_detail=None,
):
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    generation_run_id = UUID(int=20)
    predecessor_id, release_id = UUID(int=21), UUID(int=23)
    predecessor = CellOperationRecord(
        operation_id=predecessor_id,
        kind=predecessor_kind,
        status=predecessor_status,
        phase=predecessor_status,
        request_digest="a" * 64,
        fencing_epoch=10,
        generation_run_id=generation_run_id,
        bundle_state=predecessor_bundle,
    )
    operations = [predecessor]
    if not immediate:
        operations.append(
            CellOperationRecord(
                operation_id=UUID(int=22),
                kind="checkpoint",
                status="completed",
                phase="completed",
                request_digest="b" * 64,
                fencing_epoch=10,
                generation_run_id=generation_run_id,
                bundle_state="resources_ready",
            )
        )
    release = CellOperationRecord(
        operation_id=release_id,
        kind="release",
        status="completed",
        phase="completed",
        request_digest="c" * 64,
        fencing_epoch=11,
        generation_run_id=(generation_run_id if same_run else UUID(int=24)),
        bundle_state="resources_ready",
        detail=release_detail,
    )
    operations.append(release)
    state.fencing_epoch = 11
    state.last_operation_id = release_id
    state.operations = tuple(operations)
    state.operation = lambda operation_id: next(
        (item for item in state.operations if item.operation_id == operation_id), None
    )
    return generation_run_id, predecessor_id, release_id


def _set_serving_epoch(monkeypatch, module, machine, epoch):
    machine.state = lambda: {
        "manifest": {"routes": ["/"]},
        "ready_epoch": epoch,
        "epoch": epoch,
    }
    monkeypatch.setattr(
        module,
        "_gateway_config",
        lambda _gateway: {
            "project_id": str(UUID(int=3)),
            "epoch": epoch,
            "core_host": "10.0.0.8",
            "machine_host": "10.0.0.7",
            "routes": [],
        },
    )


def test_detached_application_database_binding_fails_closed(monkeypatch):
    module, backend, machine, state, app = _live_source_fixture(monkeypatch)
    app.attrs["Config"]["Env"] = ["PGDATABASE=another"]
    with pytest.raises(RuntimeError, match="another database"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )


def test_live_source_canonicalizes_controller_resource_workspace_uuid(monkeypatch):
    from omnia_orchestrator.core.cell_resources import CellResourceNames

    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    state.resource_names = CellResourceNames.for_workspace(
        state.workspace_id,
        namespace="prod",
    )

    observed = module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )

    assert len(observed["controller_resource_digest"]) == 64

    state.resource_names = CellResourceNames.for_workspace(UUID(int=99), namespace="prod")
    with pytest.raises(RuntimeError, match="resource workspace identity"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )


def test_generation_aba_changes_live_source_identity(monkeypatch):
    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    first = module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )
    state.last_operation_id = UUID(int=6)
    second = module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )
    assert first["controller_generation_digest"] != second["controller_generation_digest"]


def test_stale_large_table_estimates_are_never_copy_proof(monkeypatch):
    from omnia_orchestrator.services import restoration_binding as module
    from omnia_orchestrator.services.versioning.contracts import InventoryReport

    inventory = InventoryReport.model_validate(
        {
            "presence": "present",
            "coverage": "complete",
            "schema_analysis": "complete",
            "observed_on": "source",
            "objects": [
                {
                    "object": "public.clients",
                    "kind": "table",
                    "classification": "business",
                    "presence": "present",
                    "row_count": 100_001,
                    "count_kind": "estimate",
                }
            ],
        }
    )
    monkeypatch.setattr(
        module,
        "admin_sql",
        lambda backend, *_args, **_kwargs: b"[100001]" if backend == "source" else b"[100002]",
    )
    source = module.exact_inventory_partition_digests("source", inventory)
    candidate = module.exact_inventory_partition_digests("candidate", inventory)
    assert module.inventory_partition_digests(inventory) == module.inventory_partition_digests(
        inventory.model_copy(update={"observed_on": "candidate_copy"})
    )
    assert source != candidate


def test_managed_core_upstream_mismatch_fails_closed(monkeypatch):
    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    monkeypatch.setattr(
        module,
        "_gateway_config",
        lambda _gateway: {
            "project_id": str(UUID(int=3)),
            "epoch": 7,
            "core_host": "10.0.0.99",
            "machine_host": "10.0.0.7",
            "routes": [],
        },
    )
    with pytest.raises(RuntimeError, match="detached"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )


def test_legacy_release_uses_exact_same_generation_predecessor(monkeypatch):
    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    _legacy_release_state(state)
    _set_serving_epoch(monkeypatch, module, machine, 10)

    observed = module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )

    assert module.serving_fencing_epoch(state, machine_state=machine.state()) == 10
    assert observed["controller_generation_digest"]


@pytest.mark.parametrize(
    ("changes", "machine_epoch"),
    [
        ({"same_run": False}, 10),
        ({"predecessor_kind": "checkpoint"}, 10),
        ({"predecessor_status": "failed"}, 10),
        ({"predecessor_bundle": "retained"}, 10),
        ({"immediate": False}, 10),
        ({}, 9),
    ],
)
def test_legacy_release_proof_fails_closed(monkeypatch, changes, machine_epoch):
    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    _legacy_release_state(state, **changes)
    _set_serving_epoch(monkeypatch, module, machine, machine_epoch)

    with pytest.raises(RuntimeError, match="release epoch proof"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )


def test_legacy_release_route_must_match_proven_serving_epoch(monkeypatch):
    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    _legacy_release_state(state)
    _set_serving_epoch(monkeypatch, module, machine, 10)
    monkeypatch.setattr(
        module,
        "_gateway_config",
        lambda _gateway: {
            "project_id": str(UUID(int=3)),
            "epoch": 9,
            "core_host": "10.0.0.8",
            "machine_host": "10.0.0.7",
            "routes": [],
        },
    )

    with pytest.raises(RuntimeError, match="detached"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )


def test_explicit_release_receipt_is_authoritative_but_machine_bound(monkeypatch):
    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    _legacy_release_state(
        state,
        predecessor_kind="checkpoint",
        release_detail="retained_source_fencing_epoch=10",
    )
    _set_serving_epoch(monkeypatch, module, machine, 10)

    assert module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )

    machine.state = lambda: {"manifest": {}, "ready_epoch": 10, "epoch": 9}
    with pytest.raises(RuntimeError, match="serving machine epoch"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )


def test_live_source_stays_detached_until_stale_ready_epoch_is_reproved(monkeypatch):
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    release = CellOperationRecord(
        operation_id=UUID(int=28),
        kind="release",
        status="completed",
        phase="completed",
        request_digest="d" * 64,
        fencing_epoch=28,
        bundle_state="resources_ready",
        detail="retained_source_fencing_epoch=27",
    )
    state.fencing_epoch = 28
    state.last_operation_id = release.operation_id
    state.operations = (release,)
    state.operation = lambda operation_id: release if operation_id == release.operation_id else None
    observed_machine = {
        "manifest": {"routes": ["/"]},
        "epoch": 27,
        "ready_epoch": 23,
        "cancelled_epoch": 0,
    }
    machine.state = lambda: dict(observed_machine)
    monkeypatch.setattr(
        module,
        "_gateway_config",
        lambda _gateway: {
            "project_id": str(state.project_id),
            "epoch": 27,
            "core_host": "10.0.0.8",
            "machine_host": "10.0.0.7",
            "routes": [],
        },
    )

    with pytest.raises(RuntimeError, match="serving machine epoch"):
        module.observe_live_source(
            backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
        )

    observed_machine["ready_epoch"] = 27
    assert module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )


def test_two_rejections_keep_actual_serving_epoch_for_next_prepare(monkeypatch):
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    module, backend, machine, state, _ = _live_source_fixture(monkeypatch)
    first_id, second_id = UUID(int=8), UUID(int=9)
    operations = {
        operation_id: CellOperationRecord(
            operation_id=operation_id,
            kind="restoration_rejection",
            status="completed",
            phase="rejected",
            request_digest="receipt-" + str(fence),
            fencing_epoch=fence,
            bundle_state="resources_ready",
            detail="retained_source_fencing_epoch=3",
        )
        for operation_id, fence in ((first_id, 4), (second_id, 5))
    }
    state.fencing_epoch = 5
    state.last_operation_id = second_id
    state.operation = operations.get
    machine.state = lambda: {"manifest": {"routes": ["/"]}, "ready_epoch": 3, "epoch": 3}
    monkeypatch.setattr(
        module,
        "_gateway_config",
        lambda _gateway: {
            "project_id": str(UUID(int=3)),
            "epoch": 3,
            "core_host": "10.0.0.8",
            "machine_host": "10.0.0.7",
            "routes": [],
        },
    )

    observed = module.observe_live_source(
        backend, machine, state, source_files={"page.tsx": b"x"}, schema={}
    )

    assert module.serving_fencing_epoch(state) == 3
    assert observed["controller_generation_digest"]
