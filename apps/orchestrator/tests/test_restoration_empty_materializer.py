from __future__ import annotations

import json
from types import SimpleNamespace

DRIZZLE_CONFIG = '''import type { Config } from "drizzle-kit";

export default {
  schema: "./src/lib/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL as string,
  },
} satisfies Config;
'''


def drizzle_files(**changes: str) -> dict[str, str]:
    files = {
        "package.json": json.dumps(
            {
                "private": True,
                "packageManager": "pnpm@9.15.0",
                "scripts": {"db:push": "do not execute package scripts"},
                "dependencies": {"drizzle-orm": "^0.36.0"},
                "devDependencies": {"drizzle-kit": "^0.28.0"},
            }
        ),
        "pnpm-lock.yaml": """lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      drizzle-orm:
        specifier: ^0.36.0
        version: 0.36.4
    devDependencies:
      drizzle-kit:
        specifier: ^0.28.0
        version: 0.28.1
""",
        "drizzle.config.ts": DRIZZLE_CONFIG,
        "src/lib/db/schema.ts": "export const tasks = {};",
    }
    files.update(changes)
    return files


def test_schema_only_drizzle_materializer_uses_direct_exact_argv():
    from omnia_orchestrator.services.code_restoration_engine import (
        empty_database_materializer,
    )

    assert empty_database_materializer(drizzle_files()) == [
        "pnpm",
        "exec",
        "drizzle-kit",
        "push",
        "--config=drizzle.config.ts",
        "--force",
    ]


def test_legacy_materializer_remains_supported_without_drizzle_metadata():
    from omnia_orchestrator.services.code_restoration_engine import (
        empty_database_materializer,
    )

    assert empty_database_materializer(
        {"scripts/apply-migrations.mjs": "// historical trusted runner"}
    ) == ["node", "scripts/apply-migrations.mjs"]


def test_schema_only_materializer_fails_closed_without_exact_recipe():
    from omnia_orchestrator.services.code_restoration_engine import (
        empty_database_materializer,
    )

    for change in (
        {"pnpm-lock.yaml": ""},
        {"drizzle.config.ts": DRIZZLE_CONFIG.replace("schema.ts", "other.ts")},
        {
            "package.json": json.dumps(
                {
                    "packageManager": "pnpm@9.15.0",
                    "dependencies": {"drizzle-orm": "^0.36.0"},
                    "devDependencies": {"drizzle-kit": "latest"},
                }
            )
        },
    ):
        assert empty_database_materializer(drizzle_files(**change)) is None


def test_schema_only_materializer_requires_versions_in_root_importer():
    from omnia_orchestrator.services.code_restoration_engine import (
        empty_database_materializer,
    )

    nested_only = drizzle_files()["pnpm-lock.yaml"].replace("  .:", "  nested-app:")

    assert empty_database_materializer(drizzle_files(**{"pnpm-lock.yaml": nested_only})) is None


def test_schema_only_materializer_ignores_duplicate_versions_in_nested_importer():
    from omnia_orchestrator.services.code_restoration_engine import (
        empty_database_materializer,
    )

    root = drizzle_files()["pnpm-lock.yaml"]
    nested = root.replace("  .:", "  nested-app:").split("importers:\n", 1)[1]

    assert empty_database_materializer(
        drizzle_files(**{"pnpm-lock.yaml": root + nested})
    ) == [
        "pnpm",
        "exec",
        "drizzle-kit",
        "push",
        "--config=drizzle.config.ts",
        "--force",
    ]


def test_structural_proof_ignores_unmeasured_json_metadata_and_default():
    from omnia_orchestrator.services.code_restoration_engine import (
        structural_materialization_matches,
    )
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    expected = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "documents",
                    "columns": [
                        {
                            "name": "payload",
                            "type": "jsonb",
                            "nullable": False,
                            "json_keys": ["text"],
                        }
                    ],
                }
            ],
        }
    )
    actual = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "documents",
                    "columns": [
                        {
                            "name": "payload",
                            "type": "jsonb",
                            "nullable": False,
                            "default": "'{}'::jsonb",
                        }
                    ],
                }
            ],
        }
    )

    assert structural_materialization_matches(expected, actual)


