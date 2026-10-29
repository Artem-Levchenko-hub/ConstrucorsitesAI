"""Legacy adoption is explicit, bounded and immutable before model ownership."""

import hashlib
import importlib
import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from yleum_api.core.errors import ApiError
from yleum_api.routers.max_studio import _prepare_config_change
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.schemas.project import ProjectCreate
from yleum_api.services.generation.agent_preparation import _seed_saved_max_config
from yleum_api.services.generation.contracts import GenerationRuntime
from yleum_api.services.max_content_delivery import public_business_config
from yleum_api.services.max_project_kit import (
    MAX_SECURITY_LOCKED_FILES,
    _template_file,
    render_max_managed_files,
)

SQL_PATH = "drizzle/0090_yleum_legacy_orders_management.sql"
META_PATH = ".omnia/legacy-orders.json"
CORE_PATH = "drizzle/0003_owner_management.sql"
RECIPE_PATH = "compat/legacy-orders-owner-mirror.sql.template"
PROJECT_ID = UUID("4912b9fb-3fe0-4b10-84ef-002329c247a7")
ROUTE = """import { pool } from "@/lib/db";
const COLUMNS = "id, customer_name, items, total_rub, status, created_at";
export async function GET() {
  return pool.query(`SELECT ${COLUMNS} FROM sever_orders
    WHERE owner_id = $1 ORDER BY created_at DESC LIMIT 100`, ["actor"]);
}
export async function POST() {
  await pool.query(`SELECT ${COLUMNS} FROM sever_orders
    WHERE owner_id=$1 AND operation_key=$2`, ["actor", "operation"]);
  return pool.query(`INSERT INTO sever_orders (id, owner_id, customer_name, items, total_rub,
    status, operation_key, pickup_option, pickup_at, barista_comment)
    VALUES ($1,$2,$3,$4::jsonb,$5,'accepted',$6,$7,$8,$9)
    ON CONFLICT (owner_id, operation_key) DO NOTHING RETURNING ${COLUMNS}`, []);
}
"""


def service():
    name = "yleum_api.services.max_legacy_order_management"
    assert importlib.util.find_spec(name) is not None, "legacy adoption service is missing"
    return importlib.import_module(name)


def config(**extra):
    return MaxProjectConfigPayload.model_validate(
        {
            "app_name": "Кофейня",
            "app_type": "catalog",
            "summary": "Самовывоз",
            "management_modules": ["catalog", "orders", "leads"],
            **extra,
        }
    )


def test_detector_accepts_only_one_static_compatible_table():
    detect = service().detect_legacy_order_table
    assert detect(ROUTE) == "sever_orders"
    assert detect(ROUTE.replace("sever_orders", "orders_v2")) == "orders_v2"
    assert detect(ROUTE.replace("sever_orders", "x" * 48)) == "x" * 48


@pytest.mark.parametrize(
    "table",
    [
        "max_orders",
        "pg_orders",
        "yleum_orders",
        "Sever_orders",
        "public.orders",
        "x" * 49,
        "orders;DROP TABLE users",
        'orders"',
        "orders$1",
    ],
)
def test_detector_rejects_unsafe_or_reserved_table(table):
    assert service().detect_legacy_order_table(ROUTE.replace("sever_orders", table)) is None


