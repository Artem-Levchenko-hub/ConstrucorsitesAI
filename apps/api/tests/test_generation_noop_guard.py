from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yleum_api.services.generation.agent_finalization import (
    explicitly_readonly_request,
    source_change_allows_partial_save,
    unchanged_candidate_before_finalization,
    validate_edit_source_change,
)
from yleum_api.services.max_project_kit import enable_catalog_runtime_manifest


@pytest.mark.parametrize("prompt,expected", [
    ("В текущем приложении устаревшая история. Устрани вкладку История. SQL не меняй.", True),
    ("В текущем приложении убери Историю, не меняй авторизацию и каталог.", True),
    ("Сначала изучи файл, затем удали вкладку История.", True),
    ("Please inspect the page, then remove the History tab. Do not modify auth.", True),
    ("Remove History. Do not change code outside this page.", True),
    ("Удали вкладку «История», сохрани остальные функции.", True),
    ("Не удаляй Историю, исправь только подпись.", True),
    ("Ничего не меняй, только объясни как удалить Историю.", False),
    ("Объясни, как исправить историю.", False),
    ("Проверь, почему команда «удали Историю» не сработала.", False),
    ('Explain the command "remove History", do not change anything.', False),
    ("Не убирай Историю.", False),
    ("Please do not remove History.", False),
    ("Нужно проверить текст `удали Историю`.", False),
    ("продолжи", False),
    ("Продолжи исследование проблемы.", False),
    ("Проверь код и объясни причину.", False),
    ("Review the latest change and explain it.", False),
    ("Inspect the fix for the History tab.", False),
    ("Continue reviewing the latest update.", False),
    ("Review the latest change. Remove History.", True),
    ("Inspect the fix, then remove History.", True),
    ("Review the current page and remove History.", True),
    ("Can you review the latest change?", False),
    ("Could you inspect the fix for the History tab?", False),
    ("This is a read-only review of the latest change.", False),
    ("Can you remove the History tab?", True),
    ("Would you please remove the History tab?", True),
    ("Current problem: remove the History tab.", True),
    ("Could you inspect the page and remove History?", True),
])
def test_contextual_edit_intent_preserves_readonly_and_quoted_instructions(prompt, expected):
    from yleum_api.services.generation.agent_generation import requested_source_edit

    assert requested_source_edit(prompt) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog_bootstrap", [False, True])
@pytest.mark.parametrize("readonly", [False, True])
async def test_noop_never_invokes_production_build_or_migrations(catalog_bootstrap, readonly):
    from yleum_api.services.generation.agent_finalization import finalize_max_candidate

    baseline, files = _catalog_source_files() if catalog_bootstrap else (
        {"src/app/page.tsx": "same product"}, {"src/app/page.tsx": "same product"},
    )
    finalize = AsyncMock()
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(finalize_with_repair=finalize),
        handle=SimpleNamespace(
            prove_restoration_adaptation=None, snapshot_files=AsyncMock(return_value=files),
        ),
    )
    candidate = finalize_max_candidate(
        _is_edit=True, _max_has_generated_snapshot=True, _max_shell_enabled=True,
        accumulated="inspection", baseline=SimpleNamespace(files=baseline), files=files,
        ids=SimpleNamespace(), is_free=False,
        prompt_text="Explain the code" if readonly else "Change the button",
        runtime=runtime, plan=SimpleNamespace(), operations=SimpleNamespace(),
        catalog_runtime=catalog_bootstrap,
    )
    if readonly:
        result = await candidate
        assert result.files == {} and result.message == "inspection"
    else:
        with pytest.raises(RuntimeError, match="no source changes"):
            await candidate
    finalize.assert_not_awaited()
    runtime.handle.snapshot_files.assert_awaited_once()


def test_readonly_exception_does_not_swallow_a_requested_change():
    assert not explicitly_readonly_request("ничего не меняй кроме кнопки")
    assert not explicitly_readonly_request('добавь текст "ничего не меняй"')


def test_managed_sdk_refresh_does_not_count_as_requested_product_edit():
    verdict = unchanged_candidate_before_finalization(
        baseline_files={"src/app/page.tsx": "same", "src/lib/omnia/integration-client.ts": "old"},
        workspace_files={"src/app/page.tsx": "same", "src/lib/omnia/integration-client.ts": "new"},
        requires_source_change=True,
        message="done",
    )
    assert verdict is not None and verdict.failure


def _catalog_source_files(*, explicit_null=False):
    manifest_data = {
        "version": 1,
        "services": [{"name": "app", "command": "npm start", "port": 3000}],
        "routes": [{"path": "/", "service": "app"}],
    }
    if explicit_null:
        manifest_data["app_config_protocol"] = None
    manifest = json.dumps(manifest_data)
    baseline = {
        ".omnia/cell.json": manifest,
        "src/app/page.tsx": "same product",
        "src/lib/omnia/client.ts": "old managed SDK",
    }
    prepared = {
        **baseline,
        ".omnia/cell.json": enable_catalog_runtime_manifest(manifest),
        "src/lib/omnia/client.ts": "new managed SDK",
        "src/lib/omnia/catalog-server.ts": "new managed catalog SDK",
    }
    return baseline, prepared


