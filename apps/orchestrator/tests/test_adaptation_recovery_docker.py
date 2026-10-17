"""Root-executed opt-in fixture: real recovery engine/adapter/backend, transport isolated."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4, uuid5

import docker
import pytest

from tests._task9_recovery_docker_fixture import (
    POSTGRES_IMAGE,
    PYTHON_IMAGE,
    FixtureDocker,
    require,
    safe_fixture_error,
)
from tests.test_cell_checkpoint import _make_fixture
from tests.test_project_machine_manifest import payload
from tests.test_restoration_adaptation_workspace import _request
from yleum_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    LifecycleMutation,
    identity_labels,
)
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
from yleum_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationRecoveryHold,
    RestorationAdaptationRecoveryIdentity,
    RestorationAdaptationRecoveryRebind,
    RestorationAdaptationRecoveryReceipt,
)
from yleum_orchestrator.services.adaptation_recovery_engine import DockerAdaptationRecovery
from yleum_orchestrator.services.docker_machine_backend import DockerMachineBackend
from yleum_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend
from yleum_orchestrator.services.machine_adapter import MachineAdapter
from yleum_orchestrator.services.machine_environment import MachineEnvironmentStore
from yleum_orchestrator.services.project_database_roles import (
    PROJECT_OWNER_ROLE,
    PROJECT_RUNTIME_ROLE,
)
from yleum_orchestrator.services.project_machine import machine_effect, write_controller_json


def _summary_digest(files):
    return hashlib.sha256(
        json.dumps(
            {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _postgres(backend, argv):
    pg = backend._project_postgres()
    require(pg is not None, "postgres absent")
    result = pg.exec_run(
        argv,
        user="postgres",
        environment={},
    )
    require(result.exit_code == 0, "private PostgreSQL probe failed")
    return bytes(result.output)


def _sql(backend, sql):
    return _postgres(
        backend,
        [
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "/tmp",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-c",
            sql,
        ],
    )


def _schema_diagnostics(schema):
    """Hash diagnostic partitions only; full unmodified dump remains the acceptance key."""
    partitions = {"schema_comments": [], "schema_restrict": [], "schema_body": []}
    for line in schema.splitlines(keepends=True):
        if line.startswith(b"--"):
            component = "schema_comments"
        elif line.startswith((b"\\restrict ", b"\\unrestrict ")):
            component = "schema_restrict"
        else:
            component = "schema_body"
        partitions[component].append(line)
    return {name: hashlib.sha256(b"".join(lines)).hexdigest()
            for name, lines in partitions.items()}


def _evidence_digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
            "invalid private evidence digest")
    return value


def _database_digests(evidence):
    components = ("schema", "sequence", "schema_comments", "schema_body", "schema_restrict")
    require(isinstance(evidence, dict) and set(evidence) == {"rows", *components},
            "invalid private evidence shape")
    rows = evidence["rows"]
    require(isinstance(rows, dict) and set(rows) == {"count", "rows"}
            and type(rows["count"]) is int and rows["count"] >= 0,
            "invalid private row evidence")
    if rows["rows"] is None:
        require(rows["count"] == 0, "invalid empty row evidence")
    else:
        _evidence_digest(rows["rows"])
    return {"rows": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            **{name: _evidence_digest(evidence[name]) for name in components}}


def _assert_database_preserved(
    before, after, held_incarnation, resumed_incarnation, *,
    before_source, after_source, recopy_count,
):
    """Emit only fixed component names, validated digests and booleans before failing."""
    first, second = _database_digests(before), _database_digests(after)
    held, resumed = _evidence_digest(held_incarnation), _evidence_digest(resumed_incarnation)
    original_source = _evidence_digest(before_source)
    current_source = _evidence_digest(after_source)
    require(type(recopy_count) is int and recopy_count >= 0, "invalid recopy evidence")
    print(json.dumps({
        "database_preservation": {
            name: {"equal": first[name] == second[name],
                   "before_sha256": first[name], "after_sha256": second[name]}
            for name in first
        },
        "row_count_equal": before["rows"]["count"] == after["rows"]["count"],
        "row_multiset_equal": before["rows"]["rows"] == after["rows"]["rows"],
        "source_files": {"equal": original_source == current_source,
                         "before_sha256": original_source, "after_sha256": current_source},
        "recopy_zero": recopy_count == 0,
        "all_volume_incarnations": {"equal": held == resumed,
                                    "held_sha256": held, "resumed_sha256": resumed},
    }, sort_keys=True), flush=True)
    require(before == after, "database preservation failed")
    require(held == resumed, "volume incarnation changed")
    require(original_source == current_source, "source file preservation failed")
    require(recopy_count == 0, "recopy occurred")


def _database_evidence(backend, nonce):
    rows = json.loads(
        _sql(
            backend,
            """
      BEGIN READ ONLY;
      SELECT json_build_object('count',count(*),'rows',encode(sha256(convert_to(
        string_agg(encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex'),''
          ORDER BY encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex')),'UTF8')),'hex'))
      FROM public.recovery_fixture AS t;
      COMMIT;
    """,
        )
        .decode()
        .splitlines()[1]
    )
    schema = _postgres(
        backend,
        [
            "pg_dump",
            "-h",
            "/tmp",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "--schema-only",
            "--restrict-key=" + nonce,
        ],
    )
    sequence = _sql(backend, "SELECT last_value,is_called FROM public.recovery_fixture_id_seq")
    return {
        "rows": rows,
        "schema": hashlib.sha256(schema).hexdigest(),
        "sequence": hashlib.sha256(sequence).hexdigest(),
        **_schema_diagnostics(schema),
    }


def _role_catalog(backend, *, probe):
    """Fixed synthetic object aliases only; PostgreSQL hashes owner/ACL before return."""
    require(type(probe) is bool, "invalid catalog scope")
    table = "recovery_owner_probe" if probe else "recovery_fixture"
    raw = _sql(backend, f"""
      BEGIN READ ONLY;
      SELECT jsonb_object_agg(CASE c.relkind WHEN 'r' THEN 'table' ELSE 'sequence' END,
        jsonb_build_object(
          'owner_postgres',r.rolname='postgres','owner_project',r.rolname='{PROJECT_OWNER_ROLE}',
          'owner_sha256',encode(sha256(convert_to(r.rolname::text,'UTF8')),'hex'),
          'acl_sha256',encode(sha256(convert_to(COALESCE(c.relacl::text,'<null>'),'UTF8')),'hex'),
          'runtime_acl',CASE WHEN c.relkind='S' THEN
            has_sequence_privilege('{PROJECT_RUNTIME_ROLE}',c.oid,'USAGE') AND
            has_sequence_privilege('{PROJECT_RUNTIME_ROLE}',c.oid,'SELECT')
          ELSE has_table_privilege('{PROJECT_RUNTIME_ROLE}',c.oid,'SELECT') AND
            has_table_privilege('{PROJECT_RUNTIME_ROLE}',c.oid,'INSERT') AND
            has_table_privilege('{PROJECT_RUNTIME_ROLE}',c.oid,'UPDATE') AND
            has_table_privilege('{PROJECT_RUNTIME_ROLE}',c.oid,'DELETE') END))
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_roles r ON r.oid=c.relowner
      WHERE n.nspname='public' AND c.relname IN ('{table}','{table}_id_seq')
        AND c.relkind IN ('r','S');
      COMMIT;
    """)
    result = json.loads(raw.decode().splitlines()[1])
    require(isinstance(result, dict) and set(result) == {"table", "sequence"},
            "invalid role catalog objects")
    flags = {"owner_postgres", "owner_project", "runtime_acl"}
    for item in result.values():
        require(isinstance(item, dict) and set(item) == flags | {"owner_sha256", "acl_sha256"},
                "invalid role catalog shape")
        require(all(type(item[name]) is bool for name in flags), "invalid role catalog flags")
        _evidence_digest(item["owner_sha256"])
        _evidence_digest(item["acl_sha256"])
    return result


def _require_catalog_role(catalog, *, project_owner):
    require(all(item["owner_project"] is project_owner
                and item["owner_postgres"] is not project_owner
                and item["runtime_acl"] is project_owner for item in catalog.values()),
            "unexpected synthetic object owner or runtime ACL")


async def _seed_database_fixture(backend, nonce):
    # Deliberately wrong owner reproduces the old fixture setup, in separate empty objects.
    _sql(backend, "CREATE TABLE public.recovery_owner_probe(id bigserial PRIMARY KEY);")
    before_probe = _role_catalog(backend, probe=True)
    print(json.dumps({"owner_probe_before": before_probe}, sort_keys=True), flush=True)
    _require_catalog_role(before_probe, project_owner=False)
    await machine_effect(backend.bootstrap_project_database_roles)
    after_probe = _role_catalog(backend, probe=True)
    print(json.dumps({"owner_probe_after": after_probe}, sort_keys=True), flush=True)
    _require_catalog_role(after_probe, project_owner=True)
    require(all(before_probe[k]["owner_sha256"] != after_probe[k]["owner_sha256"]
                and before_probe[k]["acl_sha256"] != after_probe[k]["acl_sha256"]
                for k in ("table", "sequence")), "owner probe transition missing")
    # This is hardcoded trusted synthetic fixture SQL, never generated SQL on an admin login.
    _sql(backend, f"""BEGIN; SET LOCAL ROLE {PROJECT_OWNER_ROLE};
      CREATE TABLE public.recovery_fixture(id bigserial PRIMARY KEY, value text, details jsonb);
      INSERT INTO public.recovery_fixture(value,details) VALUES
        ('one','{{"x":1}}'),('данные','{{"nested":[1,true,null]}}'),(NULL,NULL);
      COMMIT;""")
    before_catalog = _role_catalog(backend, probe=False)
    print(json.dumps({"canonical_seed_catalog_before": before_catalog}, sort_keys=True), flush=True)
    _require_catalog_role(before_catalog, project_owner=True)
    baseline = _database_evidence(backend, nonce)
    first = _database_digests(baseline)
    require(baseline["rows"]["count"] == 3, "empty/invalid baseline")
    await machine_effect(backend.bootstrap_project_database_roles)
    after_catalog = _role_catalog(backend, probe=False)
    after = _database_evidence(backend, nonce)
    second = _database_digests(after)
    print(json.dumps({
        "canonical_seed_catalog_after": after_catalog,
        "canonical_seed_database": {name: {"equal": first[name] == second[name],
                                             "before_sha256": first[name],
                                             "after_sha256": second[name]} for name in first},
    }, sort_keys=True), flush=True)
    _require_catalog_role(after_catalog, project_owner=True)
    require(before_catalog == after_catalog, "canonical seed catalog drift")
    require(baseline == after, "canonical seed database drift")
    return baseline


def _observe_halt_ack_loss(monkeypatch):
    """Count real halt dispatches across every adapter instance; lose only first ACK."""
    real_halt = MachineAdapter.halt
    calls = []

    async def observed_halt(adapter, state, **kwargs):
        calls.append(adapter)
        ordinal = len(calls)
        await real_halt(adapter, state, **kwargs)
        if ordinal == 1:
            raise RuntimeError("fixture lost halt acknowledgement")

    monkeypatch.setattr(MachineAdapter, "halt", observed_halt)
    return calls


@pytest.mark.skipif(
    os.environ.get("YLEUM_TEST_RECOVERY_DOCKER") != "1",
    reason="root-only reviewed nonce-owned actual recovery database fixture",
)
async def test_owned_actual_candidate_hold_restart_rebind_preserves_database(tmp_path, monkeypatch):
    raw = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=30)
    original = _request()
    candidate = uuid5(original.operation_id, f"restoration-adaptation:{original.generation_run_id}")
    source, *_ = _make_fixture(tmp_path / "source")
    manager, *_ = _make_fixture(tmp_path / "candidate")
    for current, wid, epoch in ((source, original.workspace_id, 7), (manager, candidate, 1)):
        spec = WorkspaceSpec(
            workspace_id=wid,
            project_id=original.project_id,
            owner_id=original.owner_id,
            generation_run_id=original.generation_run_id,
            profile_version=current.profile.profile_version,
        )
        # Only isolated controller-state setup uses the existing fake admission fixture.
        await current.ensure(spec, LifecycleMutation(uuid4(), epoch, "a" * 64))
    manager.profile = replace(manager.profile, postgres_image=POSTGRES_IMAGE)
    settings = SimpleNamespace(
        cell_machine_base_image=PYTHON_IMAGE,
        cell_machine_guard_image=PYTHON_IMAGE,
        cell_network_pool="10.240.0.0/16",
        cell_machine_denied_cidrs=("0.0.0.0/0",),
    )
    manager.docker = DockerPyCellBackend(
        "unix:///var/run/docker.sock",
        PYTHON_IMAGE,
        client_factory=lambda _: raw,
        write_helper_image=PYTHON_IMAGE,
    )
    manager.machine_runtime = MachineAdapter(manager, settings)
    state = manager.state_store.load(candidate)
    machine, backend = manager.machine_runtime.parts(state)
    scope = FixtureDocker(raw, nonce=uuid4().hex, backend=backend)
    manager.docker = DockerPyCellBackend(
        "unused", PYTHON_IMAGE, client_factory=lambda _: scope, write_helper_image=PYTHON_IMAGE
    )
    # Production parts() creates the real backend for every restart/read.
    manifest_payload = payload()
    manifest_payload["services"] = [manifest_payload["services"][0]]
    manifest_payload["services"][0]["resources"].update(
        cpu_cores=0.1, memory_bytes=32 * 1024**2, pids=16
    )
    manifest = MachineManifest.model_validate(manifest_payload)
    scope.allowed_volumes = set(backend.environment_volume_names(manifest)) | {
        backend.stem + "-logs"
    }
    scope.allowed_environments = {
        "development": backend.container_options(manifest, "pending", 1)["environment"],
        "project-postgres": backend._project_postgres_options("pending", 1)["environment"],
        "project-postgres-init": {
            "PGDATA": "/var/lib/postgresql/data",
            "POSTGRES_PASSWORD": backend.project_postgres_password,
        },
    }

    # The transport-only proxy readiness check cannot wait for an intentionally absent proxy server.
    def no_network_proxy(self, proxy, address):
        require(address == "127.0.0.1" and proxy.status == "running", "isolated proxy state")
        scope.check_container(proxy.raw)

    monkeypatch.setattr(DockerMachineBackend, "_wait_proxy_ready", no_network_proxy)
    recopy = {"calls": 0}

    def forbidden_copy(*args, **kwargs):
        recopy["calls"] += 1
        raise RuntimeError("recovery must not copy/import candidate data")

    monkeypatch.setattr(DockerMachineBackend, "import_volume", forbidden_copy)
    monkeypatch.setattr(MachineEnvironmentStore, "restore", forbidden_copy)
    reports, stage = [], "initial"
    try:
        stage = "initial-volume-create"
        labels = identity_labels(manager._spec_from_state(state), "workspace")
        scope.volumes.create(
            name=backend.workspace_volume, labels={**labels, "omnia.namespace": "test"}
        )
        stage = "initial-source-write"
        await manager.docker.write_volume_files(
            backend.workspace_volume,
            {
                "fixture.txt": "persisted Unicode: данные\n".encode(),
                "nested/exact.bin": bytes(range(256)),
            },
        )
        stage = "initial-controller-seed"
        write_controller_json(
            machine.path,
            {
                "workspace_id": str(candidate),
                "epoch": 1,
                "ready_epoch": 1,
                "manifest": manifest.model_dump(mode="json"),
                "restoration_adaptation_run_id": str(original.generation_run_id),
                "operations": {},
            },
        )
        stage = "initial-runtime-ensure"
        machine, backend = manager.machine_runtime.parts(state)
        await machine_effect(backend.ensure, manifest, 1)
        stage = "initial-database-seed"
        baseline = await _seed_database_fixture(backend, scope.nonce)
        stage = "initial-source-evidence"
        file_baseline = _summary_digest(
            await manager.docker.read_workspace_source_files(backend.workspace_volume)
        )
        previous = None
        source_epoch = 7
        for cycle in range(2):
            stage = "hold-" + str(cycle)
            manager.machine_runtime = MachineAdapter(manager, settings)
            recovery = DockerAdaptationRecovery(lambda _: source)
            recovery._candidate_manager = lambda _: manager
            fence = manager.state_store.load(candidate).active_generation_fencing_epoch
            request = RestorationAdaptationRecoveryHold(
                version=1,
                transition_id=uuid4(),
                workspace_id=original.workspace_id,
                generation_run_id=original.generation_run_id,
                operation_id=original.operation_id,
                project_id=original.project_id,
                owner_id=original.owner_id,
                candidate_workspace_id=candidate,
                request_digest=original.digest(),
                expected_cycle=cycle,
                control_fencing_epoch=source_epoch,
                candidate_fencing_epoch=fence,
            )
            pending = RestorationAdaptationRecoveryReceipt(
                **{
                    k: getattr(request, k)
                    for k in RestorationAdaptationRecoveryIdentity.model_fields
                },
                state="hold_pending",
                cycle=cycle,
                original_control_fencing_epoch=7,
                current_control_fencing_epoch=source_epoch,
                original_candidate_fencing_epoch=1,
                current_candidate_fencing_epoch=fence,
                barrier_operation_id=uuid5(request.transition_id, "candidate-writer-barrier"),
            )
            held = await recovery.hold(original, request, pending)
            require(held.state == "held", "hold not confirmed")
            if previous is not None:
                require(held.physical_incarnation_digest == previous, "volume incarnation changed")
            previous = held.physical_incarnation_digest
            manager.machine_runtime = MachineAdapter(manager, settings)
            restarted = DockerAdaptationRecovery(lambda _: source)
            restarted._candidate_manager = lambda _: manager
            require(
                await restarted.inspect(original, pending) == held, "cold terminal status changed"
            )
            # Old executor authority still exists at the source, yet cannot write/wake candidate.
            state = manager.state_store.load(candidate)
            _old_machine, old_backend = manager.machine_runtime.parts(state)
            with pytest.raises(CellIdentityConflict):
                old_backend.exec_start(["sh", "-c", "exit 99"], "/workspace", "late")
            with pytest.raises(CellIdentityConflict):
                await manager.wake(candidate, LifecycleMutation(uuid4(), fence + 1, "b" * 64))
            source_epoch += 2
            source_state = source.state_store.load(original.workspace_id)
            await source.ensure(
                source._spec_from_state(source_state, generation_run_id=original.generation_run_id),
                LifecycleMutation(uuid4(), source_epoch, "c" * 64),
            )
            command = RestorationAdaptationRecoveryRebind(
                **{
                    k: getattr(request, k)
                    for k in RestorationAdaptationRecoveryIdentity.model_fields
                    if k != "transition_id"
                },
                transition_id=uuid4(),
                expected_cycle=cycle,
                expected_hold_receipt_digest=held.terminal_receipt_digest,
                new_control_fencing_epoch=source_epoch,
            )
            next_pending = pending.model_copy(
                update={
                    "transition_id": command.transition_id,
                    "state": "rebinding",
                    "cycle": cycle + 1,
                    "current_control_fencing_epoch": source_epoch,
                    "current_candidate_fencing_epoch": fence + 1,
                    "restore_operation_id": uuid5(command.transition_id, "candidate-resume"),
                }
            )
            stage = "rebind-" + str(cycle)
            resumed = await restarted.rebind(original, command, next_pending)
            require(resumed.state == "resumable", "restore not confirmed")
            await restarted.publish(original, resumed)
            require(
                await restarted.inspect(original, resumed) == resumed, "fresh runtime proof changed"
            )
            state = manager.state_store.load(candidate)
            _, backend = manager.machine_runtime.parts(state)
            current_database = _database_evidence(backend, scope.nonce)
            current_source = _summary_digest(
                await manager.docker.read_workspace_source_files(backend.workspace_volume)
            )
            _assert_database_preserved(
                baseline, current_database,
                held.physical_incarnation_digest, resumed.physical_incarnation_digest,
                before_source=file_baseline, after_source=current_source,
                recopy_count=recopy["calls"],
            )
            reports.append(
                {
                    "cycle": cycle + 1,
                    "rows": 3,
                    "schema_sequence_files_preserved": True,
                    "same_incarnation": True,
                    "recopy": 0,
                }
            )
        # A lost ACK after real halt must retain unknown intent across a new coordinator.
        stage = "unknown-halt"
        transition = uuid4()
        unknown_request = request.model_copy(
            update={
                "transition_id": transition,
                "expected_cycle": 2,
                "control_fencing_epoch": source_epoch,
                "candidate_fencing_epoch": fence + 1,
            }
        )
        unknown_pending = pending.model_copy(
            update={
                "transition_id": transition,
                "cycle": 2,
                "current_control_fencing_epoch": source_epoch,
                "current_candidate_fencing_epoch": fence + 1,
                "barrier_operation_id": uuid5(transition, "candidate-writer-barrier"),
            }
        )
        halt_calls = _observe_halt_ack_loss(monkeypatch)
        with pytest.raises(RuntimeError, match="fixture lost halt acknowledgement"):
            await restarted.hold(original, unknown_request, unknown_pending)
        manager.machine_runtime = MachineAdapter(manager, settings)
        cold = DockerAdaptationRecovery(lambda _: source)
        cold._candidate_manager = lambda _: manager
        require(
            await cold.inspect(original, unknown_pending) == unknown_pending,
            "unknown halt promoted to terminal",
        )
        require(
            await cold.hold(original, unknown_request, unknown_pending) == unknown_pending
            and len(halt_calls) == 1,
            "unknown halt replayed",
        )
        reports.append({
            "unknown_halt": True, "physical_halts": len(halt_calls),
            "redispatch": len(halt_calls) - 1,
        })
        stage = "complete"
    except BaseException as exc:
        # Never leak runtime environment, DSNs, SQL rows or raw Docker payloads.
        raise RuntimeError(
            "private recovery fixture failed at " + stage + ": " + safe_fixture_error(exc)
        ) from None
    finally:
        cleanup = scope.cleanup()
        print(
            json.dumps(
                {
                    "fixture": "task9-recovery",
                    "stage": stage,
                    "cycles": reports,
                    "cleanup": cleanup,
                    "network_transport_only": True,
                },
                sort_keys=True,
            )
        )
        raw.close()
