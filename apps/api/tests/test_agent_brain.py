from __future__ import annotations

import json

from omnia_api.services.agent_brain import (
    brain_prompt_view,
    durable_brain_memory,
    error_signatures,
    new_brain,
    normalize_error_signature,
    record_acceptance_evidence,
    record_hypothesis,
    record_mutation,
    record_observation,
    restore_durable_brain,
    semantic_loop_count,
    sync_acceptance,
    upgrade_brain,
)


def test_error_signature_ignores_locations_ids_and_whitespace() -> None:
    first = "src/a.tsx:437:9 TS2305: Module has no exported member 'trackMaxEvent'"
    second = "src/b.tsx:99:2  TS2305: Module has no exported member 'trackMaxEvent'"

    assert normalize_error_signature(first) == normalize_error_signature(second)


def test_error_signature_is_stable_for_reordered_diagnostics_and_ignores_noise() -> None:
    first = """
src/a.tsx(12,4): error TS2307: Cannot find module '@/lib/session'.
Found 2 errors.
src/b.tsx(90,8): error TS2322: Type 'string' is not assignable to type 'number'.
"""
    second = """
src/other.tsx(1,1): error TS2322: Type 'string' is not assignable to type 'number'.
src/moved.tsx(44,2): error TS2307: Cannot find module '@/lib/session'.
Found 2 errors in 2 files.
"""

    assert len(error_signatures(first)) == 2
    assert normalize_error_signature(first) == normalize_error_signature(second)


def test_semantic_loop_counts_repeated_runtime_errors_across_clean_builds() -> None:
    brain = new_brain("Goal", ["runtime clean"])
    for _ in range(3):
        brain = record_observation(
            brain,
            kind="runtime_check",
            status="error",
            summary="render failed",
            error_signature="runtime-signature",
        )
        brain = record_observation(
            brain,
            kind="build",
            status="ok",
            summary="typecheck clean",
        )

    assert semantic_loop_count(brain) == 3


def test_terminal_brain_memory_preserves_anti_loop_state_without_secret_or_path() -> None:
    brain = new_brain("Private tenant request", ["ship"])
    brain = record_hypothesis(
        brain,
        root_cause="src/private/customer.ts uses sk-abcdefghijklmnop",
        evidence=["src/private/customer.ts:9"],
        experiment="replace private import",
        expected_result="runtime green",
    )
    brain = record_observation(
        brain,
        kind="runtime_check",
        status="error",
        summary="src/private/customer.ts failed",
        error_signature="stable-runtime-signature",
    )

    memory = durable_brain_memory(brain)
    restored = restore_durable_brain(
        memory,
        objective="Retry objective",
        acceptance=["runtime green"],
    )
    serialized = json.dumps(memory)

    assert "Private tenant request" not in serialized
    assert "src/private/customer.ts" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized
    assert "stable-runtime-signature" in serialized
    assert restored["objective"] == "Retry objective"
    assert restored["failed_approaches"] == memory["failed_approaches"]


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


def test_semantic_loop_tracks_persistent_diagnostic_inside_changing_sets() -> None:
    brain = new_brain("Build app", ["typecheck clean"])
    for index, changing in enumerate(("error-b", "error-c", "error-d")):
        brain = record_hypothesis(
            brain,
            root_cause=f"attempt {index}",
            evidence=["error-a"],
            experiment=f"edit {index}",
            expected_result="typecheck clean",
        )
        brain = record_observation(
            brain,
            kind="build",
            status="error",
            summary="compile failed",
            error_signature=f"aggregate-{index}",
            diagnostic_signatures=["error-a", changing],
        )

    assert semantic_loop_count(brain) == 3


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


def test_acceptance_closes_only_with_evidence_and_reopens_after_mutation() -> None:
    brain = new_brain("Goal", ["typecheck clean", "runtime verified"])

    brain = record_acceptance_evidence(
        brain,
        proof_ids=["build:run-1", "runtime:run-1"],
        passed=True,
    )

    assert {item["status"] for item in brain["acceptance"]} == {"done"}
    assert brain["acceptance_evidence"] == ["build:run-1", "runtime:run-1"]

    brain = record_mutation(brain, paths=["src/product.tsx"], revision=2)

    assert {item["status"] for item in brain["acceptance"]} == {"open"}
    assert brain["acceptance_evidence"] == []


def test_plan_acceptance_sync_reopens_legacy_done_without_proof() -> None:
    legacy = {
        **new_brain("Goal", ["typecheck clean"]),
        "version": 1,
    }
    legacy["acceptance"] = [{"criterion": "typecheck clean", "status": "done"}]

    upgraded = upgrade_brain(legacy)
    synced = sync_acceptance(upgraded, ["typecheck clean", "runtime verified"])

    assert synced["version"] == 2
    assert synced["acceptance"] == [
        {"criterion": "typecheck clean", "status": "open", "evidence": []},
        {"criterion": "runtime verified", "status": "open", "evidence": []},
    ]


def test_prompt_view_is_bounded_and_contains_no_artifact_source() -> None:
    brain = new_brain("Goal", ["criterion"])
    brain = record_mutation(brain, paths=["src/product.tsx"], revision=1)

    view = brain_prompt_view(brain, max_chars=1200)

    assert len(view) <= 1200
    assert "PROJECT BRAIN v2" in view
    assert "NEXT REQUIRED ACTION" in view
    assert "src/product.tsx" not in view