@pytest.mark.parametrize(
    "source",
    [
        "// " + ROUTE.replace("\n", "\n// "),
        ROUTE.replace("INSERT INTO sever_orders", "INSERT INTO another_orders"),
        ROUTE.replace("owner_id = $1", "customer_name = $1"),
        ROUTE.replace("operation_key, pickup_option", "another_column, pickup_option"),
        ROUTE.replace("FROM sever_orders", "FROM sever_orders JOIN other ON true"),
        ROUTE.replace("FROM sever_orders", "FROM ${table}"),
        ROUTE.replace('const COLUMNS = "id,', 'const COLUMNS = sql + "id,'),
        ROUTE.replace('from "@/lib/db"', 'from "./untrusted"'),
        ROUTE.replace("export async function GET", "async function unrelated"),
        ROUTE.replace("pool.query(`INSERT", "other.query(`INSERT"),
        ROUTE + "\n pool.query(`SELECT id FROM another_orders WHERE owner_id=$1`, []);",
        ROUTE + "\n pool.query(sql, []);",
        ROUTE + "\n" + (" " * (256 * 1024)),
        ROUTE + "\n export async function broken( {",
    ],
    ids=[
        "comment",
        "mixed-table",
        "no-owner",
        "no-key",
        "join",
        "dynamic-table",
        "dynamic-columns",
        "foreign-pool",
        "no-get",
        "foreign-query",
        "extra-table",
        "dynamic-query",
        "oversize",
        "syntax-error",
    ],
)
def test_detector_rejects_incomplete_dynamic_or_ambiguous_routes(source):
    assert service().detect_legacy_order_table(source) is None


def test_render_binds_only_valid_uuid_and_identifier_to_reviewed_template():
    render = service().render_legacy_order_management
    rendered = render(PROJECT_ID, "sever_orders")
    template = _template_file(RECIPE_PATH)
    assert set(rendered) == {SQL_PATH, META_PATH}
    assert rendered[SQL_PATH] == template.replace(
        "__PROJECT_ID__",
        str(PROJECT_ID),
    ).replace("__ORDERS_TABLE__", "sever_orders")
    metadata = json.loads(rendered[META_PATH])
    assert metadata == {
        "protocol": 1,
        "project_id": str(PROJECT_ID),
        "orders_table": "sever_orders",
        "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
    }
    assert render(str(PROJECT_ID), "sever_orders") == rendered
    assert "__PROJECT_ID__" not in rendered[SQL_PATH]
    assert "__ORDERS_TABLE__" not in rendered[SQL_PATH]


@pytest.mark.parametrize(
    "project_id",
    [
        "not-a-uuid",
        PROJECT_ID.hex,
        str(PROJECT_ID).upper(),
        "{" + str(PROJECT_ID) + "}",
    ],
)
def test_render_rejects_noncanonical_project_binding(project_id):
    with pytest.raises(ValueError):
        service().render_legacy_order_management(project_id, "sever_orders")


@pytest.mark.parametrize("table", ["max_orders", "pg_orders", "yleum_orders", "bad-name", " x"])
def test_render_rejects_unsafe_table_binding(table):
    with pytest.raises(ValueError):
        service().render_legacy_order_management(PROJECT_ID, table)


def test_render_rejects_unresolved_template_parameters(monkeypatch):
    from yleum_api.services import max_project_kit

    monkeypatch.setattr(max_project_kit, "_template_file", lambda _: "__PROJECT_ID__ __UNKNOWN__")
    with pytest.raises(ValueError):
        service().render_legacy_order_management(PROJECT_ID, "sever_orders")


def test_private_binding_is_validated_and_never_reaches_public_source():
    bound = config(legacy_orders_table="sever_orders")
    assert getattr(bound, "legacy_orders_table", None) == "sever_orders"
    for value in (bound, config()):
        assert "legacy_orders_table" not in public_business_config(value)
    files = render_max_managed_files(bound, PROJECT_ID)
    assert {SQL_PATH, META_PATH} <= files.keys()
    assert {SQL_PATH, META_PATH} <= MAX_SECURITY_LOCKED_FILES
    assert "legacy_orders_table" not in files["src/lib/omnia/max-config.ts"]
    assert "sever_orders" not in files["src/lib/omnia/max-config.ts"]
    assert SQL_PATH not in render_max_managed_files(config(), PROJECT_ID)
    assert META_PATH not in render_max_managed_files(config(), PROJECT_ID)


@pytest.mark.parametrize("table", ["max_orders", "pg_orders", "yleum_orders", "bad-name", " x", 42])
def test_config_rejects_noncanonical_legacy_binding(table):
    with pytest.raises(ValidationError):
        config(legacy_orders_table=table)


