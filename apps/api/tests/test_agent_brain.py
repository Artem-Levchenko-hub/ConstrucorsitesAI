from __future__ import annotations

from omnia_api.services.agent_brain import (
    brain_prompt_view,
    new_brain,
    normalize_error_signature,
    record_hypothesis,
    record_mutation,
    record_observation,
    semantic_loop_count,
)


def test_error_signature_ignores_locations_ids_and_whitespace() -> None:
    first = "src/a.tsx:437:9 TS2305: Module has no exported member 'trackMaxEvent'"
    second = "src/b.tsx:99:2  TS2305: Module has no exported member 'trackMaxEvent'"

    assert normalize_error_signature(first) == normalize_error_signature(second)


def test_repeated_error_with_distinct_edits_is_semantic_loop() -> None:
    brain = new_brain("Build fitness app", ["typecheck clean"])
    signature = normalize_error_signature("TS2305 missing export trackMaxEvent")
    for index in range(3):
        brain = record_hypothesis(
            brain,
            root_cause=f"attempt {index}",
            evidence=[signature],
            experiment=f"edit {index}",
            expected_result="typecheck clean",
        )
        brain = record_observation(
            brain,
            kind="build",
            status="error",
            summary="compile failed",
            error_signature=signature,
            evidence=["typecheck red"],
        )

    assert semantic_loop_count(brain) == 3
    assert len(brain["failed_approaches"]) == 3


def test_brain_history_is_bounded_and_tracks_mutation_revisions() -> None:
    brain = new_brain("Goal", ["criterion"])
    for revision in range(30):
        brain = record_mutation(
            brain,
            paths=[f"src/component-{revision}.tsx"],
            revision=revision,
        )
        brain = record_observation(
            brain,
            kind="build",
            status="ok",
            summary=f"build {revision}",
        )

    assert len(brain["artifacts"]) == 20
    assert brain["artifacts"][-1]["revision"] == 29
    assert len(brain["observations"]) == 20


def test_prompt_view_is_bounded_and_contains_no_artifact_source() -> None:
    brain = new_brain("Goal", ["criterion"])
    brain = record_mutation(brain, paths=["src/product.tsx"], revision=1)

    view = brain_prompt_view(brain, max_chars=1200)

    assert len(view) <= 1200
    assert "PROJECT BRAIN v1" in view
    assert "NEXT REQUIRED ACTION" in view
    assert "src/product.tsx" not in view