def _source_change_verdict(boundary, baseline, prepared, *, catalog_runtime=True):
    options = {} if catalog_runtime is None else {"catalog_runtime": catalog_runtime}
    if boundary == "precheck":
        return unchanged_candidate_before_finalization(
            baseline_files=baseline, workspace_files=prepared,
            requires_source_change=True, message="done", **options,
        )
    candidate = prepared if boundary == "exact" else {
        **{path: content for path, content in prepared.items() if baseline.get(path) != content},
        **{path: "" for path in baseline.keys() - prepared.keys()},
    }
    return validate_edit_source_change(
        requires_source_change=True, baseline_files=baseline,
        candidate_files=candidate, exact_tree=boundary == "exact", message="done", **options,
    )


@pytest.mark.parametrize("boundary", ["precheck", "exact", "patch"])
@pytest.mark.parametrize("explicit_null", [False, True])
def test_catalog_bootstrap_is_not_a_requested_product_edit(boundary, explicit_null):
    baseline, prepared = _catalog_source_files(explicit_null=explicit_null)
    original_baseline, original_prepared = dict(baseline), dict(prepared)
    verdict = _source_change_verdict(boundary, baseline, prepared)
    assert verdict is not None and verdict.failure == "edit produced no source changes"
    assert baseline == original_baseline and prepared == original_prepared


@pytest.mark.parametrize("boundary", ["precheck", "exact", "patch"])
@pytest.mark.parametrize("catalog_runtime", [False, None])
def test_catalog_upgrade_remains_a_change_outside_explicit_max_scope(boundary, catalog_runtime):
    baseline, prepared = _catalog_source_files()
    verdict = _source_change_verdict(boundary, baseline, prepared, catalog_runtime=catalog_runtime)
    assert verdict is None or verdict.failure is None


@pytest.mark.parametrize("boundary", ["precheck", "exact", "patch"])
@pytest.mark.parametrize("change", ["app", "service", "route", "remove_manifest", "add_manifest"])
def test_catalog_upgrade_preserves_real_product_and_manifest_changes(boundary, change):
    baseline, prepared = _catalog_source_files()
    if change == "app":
        prepared["src/app/page.tsx"] = "requested product edit"
    elif change == "remove_manifest":
        prepared.pop(".omnia/cell.json")
    elif change == "add_manifest":
        baseline.pop(".omnia/cell.json")
    else:
        manifest = json.loads(prepared[".omnia/cell.json"])
        if change == "service":
            manifest["services"][0]["command"] = "node new-server.js"
        else:
            manifest["routes"][0]["path"] = "/catalog"
        prepared[".omnia/cell.json"] = json.dumps(manifest)
    verdict = _source_change_verdict(boundary, baseline, prepared)
    assert verdict is None or verdict.failure is None


@pytest.mark.parametrize("boundary", ["precheck", "exact", "patch"])
@pytest.mark.parametrize("side,protocol", [
    ("before", True), ("before", False), ("before", 0), ("before", 1),
    ("before", 1.0), ("before", "1"), ("after", True), ("after", False),
    ("after", 1.0), ("after", "1"), ("after", None),
])
def test_only_absent_or_null_to_integer_one_protocol_migration_is_ignored(boundary, side, protocol):
    baseline, prepared = _catalog_source_files()
    selected = baseline if side == "before" else prepared
    manifest = json.loads(selected[".omnia/cell.json"])
    manifest["app_config_protocol"] = protocol
    selected[".omnia/cell.json"] = json.dumps(manifest)
    verdict = _source_change_verdict(boundary, baseline, prepared)
    assert verdict is None or verdict.failure is None


@pytest.mark.parametrize("boundary", ["precheck", "exact", "patch"])
@pytest.mark.parametrize("before_value,after_value", [(True, 1), (3000, 3000.0)])
def test_protocol_upgrade_does_not_erase_other_manifest_field_types(
    boundary, before_value, after_value,
):
    baseline, prepared = _catalog_source_files()
    for files, value in [(baseline, before_value), (prepared, after_value)]:
        manifest = json.loads(files[".omnia/cell.json"])
        manifest["services"][0]["port"] = value
        files[".omnia/cell.json"] = json.dumps(manifest)
    verdict = _source_change_verdict(boundary, baseline, prepared)
    assert verdict is None or verdict.failure is None