def test_legacy_binding_requires_orders_and_cannot_enter_new_project_creation():
    with pytest.raises(ValidationError):
        config(legacy_orders_table="sever_orders", management_modules=["catalog", "leads"])
    with pytest.raises(ValidationError):
        ProjectCreate(name="New", max_config=config(legacy_orders_table="sever_orders"))
    assert ProjectCreate(name="New", max_config=config()).max_config.legacy_orders_table is None


def test_settings_omission_preserves_private_binding_and_old_client_modules():
    current = config(legacy_orders_table="sever_orders")
    desired = config(app_name="Новое имя")
    desired = MaxProjectConfigPayload.model_validate(
        desired.model_dump(
            exclude={"legacy_orders_table", "management_modules"},
        )
    )
    actual = _prepare_config_change(current, desired, current_version=3, expected_version=3)
    assert getattr(actual, "legacy_orders_table", None) == "sever_orders"
    assert actual.management_modules == current.management_modules
    assert actual.app_name == "Новое имя"


@pytest.mark.parametrize(
    "before,after",
    [
        ("sever_orders", "another_orders"),
        ("sever_orders", None),
        (None, "sever_orders"),
    ],
)
def test_settings_cannot_bind_remove_or_rebind_legacy_migration(before, after):
    with pytest.raises(ApiError) as error:
        _prepare_config_change(
            config(legacy_orders_table=before),
            config(legacy_orders_table=after),
            current_version=3,
            expected_version=3,
        )
    assert error.value.status_code == 409


def test_settings_cannot_disable_orders_after_adoption():
    with pytest.raises(ApiError) as error:
        _prepare_config_change(
            config(legacy_orders_table="sever_orders"),
            config(management_modules=["catalog", "leads"]),
            current_version=3,
            expected_version=3,
        )
    assert error.value.status_code == 409


def seeded_context(monkeypatch, current):
    from yleum_api.services import max_managed_generation

    bound = config(legacy_orders_table="sever_orders")
    monkeypatch.setattr(
        max_managed_generation,
        "load_saved_business_config",
        AsyncMock(
            return_value=(bound, 7),
        ),
    )
    tree = dict(current)
    events = []

    async def seed(version, public):
        events.append("public")
        assert version == 7 and "legacy_orders_table" not in public

    async def stage(files, deleted):
        events.append("source")
        assert deleted == ()
        tree.update(files)

    handle = SimpleNamespace(
        seed_business_config=seed,
        snapshot_files=AsyncMock(side_effect=lambda: dict(tree)),
        stage_patch=AsyncMock(side_effect=stage),
    )
    return bound, tree, events, GenerationRuntime(handle=handle)


async def test_saved_config_seeds_exact_trusted_migrations_before_model_baseline(monkeypatch):
    bound, tree, events, runtime = seeded_context(monkeypatch, {"src/app/page.tsx": "product"})
    ids = SimpleNamespace(project_id=PROJECT_ID, user_id=uuid4())
    for _ in range(2):
        await _seed_saved_max_config(
            None, ids, SimpleNamespace(name="Saved"), runtime, catalog_runtime=True
        )
    expected = render_max_managed_files(bound, PROJECT_ID)
    assert events[0:2] == ["public", "source"]
    assert runtime.migration_baseline == {
        path: expected[path] for path in (CORE_PATH, SQL_PATH, META_PATH)
    }
    assert tree[SQL_PATH] == expected[SQL_PATH] and tree[META_PATH] == expected[META_PATH]
    assert tree["src/app/page.tsx"] == "product"


