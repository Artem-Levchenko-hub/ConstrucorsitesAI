from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yleum_api.services.generation.agent_finalization import (
    explicitly_readonly_request,
    source_change_allows_partial_save,
    unchanged_candidate_before_finalization,
    validate_edit_source_change,
)


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
async def test_noop_never_invokes_production_build_or_migrations():
    from yleum_api.services.generation.agent_finalization import finalize_max_candidate

    files = {"src/app/page.tsx": "same product"}
    finalize = AsyncMock()
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(finalize_with_repair=finalize),
        handle=SimpleNamespace(
            prove_restoration_adaptation=None, snapshot_files=AsyncMock(return_value=files),
        ),
    )
    with pytest.raises(RuntimeError, match="no source changes"):
        await finalize_max_candidate(
            _is_edit=True, _max_has_generated_snapshot=True, _max_shell_enabled=True,
            accumulated="done", baseline=SimpleNamespace(files=files), files=files,
            ids=SimpleNamespace(), is_free=False, prompt_text="Change the button",
            runtime=runtime, plan=SimpleNamespace(), operations=SimpleNamespace(),
        )
    finalize.assert_not_awaited()


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
