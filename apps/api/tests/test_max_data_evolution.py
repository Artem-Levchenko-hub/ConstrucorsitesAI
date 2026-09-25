"""Policy delivery at prompt boundaries; not proof of generated SQL safety."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services import (
    agent_builder,
    agent_native,
    autoheal,
    max_generation_contract,
)
from yleum_api.services.generation import (
    agent_verification,
)
from yleum_api.services.generation import (
    runtime as generation_runtime,
)

POLICY_HEADER = "MAX DATA EVOLUTION POLICY v1"


def test_max_migration_contract_accepts_only_canonical_forward_migration():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    before = {
        "src/lib/db/schema.ts": "export const tasks = oldTasks;",
        "scripts/apply-migrations.mjs": "// platform-owned",
    }
    after = {
        **before,
        "src/lib/db/schema.ts": "export const tasks = newTasks;",
        "drizzle/0004_add_private_note.sql": "ALTER TABLE tasks ADD COLUMN private_note text;",
    }

    assert max_migration_contract_errors(before, after) == ()


@pytest.mark.parametrize(
    ("sql", "keyword"),
    [
        ("DELETE FROM tasks WHERE archived = true;", "DELETE"),
        ("TRUNCATE TABLE tasks;", "TRUNCATE"),
        ("ALTER TABLE tasks DROP COLUMN legacy_value;", "DROP"),
        ("DO $$ BEGIN EXECUTE 'DROP TABLE tasks'; END $$;", "DO"),
        ("DO $$ BEGIN EXECUTE format('DROP TABLE %I', 'tasks'); END $$;", "DO"),
        ("DO $$ BEGIN EXECUTE E'DROP TABLE tasks'; END $$;", "DO"),
        ("DO $$ BEGIN EXECUTE 'DR' || 'OP TABLE tasks'; END $$;", "DO"),
        ("SET standard_conforming_strings = on; DELETE FROM tasks;", "DELETE"),
        ("SELECT E'\\''; DROP TABLE tasks; --';", "DROP"),
        ("-- harmless comment\rDROP TABLE tasks;", "DROP"),
        ("CALL purge_tasks();", "CALL"),
        (
            "SET standard_conforming_strings = off; SELECT '\\''; DROP TABLE tasks;",
            "STANDARD_CONFORMING_STRINGS",
        ),
        ("SELECT E'x'\n'\\''; DROP TABLE tasks; --';", "STRING_ESCAPE"),
    ],
)
def test_max_migration_contract_rejects_destructive_sql_before_execution(sql, keyword):
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/0004.sql"

    assert max_migration_contract_errors({}, {path: sql}) == (
        f"{path} contains unsafe SQL keyword {keyword}; "
        "destructive or procedural migrations require isolated database-copy verification",
    )


def test_max_migration_contract_ignores_destructive_words_in_comments_and_literals():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/0004.sql"
    sql = "-- never DROP storage\nINSERT INTO audit(message) VALUES ('DELETE is disabled');"

    assert max_migration_contract_errors({}, {path: sql}) == ()


def test_max_migration_contract_allows_foreign_key_delete_action():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/0004.sql"
    sql = (
        "ALTER TABLE task_events ADD CONSTRAINT task_events_task_fk "
        "FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;"
    )

    assert max_migration_contract_errors({}, {path: sql}) == ()


def test_max_migration_contract_allows_dollar_quoted_data_literal():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/0004.sql"
    sql = "INSERT INTO audit(message) VALUES ($$DROP is disabled$$);"

    assert max_migration_contract_errors({}, {path: sql}) == ()


def test_max_migration_contract_allows_upsert_do_nothing():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/0004.sql"
    sql = "INSERT INTO tasks(id) VALUES (1) ON CONFLICT (id) DO NOTHING;"

    assert max_migration_contract_errors({}, {path: sql}) == ()


@pytest.mark.parametrize(
    "path",
    [
        "migrations/0001_add_private_note.sql",
        "src/lib/db/migrations/0001_add_private_note.sql",
        "scripts/migrate.mjs",
        "scripts/run-migrations.ts",
    ],
)
def test_max_migration_contract_rejects_alternative_migration_paths(path):
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    before = {"scripts/apply-migrations.mjs": "// platform-owned"}
    after = {**before, path: "SELECT 1;"}

    errors = max_migration_contract_errors(before, after)

    assert len(errors) == 1
    assert path in errors[0]
    assert "drizzle/*.sql" in errors[0]


def test_max_migration_contract_rejects_schema_change_without_new_canonical_migration():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    before = {
        "src/lib/db/schema.ts": "export const tasks = oldTasks;",
        "drizzle/0003_existing.sql": "CREATE TABLE tasks(id uuid);",
        "scripts/apply-migrations.mjs": "// platform-owned",
    }
    after = {**before, "src/lib/db/schema.ts": "export const tasks = newTasks;"}

    assert max_migration_contract_errors(before, after) == (
        "src/lib/db/schema.ts changed without a new canonical drizzle/*.sql migration",
    )


def test_max_migration_contract_rejects_changes_to_platform_runner():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    before = {"scripts/apply-migrations.mjs": "// platform-owned"}
    after = {"scripts/apply-migrations.mjs": "// model-owned replacement"}

    assert max_migration_contract_errors(before, after) == (
        "scripts/apply-migrations.mjs is platform-owned and must not be changed",
    )


@pytest.mark.parametrize("replacement", ["ALTER TABLE tasks ADD COLUMN x text;", None])
def test_max_migration_contract_rejects_rewrite_or_delete_of_existing_migration(replacement):
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/0003_existing.sql"
    before = {path: "CREATE TABLE tasks(id uuid);"}
    after = {} if replacement is None else {path: replacement}

    assert max_migration_contract_errors(before, after) == (
        f"{path} is an existing canonical migration and must remain immutable",
    )


def test_max_migration_contract_rejects_nested_sql_ignored_by_platform_runner():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    path = "drizzle/generated/0004.sql"

    assert max_migration_contract_errors({}, {path: "SELECT 1;"}) == (
        f"{path} is not canonical; use direct drizzle/*.sql files",
    )


def test_max_migration_contract_allows_removing_legacy_nested_sql_during_conversion():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    runner = "scripts/apply-migrations.mjs"
    legacy = "drizzle/generated/0004.sql"
    before = {runner: "// platform-owned", legacy: "SELECT 4;"}
    after = {
        runner: "// platform-owned",
        "drizzle/0004.sql": "SELECT 4;",
    }

    assert max_migration_contract_errors(before, after) == ()


def test_max_migration_contract_rejects_modifying_legacy_nested_sql():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    runner = "scripts/apply-migrations.mjs"
    path = "drizzle/generated/0004.sql"

    assert max_migration_contract_errors(
        {runner: "// platform-owned", path: "SELECT 4;"},
        {runner: "// platform-owned", path: "SELECT 5;"},
    ) == (f"{path} is not canonical; use direct drizzle/*.sql files",)


def test_max_migration_contract_rejects_new_package_script_using_custom_runner():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    before = {"package.json": '{"scripts":{"db:push":"drizzle-kit push"}}'}
    after = {
        "package.json": (
            '{"scripts":{"db:push":"drizzle-kit push","db:migrate":"node scripts/migrate.mjs"}}'
        )
    }

    assert max_migration_contract_errors(before, after) == (
        "package.json introduces a custom migration command; use drizzle/*.sql",
    )


@pytest.mark.parametrize(
    "legacy_path",
    [
        "migrations/0001.sql",
        "src/lib/db/migrations/0001.sql",
        "scripts/migrate.mjs",
    ],
)
def test_max_migration_contract_allows_controlled_legacy_removal(legacy_path):
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    runner = "scripts/apply-migrations.mjs"
    before = {runner: "// platform-owned", legacy_path: "legacy"}
    after = {runner: "// platform-owned"}

    assert max_migration_contract_errors(before, after) == ()


def test_max_migration_contract_rejects_non_append_canonical_migration():
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    runner = "scripts/apply-migrations.mjs"
    before = {
        runner: "// platform-owned",
        "drizzle/0003_existing.sql": "SELECT 3;",
    }
    after = {
        **before,
        "drizzle/0002_late.sql": "SELECT 2;",
    }

    assert max_migration_contract_errors(before, after) == (
        "drizzle/0002_late.sql must append after existing canonical migration "
        "drizzle/0003_existing.sql",
    )


@pytest.mark.parametrize(
    "after",
    [
        {
            "scripts/apply-migrations.mjs": "// platform-owned",
            "./drizzle/0004.sql": "SELECT 1;",
            "drizzle/0004.sql": "SELECT 2;",
        },
        {
            "scripts/apply-migrations.mjs": "// platform-owned",
            "src/../drizzle/0004.sql": "SELECT 1;",
        },
    ],
)
def test_max_migration_contract_rejects_alias_collision_and_traversal(after):
    from yleum_api.services.max_data_evolution import max_migration_contract_errors

    errors = max_migration_contract_errors(
        {"scripts/apply-migrations.mjs": "// platform-owned"},
        after,
    )

    assert errors
    assert any("unsafe path" in error or "aliases" in error for error in errors)


@pytest.mark.asyncio
async def test_agent_verification_aborts_schema_only_max_candidate_before_backend_guard(
    monkeypatch,
):
    baseline_files = {
        "scripts/apply-migrations.mjs": "// platform-owned",
        "src/lib/db/schema.ts": "export const tasks = oldTasks;",
    }
    files = {"src/lib/db/schema.ts": "export const tasks = newTasks;"}
    settings = SimpleNamespace(
        agent_gate_max_attempts=0,
        use_agent_gate_feedback=False,
        use_native_agent=False,
        use_sast_gate=False,
    )
    monkeypatch.setattr(agent_verification, "get_settings", lambda: settings)
    monkeypatch.setattr(
        max_generation_contract,
        "unsafe_max_backend_paths",
        lambda _files: pytest.fail("backend guard must run after migration contract"),
    )
    rollback = AsyncMock()
    monkeypatch.setattr(generation_runtime, "_apply_project_cell_preview_files", rollback)

    with pytest.raises(ApiError, match="migration contract") as raised:
        await agent_verification.check_backend_and_normalize_css(
            _active_max_locked_files=frozenset(),
            _agent_res=SimpleNamespace(steps=1),
            _is_edit=True,
            _max_seed_files={},
            baseline=SimpleNamespace(files=baseline_files),
            files=files,
            ids=SimpleNamespace(project_id=uuid4()),
            project_info=SimpleNamespace(template="max_miniapp", slug="fixture"),
            runtime=SimpleNamespace(handle=None),
            plan=SimpleNamespace(),
            operations=SimpleNamespace(),
        )

    assert raised.value.code == "unsafe_generated_backend"
    assert files == {"src/lib/db/schema.ts": baseline_files["src/lib/db/schema.ts"]}
    rollback.assert_awaited_once()


@pytest.mark.parametrize("stale", [{}, {"database_admin": "protected", "secure_data_crud": True}])
def test_project_database_guide_grants_ordinary_development_admin_access(stale):
    from yleum_api.services.portable_cell_contract import machine_stack_guide

    guide = machine_stack_guide(
        "legacy",
        {"portable_machine": True, **stale},
        {".omnia/cell.json": "{}"},
    )
    assert "development admin access" in guide
    assert "Manage your own schema, migrations" in guide
    assert "omnia-db" not in guide
    assert "data-contract.json" not in guide
    assert "secureCollection" not in guide
    assert "MAX DATA EVOLUTION POLICY" in guide


def test_shared_evolution_guidance_has_no_protected_controller_command():
    from yleum_api.services.max_data_evolution import MAX_DATA_EVOLUTION_POLICY

    assert "omnia-db" not in MAX_DATA_EVOLUTION_POLICY
    assert "protected database" not in MAX_DATA_EVOLUTION_POLICY
    assert "service startup" in MAX_DATA_EVOLUTION_POLICY


@pytest.mark.parametrize("mode", ["build", "edit", "native"])
@pytest.mark.parametrize("provider", ["legacy", "cell-legacy", "portable", "missing-manifest"])
async def test_agent_policy_survives_provider_replacement_and_all_prompt_protocols(mode, provider):
    from yleum_api.services.max_data_evolution import build_max_agent_guide

    legacy = "MAX PLATFORM CORE CONTRACT\nLEGACY-ONLY-INSTRUCTIONS"
    snapshot = AsyncMock(
        return_value=({} if provider == "missing-manifest" else {".omnia/cell.json": "{}"})
    )
    executor = (
        None
        if provider == "legacy"
        else SimpleNamespace(
            capabilities={"portable_machine": provider != "cell-legacy"},
            snapshot_files=snapshot,
        )
    )
    guide = await build_max_agent_guide(legacy, executor)
    builders = {
        "build": agent_builder.build_system_prompt,
        "edit": agent_builder.build_edit_system_prompt,
        "native": agent_native.native_system_prompt,
    }
    prompt = builders[mode](guide)
    assert prompt.count(POLICY_HEADER) == 1
    assert ("LEGACY-ONLY-INSTRUCTIONS" in prompt) == (provider != "portable")
    assert ("EXTENSIBLE MAIN STACK" in prompt) == (provider == "portable")
    assert snapshot.await_count == (provider != "legacy")
    if mode == "native":
        assert "MAX VERIFICATION OVERRIDE" in prompt


@pytest.mark.parametrize("template", ["max_miniapp", "fullstack"])
async def test_autoheal_sends_project_policy_to_actual_agent_boundary(monkeypatch, template):
    monkeypatch.setattr(
        autoheal,
        "get_settings",
        lambda: SimpleNamespace(
            use_autoheal_on_open=True,
            autoheal_debounce_seconds=300,
        ),
    )
    monkeypatch.setattr(
        autoheal,
        "get_redis",
        lambda: SimpleNamespace(
            set=AsyncMock(return_value=True),
        ),
    )
    monkeypatch.setattr(
        autoheal.orchestrator_client,
        "compile_status",
        AsyncMock(
            side_effect=[{"ok": False, "error": "type mismatch"}, {"ok": True}],
        ),
    )
    captured = []

    async def run(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(files={"src/app/page.tsx": "fixed"})

    monkeypatch.setattr(autoheal.agent_builder, "run_agent_build", run)
    result = await autoheal.maybe_autoheal_on_open(uuid4(), "fixture", template=template)
    assert result == {"healed": True, "files": 1}
    assert len(captured) == 1
    assert captured[0]["system_prompt"].count(POLICY_HEADER) == (template == "max_miniapp")
    if template == "fullstack":
        assert captured[0]["system_prompt"] == agent_builder.EDIT_SYSTEM_PROMPT
    else:
        assert "Use `window.WebApp` only" in captured[0]["system_prompt"]


async def test_disabled_autoheal_does_not_load_guide_or_call_model(monkeypatch):
    monkeypatch.setattr(
        autoheal,
        "get_settings",
        lambda: SimpleNamespace(
            use_autoheal_on_open=False,
        ),
    )
    run = AsyncMock(side_effect=AssertionError("disabled autoheal must not spend tokens"))
    monkeypatch.setattr(autoheal.agent_builder, "run_agent_build", run)
    result = await autoheal.maybe_autoheal_on_open(uuid4(), "fixture", template="max_miniapp")
    assert result == {"healed": False, "reason": "disabled"}
    run.assert_not_called()