@pytest.mark.parametrize("modules", [[], ["catalog"], None], ids=["empty", "catalog", "legacy"])
@pytest.mark.parametrize("existing_core", [True, False], ids=["applied-core", "no-core"])
async def test_empty_management_contract_preserves_applied_core_without_creating_sql(
    monkeypatch,
    modules,
    existing_core,
):
    from yleum_api.services import max_managed_generation

    current = {CORE_PATH: _template_file(CORE_PATH)} if existing_core else {}
    saved = config(management_modules=modules)
    _, tree, _, runtime = seeded_context(monkeypatch, current)
    monkeypatch.setattr(
        max_managed_generation,
        "load_saved_business_config",
        AsyncMock(
            return_value=(saved, 7),
        ),
    )
    await _seed_saved_max_config(
        None,
        SimpleNamespace(project_id=PROJECT_ID, user_id=uuid4()),
        SimpleNamespace(name="Saved"),
        runtime,
        catalog_runtime=True,
    )
    if existing_core:
        assert tree[CORE_PATH] == _template_file(CORE_PATH)
        assert runtime.migration_baseline == {CORE_PATH: current[CORE_PATH]}
        assert all(
            CORE_PATH not in call.args[0] for call in runtime.handle.stage_patch.await_args_list
        )
    else:
        assert CORE_PATH not in tree and CORE_PATH not in (runtime.migration_baseline or {})
    assert SQL_PATH not in tree and META_PATH not in tree
    if modules is None:
        assert ".omnia/management.json" not in tree
        runtime.handle.stage_patch.assert_not_called()
    else:
        contract = json.loads(tree[".omnia/management.json"])
        assert contract["resources"] == []


@pytest.mark.parametrize("modules", [[], ["catalog"], None], ids=["empty", "catalog", "legacy"])
async def test_empty_management_contract_still_rejects_altered_applied_core(monkeypatch, modules):
    from yleum_api.services import max_managed_generation

    current = {CORE_PATH: _template_file(CORE_PATH) + "-- untrusted modification"}
    _, tree, _, runtime = seeded_context(monkeypatch, current)
    monkeypatch.setattr(
        max_managed_generation,
        "load_saved_business_config",
        AsyncMock(
            return_value=(config(management_modules=modules), 7),
        ),
    )
    with pytest.raises(ApiError) as error:
        await _seed_saved_max_config(
            None,
            SimpleNamespace(project_id=PROJECT_ID, user_id=uuid4()),
            SimpleNamespace(name="Saved"),
            runtime,
            catalog_runtime=True,
        )
    assert error.value.status_code == 409
    assert tree == current and runtime.migration_baseline is None
    runtime.handle.stage_patch.assert_not_called()


@pytest.mark.parametrize("tampered_path", [CORE_PATH, SQL_PATH, META_PATH])
async def test_saved_config_rejects_mutated_seed_without_rewriting_or_trusting_it(
    monkeypatch,
    tampered_path,
):
    bound = config(legacy_orders_table="sever_orders")
    files = render_max_managed_files(bound, PROJECT_ID)
    current = {path: files[path] for path in (CORE_PATH, SQL_PATH, META_PATH)}
    current[tampered_path] += "-- tampered"
    _, tree, _, runtime = seeded_context(monkeypatch, current)
    with pytest.raises(ApiError) as error:
        await _seed_saved_max_config(
            None,
            SimpleNamespace(project_id=PROJECT_ID, user_id=uuid4()),
            SimpleNamespace(name="Saved"),
            runtime,
            catalog_runtime=True,
        )
    assert error.value.status_code == 409
    assert tree == current and runtime.migration_baseline is None
    runtime.handle.stage_patch.assert_not_called()


async def test_saved_config_rejects_valid_metadata_bound_to_another_project(monkeypatch):
    files = render_max_managed_files(config(legacy_orders_table="sever_orders"), PROJECT_ID)
    current = {path: files[path] for path in (CORE_PATH, SQL_PATH, META_PATH)}
    metadata = json.loads(current[META_PATH])
    metadata["project_id"] = str(uuid4())
    current[META_PATH] = json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n"
    _, tree, _, runtime = seeded_context(monkeypatch, current)
    with pytest.raises(ApiError) as error:
        await _seed_saved_max_config(
            None,
            SimpleNamespace(project_id=PROJECT_ID, user_id=uuid4()),
            SimpleNamespace(name="Saved"),
            runtime,
            catalog_runtime=True,
        )
    assert error.value.status_code == 409
    assert tree == current and runtime.migration_baseline is None
    runtime.handle.stage_patch.assert_not_called()


