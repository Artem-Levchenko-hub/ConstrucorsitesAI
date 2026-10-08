"""Registry parsing only; executable pin/installation has separate integration tests."""

import json
from types import SimpleNamespace as NS

import pytest

from yleum_api.services import behavior_driver_configuration as config
from yleum_api.services.behavior_platform_assets import MAX_SDK_URL, PLATFORM_ASSET_LOCATIONS
from yleum_api.services.max_behavior_proof import BehaviorProofError


def _configure(monkeypatch, assets):
    monkeypatch.setattr(config, "_pin", lambda *args: None)
    return config.configured_behavior_driver(NS(), NS(
        max_behavior_browser_executable="/trusted/test-only-browser", env="test",
        max_behavior_browser_sha256="a" * 64,
        max_behavior_adapter_registry=json.dumps({
            "max-miniapp-nextjs": {"preset": "coffee-summary-header-theme-ui-v1"},
        }),
        max_behavior_vendor_asset_registry=json.dumps(assets),
    ))


def _asset(url):
    return {"url": url, "sha256": "a" * 64, "bytes": 7}


@pytest.mark.parametrize("urls", [[MAX_SDK_URL], sorted(PLATFORM_ASSET_LOCATIONS)])
def test_registry_accepts_only_operator_pinned_fixed_assets(monkeypatch, urls):
    driver = _configure(monkeypatch, [_asset(url) for url in urls])
    assert driver.supported_capabilities == frozenset({
        "header_theme_v1", "coffee_local_summary_v1",
    })


@pytest.mark.parametrize("assets", [
    [_asset("/omnia-inspector.js")] * 2,
    [_asset(url) for url in sorted(PLATFORM_ASSET_LOCATIONS)] + [_asset(MAX_SDK_URL)],
    [_asset("/omnia-inspector.js?token=private")],
    [_asset("https://product.example/omnia-inspector.js")],
    [_asset("//st.max.ru/js/max-web-app.js")],
    [_asset("/custom.js")],
    [_asset("/../omnia-inspector.js")],
    [{**_asset("/omnia-inspector.js"), "sha256": "wrong"}],
    [{**_asset("/omnia-inspector.js"), "bytes": 0}],
    [{**_asset("/omnia-inspector.js"), "bytes": True}],
    [{**_asset("/omnia-inspector.js"), "bytes": 8388609}],
])
def test_registry_rejects_duplicates_unapproved_locations_and_bad_pins(monkeypatch, assets):
    with pytest.raises(BehaviorProofError, match="BEHAVIOR_CONFIGURATION_UNAVAILABLE"):
        _configure(monkeypatch, assets)