def test_structural_proof_rejects_mismatched_declared_default_and_identity():
    from omnia_orchestrator.services.code_restoration_engine import (
        structural_materialization_matches,
    )
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    expected = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "documents",
                    "columns": [
                        {
                            "name": "id",
                            "type": "bigint",
                            "nullable": False,
                            "default": "nextval('documents_id_seq'::regclass)",
                            "identity": "by_default",
                        }
                    ],
                }
            ],
        }
    )
    actual_payload = expected.model_dump(mode="json")
    actual_payload["tables"][0]["columns"][0]["identity"] = "always"

    assert not structural_materialization_matches(
        expected, DataContract.model_validate(actual_payload)
    )


def test_structural_proof_matches_columns_by_name_not_physical_order():
    from omnia_orchestrator.services.code_restoration_engine import (
        structural_materialization_matches,
    )
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    expected = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "tasks",
                    "columns": [
                        {"name": "id", "type": "uuid", "nullable": False},
                        {"name": "title", "type": "text", "nullable": False},
                    ],
                }
            ],
        }
    )
    actual_payload = expected.model_dump(mode="json")
    actual_payload["tables"][0]["columns"].reverse()

    assert structural_materialization_matches(
        expected, DataContract.model_validate(actual_payload)
    )


def test_structural_proof_ignores_typescript_only_text_enum_hint():
    from omnia_orchestrator.services.code_restoration_engine import (
        structural_materialization_matches,
    )
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    expected = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "tasks",
                    "columns": [
                        {
                            "name": "state",
                            "type": "text",
                            "values": ["todo", "done"],
                        }
                    ],
                }
            ],
        }
    )
    actual_payload = expected.model_dump(mode="json")
    actual_payload["tables"][0]["columns"][0]["values"] = None

    assert structural_materialization_matches(
        expected, DataContract.model_validate(actual_payload)
    )


def test_structural_proof_rejects_changed_postgresql_enum_labels():
    from omnia_orchestrator.services.code_restoration_engine import (
        structural_materialization_matches,
    )
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    expected = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "tasks",
                    "columns": [
                        {
                            "name": "state",
                            "type": "task_state",
                            "values": ["todo", "done"],
                        }
                    ],
                }
            ],
        }
    )
    actual_payload = expected.model_dump(mode="json")
    actual_payload["tables"][0]["columns"][0]["values"] = ["todo", "closed"]

    assert not structural_materialization_matches(
        expected, DataContract.model_validate(actual_payload)
    )


def _prepare_request(files: dict[str, str]):
    import base64

    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare
    from tests.test_code_restoration_engine import plain_prepare_request

    payload = plain_prepare_request().model_dump(mode="json")
    payload.update(
        binding_contract_version=3,
        files=[
            {
                "path": path,
                "content_base64": base64.b64encode(content.encode()).decode(),
            }
            for path, content in files.items()
        ],
    )
    return CodeRestorationPrepare.model_validate(payload)


