from __future__ import annotations

from omnia_api.services import agent_builder, agent_native
from omnia_api.services.max_agent_skills import (
    read_max_skill,
    render_selected_max_skills,
    select_max_skills,
)

CREATIVE_CAPABILITY_PACKS = {
    "premium-mobile-foundation",
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


def test_stable_max_system_does_not_force_optional_skill_catalog() -> None:
    index = agent_builder.load_stack_skill_index("max-miniapp-nextjs")

    assert index is not None
    assert "MAX capability catalog" in index
    assert "`read_skill`" in index
    assert "`ui-ux-pro-max`" in index
    assert "Micro feedback" not in index

    prompt = agent_native.native_system_prompt(
        "MAX PLATFORM CORE CONTRACT",
        index,
        stable_max_loop=True,
    )
    assert "MAX capability catalog" not in prompt
    assert "read_skill(`premium-mobile-foundation`)" not in prompt
    assert "visual-evaluation" not in prompt
    assert "read_skill(`ui-ux-pro-max`)" not in prompt
    assert "MAX PLATFORM CORE CONTRACT" in prompt


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


def test_structured_skill_router_is_minimal_stable_and_excludes_visual_loop() -> None:
    available = {
        "ui-ux-pro-max",
        "max-platform",
        "domain-fitness",
        "product-strategy",
        "production-readiness",
        "ai-native-ux",
        "trust-safety",
        "visual-evaluation",
    }
    spec = {
        "industry": "fitness",
        "capabilities": ["AI coach", "history", "payments", "medical data"],
    }

    assert select_max_skills(spec, available_skill_ids=available) == (
        "ui-ux-pro-max",
        "max-platform",
        "domain-fitness",
        "production-readiness",
        "ai-native-ux",
        "trust-safety",
    )


def test_structured_skill_router_falls_back_and_respects_available_cap() -> None:
    assert select_max_skills(
        {"product": "неопределённая услуга", "ai": False},
        available_skill_ids={"max-platform", "product-strategy", "visual-evaluation"},
    ) == ("max-platform", "product-strategy")
    assert select_max_skills(
        {"industry": "fitness", "ai": True},
        available_skill_ids={"ui-ux-pro-max", "max-platform", "domain-fitness", "ai-native-ux"},
        max_skills=3,
    ) == ("ui-ux-pro-max", "max-platform", "domain-fitness")


def test_kernel_skill_context_keeps_craft_without_design_or_proof_ceremony() -> None:
    rendered = render_selected_max_skills(
        [
            "ui-ux-pro-max",
            "product-strategy",
            "max-platform",
            "production-readiness",
            "trust-safety",
        ],
        prompt="Приложение записи на услуги",
        project_id="booking-1",
    )

    assert "Craft bar" in rendered
    assert "ProductSpec style/plan are final" in rendered
    assert "three real directions" not in rendered
    assert "three-direction exploration" not in rendered
    assert "max-design-spec.json" not in rendered
    assert "before `done`" not in rendered
    assert "call `docs`" not in rendered
    assert "Reload the app and prove" not in rendered
    assert "authenticated runtime check" not in rendered
    assert "## Safety proof" not in rendered
    assert "User-owned data starts empty" in rendered
