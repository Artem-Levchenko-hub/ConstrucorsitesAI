from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnia_api.services.agent_brain import (
    new_brain,
    normalize_error_signature,
    record_hypothesis,
    record_mutation,
    record_observation,
    semantic_loop_count,
)

FIXTURE = Path(__file__).parent / "fixtures/max_agent_kernel_v2/semantic-loop.json"


@dataclass(frozen=True)
class ReplayVerdict:
    stop_reason: str | None
    processed_mutations: int
    processed_builds: int
    provider_calls: int = 0


def replay_trace(events: list[dict[str, Any]]) -> ReplayVerdict:
    """Replay sanitized evidence through the pure Brain, never through a provider."""

    brain = new_brain("replay", ["build is green"])
    mutations = 0
    builds = 0
    for event in events:
        kind = event.get("kind")
        if kind == "diagnose":
            brain = record_hypothesis(
                brain,
                root_cause=str(event.get("root_cause") or ""),
                evidence=(),
                experiment=str(event.get("experiment") or ""),
                expected_result=str(event.get("expected_result") or ""),
            )
        elif kind == "mutation":
            mutations += 1
            brain = record_mutation(
                brain,
                paths=[str(path) for path in event.get("paths") or []],
                revision=int(event.get("revision") or 0),
            )
        elif kind == "build":
            builds += 1
            summary = str(event.get("summary") or "")
            status = str(event.get("status") or "")
            brain = record_observation(
                brain,
                kind="build",
                status=status,
                summary=summary,
                error_signature=(
                    normalize_error_signature(summary) if status == "error" else ""
                ),
            )
            if semantic_loop_count(brain) >= 3:
                return ReplayVerdict("semantic_loop_red", mutations, builds)

    return ReplayVerdict(None, mutations, builds)


def test_recorded_churn_stops_before_fourth_same_error_experiment() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    verdict = replay_trace(fixture["events"])

    assert verdict.stop_reason == "semantic_loop_red"
    assert verdict.processed_mutations < fixture["recorded_mutation_count"]
    assert verdict.processed_mutations == 3
    assert verdict.processed_builds == 3
    assert verdict.provider_calls == 0