@pytest.mark.parametrize("boundary", ["precheck", "exact", "patch"])
@pytest.mark.parametrize("before_content,after_content", [
    ("{", None), (None, "{"), ("[]", None),
    *[
        (json.dumps(manifest), json.dumps({**manifest, "app_config_protocol": 1}))
        for manifest in [
            {"version": True, "services": [], "routes": []},
            {"version": 1.0, "services": [], "routes": []},
            {"version": 1, "services": None, "routes": []},
            {"version": 1, "services": []},
        ]
    ],
])
def test_malformed_manifest_delta_is_never_hidden_as_catalog_preparation(
    boundary, before_content, after_content,
):
    baseline, prepared = _catalog_source_files()
    if before_content is not None:
        baseline[".omnia/cell.json"] = before_content
    if after_content is not None:
        prepared[".omnia/cell.json"] = after_content
    verdict = _source_change_verdict(boundary, baseline, prepared)
    assert verdict is None or verdict.failure is None


def test_file_deletion_is_a_real_change():
    assert (
        unchanged_candidate_before_finalization(
            baseline_files={"src/app/page.tsx": "same", "src/app/old/page.tsx": "delete"},
            workspace_files={"src/app/page.tsx": "same"},
            requires_source_change=True,
            message="done",
        )
        is None
    )


def test_unchanged_candidate_is_rejected_before_expensive_finalization():
    baseline = {"src/app/page.tsx": "existing product"}
    verdict = unchanged_candidate_before_finalization(
        baseline_files=baseline,
        workspace_files=baseline,
        requires_source_change=True,
        message="done",
    )
    assert verdict is not None and verdict.failure == "edit produced no source changes"


def test_readonly_inspection_can_end_without_build_or_new_snapshot():
    baseline = {"src/app/page.tsx": "existing product"}
    verdict = unchanged_candidate_before_finalization(
        baseline_files=baseline,
        workspace_files=baseline,
        requires_source_change=False,
        message="inspection result",
    )
    assert verdict is not None and verdict.failure is None and verdict.files == {}
    assert verdict.message == "inspection result"


def test_changed_candidate_still_requires_full_finalization():
    assert (
        unchanged_candidate_before_finalization(
            baseline_files={"a": "before"},
            workspace_files={"a": "after"},
            requires_source_change=True,
            message="done",
        )
        is None
    )


def test_identical_exact_edit_is_rejected_before_versioning() -> None:
    baseline = {"src/app/page.tsx": "page", "src/lib/schema.ts": "schema", "empty": ""}

    verdict = validate_edit_source_change(
        requires_source_change=True,
        baseline_files=baseline,
        candidate_files={
            "empty": "",
            "src/lib/schema.ts": "schema",
            "src/app/page.tsx": "page",
        },
        exact_tree=True,
        message="Готово — правка применена и проверена.",
    )

    assert verdict.files == {}
    assert verdict.failure == "edit produced no source changes"
    assert verdict.message == (
        "Не удалось применить правку: итоговый код не изменился. "
        "Повтори запрос или уточни, что именно нужно изменить."
    )


def test_nonempty_patch_that_only_rewrites_same_content_is_rejected() -> None:
    verdict = validate_edit_source_change(
        requires_source_change=True,
        baseline_files={"page.tsx": "same"},
        candidate_files={"page.tsx": "same", "already-missing.ts": ""},
        exact_tree=False,
        message="done",
    )

    assert verdict.failure == "edit produced no source changes"
    assert verdict.files == {}


@pytest.mark.parametrize(
    ("candidate", "exact_tree"),
    [
        ({}, True),
        ({"page.tsx": "changed"}, False),
        ({"page.tsx": ""}, False),
        ({"page.tsx": "same", "new.ts": "new"}, True),
        ({}, False),
    ],
)
def test_legitimate_empty_noop_or_real_source_change_is_preserved(
    candidate: dict[str, str], exact_tree: bool
) -> None:
    baseline = {"page.tsx": "same"}
    verdict = validate_edit_source_change(
        requires_source_change=True,
        baseline_files=baseline,
        candidate_files=candidate,
        exact_tree=exact_tree,
        message="original result",
    )

    if not candidate:
        assert verdict.failure is None
        assert verdict.files == {}
        assert verdict.message == "original result"
    else:
        assert verdict.failure is None
        assert verdict.files == candidate


def test_identical_tree_is_not_rejected_for_non_edit_generation() -> None:
    candidate = {"page.tsx": "same"}

    verdict = validate_edit_source_change(
        requires_source_change=False,
        baseline_files=candidate,
        candidate_files=candidate,
        exact_tree=True,
        message="build complete",
    )

    assert verdict.failure is None
    assert verdict.files == candidate
    assert verdict.message == "build complete"


def test_rejected_no_diff_edit_cannot_claim_a_partial_save() -> None:
    verdict = validate_edit_source_change(
        requires_source_change=True,
        baseline_files={"page.tsx": "same"},
        candidate_files={"page.tsx": "same"},
        exact_tree=True,
        message="done",
    )

    assert source_change_allows_partial_save(verdict) is False
