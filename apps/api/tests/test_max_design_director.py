from __future__ import annotations

import json

from omnia_api.services import agent_native
from omnia_api.services.max_design_director import (
    compile_max_design_dna,
    completion_gap,
    evidence_verdict,
    merge_into_discovery,
    read_from_discovery,
)


def test_director_compiles_three_distinct_concepts_capabilities_and_domain_skills() -> None:
    dna = compile_max_design_dna(
        "Тёмный фитнес MAX: ИИ-тренер, статистика, история и оплата ЮKassa",
        project_id="fitness-42",
    )

    assert len(dna.concepts) == 3
    assert len({item.composition for item in dna.concepts}) == 3
    assert len({item.typography for item in dna.concepts}) == 3
    assert dna.chosen_id in {item.id for item in dna.concepts}
    assert dna.appearance == "dark"
    assert "premium-mobile-foundation" in dna.skill_slices
    assert "domain-fitness" in dna.skill_slices
    assert {"managed_ai", "payments", "analytics", "persisted_actions"} <= {
        item.id for item in dna.capabilities
    }
    block = dna.prompt_block()
    assert "DESIGN DIRECTOR DECISION" in block
    assert ".omnia/max-design-spec.json" in block
    assert "never simulate success" in block


def test_director_round_trips_in_discovery_and_validates_exact_art_direction() -> None:
    dna = compile_max_design_dna(
        "Сервис записи и лидов с историей",
        project_id="booking-7",
    )
    discovery = merge_into_discovery({"business": "studio"}, dna)

    restored = read_from_discovery(discovery)
    assert restored == dna

    source = "\n".join(
        f'<button data-omnia-capability="{item.id}">action</button>'
        for item in dna.capabilities
        if item.ui_marker_required
    )
    files = {
        ".omnia/max-design-spec.json": json.dumps(dna.design_spec(), ensure_ascii=False),
        "src/components/product/ProductApp.tsx": source,
    }
    assert completion_gap(dna, files) is None
    verdict = evidence_verdict(dna, files)
    assert verdict.passed
    assert "selected=" in verdict.checks[0].detail

    drifted = dict(files)
    bad = dna.design_spec()
    bad["chosen_id"] = "generic-dashboard"
    drifted[".omnia/max-design-spec.json"] = json.dumps(bad)
    assert "chosen_id" in str(completion_gap(dna, drifted))


def test_ordinary_native_generator_prompt_is_unchanged_by_max_identity() -> None:
    ordinary = agent_native.native_system_prompt("WEB STACK", None, stable_max_loop=False)
    max_prompt = agent_native.native_system_prompt("MAX STACK", None, stable_max_loop=True)

    assert "OMNIA MAX APP ENGINEER" not in ordinary
    assert "OMNIA MAX APP ENGINEER" in max_prompt
