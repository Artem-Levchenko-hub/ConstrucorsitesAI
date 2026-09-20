"""Secret-free, canonical evidence for code-restoration source binding."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any, cast
from uuid import UUID

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.services.cell_state import retained_serving_fencing_epoch
from omnia_orchestrator.services.restoration_database import admin_sql
from omnia_orchestrator.services.versioning.contracts import InventoryReport
from omnia_orchestrator.services.versioning.inventory import quote_ident


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def inventory_partition_digests(inventory: InventoryReport) -> tuple[str, str]:
    def digest(classifications: set[str]) -> str:
        rows = [
            item.model_dump(mode="json")
            for item in inventory.objects
            if item.classification in classifications
        ]
        rows.sort(key=lambda item: (item["object"], item["kind"]))
        return canonical_digest(rows)

    # Unknown stored objects are business-safety evidence: they never disappear
    # into the migration-ledger bucket merely because they cannot be classified.
    return digest({"business", "unknown"}), digest({"technical"})


def exact_inventory_partition_digests(
    backend: Any, inventory: InventoryReport
) -> tuple[str, str]:
    """Replace bounded/stats estimates with exact read-only counts for copy proof."""
    measured = [
        item
        for item in inventory.objects
        if item.classification in {"business", "unknown", "technical"}
    ]
    if any(item.kind not in {"table", "partitioned_table"} for item in measured):
        raise CellIdentityConflict("restoration inventory cannot be measured exactly")
    expressions = []
    for item in measured:
        if "." not in item.object:
            raise CellIdentityConflict("restoration inventory identity is invalid")
        schema, name = item.object.split(".", 1)
        expressions.append(
            f"(SELECT count(*) FROM {quote_ident(schema)}.{quote_ident(name)})"
        )
    sql = (
        "SET statement_timeout = '60s';\nSET default_transaction_read_only = on;\n"
        + "SELECT json_build_array("
        + ",".join(expressions)
        + ");"
    )
    try:
        counts = json.loads(admin_sql(backend, sql, max_bytes=64 * 1024))
    except (TypeError, ValueError) as exc:
        raise CellIdentityConflict("restoration exact inventory is unavailable") from exc
    if (
        not isinstance(counts, list)
        or len(counts) != len(measured)
        or any(type(value) is not int or value < 0 for value in counts)
    ):
        raise CellIdentityConflict("restoration exact inventory is invalid")
    by_name = {item.object: value for item, value in zip(measured, counts, strict=True)}
    objects = [
        item.model_copy(
            update={
                "row_count": by_name[item.object],
                "count_kind": "exact",
                "presence": "present" if by_name[item.object] else "empty",
                "diagnostic": None,
            }
        )
        if item.object in by_name
        else item
        for item in inventory.objects
    ]
    exact = inventory.model_copy(update={"objects": objects})
    return inventory_partition_digests(exact)


def source_artifact_digest(files: dict[str, bytes]) -> str:
    return canonical_digest(
        [
            {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(files.items())
        ]
    )


def _trusted_identity(backend: Any, resource: Any, kind: str) -> dict[str, str]:
    if resource is None:
        raise CellIdentityConflict(f"restoration source {kind} is missing")
    identity = backend.trusted_container_identity(resource, kind)
    if identity is None:
        raise CellIdentityConflict(f"restoration source {kind} is not trusted")
    return cast(dict[str, str], identity)


def _gateway_config(gateway: Any) -> dict[str, Any]:
    # The trusted script emits an allow-list. The gateway secret never crosses
    # this boundary and therefore cannot enter a journal, digest input or log.
    script = (
        "import json;v=json.load(open('/run/omnia-boundary/config.json'));"
        "print(json.dumps({k:v.get(k) for k in "
        "('project_id','epoch','core_host','machine_host','routes','public_mode','public_origin')}))"
    )
    result = gateway.exec_run(["python3", "-c", script])
    exit_code = getattr(result, "exit_code", result[0] if isinstance(result, tuple) else None)
    output = getattr(result, "output", result[1] if isinstance(result, tuple) else None)
    if exit_code != 0 or not isinstance(output, bytes) or len(output) > 65536:
        raise CellIdentityConflict("restoration serving route cannot be observed")
    try:
        value = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise CellIdentityConflict("restoration serving route is invalid") from exc
    if not isinstance(value, dict) or not isinstance(value.get("routes"), list):
        raise CellIdentityConflict("restoration serving route is incomplete")
    return value


def _environment(container: Any) -> dict[str, str]:
    values = container.attrs.get("Config", {}).get("Env")
    if not isinstance(values, list):
        raise CellIdentityConflict("restoration application environment is unavailable")
    result: dict[str, str] = {}
    for item in values:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def _database_observation(backend: Any) -> dict[str, Any]:
    expected = backend.project_database_env()
    database, role = expected.get("PGDATABASE"), expected.get("PGUSER")
    if not isinstance(database, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise CellIdentityConflict("restoration database name is invalid")
    if not isinstance(role, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", role):
        raise CellIdentityConflict("restoration database role is invalid")
    sql = f"""