async def _prepare_empty(
    tmp_path,
    monkeypatch,
    files: dict[str, str],
    *,
    materialized_schema: str = "exact",
):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
    from omnia_orchestrator.services.restoration_data_contract import DataContract
    from omnia_orchestrator.services.restoration_empty import EmptyDatabaseWitness
    from tests.test_code_restoration_engine import Lock
    from tests.test_project_machine_manifest import payload

    request = _prepare_request(files)
    manifest = MachineManifest.model_validate(payload())
    events: list[object] = []
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(cell_required_free_disk_bytes=0)
    state = SimpleNamespace(
        workspace_id=request.workspace_id,
        fencing_epoch=request.fencing_epoch,
        last_operation_id=None,
        operations=(),
        operation=lambda _operation_id: None,
    )
    expected_labels = {
        "omnia.workspace_id": str(request.workspace_id),
        "omnia.project_id": str(request.project_id),
        "omnia.owner_id": str(request.owner_id),
        "omnia.resource_kind": "project-volume",
    }
    volume = SimpleNamespace(
        attrs={
            "Name": "live-db",
            "CreatedAt": "2026-09-21T00:00:00Z",
            "Driver": "local",
            "Scope": "local",
            "Options": {},
            "Labels": expected_labels,
        }
    )

    class Container:
        def __init__(self, *, database):
            self.labels = {"omnia.fencing_epoch": str(request.fencing_epoch)}
            self.status = "running"
            self.attrs = {
                "Mounts": [
                    {
                        "Name": "live-db" if database else "live-code",
                        "Destination": ("/var/lib/postgresql/data" if database else "/workspace"),
                    }
                ]
            }

        def reload(self):
            return None

    application = Container(database=False)
    postgres = Container(database=True)
    source = SimpleNamespace(
        name="source",
        workspace_volume="live-code",
        project_postgres_volume="live-db",
        project_postgres_password="live-password",
        client=SimpleNamespace(volumes=object()),
        labels=lambda kind: {**expected_labels, "omnia.resource_kind": kind},
        _lookup=lambda _collection, name, kind: (
            volume if name == "live-db" and kind == "project-volume" else None
        ),
        _container=lambda: application,
        _project_postgres=lambda: postgres,
        service_status=lambda *_args, **_kwargs: {"state": "running", "ready": True},
        is_running=lambda: True,
    )
    machine = SimpleNamespace(
        state=lambda: {
            "epoch": request.fencing_epoch,
            "ready_epoch": request.fencing_epoch,
            "manifest": manifest.model_dump(),
        }
    )
    candidate = SimpleNamespace(
        name="candidate",
        workspace_volume="candidate-code",
        project_postgres_volume="candidate-db",
        base_image="image",
        stop_machine=lambda: events.append("candidate-writers-stopped"),
        stop=lambda: events.append("candidate-stopped"),
    )

    async def read_sources(_volume):
        return {}

    class Runtime:
        def parts(self, observed_state):
            assert observed_state is state
            return machine, source

        def preview(self, observed_state):
            assert observed_state is state
            return "running", "127.0.0.1"

    manager = SimpleNamespace(
        operation_lock=Lock(),
        machine_runtime=Runtime(),
        docker=SimpleNamespace(read_workspace_source_files=read_sources),
    )
    engine._manager = lambda _: manager
    engine._state = lambda *args, **kwargs: state
    engine._runtime_identity = lambda *_args: {"runtime": "source"}
    engine._repair_legacy_release_receipt = lambda _manager, _request, value, _saved: value
    monkeypatch.setattr(module, "validate_supported_runtime", lambda _files: manifest)
    monkeypatch.setattr(module, "verify_source_inventory", lambda *_args: None)

    async def workspace_files(*_args):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", workspace_files
    )
    live = {
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
        "database_system_identifier": "system",
    }
    monkeypatch.setattr(module, "observe_live_source", lambda *_args, **_kwargs: live)
    source_witness = EmptyDatabaseWitness(
        operation_id=request.operation_id,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        database_identity_digest="8" * 64,
        catalog_digest="b" * 64,
        objects_digest="c" * 64,
        technical_state_digest="d" * 64,
        identity_rows_digest="e" * 64,
        identity_relations=[],
        observation_kind="source",
    )

    def witness(backend, **kwargs):
        if backend is source:
            return source_witness.model_copy(
                update={"observation_kind": kwargs["observation_kind"]}
            )
        return source_witness.model_copy(
            update={
                "database_identity_digest": kwargs["database_identity_digest"],
                "catalog_digest": "f" * 64,
                "objects_digest": "0" * 64,
                "technical_state_digest": "1" * 64,
                "observation_kind": kwargs["observation_kind"],
            }
        )

    monkeypatch.setattr(module, "observe_empty_database", witness)
    expected_contract = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "qa_tasks",
                    "columns": [
                        {"name": "id", "type": "uuid", "nullable": False},
                        {"name": "title", "type": "text", "nullable": False},
                        {
                            "name": "payload",
                            "type": "jsonb",
                            "nullable": False,
                            "json_keys": ["text"],
                        },
                    ],
                    "primary_key": ["id"],
                },
                {
                    "name": "max_catalog_items",
                    "columns": [{"name": "id", "type": "uuid", "nullable": False}],
                    "primary_key": ["id"],
                },
            ],
        }
    )

    def historical_contract(backend, contract_files):
        assert backend is candidate
        assert contract_files == files
        events.append("historical-contract-derived")
        return expected_contract

    def materialized_catalog(backend):
        assert backend is candidate
        events.append("candidate-catalog-observed")
        actual_payload = expected_contract.model_dump(mode="json")
        actual_payload["tables"][0]["columns"][2]["json_keys"] = None
        if materialized_schema == "missing_table":
            actual_payload["tables"] = []
        elif materialized_schema == "missing_json_column":
            actual_payload["tables"][0]["columns"].pop()
        elif materialized_schema == "missing_max_catalog_items":
            actual_payload["tables"] = [
                table
                for table in actual_payload["tables"]
                if table["name"] != "max_catalog_items"
            ]
        actual = DataContract.model_validate(actual_payload)
        return actual, [], []

    monkeypatch.setattr(module, "candidate_contract", historical_contract)
    monkeypatch.setattr(module, "describe_live_catalog", materialized_catalog)
    engine._dump_identity_rows = lambda *_args: b""

    async def make_candidate(*_args):
        events.append("candidate-provisioned")
        return candidate

    engine._candidate = make_candidate
    engine._seed_source = lambda *_args: events.append("seed")

    async def run_stage(_request, _backend, argv, _timeout, _cwd=".", *, stage):
        events.append((stage, argv))

    engine._run_stage = run_stage
    engine._disable_egress = lambda *_args: events.append("egress-off")
    engine._install_identity_rows = lambda *_args: events.append("identity-installed")

    async def start(*_args):
        events.append("started")

    engine._start = start
    engine._verify_source = lambda *_args: events.append("source-verified")
    engine._rotate_database_password = lambda *_args: events.append("password-rotated")
    engine._capture_code = lambda *_args, **_kwargs: "2" * 64
    engine._capture_database = lambda *_args, **_kwargs: "3" * 64

    async def cleanup(*_args):
        events.append("cleanup")

    engine._cleanup_candidate = cleanup
    return await engine.prepare(request), events


