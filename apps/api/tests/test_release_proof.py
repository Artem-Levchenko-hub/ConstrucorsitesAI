from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.core.config import get_settings
from omnia_api.services import release_proof
from omnia_api.services.functional_gate import Check, FunctionalVerdict


async def test_release_proof_combines_build_runtime_and_transport_checks(
    monkeypatch,
) -> None:
    project_id = uuid4()

    async def build(_project_id, _slug):
        return {"ok": True, "detail": "typecheck clean"}

    async def runtime(_project_id, *, slug, path):
        assert slug == "max-app"
        assert path == "/"
        return {"ok": True, "status_code": 200}

    async def status(_project_id):
        return {"state": "running", "dev_url": "https://max-app-dev.example.test"}

    async def security(base_url):
        assert base_url == "https://max-app-dev.example.test"
        return FunctionalVerdict(
            passed=True,
            checks=[Check("nosniff", True, "present")],
            summary="passed",
        )

    settings = get_settings().model_copy(update={"use_security_gate": True})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(release_proof.orchestrator_client, "agent_build", build)
    monkeypatch.setattr(release_proof.orchestrator_client, "runtime_status", runtime)
    monkeypatch.setattr(release_proof.orchestrator_client, "get_status", status)
    monkeypatch.setattr(
        "omnia_api.services.security_gate.run_security_gate",
        security,
    )

    verdict = await release_proof.run_release_proof(project_id, "max-app")

    assert verdict.passed
    assert [(check.name, check.ok) for check in verdict.checks] == [
        ("typecheck", True),
        ("runtime", True),
        ("nosniff", True),
    ]


async def test_max_release_proof_requires_hydrated_product(monkeypatch) -> None:
    project_id = uuid4()

    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "agent_build",
        AsyncMock(return_value={"ok": True, "detail": "clean"}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    failed = Check("max_hydration", False, "ProductApp did not mount")

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_hydrated_product=True,
        hydrated_product_check=failed,
    )

    assert not verdict.passed
    assert verdict.checks[-1] == failed


async def test_build_infrastructure_failure_is_retryable_not_product_red(monkeypatch) -> None:
    project_id = uuid4()
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "agent_build",
        AsyncMock(
            return_value={
                "ok": False,
                "error": "container command timed out",
                "infra_dead": True,
            }
        ),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(side_effect=TimeoutError("runtime unavailable")),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(project_id, "max-app")

    typecheck = next(check for check in verdict.checks if check.name == "typecheck")
    assert typecheck.detail.startswith("infrastructure unavailable: ")
    assert release_proof.release_proof_infrastructure_unavailable(verdict)


async def test_hydration_browser_outage_is_infra_but_rendered_gap_is_product_red(
    monkeypatch,
) -> None:
    from omnia_api.services.max_hydration_gate import MaxHydrationReport

    project_id = uuid4()
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    audit = AsyncMock(
        return_value=MaxHydrationReport(False, False, "browser proof failed: TimeoutError")
    )
    monkeypatch.setattr("omnia_api.services.max_hydration_gate.audit_url", audit)

    unavailable = await release_proof.run_max_hydration_check(project_id)
    assert unavailable.detail.startswith("infrastructure unavailable: ")

    audit.return_value = MaxHydrationReport(
        False,
        True,
        "generated ProductApp did not mount after hydration",
    )
    product_red = await release_proof.run_max_hydration_check(project_id)
    assert not product_red.ok
    assert not product_red.detail.startswith("infrastructure unavailable: ")


