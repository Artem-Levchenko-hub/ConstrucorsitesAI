"""MAX projects stay MAX projects on both surgical edit paths."""

from omnia_api.services import agent_builder, prompt_builder


def test_text_edit_prompt_preserves_max_platform() -> None:
    messages = prompt_builder._build_edit_messages(
        current_files={"src/app/page.tsx": "export default function Page() {}"},
        history=[],
        user_prompt="Сделай вместо этого обычный сайт",
        selected_elements=None,
        template="max_miniapp",
    )

    system = messages[0]["content"]
    assert "MINI APP ВНУТРИ МЕССЕНДЖЕРА MAX" in system
    assert "обычный сайт" in system
    assert "Telegram/VK Mini App" in system
    assert "серверную проверку initData" in system


def test_agent_edit_prompt_includes_max_template_contract() -> None:
    guide = agent_builder.load_stack_system_prompt("max-miniapp-nextjs")
    assert guide is not None

    system = agent_builder.build_edit_system_prompt(guide)

    assert "STACK-SPECIFIC CONTRACT" in system
    assert "Use `window.WebApp` only" in system
    assert "Do not add Telegram WebApp" in system
    assert "MAX UI is optional" in system
    assert "Never add a platform-owned visual shell or persistent legal footer" in system


def test_generic_agent_edit_prompt_is_unchanged_without_stack_guide() -> None:
    assert (
        agent_builder.build_edit_system_prompt(None)
        == agent_builder.EDIT_SYSTEM_PROMPT
    )
