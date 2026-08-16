from __future__ import annotations

from typing import TypedDict

import playwright.async_api as playwright_api
from pytest import MonkeyPatch

from omnia_api.services.max_functional_gate import (
    MaxCapabilityEvidence,
    MaxPlannedFlow,
    MaxPlannedFlowEvidence,
    _proof_headers,
    _proof_idempotency_key,
    _request_action_type,
    _response_is_owner_dependency,
    _response_is_proof_infrastructure,
    evaluate_planned_flow_evidence,
    evaluate_static_observation,
    run_max_functional_gate,
)


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


def _planned_flow(
    *,
    persisted: bool = False,
    action: str = "Create booking",
    primary_action_kind: str | None = None,
    primary_integration_operation: str | None = None,
) -> MaxPlannedFlow:
    return MaxPlannedFlow(
        screen_ids=("/view-1", "/view-2", "/view-3"),
        capability_ids=("primary_action",),
        capability_actions=(("primary_action", action),),
        primary_action_id="primary_action",
        source_digest="a" * 64,
        primary_action_kind=(
            primary_action_kind or ("managed_write" if persisted else "local_navigation")
        ),
        primary_integration_operation=primary_integration_operation,
        persistence_action_id="primary_action" if persisted else None,
    )


class _ScreenTrace(TypedDict):
    visible_screen_states: tuple[tuple[str, ...], ...]
    navigated_screen_ids: tuple[str, ...]
    meaningful_screen_ids: tuple[str, ...]


def _screen_trace(*screens: str) -> _ScreenTrace:
    states = tuple((screen,) for screen in screens)
    return {
        "visible_screen_states": states,
        "navigated_screen_ids": tuple(screens),
        "meaningful_screen_ids": tuple(screens),
    }


def _result_action(**kwargs: object) -> MaxCapabilityEvidence:
    raw_ids = kwargs.get("managed_write_ids", ("record-1",))
    record_ids = raw_ids if isinstance(raw_ids, tuple) else ()
    record_id = str(record_ids[0]) if record_ids else ""
    values: dict[str, object] = {
        "marker_count": 1,
        "clicked": True,
        "observable_change": True,
        "semantic_result": True,
        "control_ready": True,
        "action_label_match": True,
        "accessible_name_match": True,
        "primary_marker": True,
        "causal_managed_write_statuses": (201,),
        "managed_write_ids": record_ids,
        "scoped_result_semantics": (f"Create booking {record_id}",),
    }
    values.update(kwargs)
    return MaxCapabilityEvidence(**values)  # type: ignore[arg-type]


class _Request:
    def __init__(self, post_data: str) -> None:
        self.post_data = post_data


def test_managed_write_requires_exact_json_action_type() -> None:
    assert _request_action_type(_Request('{"actionType":"primary_action"}')) == "primary_action"
    assert _request_action_type(_Request('{"action_type":"primary_action"}')) is None
    assert _request_action_type(_Request("not-json")) is None


def test_functional_proof_key_is_stable_bounded_and_capability_scoped() -> None:
    flow = _planned_flow(persisted=True)
    first = _proof_idempotency_key(flow, "primary_action")

    assert first == _proof_idempotency_key(flow, "primary_action")
    assert len(first) == 64
    assert first.isascii() and all(char in "0123456789abcdef" for char in first)
    assert first != _proof_idempotency_key(flow, "other_action")
    assert first != _proof_idempotency_key(
        MaxPlannedFlow(
            screen_ids=flow.screen_ids,
            capability_ids=flow.capability_ids,
            capability_actions=flow.capability_actions,
            primary_action_id=flow.primary_action_id,
            source_digest="b" * 64,
            persistence_action_id=flow.persistence_action_id,
        ),
        "primary_action",
    )
    assert first != _proof_idempotency_key(
        MaxPlannedFlow(
            screen_ids=flow.screen_ids,
            capability_ids=flow.capability_ids,
            capability_actions=(("primary_action", "Cancel booking"),),
            primary_action_id=flow.primary_action_id,
            source_digest=flow.source_digest,
            persistence_action_id=flow.persistence_action_id,
        ),
        "primary_action",
    )


