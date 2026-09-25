from __future__ import annotations

import pytest

from yleum_api.services.generation.agent_finalization import (
    source_change_allows_partial_save,
    validate_edit_source_change,
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
