from __future__ import annotations

from omnia_api.services.max_functional_gate import evaluate_static_observation


def test_max_static_browser_contract_passes_complete_mobile_product() -> None:
    checks = evaluate_static_observation(
        {
            "nav_count": 4,
            "primary_count": 1,
            "heading_count": 3,
            "unlabeled_controls": 0,
            "fake_controls": 0,
            "small_targets": 0,
            "horizontal_overflow": 0,
        }
    )

    assert checks
    assert all(check.ok for check in checks)


def test_max_static_browser_contract_rejects_decorative_inaccessible_shell() -> None:
    checks = evaluate_static_observation(
        {
            "nav_count": 1,
            "primary_count": 0,
            "heading_count": 0,
            "unlabeled_controls": 2,
            "fake_controls": 3,
            "small_targets": 4,
            "horizontal_overflow": 37,
        }
    )

    failures = {check.name for check in checks if not check.ok}
    assert failures == {
        "max_main_navigation",
        "max_primary_action",
        "max_mobile_layout",
        "max_accessibility",
    }
