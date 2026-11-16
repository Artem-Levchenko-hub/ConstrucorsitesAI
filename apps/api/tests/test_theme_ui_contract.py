from yleum_api.services import max_behavior_ui_contract as ui
from yleum_api.services.behavior_driver_configuration import _adapter
from yleum_api.services.max_behavior_proof import required_contract

REQUEST = "Add a visible header Light/Dark theme control and local persistence."
COFFEE = "Добавь кнопку «Проверить заявку»: локальное резюме без отправки данных."


def test_theme_preset_and_guidance_share_every_browser_selector_and_storage_key():
    adapter = _adapter({"preset": "header-theme-ui-v1"})
    guidance = ui.named_ui_guidance(required_contract(REQUEST, template="max_miniapp"))
    assert adapter.coffee_summary is None and adapter.density is None
    for name in ("header", "light", "dark", "card", "text"):
        assert getattr(adapter.theme, name) in guidance
    assert adapter.theme.storage_key in guidance
    assert adapter.theme.light_name is None and adapter.theme.dark_name is None
    assert "exact accessible names Light and Dark" not in guidance
    assert "owner's requested accessible labels" in guidance
    assert "UNSUPPORTED" not in guidance
    for requirement in ("4.5", "44", "0.75", "0.18", "localStorage", "reload", "reversible"):
        assert requirement in guidance


def test_custom_theme_mapping_keeps_operator_selected_accessible_names():
    adapter = _adapter({
        "theme": {
            **ui.THEME_SELECTORS,
            "storage_key": ui.THEME_STORAGE_KEY,
            "light_name": "Светлая",
            "dark_name": "Тёмная",
        },
    })
    assert adapter.theme.light_name == "Светлая"
    assert adapter.theme.dark_name == "Тёмная"


def test_combined_preset_retains_coffee_contract_and_advertises_theme_and_density():
    adapter = _adapter({
        "preset": "coffee-summary-header-theme-ui-v1", "read_paths": ["/api/items"],
    })
    coffee = _adapter({"preset": "coffee-local-summary-ui-v1"})
    assert adapter.coffee_summary == coffee.coffee_summary
    assert adapter.theme is not None and adapter.density is not None
    assert adapter.read_paths == ("/api/items",)
    contract = required_contract(REQUEST + "\n" + COFFEE, template="max_miniapp")
    assert set(contract.capabilities) == {"coffee_local_summary_v1", "header_theme_v1"}
    guidance = ui.named_ui_guidance(contract)
    assert "PLATFORM COFFEE UI CONTRACT" in guidance
    assert "PLATFORM THEME UI CONTRACT" in guidance
    assert "UNSUPPORTED" not in guidance