async def test_schema_only_empty_prepare_is_exact_ready_and_uses_no_compatibility(
    tmp_path, monkeypatch
):
    result, events = await _prepare_empty(tmp_path, monkeypatch, drizzle_files())

    assert result["state"] == "ready"
    assert result["report"]["mode"] == "exact"
    assert result["report"]["database_state"] == "empty"
    assert result["binding"]["version"] == 3
    assert result["binding"]["database_strategy"] == "replace_verified_empty"
    assert events.index("historical-contract-derived") < events.index(
        "candidate-catalog-observed"
    )
    migration = next(
        item
        for item in events
        if isinstance(item, tuple) and item[0] == "empty-database-migrations"
    )
    assert migration[1] == [
        "pnpm",
        "exec",
        "drizzle-kit",
        "push",
        "--config=drizzle.config.ts",
        "--force",
    ]


async def test_successful_materializer_without_expected_table_needs_changes(
    tmp_path, monkeypatch
):
    result, events = await _prepare_empty(
        tmp_path,
        monkeypatch,
        drizzle_files(),
        materialized_schema="missing_table",
    )

    assert (
        "empty-database-migrations",
        [
            "pnpm",
            "exec",
            "drizzle-kit",
            "push",
            "--config=drizzle.config.ts",
            "--force",
        ],
    ) in events
    assert result["state"] == "needs_changes"
    assert result["report"]["database_state"] == "empty"
    assert result["report"]["blockers"] == [
        "Историческая схема не создана в изолированной базе."
    ]
    assert "identity-installed" not in events


async def test_materializer_missing_json_column_needs_changes(tmp_path, monkeypatch):
    result, events = await _prepare_empty(
        tmp_path,
        monkeypatch,
        drizzle_files(),
        materialized_schema="missing_json_column",
    )

    assert result["state"] == "needs_changes"
    assert result["report"]["blockers"] == [
        "Историческая схема не создана в изолированной базе."
    ]
    assert "identity-installed" not in events


async def test_materializer_missing_named_max_business_table_needs_changes(
    tmp_path, monkeypatch
):
    result, events = await _prepare_empty(
        tmp_path,
        monkeypatch,
        drizzle_files(),
        materialized_schema="missing_max_catalog_items",
    )

    assert result["state"] == "needs_changes"
    assert result["report"]["blockers"] == [
        "Историческая схема не создана в изолированной базе."
    ]
    assert "identity-installed" not in events


async def test_legacy_empty_prepare_still_uses_historical_runner(tmp_path, monkeypatch):
    result, events = await _prepare_empty(
        tmp_path,
        monkeypatch,
        {
            "package.json": json.dumps({"scripts": {}}),
            "scripts/apply-migrations.mjs": "// historical trusted runner",
        },
    )

    assert result["state"] == "ready"
    assert ("empty-database-migrations", ["node", "scripts/apply-migrations.mjs"]) in events


async def test_missing_empty_schema_recipe_fails_before_candidate_with_empty_state(
    tmp_path, monkeypatch
):
    result, events = await _prepare_empty(
        tmp_path,
        monkeypatch,
        {"package.json": json.dumps({"scripts": {}}), "src/lib/db/schema.ts": "export {};"},
    )

    assert result["state"] == "needs_changes"
    assert result["report"]["database_state"] == "empty"
    assert result["report"]["blockers"] == [
        "В выбранной версии нет поддерживаемого описания исторической схемы."
    ]
    assert "candidate-provisioned" not in events
