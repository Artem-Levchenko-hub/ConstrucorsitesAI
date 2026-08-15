from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

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

    async def signed_gate(url: str, *, require_persistence: bool) -> FunctionalVerdict:
        assert url.endswith("/signed")
        assert require_persistence is True
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
    assert dependency_check.detail == "OSV lockfile scan did not complete"


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
