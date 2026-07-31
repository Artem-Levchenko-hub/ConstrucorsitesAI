from __future__ import annotations

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