async def test_transport_security_execution_outage_is_infrastructure(monkeypatch) -> None:
    from omnia_api.services.security_gate import SecCheck, SecurityVerdict

    project_id = uuid4()
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "agent_build",
        AsyncMock(return_value={"ok": True, "detail": "clean"}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": True})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "omnia_api.services.security_gate.run_security_gate",
        AsyncMock(
            return_value=SecurityVerdict(
                passed=False,
                checks=[SecCheck("security gate executed", False, "TimeoutError")],
                summary="security gate FAILED: security gate executed",
                executed=False,
            )
        ),
    )

    verdict = await release_proof.run_release_proof(project_id, "max-app")

    transport = next(check for check in verdict.checks if check.name == "transport_security")
    assert transport.detail.startswith("infrastructure unavailable: ")
    assert release_proof.release_proof_infrastructure_unavailable(verdict)


async def test_max_release_proof_includes_signed_functional_gate(monkeypatch) -> None:
    project_id = uuid4()
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "agent_build",
        AsyncMock(return_value={"ok": True, "detail": "clean"}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "create_max_preview_session",
        AsyncMock(return_value={"bootstrap_url": "https://max-dev.example.test/signed"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    async def signed_gate(
        url: str,
        *,
        require_persistence: bool,
        planned_flow=None,
    ) -> FunctionalVerdict:
        assert url.endswith("/signed")
        assert require_persistence is True
        assert planned_flow is None
        return FunctionalVerdict(
            passed=False,
            checks=[Check("max_reload_persistence", False, "reload read missing")],
            summary="failed",
        )

    monkeypatch.setattr(
        "omnia_api.services.max_functional_gate.run_max_functional_gate",
        signed_gate,
    )
    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_max_functional=True,
        max_require_persistence=True,
    )

    assert not verdict.passed
    assert verdict.checks[-1].name == "max_reload_persistence"


async def test_dependency_security_is_a_fail_closed_canary_release_check(monkeypatch) -> None:
    project_id = uuid4()
    build = AsyncMock(
        return_value={
            "ok": True,
            "detail": "clean",
            "security_scan_completed": True,
            "security_findings": [
                {
                    "source": "osv-scanner",
                    "severity": "error",
                    "message": "OSV-TEST vulnerable-package",
                }
            ],
        }
    )
    monkeypatch.setattr(release_proof.orchestrator_client, "agent_build", build)
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_dependency_security=True,
    )

    build.assert_awaited_once_with(
        project_id,
        "max-app",
        code_intelligence=True,
        security_scan=True,
    )
    dependency_check = next(
        check for check in verdict.checks if check.name == "dependency_security"
    )
    assert dependency_check.ok is False
    assert verdict.passed is False


async def test_dependency_security_fails_closed_when_scan_did_not_complete(
    monkeypatch,
) -> None:
    project_id = uuid4()
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "agent_build",
        AsyncMock(return_value={"ok": True, "detail": "clean"}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_dependency_security=True,
    )

    dependency_check = next(
        check for check in verdict.checks if check.name == "dependency_security"
    )
    assert dependency_check.ok is False
    assert dependency_check.detail == (
        "infrastructure unavailable: OSV lockfile scan did not complete"
    )
    assert release_proof.release_proof_infrastructure_unavailable(verdict)


async def test_dependency_security_rejects_any_nonempty_dedicated_finding(
    monkeypatch,
) -> None:
    project_id = uuid4()
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "agent_build",
        AsyncMock(
            return_value={
                "ok": True,
                "detail": "clean",
                "security_scan_completed": True,
                "security_findings": [
                    {"source": "unexpected", "severity": "warning", "message": "finding"}
                ],
            }
        ),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(
        project_id, "max-app", require_dependency_security=True
    )

    dependency_check = next(
        check for check in verdict.checks if check.name == "dependency_security"
    )
    assert dependency_check.ok is False
    assert not release_proof.release_proof_infrastructure_unavailable(verdict)


def test_release_proof_cache_requires_exact_version_digests_and_green_shape() -> None:
    verdict = FunctionalVerdict(
        passed=True,
        checks=[Check("typecheck", True, "clean"), Check("runtime", True, "HTTP 200")],
        summary="passed",
    )
    source_digest = "a" * 64
    contract_digest = "b" * 64
    payload = release_proof.serialize_release_proof(
        verdict,
        source_digest=source_digest,
        contract_digest=contract_digest,
    )

    restored = release_proof.restore_release_proof(
        payload,
        source_digest=source_digest,
        contract_digest=contract_digest,
    )

    assert restored is not None
    assert restored.passed
    assert payload["version"] == 3
    legacy_payload = dict(payload)
    del legacy_payload["contract_digest"]
    assert (
        release_proof.restore_release_proof(
            legacy_payload,
            source_digest=source_digest,
            contract_digest=contract_digest,
        )
        is None
    )
    assert (
        release_proof.restore_release_proof(
            payload,
            source_digest="c" * 64,
            contract_digest=contract_digest,
        )
        is None
    )
    assert (
        release_proof.restore_release_proof(
            payload,
            source_digest=source_digest,
            contract_digest="c" * 64,
        )
        is None
    )
    payload["version"] = 0
    assert (
        release_proof.restore_release_proof(
            payload,
            source_digest=source_digest,
            contract_digest=contract_digest,
        )
        is None
    )
    payload["version"] = 3
    payload["checks"] = [{"name": "runtime", "ok": False, "detail": "red"}]
    assert (
        release_proof.restore_release_proof(
            payload,
            source_digest=source_digest,
            contract_digest=contract_digest,
        )
        is None
    )
    payload["checks"] = [{"name": "runtime", "ok": True, "detail": "green"}] * 41
    assert (
        release_proof.restore_release_proof(
            payload,
            source_digest=source_digest,
            contract_digest=contract_digest,
        )
        is None
    )


def test_release_proof_cache_refuses_red_or_unbounded_serialization() -> None:
    source_digest = "a" * 64
    contract_digest = "b" * 64
    red = FunctionalVerdict(
        passed=False,
        checks=[Check("typecheck", False, "red")],
        summary="failed",
    )
    oversized = FunctionalVerdict(
        passed=True,
        checks=[Check("typecheck", True, "x" * 241)],
        summary="passed",
    )

    with pytest.raises(ValueError, match="all-green"):
        release_proof.serialize_release_proof(
            red,
            source_digest=source_digest,
            contract_digest=contract_digest,
        )
    with pytest.raises(ValueError, match="bounded all-green checks"):
        release_proof.serialize_release_proof(
            oversized,
            source_digest=source_digest,
            contract_digest=contract_digest,
        )


def test_objective_failure_dominates_mixed_infrastructure_failure() -> None:
    verdict = FunctionalVerdict(
        passed=False,
        checks=[
            Check("typecheck", False, "TS2322 product error"),
            Check("runtime", False, "infrastructure unavailable: TimeoutError"),
        ],
        summary="failed",
    )

    assert not release_proof.release_proof_infrastructure_unavailable(verdict)


def test_owner_dependency_is_terminal_only_when_every_failure_needs_owner() -> None:
    owner_only = FunctionalVerdict(
        passed=False,
        checks=[Check("provider", False, "owner dependency: integration_required")],
        summary="failed",
    )
    mixed = FunctionalVerdict(
        passed=False,
        checks=[
            *owner_only.checks,
            Check("typecheck", False, "TS2322 product error"),
        ],
        summary="failed",
    )

    assert release_proof.release_proof_owner_dependency(owner_only)
    assert not release_proof.release_proof_owner_dependency(mixed)


async def test_kernel_release_proof_disables_dependency_doctor(monkeypatch) -> None:
    project_id = uuid4()
    build = AsyncMock(return_value={"ok": True, "detail": "clean"})
    monkeypatch.setattr(release_proof.orchestrator_client, "agent_build", build)
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "runtime_status",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        release_proof.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"dev_url": "https://max-dev.example.test"}),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        dependency_doctor=False,
    )

    assert verdict.passed
    build.assert_awaited_once_with(project_id, "max-app", dependency_doctor=False)