SELECT json_build_object(
  'database_name', d.datname,
  'database_oid', d.oid::bigint,
  'role_name', r.rolname,
  'role_login', r.rolcanlogin,
  'role_superuser', r.rolsuper,
  'role_create_db', r.rolcreatedb,
  'role_create_role', r.rolcreaterole,
  'database_acl', COALESCE(d.datacl::text, ''),
  'system_identifier', (SELECT system_identifier::text FROM pg_control_system())
)::text
FROM pg_database d
JOIN pg_roles r ON r.rolname = '{role}'
WHERE d.datname = '{database}';
"""
    try:
        value = json.loads(admin_sql(backend, sql, max_bytes=16384))
    except (TypeError, ValueError) as exc:
        raise CellIdentityConflict("restoration database identity is unavailable") from exc
    if (
        not isinstance(value, dict)
        or value.get("database_name") != database
        or value.get("role_name") != role
        or type(value.get("database_oid")) is not int
        or not isinstance(value.get("system_identifier"), str)
    ):
        raise CellIdentityConflict("restoration database identity is incomplete")
    return value


def serving_fencing_epoch(
    state: Any,
    *,
    machine_state: dict[str, Any] | None = None,
) -> int:
    snapshot = machine_state or {}
    return retained_serving_fencing_epoch(
        state,
        machine_epoch=snapshot.get("epoch"),
        machine_ready_epoch=snapshot.get("ready_epoch"),
    )


def observe_live_source(
    backend: Any,
    machine: Any,
    state: Any,
    *,
    source_files: dict[str, bytes],
    schema: object,
    machine_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the live serving/runtime/DB identity without returning secrets."""
    app = backend._container()
    postgres = backend._project_postgres()
    gateway = backend._lookup(
        backend.client.containers, backend.stem + "-gateway", "max-gateway"
    )
    core = backend._lookup(
        backend.client.containers, backend.stem + "-max-core", "managed-max-core"
    )
    app_identity = _trusted_identity(backend, app, "development")
    postgres_identity = _trusted_identity(backend, postgres, "project-postgres")
    gateway_identity = _trusted_identity(backend, gateway, "max-gateway")
    core_identity = _trusted_identity(backend, core, "managed-max-core")
    volume = backend._lookup(
        backend.client.volumes, backend.project_postgres_volume, "project-volume"
    )
    if volume is None:
        raise CellIdentityConflict("restoration database volume is missing")
    volume_identity = {
        key: volume.attrs.get(key) for key in ("Name", "CreatedAt", "Labels")
    }
    expected_env = backend.project_database_env()
    actual_env = _environment(app)
    if any(actual_env.get(key) != value for key, value in expected_env.items()):
        raise CellIdentityConflict("serving application is bound to another database")
    route = _gateway_config(gateway)
    observed_machine_state = machine_state if machine_state is not None else machine.state()
    if not isinstance(observed_machine_state, dict):
        raise CellIdentityConflict("restoration serving machine state is unavailable")
    serving_epoch = serving_fencing_epoch(state, machine_state=observed_machine_state)
    if (
        observed_machine_state.get("epoch") != serving_epoch
        or observed_machine_state.get("ready_epoch") != serving_epoch
    ):
        raise CellIdentityConflict("restoration serving machine epoch is detached")
    networks = core.attrs.get("NetworkSettings", {}).get("Networks", {})
    core_network = networks.get(backend.internal_network) if isinstance(networks, dict) else None
    core_ip = core_network.get("IPAddress") if isinstance(core_network, dict) else None
    if (
        route.get("project_id") != str(state.project_id)
        or route.get("epoch") != serving_epoch
        or route.get("machine_host") != backend.address()
        or not isinstance(core_ip, str)
        or not core_ip
        or route.get("core_host") != core_ip
    ):
        raise CellIdentityConflict("serving route is detached from restoration source")
    database = _database_observation(backend)
    resources = None
    if is_dataclass(state.resource_names):
        resources = asdict(cast(Any, state.resource_names))
        resource_workspace_id = resources.get("workspace_id")
        if (
            not isinstance(resource_workspace_id, UUID)
            or resource_workspace_id != state.workspace_id
        ):
            raise CellIdentityConflict("controller resource workspace identity is invalid")
        resources["workspace_id"] = str(resource_workspace_id)
    return {
        "serving_route_digest": canonical_digest(route),
        "serving_release_digest": canonical_digest(
            {
                "app": app_identity,
                "gateway": gateway_identity,
                "core": core_identity,
                "manifest": observed_machine_state.get("manifest"),
                "ready_epoch": observed_machine_state.get("ready_epoch"),
            }
        ),
        "controller_resource_digest": canonical_digest(
            {
                "workspace_id": str(state.workspace_id),
                "project_id": str(state.project_id),
                "owner_id": str(state.owner_id),
                "profile_version": state.profile_version,
                "resources": resources,
            }
        ),
        "controller_incarnation_digest": canonical_digest(
            {
                "app": app_identity,
                "postgres": postgres_identity,
                "gateway": gateway_identity,
                "core": core_identity,
                "database_volume": volume_identity,
            }
        ),
        "controller_generation_digest": canonical_digest(
            {
                "fencing_epoch": state.fencing_epoch,
                "serving_fencing_epoch": serving_epoch,
                "active_generation_run_id": str(state.active_generation_run_id)
                if state.active_generation_run_id else None,
                "active_generation_fencing_epoch": state.active_generation_fencing_epoch,
                "last_operation_id": str(state.last_operation_id)
                if state.last_operation_id else None,
                "machine_epoch": observed_machine_state.get("epoch"),
            }
        ),
        "provider_digest": canonical_digest(
            {
                "provider_ref": state.provider_ref,
                "namespace": backend.namespace,
                "stem": backend.stem,
            }
        ),
        "source_artifact_digest": source_artifact_digest(source_files),
        "database_identity_digest": canonical_digest(
            {
                "database_name": database["database_name"],
                "database_oid": database["database_oid"],
                "postgres": postgres_identity,
                "volume": volume_identity,
            }
        ),
        "database_schema_digest": canonical_digest(schema),
        "database_role_binding_digest": canonical_digest(
            {
                key: database[key]
                for key in (
                    "role_name",
                    "role_login",
                    "role_superuser",
                    "role_create_db",
                    "role_create_role",
                    "database_acl",
                )
            }
        ),
        "database_system_identifier": database["system_identifier"],
    }
