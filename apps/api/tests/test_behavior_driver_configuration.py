import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from yleum_api.services.behavior_driver_configuration import configured_behavior_driver
from yleum_api.services.max_behavior_proof import BehaviorProofError


def settings(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_bytes(b"synthetic executable pin, never launched")
    exe.chmod(0o700)
    return NS(
        max_behavior_browser_executable=str(exe),
        max_behavior_browser_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
        max_behavior_adapter_registry=json.dumps(
            {
                "max-miniapp-nextjs": {
                    "coffee_summary": {
                        "button": "#check",
                        "form": "#form",
                        "text_input": "#name",
                        "summary": "#summary",
                    }
                }
            }
        ),
        max_behavior_vendor_asset_registry="",
    )


def test_empty_configuration_leaves_honest_missing_driver(tmp_path):
    config = settings(tmp_path)
    config.max_behavior_browser_executable = ""
    assert configured_behavior_driver(NS(), config) is None


def test_exact_pinned_operator_configuration_installs_registered_driver(tmp_path):
    driver = configured_behavior_driver(NS(), settings(tmp_path))
    assert driver.supported_capabilities == frozenset({"coffee_local_summary_v1"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_behavior_browser_sha256", "0" * 64),
        ("max_behavior_adapter_registry", '{"max-miniapp-nextjs":{"model_callback":"eval"}}'),
        ("max_behavior_adapter_registry", '{"unknown":{"coffee_summary":{}}}'),
        (
            "max_behavior_vendor_asset_registry",
            '[{"url":"https://provider.example/write","sha256":"' + "a" * 64 + '","bytes":10}]',
        ),
        (
            "max_behavior_vendor_asset_registry",
            '[{"url":"https://st.max.ru/js/max-web-app.js?token=private","sha256":"'
            + "a" * 64
            + '","bytes":10}]',
        ),
        ("max_behavior_adapter_registry", '{"max-miniapp-nextjs":{"read_paths":["/api/*"]}}'),
    ],
)
def test_bad_operator_configuration_fails_closed_without_raw_values(tmp_path, field, value):
    config = settings(tmp_path)
    setattr(config, field, value)
    with pytest.raises(BehaviorProofError, match="BEHAVIOR_CONFIGURATION_UNAVAILABLE") as info:
        configured_behavior_driver(NS(), config)
    assert value not in str(info.value)


def test_symlink_executable_pin_rejected(tmp_path):
    config = settings(tmp_path)
    original = config.max_behavior_browser_executable
    link = tmp_path / "link"
    link.symlink_to(original)
    config.max_behavior_browser_executable = str(link)
    with pytest.raises(BehaviorProofError):
        configured_behavior_driver(NS(), config)


def test_exact_official_sdk_pin_is_registered_without_fetch_or_stub(tmp_path):
    config = settings(tmp_path)
    config.max_behavior_vendor_asset_registry = json.dumps(
        [{"url": "https://st.max.ru/js/max-web-app.js", "sha256": "a" * 64, "bytes": 1234}]
    )
    assert configured_behavior_driver(NS(), config) is not None


async def test_executable_changed_after_configuration_is_rejected_before_transport(tmp_path):
    from .test_behavior_compilation_resolver import setup

    request, handle, _, _, _ = setup()
    config = settings(tmp_path)
    driver = configured_behavior_driver(handle, config)
    import asyncio
    from pathlib import Path

    await asyncio.to_thread(
        Path(config.max_behavior_browser_executable).write_bytes, b"changed executable"
    )
    with pytest.raises(BehaviorProofError, match="BEHAVIOR_CONFIGURATION_UNAVAILABLE"):
        await driver.collect_compiled(request)
    handle.operation_status.assert_not_awaited()
