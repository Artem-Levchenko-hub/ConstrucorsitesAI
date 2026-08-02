from __future__ import annotations

from omnia_api.services import agent_builder, agent_native
from omnia_api.services.max_agent_skills import read_max_skill

CREATIVE_CAPABILITY_PACKS = {
    "product-flow",
    "art-direction",
    "interaction-motion",
    "domain-fitness",
    "domain-restaurant",
    "domain-booking",
    "domain-education",
    "domain-commerce",
    "trust-safety",
    "growth-analytics",
    "visual-evaluation",
    "production-readiness",
}


def test_max_system_gets_only_compact_capability_catalog() -> None:
    index = agent_builder.load_stack_skill_index("max-miniapp-nextjs")

    assert index is not None
    assert "MAX capability catalog" in index
    assert "`read_skill`" in index
    assert "`ui-ux-pro-max`" in index
    assert "Micro feedback" not in index

    prompt = agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT", index)
    assert "MAX capability catalog" in prompt
    assert "`product-flow`" in prompt
    assert "read_skill(`ui-ux-pro-max`)" in prompt
    assert "read_skill(`production-readiness`)" in prompt
    assert "read_skill(`visual-evaluation`)" in prompt


def test_creative_capability_architecture_is_routable_without_templates() -> None:
    index = agent_builder.load_stack_skill_index("max-miniapp-nextjs")

    assert index is not None
    for skill_id in CREATIVE_CAPABILITY_PACKS:
        assert f"`{skill_id}`" in index
        loaded = agent_builder.load_stack_skill("max-miniapp-nextjs", skill_id)
        assert loaded is not None
        _, body = loaded
        assert "template" not in body.lower() or "not a template" in body.lower()

    assert "Lifecycle core" in index
    assert "Trigger only when" in index


def test_stack_skill_loader_is_slug_allowlisted() -> None:
    loaded = agent_builder.load_stack_skill("max-miniapp-nextjs", "mobile-motion")

    assert loaded is not None
    path, body = loaded
    assert path == ".omnia/skills/mobile-motion.md"
    assert "spatial continuity" in body
    assert agent_builder.load_stack_skill("max-miniapp-nextjs", "../SYSTEM_PROMPT") is None
    assert agent_builder.load_stack_skill("max-miniapp-nextjs", "INDEX") is None


def test_read_skill_returns_structured_recovery_for_unknown_pack() -> None:
    result = read_max_skill(
        "not-installed",
        prompt="собери фитнес приложение",
        project_id="project-1",
    )

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["next_actions"]
    assert "use one exact catalog slug" in str(result["error"])


def test_ui_ux_pro_skill_is_project_matched_evidence_not_preset() -> None:
    result = read_max_skill(
        "ui-ux-pro-max",
        prompt="Фитнес тренер со статистикой, трендами и аналитикой тренировок",
        project_id="fitness-42",
    )

    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["artifacts"] == [".omnia/skills/ui-ux-pro-max.md"]
    content = str(result["content"])
    assert "PLUGIN EVIDENCE: ui-ux-pro-max" in content
    assert "not a visual prescription" in content
    assert "Data-viz candidates" in content
    assert "High-severity UX evidence" in content
    assert "creative virtuoso lens" in content
    assert "Build a delight map" in content
    assert "three-direction exploration" in content
    assert "LANDING PATTERN" not in content
    assert "Phosphor" not in content  # not installed in the pinned MAX starter


def test_read_skill_returns_specialist_principles_without_layout_recipe() -> None:
    result = read_max_skill(
        "ai-native-ux",
        prompt="ИИ-тренер в MAX",
        project_id="ai-coach",
    )

    assert result["ok"] is True
    content = str(result["content"])
    assert "requestOmniaAI" in content
    assert "AI can be an analyser" in content
    assert "hero + features" not in content