async def test_sdk_refresh_cannot_rewrite_an_applied_management_migration():
    from yleum_api.services.max_managed_generation import (
        MANAGED_GENERATION_PATHS,
        refresh_integration_sdk,
    )

    assert SQL_PATH not in MANAGED_GENERATION_PATHS and META_PATH not in MANAGED_GENERATION_PATHS
    handle = SimpleNamespace(
        snapshot_files=AsyncMock(return_value={CORE_PATH: "SELECT 'not canonical'"}),
        stage_patch=AsyncMock(),
    )
    with pytest.raises(ApiError) as error:
        await refresh_integration_sdk(handle)
    assert error.value.status_code == 409
    handle.stage_patch.assert_not_called()


def source_session(*, owner=True, snapshot=True, correct_project=True):
    from yleum_api.models.project import Project
    from yleum_api.models.snapshot import Snapshot

    owner_id = uuid4()
    selected = uuid4()
    project = SimpleNamespace(
        id=PROJECT_ID, owner_id=owner_id if owner else uuid4(), current_snapshot_id=selected
    )
    saved = (
        SimpleNamespace(
            id=selected, project_id=PROJECT_ID if correct_project else uuid4(), commit_sha="a" * 40
        )
        if snapshot
        else None
    )

    async def get(model, identity):
        if model is Project:
            assert identity == PROJECT_ID
            return project
        assert model is Snapshot and identity == selected
        return saved

    return SimpleNamespace(get=AsyncMock(side_effect=get)), owner_id


@pytest.mark.parametrize(
    "body, expected",
    [
        (ROUTE.encode(), "sever_orders"),
        (None, None),
        (b"", None),
    ],
    ids=["legacy-route", "absent", "empty"],
)
async def test_reader_uses_owned_accepted_snapshot_only(monkeypatch, body, expected):
    from yleum_api.services import repo

    session, owner_id = source_session()
    read = AsyncMock(side_effect=[body, {"drizzle/0089_existing.sql": "SELECT 1;"}])
    monkeypatch.setattr("asyncio.to_thread", read)
    assert await service().read_legacy_order_table(session, PROJECT_ID, owner_id) == expected
    read.assert_any_await(
        repo.read_file,
        PROJECT_ID,
        "a" * 40,
        "src/app/api/orders/route.ts",
    )
    if expected is not None:
        read.assert_any_await(repo.read_files, PROJECT_ID, "a" * 40)
        assert read.await_count == 2
    else:
        assert read.await_count == 1


@pytest.mark.parametrize(
    "options",
    [
        {"owner": False},
        {"snapshot": False},
        {"correct_project": False},
    ],
)
async def test_reader_rejects_cross_owner_or_invalid_snapshot_before_source_read(
    monkeypatch, options
):
    read = AsyncMock()
    monkeypatch.setattr("asyncio.to_thread", read)
    session, owner_id = source_session(**options)
    with pytest.raises(ApiError):
        await service().read_legacy_order_table(session, PROJECT_ID, owner_id)
    read.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b"x" * (256 * 1024 + 1),
        b"export async function POST() {}",
    ],
    ids=["invalid-utf8", "oversize", "unknown-custom-route"],
)
async def test_reader_rejects_unknown_or_unbounded_existing_order_source(monkeypatch, body):
    monkeypatch.setattr("asyncio.to_thread", AsyncMock(return_value=body))
    session, owner_id = source_session()
    with pytest.raises(ApiError) as error:
        await service().read_legacy_order_table(session, PROJECT_ID, owner_id)
    assert error.value.status_code == 409 and error.value.code == "unsupported"


async def test_reader_accepts_only_exact_platform_business_route_without_legacy_sql(monkeypatch):
    canonical = _template_file("src/app/api/omnia/business/[resource]/route.ts").encode()
    monkeypatch.setattr("asyncio.to_thread", AsyncMock(return_value=canonical))
    session, owner_id = source_session()
    assert await service().read_legacy_order_table(session, PROJECT_ID, owner_id) is None