def test_functional_proof_headers_include_unforgeable_revision_authorization(
    monkeypatch: MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from pydantic import SecretStr

    from omnia_api.services import max_proof_authorization as proof_auth

    monkeypatch.setattr(
        proof_auth,
        "get_settings",
        lambda: SimpleNamespace(jwt_secret=SecretStr("test-proof-secret-at-least-32-bytes")),
    )
    headers = _proof_headers(
        _planned_flow(),
        "00000000-0000-0000-0000-000000000001",
        "primary_action",
    )

    assert len(headers["X-Omnia-Proof-Key"]) == 64
    assert headers["X-Omnia-Proof-Authorization"].startswith("v1.")
    assert headers["X-Omnia-Proof-Authorization"] != headers["X-Omnia-Proof-Key"]


def test_managed_proof_infrastructure_marker_is_typed() -> None:
    class Response:
        def __init__(self) -> None:
            self.headers = {"x-omnia-proof-infrastructure": "unavailable"}

    assert _response_is_proof_infrastructure(Response())


def test_managed_proof_owner_dependency_marker_is_typed() -> None:
    class Response:
        def __init__(self) -> None:
            self.headers = {"x-omnia-proof-owner-dependency": "required"}

    assert _response_is_owner_dependency(Response())


class _BrokenChromium:
    async def launch(self, **_: object) -> None:
        raise OSError("browser binary unavailable")


class _BrokenPlaywright:
    chromium = _BrokenChromium()

    async def __aenter__(self) -> _BrokenPlaywright:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_browser_launch_failure_is_retryable_infrastructure(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(playwright_api, "async_playwright", lambda: _BrokenPlaywright())

    verdict = await run_max_functional_gate(
        "https://preview.example.test/bootstrap",
        project_id="00000000-0000-0000-0000-000000000001",
        require_persistence=False,
    )

    failed = next(check for check in verdict.checks if check.name == "max_signed_session")
    assert not failed.ok
    assert failed.detail.startswith("infrastructure unavailable: OSError")


def test_planned_flow_passes_only_reached_screens_and_exercised_action() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    causal_managed_write_statuses=(), managed_write_ids=()
                )
            },
            primary_screen_transition=True,
        ),
        require_persistence=False,
    )

    assert checks
    assert all(check.ok for check in checks)


def test_planned_flow_rejects_dead_unreachable_screen_marker() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            # /view-3 may exist in source/DOM, but the browser never saw it as
            # the sole active planned screen after traversing the navigation.
            **_screen_trace("/view-1", "/view-2", "/view-2"),
            capabilities={
                "primary_action": _result_action(
                    causal_managed_write_statuses=(), managed_write_ids=()
                )
            },
            primary_screen_transition=True,
        ),
        require_persistence=False,
    )

    failures = {check.name: check.detail for check in checks if not check.ok}
    assert set(failures) == {"max_planned_screens"}
    assert "missing=/view-3" in failures["max_planned_screens"]


def test_planned_flow_rejects_visible_but_inert_capability_marker() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": MaxCapabilityEvidence(
                    marker_count=1,
                    clicked=True,
                    observable_change=False,
                    primary_marker=True,
                )
            },
        ),
        require_persistence=False,
    )

    failures = {check.name for check in checks if not check.ok}
    assert failures == {"max_planned_capabilities", "max_primary_action_interaction"}


def test_planned_flow_rejects_hash_only_or_control_only_action_change() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                # Changing location.hash or aria state only on this control is
                # not a visible result outside the activated control.
                "primary_action": MaxCapabilityEvidence(
                    marker_count=1,
                    clicked=True,
                    observable_change=True,
                    primary_marker=True,
                )
            },
        ),
        require_persistence=False,
    )

    assert {check.name for check in checks if not check.ok} == {
        "max_planned_capabilities",
        "max_primary_action_interaction",
    }


def test_planned_flow_rejects_generic_status_change_without_scoped_action_contract() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": MaxCapabilityEvidence(
                    marker_count=1,
                    clicked=True,
                    observable_change=True,
                    semantic_result=True,
                    action_label_match=False,
                    primary_marker=True,
                )
            },
        ),
        require_persistence=False,
    )

    assert {check.name for check in checks if not check.ok} == {
        "max_planned_capabilities",
        "max_primary_action_interaction",
    }


