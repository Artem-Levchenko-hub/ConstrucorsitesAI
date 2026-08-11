from __future__ import annotations

from omnia_api.services.max_hydration_gate import evaluate_observation


def test_hydrated_max_product_passes_without_design_scoring() -> None:
    report = evaluate_observation(
        {
            "runtime_present": True,
            "product_present": True,
            "product_visible": True,
            "text_count": 4,
        }
    )

    assert report.passed
    assert report.rendered


def test_blank_runtime_fails_even_when_http_page_rendered() -> None:
    report = evaluate_observation(
        {"runtime_present": True, "product_present": False, "text_count": 0}
    )

    assert not report.passed
    assert report.rendered
    assert "did not mount" in report.detail


def test_browser_abstention_is_not_accepted_as_proof() -> None:
    report = evaluate_observation({}, rendered=False)

    assert not report.passed
    assert not report.rendered


def test_hidden_product_fails_even_with_text_nodes() -> None:
    report = evaluate_observation(
        {
            "runtime_present": True,
            "product_present": True,
            "product_visible": False,
            "text_count": 8,
        }
    )

    assert not report.passed
    assert "not visible" in report.detail


def test_legacy_product_without_legal_marker_passes_via_runtime_wrapper() -> None:
    report = evaluate_observation(
        {
            "runtime_present": True,
            "product_present": True,
            "product_visible": True,
            "text_count": 6,
        }
    )

    assert report.passed


def test_null_product_inside_runtime_wrapper_still_fails() -> None:
    report = evaluate_observation(
        {
            "runtime_present": True,
            "product_present": True,
            "product_visible": True,
            "text_count": 0,
        }
    )

    assert not report.passed
    assert "only 0 visible" in report.detail