@pytest.mark.parametrize(
    "failure, expected",
    [(FileNotFoundError("missing"), None), (OSError("private source location"), 503)],
)
async def test_reader_handles_missing_source_and_sanitizes_unexpected_io(
    monkeypatch, failure, expected
):
    monkeypatch.setattr("asyncio.to_thread", AsyncMock(side_effect=failure))
    session, owner_id = source_session()
    if expected is None:
        assert await service().read_legacy_order_table(session, PROJECT_ID, owner_id) is None
    else:
        with pytest.raises(ApiError) as error:
            await service().read_legacy_order_table(session, PROJECT_ID, owner_id)
        assert error.value.status_code == expected
        assert "private source location" not in error.value.message


@pytest.mark.parametrize(
    "inventory",
    [
        {"drizzle/0091_customer.sql": "CREATE TABLE later_order_data(id uuid);"},
        {SQL_PATH: "SELECT 'untrusted reservation collision';"},
        {"drizzle/9999_customer.sql": "CREATE TABLE later_order_data(id uuid);"},
        {f"src/{index}.ts": "" for index in range(5001)},
    ],
    ids=["later-migration", "reserved-collision", "legacy-high-migration", "inventory-budget"],
)
async def test_first_adoption_rejects_unsupported_accepted_migration_order(monkeypatch, inventory):
    monkeypatch.setattr("asyncio.to_thread", AsyncMock(side_effect=[ROUTE.encode(), inventory]))
    session, owner_id = source_session()
    with pytest.raises(ApiError) as error:
        await service().read_legacy_order_table(session, PROJECT_ID, owner_id)
    assert error.value.status_code == 409 and error.value.code == "unsupported"


def test_future_migration_can_append_naturally_after_adopted_orders():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    before = service().render_legacy_order_management(PROJECT_ID, "sever_orders")
    after = {**before, "drizzle/0091_product_addition.sql": "CREATE TABLE product_notes(id uuid);"}
    assert max_migration_contract_errors(before, after) == ()


def test_optional_legacy_files_never_satisfy_product_edit_guard():
    from yleum_api.services.generation.agent_finalization import (
        unchanged_candidate_before_finalization,
    )

    before = {"src/app/page.tsx": "product"}
    after = {**before, **service().render_legacy_order_management(PROJECT_ID, "sever_orders")}
    result = unchanged_candidate_before_finalization(
        baseline_files=before,
        workspace_files=after,
        requires_source_change=True,
        message="done",
        catalog_runtime=True,
    )
    assert result is not None and result.failure == "edit produced no source changes"


