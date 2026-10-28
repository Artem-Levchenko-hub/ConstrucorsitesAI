"""Kubernetes nullable-column publication IO; controller owns admission/journal locks."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pglast import ast, enums, parse_sql
from pglast.stream import RawStream

from yleum_orchestrator.schemas.cell_publication import CellDeployRequest
from yleum_orchestrator.services.machine_environment import MachineEnvironmentStore
from yleum_orchestrator.services.project_database_schema_proof import legacy_schema_digest
from yleum_orchestrator.services.project_machine import machine_effect
from yleum_orchestrator.services.project_migrations import LEGACY_PATHS
from yleum_orchestrator.services.publication_additive_plan import (
    AdditivePlan,
    MigrationBinding,
    authorize_plan,
    plan_additive,
)
from yleum_orchestrator.services.publication_additive_resolver import (
    CatalogColumn,
    CatalogReceipt,
    CatalogTable,
    binding_digest,
    catalog_digest,
    resolve_additive,
)
from yleum_orchestrator.services.publication_identity import fingerprint_key
from yleum_orchestrator.services.publication_migration_backup import (
    DockerPG,
    PrivateBackup,
    RestoreSandbox,
    pack_backup,
    require_schema,
    restore_backup,
)
from yleum_orchestrator.services.publication_migration_pg import (
    LEDGER,
    MAX_DUMP,
    KubernetesPG,
    MigrationIOError,
    digest,
    identifier,
    pg_identity,
    read_catalog,
    require,
    row_proofs,
    verify_ledger,
)
from yleum_orchestrator.services.publication_migration_schema import (
    read_schema_dump,
    require_schema_dump,
)


def verify_pending(
    files: dict[str, str], applied: dict[str, str], plan: AdditivePlan
) -> dict[str, str]:
    """The ordinary startup runner must replay additions as exact no-ops, no other SQL."""
    try:
        for path, checksum in applied.items():
            require(
                path in files and hashlib.sha256(files[path].encode()).hexdigest() == checksum,
                "migration_applied_source_changed",
            )
        pending = {
            p: s
            for p, s in sorted(files.items())
            if p.startswith("drizzle/")
            and p.endswith(".sql")
            and p not in applied
            and p not in LEGACY_PATHS
        }
        require(
            0 < len(pending) <= 64 and sum(len(s.encode()) for s in pending.values()) <= 65536,
            "migration_pending_source_budget",
        )
        normalized = []
        for path, sql in pending.items():
            require(
                re.fullmatch(r"drizzle/[^/\\]+\.sql", path) is not None,
                "migration_pending_source_path",
            )
            for raw in parse_sql(sql):
                node = raw.stmt
                require(
                    isinstance(node, ast.AlterTableStmt) and bool(node.cmds),
                    "migration_pending_source_unsupported",
                )
                for command in cast(ast.AlterTableStmt, node).cmds or ():
                    require(
                        command.subtype == enums.AlterTableType.AT_AddColumn
                        and command.missing_ok is True,
                        "migration_pending_replay_unsafe",
                    )
                    command.missing_ok = False
                normalized.append(RawStream()(node))
        candidate = plan_additive(plan.binding, ";".join(normalized), dict(plan.catalog))
        require(candidate == plan, "migration_pending_effect_changed")
        return {p: hashlib.sha256(s.encode()).hexdigest() for p, s in pending.items()}
    except Exception:
        raise MigrationIOError("migration_pending_replay_unsupported") from None


def recovery_patch(
    status: str | None, *, precommit: bool, schema_matches: bool, xid: str | None = None
) -> dict[str, Any]:
    valid_xid = isinstance(xid, str) and re.fullmatch(r"[0-9]{1,20}", xid) is not None
    if status == "aborted" and valid_xid:
        return {
            "state": "failed",
            "effect_possible": False,
            "error_code": "publication_migration_aborted",
            "outcome_proof": {"kind": "xid_aborted", "xid": xid},
        }
    if status == "committed" and precommit and schema_matches and valid_xid:
        return {
            "state": "applied",
            "effect_possible": False,
            "postcommit_verified": True,
            "outcome_proof": {"kind": "xid_committed_verified", "xid": xid},
        }
    return {
        "state": "reconciling",
        "effect_possible": True,
        "error_code": "publication_migration_outcome_unconfirmed",
    }


def _receipt(
    binding: MigrationBinding, tables: tuple[CatalogTable, ...], kind: str
) -> CatalogReceipt:
    schema = binding.live_schema_sha256 if kind == "published" else binding.target_schema_sha256
    return CatalogReceipt(
        kind,
        binding_digest(binding),
        schema,
        binding.data_contract_sha256,
        catalog_digest(tables),
        tables,
        True,
    )


def _unpack_plan(saved: dict[str, Any]) -> AdditivePlan:
    try:
        values = dict(saved["binding"])
        for name in ("owner_id", "project_id", "snapshot_id", "pvc_uid"):
            values[name] = UUID(values[name])
        binding = MigrationBinding(**values)
        return AdditivePlan(
            binding,
            tuple(saved["statements"]),
            tuple((name, tuple(cols)) for name, cols in saved["catalog"]),
        )
    except Exception:
        raise MigrationIOError("migration_prepared_plan_invalid") from None


def _unpack_catalog(saved: list[dict[str, Any]]) -> tuple[CatalogTable, ...]:
    result = []
    for table in saved:
        row = dict(table)
        row["columns"] = tuple(CatalogColumn(**column) for column in row["columns"])
        result.append(CatalogTable(**row))
    return tuple(result)


def _source_binding(request: CellDeployRequest) -> dict[str, Any]:
    return {
        key: str(getattr(request, key))
        for key in (
            "owner_id",
            "project_id",
            "workspace_id",
            "snapshot_id",
            "candidate_id",
            "commit_sha",
            "source_revision",
            "fencing_epoch",
            "accepted_fencing_epoch",
            "proof_key",
            "schema_data_digest",
            "restoration_operation_id",
        )
    }


def _ledger(pg: Any) -> dict[str, str]:
    verify_ledger(pg, required=True)
    result = pg.json(
        "SELECT coalesce(json_object_agg(name,sha256),'{}'::json) "
        "FROM public.__omnia_project_migrations;"
    )
    require(
        isinstance(result, dict)
        and len(result) <= 256
        and all(
            isinstance(p, str)
            and re.fullmatch(r"drizzle/[^/\\]+\.sql", p)
            and isinstance(h, str)
            and re.fullmatch(r"[0-9a-f]{64}", h)
            for p, h in result.items()
        ),
        "migration_ledger_invalid",
    )
    return dict(result)


class PublicationMigrationIO:
    def __init__(self, publication_service: Any) -> None:
        self.service = publication_service

    async def _source(
        self, request: CellDeployRequest, manager: Any
    ) -> tuple[Any, Any, dict[str, str], dict[str, Any]]:
        from yleum_orchestrator.core.project_machine import MachineManifest
        from yleum_orchestrator.routers.runtime import _workspace_revision
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files
        from yleum_orchestrator.services.cell_deletion import require_workspace_not_deleted

        require_workspace_not_deleted(manager.profile.state_path, request.workspace_id)
        state = manager.state_store.load(request.workspace_id)
        require(
            state is not None
            and state.owner_id == request.owner_id
            and state.project_id == request.project_id
            and state.fencing_epoch == request.fencing_epoch
            and state.active_generation_run_id is None
            and state.phase == "completed"
            and state.bundle_state == "resources_ready",
            "migration_source_changed",
        )
        adapter = manager.machine_runtime
        require(adapter is not None, "migration_source_unavailable")
        machine, source = adapter.parts(state)
        files = await _read_agent_workspace_files(manager, source.workspace_volume)
        require(_workspace_revision(files) == request.source_revision, "migration_source_changed")
        self.service._verify_restoration_source(request, source)
        manifest = MachineManifest.model_validate(machine.state()["manifest"])
        sealed = self.service._checkpoint_seal(request, source, adapter, manifest)
        require(sealed is not None, "migration_accepted_seal_unavailable")
        store = MachineEnvironmentStore(
            adapter.root / "artifacts", source.workspace_id, source, max_bytes=source.disk_bytes
        )
        await machine_effect(store.validate, sealed["reference"], manifest_digest=manifest.digest())
        return source, manifest, files, sealed

    async def prepare(self, request: CellDeployRequest, operation_id: UUID) -> dict[str, Any]:
        manager = self.service._manager(request.workspace_id)
        async with manager.operation_lock.hold(request.workspace_id):
            source, manifest, files, sealed = await self._source(request, manager)
            return dict(
                await machine_effect(
                    self._prepare, request, operation_id, source, manifest, files, sealed
                )
            )

    def _target(
        self, request: CellDeployRequest | None = None, prepared: dict[str, Any] | None = None
    ) -> tuple[KubernetesPG, dict[str, Any]]:
        project = (
            request.project_id
            if request is not None
            else UUID(cast(dict[str, Any], prepared)["project_id"])
        )
        saved = self.service._read(project)
        active = saved.get("active_release") or {}
        require(
            active.get("placement", {}).get("backend") == "kubernetes"
            and not any(
                saved.get(k) for k in ("disabled", "recovery_required", "activation_pending")
            ),
            "migration_publication_target_unsupported",
        )
        if prepared is not None:
            require(
                active.get("release_id") == prepared["active_release_id"]
                and active.get("epoch") == prepared["active_epoch"],
                "migration_publication_changed",
            )
        return KubernetesPG(self.service._kubernetes().runtime, str(project)), active

    def _prepare(
        self,
        request: CellDeployRequest,
        operation: UUID,
        source: Any,
        manifest: Any,
        files: dict[str, str],
        sealed: dict[str, Any],
    ) -> dict[str, Any]:
        from yleum_orchestrator.services.published_machine_backend import data_contract_digest

        target, active = self._target(request)
        storage = target.identify()
        configured_image = self.service.settings.cell_postgres_image
        require(
            isinstance(configured_image, str)
            and "@sha256:" in configured_image
            and storage["image"].split("@", 1)[1] == configured_image.split("@", 1)[1],
            "migration_verifier_image_untrusted",
        )
        contract = data_contract_digest(manifest)
        require(contract == active.get("data_contract_digest"), "migration_data_contract_changed")
        container = source._project_postgres()
        require(container is not None, "migration_source_database_stopped")
        source_pg = DockerPG(source.client, container)
        source_dump = read_schema_dump(source_pg)
        source_schema = legacy_schema_digest(source_dump)
        require_schema(source_schema, sealed["schema_digest"])
        source_tables = read_catalog(source_pg)
        live_dump = read_schema_dump(target)
        live_schema = legacy_schema_digest(live_dump)
        require_schema(live_schema, self.service._accepted_schema_digest(active))
        globals_dump = target.globals()
        globals_hash = legacy_globals_hash(globals_dump)
        with target.session() as session:
            session.sql(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "
                "SET LOCAL statement_timeout='45s'; SET LOCAL lock_timeout='5s'; "
                "SET LOCAL idle_in_transaction_session_timeout='180s';"
            )
            snapshot = session.json("SELECT to_json(pg_catalog.pg_export_snapshot());")
            require(
                isinstance(snapshot, str) and re.fullmatch(r"[0-9A-F-]+", snapshot) is not None,
                "migration_snapshot_invalid",
            )
            live_tables = read_catalog(session)
            applied = _ledger(session)
            rows = row_proofs(session, live_tables)
            database_dump = target.command(
                [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--snapshot=" + snapshot,
                    "-h",
                    "/tmp",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                ],
                max_bytes=MAX_DUMP - len(globals_dump) - 16,
            )
            require(target.identify() == storage, "migration_storage_changed")
            require(
                legacy_globals_hash(target.globals()) == globals_hash, "migration_globals_changed"
            )
            require_schema(target.full_schema(), live_schema)
            session.sql("COMMIT;")
        packed = pack_backup(globals_dump, database_dump)
        source_bound = _source_binding(request)
        aad = {
            "storage": storage,
            "source": source_bound,
            "live_schema": live_schema,
            "target_schema": source_schema,
            "contract": contract,
        }
        backup = PrivateBackup(self.service.root, fingerprint_key(self.service.root))
        backup_receipt = backup.save(request.project_id, operation, packed, aad)
        # Read/authenticate durable ciphertext before relying on the backup.
        packed = backup.load(request.project_id, operation, backup_receipt, aad)
        binding = MigrationBinding(
            request.owner_id,
            request.project_id,
            request.snapshot_id,
            request.commit_sha,
            digest(
                {
                    "build": request.build_ref,
                    "proof": request.proof_key,
                    "verification": request.verification_ref,
                }
            ),
            UUID(storage["pvc_uid"]),
            request.fencing_epoch,
            live_schema,
            source_schema,
            contract,
            backup_receipt["sha256"],
        )
        sandbox = RestoreSandbox(source.client, request.project_id, operation, storage["image"])
        with sandbox as restored:
            restore_backup(restored, packed)
            require_schema_dump(read_schema_dump(restored), live_dump, stage="backup_restore")
            require(
                read_catalog(restored) == live_tables and row_proofs(restored, live_tables) == rows,
                "migration_backup_restore_mismatch",
            )
            plan = resolve_additive(
                binding,
                _receipt(binding, live_tables, "published"),
                _receipt(binding, source_tables, "source"),
                backup_verified=True,
            )
            pending = verify_pending(files, applied, plan)
            restored.sql(
                "BEGIN; SET LOCAL statement_timeout='30s'; "
                + ";".join(plan.statements)
                + ";COMMIT;"
            )
            require_schema_dump(read_schema_dump(restored), source_dump, stage="trial_migration")
            require(
                read_catalog(restored) == source_tables
                and row_proofs(restored, live_tables) == rows,
                "migration_sandbox_effect_changed",
            )
        require(sandbox.cleaned, "migration_verifier_cleanup_unconfirmed")
        require(
            target.identify() == storage and legacy_globals_hash(target.globals()) == globals_hash,
            "migration_target_changed",
        )
        require_schema_dump(read_schema_dump(target), live_dump, stage="live_recheck")
        require_schema_dump(read_schema_dump(source_pg), source_dump, stage="source_recheck")
        public_fields = [
            {"table": old.name, "name": c.name, "type": c.sql_type}
            for old, new in zip(live_tables, source_tables, strict=True)
            for c in new.columns[len(old.columns) :]
        ]
        return {
            "version": 1,
            "operation_id": str(operation),
            "owner_id": str(request.owner_id),
            "project_id": str(request.project_id),
            "snapshot_id": str(request.snapshot_id),
            "source_binding": source_bound,
            "active_release_id": active["release_id"],
            "active_epoch": active["epoch"],
            "storage": storage,
            "globals_sha256": globals_hash,
            "binding": binding.projection(),
            "statements": list(plan.statements),
            "catalog": [[n, list(c)] for n, c in plan.catalog],
            "plan_sha256": plan.sha256,
            "live_catalog": [asdict(t) for t in live_tables],
            "target_catalog": [asdict(t) for t in source_tables],
            "pending_migrations": pending,
            "backup": backup_receipt,
            "backup_aad": aad,
            "backup_verified": True,
            "sandbox_before_sha256": live_schema,
            "sandbox_after_sha256": source_schema,
            "sandbox_cleanup_verified": True,
            "public_fields": public_fields,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            "effect_possible": False,
            "precommit_verified": False,
        }

    async def apply(
        self,
        request: CellDeployRequest,
        prepared: dict[str, Any],
        persist: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        manager = self.service._manager(request.workspace_id)
        dispatched = False
        try:
            async with manager.operation_lock.hold(request.workspace_id):
                source, manifest, files, sealed = await self._source(request, manager)
                dispatched = True
                return dict(
                    await machine_effect(
                        self._apply, request, prepared, persist, source, manifest, files, sealed
                    )
                )
        except Exception:
            # Worker deadline/unlock can fail AFTER physical SQL and durable intent.
            # Only pre-dispatch failures prove absence of this call's effects.
            if dispatched or prepared.get("effect_possible") or prepared.get("intent_xid"):
                return recovery_patch(None, precommit=False, schema_matches=False)
            return {
                "state": "failed",
                "effect_possible": False,
                "error_code": "publication_migration_preflight_failed",
                "outcome_proof": {"kind": "preflight_no_dispatch"},
            }

    def _apply(
        self,
        request: CellDeployRequest,
        prepared: dict[str, Any],
        persist: Callable[[dict[str, Any]], None],
        source: Any,
        manifest: Any,
        files: dict[str, str],
        sealed: dict[str, Any],
    ) -> dict[str, Any]:
        from yleum_orchestrator.services.published_machine_backend import data_contract_digest

        intent_sent = False
        context = dict(prepared)
        try:
            require(
                prepared.get("effect_possible") is False and prepared.get("intent_xid") is None,
                "migration_effect_already_possible",
            )
            require(
                _source_binding(request) == prepared["source_binding"]
                and datetime.now(UTC) < datetime.fromisoformat(prepared["expires_at"]),
                "migration_prepared_stale",
            )
            plan = _unpack_plan(prepared)
            statements = authorize_plan(
                plan,
                current=plan.binding,
                owner_id=request.owner_id,
                project_id=request.project_id,
                snapshot_id=request.snapshot_id,
                acknowledged_plan_sha256=prepared["plan_sha256"],
                explicit_migration_intent=True,
            )
            require(
                prepared["backup_verified"] is True
                and prepared["sandbox_cleanup_verified"] is True
                and prepared["sandbox_before_sha256"] == plan.binding.live_schema_sha256
                and prepared["sandbox_after_sha256"] == plan.binding.target_schema_sha256,
                "migration_backup_proof_missing",
            )
            PrivateBackup(self.service.root, fingerprint_key(self.service.root)).load(
                request.project_id,
                UUID(prepared["operation_id"]),
                prepared["backup"],
                prepared["backup_aad"],
            )
            require(
                data_contract_digest(manifest) == plan.binding.data_contract_sha256,
                "migration_data_contract_changed",
            )
            source_pg = DockerPG(source.client, source._project_postgres())
            require_schema(source_pg.full_schema(), sealed["schema_digest"])
            require_schema(sealed["schema_digest"], plan.binding.target_schema_sha256)
            target, _ = self._target(request, prepared)
            require(target.identify() == prepared["storage"], "migration_storage_changed")
            require_schema(target.full_schema(), plan.binding.live_schema_sha256)
            require(
                legacy_globals_hash(target.globals()) == prepared["globals_sha256"],
                "migration_globals_changed",
            )
            live_tables = _unpack_catalog(prepared["live_catalog"])
            expected_tables = _unpack_catalog(prepared["target_catalog"])
            with target.session() as session:
                session.sql(
                    "BEGIN; SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='5s'; "
                    "SET LOCAL idle_in_transaction_session_timeout='60s'; "
                    "SET LOCAL synchronous_commit=on; "
                    "SELECT pg_catalog.pg_advisory_xact_lock(hashtext('omnia:project:roles:v1')); "
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "hashtext('omnia:project:migrations:v1'));"
                )
                lock_tables = sorted([t.name for t in live_tables] + [LEDGER])
                session.sql(
                    "LOCK TABLE "
                    + ",".join("public." + identifier(n, ledger=True) for n in lock_tables)
                    + " IN ACCESS EXCLUSIVE MODE;"
                )
                require(read_catalog(session) == live_tables, "migration_live_catalog_changed")
                before_rows = row_proofs(session, live_tables)
                require(
                    verify_pending(files, _ledger(session), plan) == prepared["pending_migrations"],
                    "migration_pending_source_changed",
                )
                current_identity = pg_identity(session)
                require(
                    all(prepared["storage"].get(k) == v for k, v in current_identity.items()),
                    "migration_database_identity_changed",
                )
                identity = session.json(
                    "SELECT json_build_object('xid',pg_current_xact_id()::text,"
                    "'pid',pg_backend_pid(),'start',(SELECT backend_start::text FROM "
                    "pg_catalog.pg_stat_activity WHERE pid=pg_backend_pid()));"
                )
                require(
                    re.fullmatch(r"[0-9]{1,20}", identity["xid"]) is not None,
                    "migration_xid_unavailable",
                )
                require(
                    isinstance(getattr(session, "pod_uid", None), str) and bool(session.pod_uid),
                    "migration_session_identity_unavailable",
                )
                identity["pod_uid"] = session.pod_uid
                patch = {
                    "intent_xid": identity["xid"],
                    "session_identity": identity,
                    "effect_possible": True,
                    "precommit_verified": False,
                    "intent_at": datetime.now(UTC).isoformat(),
                }
                persist(patch)  # Durable exact xid BEFORE any DDL.
                context.update(patch)
                intent_sent = True
                session.sql(";".join(statements) + ";")
                require(
                    read_catalog(session) == expected_tables
                    and row_proofs(session, live_tables) == before_rows,
                    "migration_transaction_postcondition_failed",
                )
                patch = {
                    "precommit_verified": True,
                    "precommit_rows_sha256": digest(before_rows),
                    "precommit_plan_sha256": plan.sha256,
                }
                persist(patch)  # Durable validated postconditions BEFORE COMMIT.
                context.update(patch)
                session.sql("COMMIT;")
            return self._reconcile(context)
        except Exception:
            if intent_sent:
                return self._reconcile(context)
            return {
                "state": "failed",
                "effect_possible": False,
                "error_code": "publication_migration_preflight_failed",
                "outcome_proof": {"kind": "preflight_no_dispatch"},
            }

    async def reconcile(self, prepared: dict[str, Any]) -> dict[str, Any]:
        return dict(await machine_effect(self._reconcile, prepared))

    def _reconcile(self, prepared: dict[str, Any]) -> dict[str, Any]:
        unknown = recovery_patch(None, precommit=False, schema_matches=False)
        try:
            xid = prepared.get("intent_xid")
            require(
                isinstance(xid, str) and re.fullmatch(r"[0-9]{1,20}", xid) is not None,
                "migration_xid_unavailable",
            )
            require(
                datetime.now(UTC) - datetime.fromisoformat(prepared["intent_at"])
                < timedelta(hours=1),
                "migration_xid_evidence_expired",
            )
            target, _ = self._target(prepared=prepared)
            require(target.identify() == prepared["storage"], "migration_recovery_identity_changed")
            with target.session() as session:
                status = session.json(
                    "SELECT to_json(pg_catalog.pg_xact_status('" + cast(str, xid) + "'::xid8));"
                )
            precommit = (
                prepared.get("precommit_verified") is True
                and prepared.get("precommit_plan_sha256") == prepared["plan_sha256"]
            )
            matches = (
                status == "committed"
                and target.full_schema() == prepared["binding"]["target_schema_sha256"]
            )
            return recovery_patch(
                status, precommit=precommit, schema_matches=matches, xid=cast(str, xid)
            )
        except Exception:
            return unknown


def legacy_globals_hash(data: bytes) -> str:
    return legacy_schema_digest(data.decode("utf-8"))
