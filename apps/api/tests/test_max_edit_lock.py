"""MAX projects stay MAX projects on the agent edit path."""

from yleum_api.services import agent_builder


def test_agent_edit_prompt_includes_max_template_contract() -> None:
    guide = agent_builder.load_stack_system_prompt("max-miniapp-nextjs")
    assert guide is not None

    system = agent_builder.build_edit_system_prompt(guide)

    assert "STACK-SPECIFIC CONTRACT" in system
    assert "Use `window.WebApp` only" in system
    assert "Do not add Telegram WebApp" in system


def test_generic_agent_edit_prompt_is_unchanged_without_stack_guide() -> None:
    assert (
        agent_builder.build_edit_system_prompt(None)
        == agent_builder.EDIT_SYSTEM_PROMPT
    )