@pytest.mark.parametrize("prepared", [False, True], ids=["existing-app", "cold-worker"])
async def test_pipeline_binds_trusted_seed_before_entering_model(monkeypatch, prepared):
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta

    from tests.test_generation_recovery_reconciliation import bootstrap
    from yleum_api.services.generation import (
        agent_pipeline,
        recovery_execution,
        recovery_tool_receipts,
    )
    from yleum_api.services.generation.contracts import (
        AgentPromptPlan,
        GenerationIds,
        ProjectGenerationFacts,
        SourceBaseline,
        StackPrompt,
    )
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ContinuationClaim,
        decode_recovery,
    )

    bound, tree, _, runtime = seeded_context(monkeypatch, {"src/app/page.tsx": "product"})
    ids = GenerationIds(uuid4(), PROJECT_ID, uuid4(), uuid4(), uuid4())
    raw = bootstrap()
    raw["identity"].update(
        run_id=str(ids.run_id), project_id=str(ids.project_id), user_id=str(ids.user_id)
    )
    raw["continuation_claim"] = ContinuationClaim(
        run_id=ids.run_id,
        token=uuid4(),
        attempt_seq=0,
        worker_id="synthetic",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    ).model_dump(mode="json")
    ref = CheckpointRef(object_key="synthetic/context", sha256="a" * 64)
    raw["context_ref"] = ref.model_dump(mode="json")
    raw["execution"]["execution_context_ref"] = ref.model_dump(mode="json")
    state = decode_recovery(raw)
    plan = AgentPromptPlan(
        "system", "Owner edit", "synthetic", None, 5, "seed", None, "system", None, False
    )
    stack = StackPrompt("seed", None, None, None, "system", False)
    entry = {
        "version": 1,
        "classification": ["Owner edit", False, True, True],
        "design_contract": None,
        "stack": stack._asdict(),
        "prompt_plan": plan.__dict__,
        "build_plan": None,
        "seed_files": {
            SQL_PATH: "untrusted entry SQL",
            META_PATH: '{"project_id":"another-project"}',
        },
        "initial_result": None,
        "edit_deadline": None,
    }
    context = {
        "schema_version": 1,
        "entry": "pipeline",
        "pipeline": {"baseline": {"files": {}}},
        "completed": {"agent_entry": entry} if prepared else {},
        "active_frame": None,
    }
    monkeypatch.setattr(
        recovery_execution, "load_execution_context", AsyncMock(return_value=deepcopy(context))
    )

    async def save(self, execution, *, context, phase="working"):
        self.context = context
        self.state = self.state.model_copy(update={"execution": execution})

    async def commit_prefix(factory, *, previous, execution, context):
        return previous.model_copy(update={"execution": execution})

    monkeypatch.setattr(recovery_execution.ExecutionDriver, "save", save)
    monkeypatch.setattr(recovery_execution, "commit_execution_cursor", commit_prefix)
    monkeypatch.setattr(recovery_tool_receipts, "require_preparation_receipts", AsyncMock())
    runtime.handle.current_identity = AsyncMock()
    runtime.handle.refresh_snapshot_files = AsyncMock(side_effect=lambda: dict(tree))
    runtime.handle.is_portable = lambda: True
    runtime.handle.control_workspace_id = uuid4()
    runtime.handle.control_fencing_epoch = 1
    bindings = SimpleNamespace(
        execute=AsyncMock(),
        probe_runtime=AsyncMock(),
        probe_build=AsyncMock(),
        preview_url=AsyncMock(),
        shell_enabled=False,
    )

    async def prepare_stack(**kwargs):
        await _seed_saved_max_config(
            None, ids, kwargs["project_info"], runtime, catalog_runtime=True
        )
        return stack

    for name, value in {
        "classify_agent_turn": ("Owner edit", False, True, True),
        "prepare_agent_runtime": (bindings, None),
        "require_named_behavior_readiness": None,
        "prepare_agent_prompt": (plan, None),
        "stage_max_starter": ({}, None),
        "stage_owner_fonts": {},
        "_load_turn_reference_images": [],
        "adjacent_checkpoint": None,
    }.items():
        monkeypatch.setattr(agent_pipeline, name, AsyncMock(return_value=value))
    monkeypatch.setattr(agent_pipeline, "prepare_stack_prompt", prepare_stack)

    class ObservedModelBoundary(BaseException):
        pass

    async def model(**kwargs):
        expected = render_max_managed_files(bound, PROJECT_ID)
        for path in (CORE_PATH, SQL_PATH, META_PATH):
            assert runtime.migration_baseline[path] == expected[path] == tree[path]
            assert kwargs["_max_seed_files"][path] == expected[path]
        assert runtime.migration_baseline["src/app/page.tsx"] == "product"
        from yleum_api.services.max_data_evolution import max_migration_contract_errors

        assert max_migration_contract_errors(runtime.migration_baseline, tree) == ()
        raise ObservedModelBoundary()

    monkeypatch.setattr(agent_pipeline, "execute_agent_turn", model)
    with pytest.raises(ObservedModelBoundary):
        await agent_pipeline.run_agent_generation(
            _consume_free_generation=AsyncMock(),
            baseline=SourceBaseline(uuid4(), "a" * 40, {"src/app/page.tsx": "product"}),
            capacity_dispatch_token=None,
            factory=None,
            force_model=None,
            ids=ids,
            is_free=False,
            model_id="synthetic",
            orchestrate=False,
            progress=SimpleNamespace(emit_agent_event=AsyncMock()),
            project_info=ProjectGenerationFacts(
                "max_miniapp", "synthetic", "Saved", None, None, False, "ru", False, "", ""
            ),
            prompt_text="Owner edit",
            runtime=runtime,
            selected_elements=None,
            _resume_state=state,
        )