def test_planned_flow_accepts_exact_primary_screen_transition() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": MaxCapabilityEvidence(
                    marker_count=1,
                    clicked=True,
                    observable_change=True,
                    control_ready=True,
                    action_label_match=True,
                    accessible_name_match=True,
                    primary_marker=True,
                )
            },
            primary_screen_transition=True,
        ),
        require_persistence=False,
    )

    assert all(check.ok for check in checks)


def test_planned_flow_accepts_causal_read_only_integration_result() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(
            action="Browse catalog",
            primary_action_kind="catalog_read",
            primary_integration_operation="catalog",
        ),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    causal_managed_write_statuses=(),
                    causal_integration_statuses=(200,),
                    causal_integration_operations=("catalog",),
                    causal_integration_values=("Espresso",),
                    managed_write_ids=(),
                    scoped_result_semantics=("Browse catalog Espresso",),
                )
            },
        ),
        require_persistence=False,
    )

    assert all(check.ok for check in checks)


def test_planned_flow_rejects_catalog_result_for_mutating_primary_action() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(
            persisted=True,
            action="Create booking",
            primary_action_kind="managed_write",
        ),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    causal_managed_write_statuses=(),
                    causal_integration_statuses=(200,),
                    causal_integration_operations=("catalog",),
                    causal_integration_values=("Espresso",),
                    managed_write_ids=(),
                    scoped_result_semantics=("Create booking Espresso",),
                )
            },
        ),
        require_persistence=True,
    )

    assert any(check.name == "max_primary_action_interaction" and not check.ok for check in checks)


def test_planned_flow_rejects_managed_result_without_server_id_in_scoped_ui() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(persisted=True),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    persisted_marker=True,
                    scoped_result_semantics=("Create booking completed",),
                )
            },
            reload_read_statuses=(200,),
            reloaded_action_ids=("record-1",),
            persistence_ui_restored=True,
        ),
        require_persistence=True,
    )

    assert {check.name for check in checks if not check.ok} == {
        "max_primary_action_interaction"
    }


def test_planned_flow_rejects_unique_local_toggles_without_causal_managed_actions() -> None:
    flow = MaxPlannedFlow(
        screen_ids=("/view-1", "/view-2"),
        capability_ids=("primary_action", "feature_1"),
        capability_actions=(
            ("primary_action", "Create booking"),
            ("feature_1", "Add to favorites"),
        ),
        primary_action_id="primary_action",
        source_digest="a" * 64,
        primary_action_kind="local_navigation",
    )
    local_toggle = MaxCapabilityEvidence(
        marker_count=1,
        clicked=True,
        observable_change=True,
        semantic_result=True,
        control_ready=True,
        action_label_match=True,
        accessible_name_match=True,
        primary_marker=True,
    )
    checks = evaluate_planned_flow_evidence(
        flow,
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2"),
            capabilities={"primary_action": local_toggle, "feature_1": local_toggle},
        ),
        require_persistence=False,
    )

    assert {check.name for check in checks if not check.ok} == {
        "max_primary_action_interaction"
    }


def test_planned_flow_rejects_screen_marker_without_exact_navigation_or_content_floor() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            visible_screen_states=(("/view-1",), ("/view-2",), ("/view-3",)),
            # A source-visible /view-3 hook can be an empty 1px element. It
            # never becomes a navigation target with meaningful visible content.
            navigated_screen_ids=("/view-1", "/view-2"),
            meaningful_screen_ids=("/view-1", "/view-2"),
            capabilities={
                "primary_action": _result_action(
                    causal_managed_write_statuses=(), managed_write_ids=()
                )
            },
            primary_screen_transition=True,
        ),
        require_persistence=False,
    )

    failure = next(check for check in checks if check.name == "max_planned_screens")
    assert not failure.ok
    assert "missing=/view-3" in failure.detail


def test_planned_flow_rejects_unrelated_primary_marker() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={"primary_action": _result_action(primary_marker=False)},
        ),
        require_persistence=False,
    )

    failures = {check.name for check in checks if not check.ok}
    assert failures == {"max_primary_action_interaction"}


def test_planned_flow_rejects_one_unexercised_planned_capability() -> None:
    flow = MaxPlannedFlow(
        screen_ids=("/view-1", "/view-2"),
        capability_ids=("primary_action", "export_report"),
        capability_actions=(
            ("primary_action", "Create booking"),
            ("export_report", "Export report"),
        ),
        primary_action_id="primary_action",
        source_digest="a" * 64,
        primary_action_kind="local_navigation",
    )
    checks = evaluate_planned_flow_evidence(
        flow,
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2"),
            capabilities={
                "primary_action": _result_action(
                    causal_managed_write_statuses=(), managed_write_ids=()
                ),
                "export_report": MaxCapabilityEvidence(marker_count=1),
            },
            primary_screen_transition=True,
        ),
        require_persistence=False,
    )

    failures = {check.name: check.detail for check in checks if not check.ok}
    assert set(failures) == {"max_planned_capabilities"}
    assert failures["max_planned_capabilities"].endswith("=export_report")


def test_planned_flow_passes_restored_managed_persistence() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(persisted=True),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    persisted_marker=True,
                    managed_write_statuses=(201,),
                    causal_managed_write_statuses=(201,),
                    managed_write_ids=("written-action",),
                )
            },
            reload_read_statuses=(200,),
            reloaded_action_ids=("written-action",),
            persistence_ui_restored=True,
        ),
        require_persistence=True,
    )

    assert all(check.ok for check in checks)


def test_planned_flow_rejects_write_not_restored_after_reload() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(persisted=True),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    persisted_marker=True,
                    managed_write_statuses=(201,),
                    causal_managed_write_statuses=(201,),
                    managed_write_ids=("written-action",),
                )
            },
            reload_read_statuses=(200,),
            reloaded_action_ids=("different-action",),
        ),
        require_persistence=True,
    )

    failures = {check.name: check.detail for check in checks if not check.ok}
    assert set(failures) == {"max_reload_persistence"}
    assert "restored_written_id=False" in failures["max_reload_persistence"]


def test_planned_flow_rejects_background_persistence_post_for_other_capability() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(persisted=True),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    persisted_marker=True,
                    # POST 201 occurred during click, but request actionType
                    # belonged to background work, not primary_action.
                    managed_write_statuses=(201,),
                    causal_managed_write_statuses=(),
                    managed_write_ids=(),
                )
            },
            reload_read_statuses=(200,),
            persistence_ui_restored=True,
        ),
        require_persistence=True,
    )

    failure = next(check for check in checks if check.name == "max_reload_persistence")
    assert not failure.ok
    assert "writes=[]" in failure.detail


def test_planned_flow_rejects_pre_write_read_as_reload_evidence() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(persisted=True),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    persisted_marker=True,
                    managed_write_statuses=(201,),
                    causal_managed_write_statuses=(201,),
                    managed_write_ids=("written-action",),
                )
            },
            # A GET before POST is intentionally absent here: the browser contract
            # only emits reads collected after the explicit reload boundary.
            reload_read_statuses=(),
            reloaded_action_ids=("written-action",),
            persistence_ui_restored=True,
        ),
        require_persistence=True,
    )

    failure = next(check for check in checks if check.name == "max_reload_persistence")
    assert not failure.ok
    assert "reload_reads=none" in failure.detail


def test_planned_flow_rejects_backend_restore_without_restored_ui_state() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(persisted=True),
        MaxPlannedFlowEvidence(
            **_screen_trace("/view-1", "/view-2", "/view-3"),
            capabilities={
                "primary_action": _result_action(
                    persisted_marker=True,
                    managed_write_statuses=(201,),
                    causal_managed_write_statuses=(201,),
                    managed_write_ids=("written-action",),
                )
            },
            reload_read_statuses=(200,),
            reloaded_action_ids=("written-action",),
            persistence_ui_restored=False,
        ),
        require_persistence=True,
    )

    failure = next(check for check in checks if check.name == "max_reload_persistence")
    assert not failure.ok
    assert "restored_ui_state=False" in failure.detail


def test_planned_flow_rejects_missing_persistence_action_contract() -> None:
    checks = evaluate_planned_flow_evidence(
        _planned_flow(primary_action_kind="managed_write"),
        MaxPlannedFlowEvidence(),
        require_persistence=True,
    )

    assert len(checks) == 1
    assert checks[0].name == "max_planned_flow_contract"
    assert not checks[0].ok
    assert checks[0].detail == "persistence_action_id is required"
