from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from omnia_api.core.config import get_settings
from omnia_api.services import release_proof
from omnia_api.services.functional_gate import Check, FunctionalVerdict
from omnia_api.services.max_runtime_probe import MaxRuntimeProbe
from omnia_api.services.orchestrator_client import ProjectCellPreviewSession


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


async def test_release_proof_blocks_max_when_protected_data_probe_fails(
    monkeypatch,
) -> None:
    project_id = uuid4()

    async def build(_project_id, _slug):
        return {"ok": True, "detail": "typecheck clean"}

    async def runtime(_project_id, *, slug, path):
        return {"ok": True, "status_code": 200}

    async def status(_project_id):
        return {"state": "running", "dev_url": "https://max-app-dev.example.test"}

    async def max_probe(_project_id, _slug, *, base_url):
        assert base_url == "https://max-app-dev.example.test"
        return MaxRuntimeProbe(False, "protected MAX data read failed (HTTP 401)")

    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(release_proof.orchestrator_client, "agent_build", build)
    monkeypatch.setattr(release_proof.orchestrator_client, "runtime_status", runtime)
    monkeypatch.setattr(release_proof.orchestrator_client, "get_status", status)
    monkeypatch.setattr(
        "omnia_api.services.max_runtime_probe.probe_max_runtime",
        max_probe,
    )

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_max_data=True,
    )

    assert verdict.passed is False
    assert [(check.name, check.ok) for check in verdict.checks] == [
        ("typecheck", True),
        ("runtime", True),
        ("max_data_plane", False),
    ]


async def test_release_proof_uses_only_selected_project_cell_runtime(monkeypatch) -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    calls: list[str] = []
    preview = ProjectCellPreviewSession(
        workspace_id=workspace_id,
        preview_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
        ),
        bootstrap_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru/"
            "api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
        ),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )

    async def execute(action):
        calls.append(action.name)
        if action.name == "build":
            return {"ok": True, "detail": "cell typecheck clean"}
        assert action.name == "runtime_check"
        return {"ok": True, "detail": "cell runtime HTTP ok"}

    async def create_preview_session():
        calls.append("preview_session")
        return preview

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy orchestrator path must not be used")

    async def cell_probe(session, *, path):
        assert session is preview
        assert path == "/"
        calls.append("max_cell_probe")
        return MaxRuntimeProbe(True, "cell protected data verified")

    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(release_proof.orchestrator_client, "agent_build", forbidden)
    monkeypatch.setattr(release_proof.orchestrator_client, "runtime_status", forbidden)
    monkeypatch.setattr(release_proof.orchestrator_client, "get_status", forbidden)
    monkeypatch.setattr(
        "omnia_api.services.max_runtime_probe.probe_max_cell_runtime", cell_probe
    )

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_max_data=True,
        project_cell_handle=SimpleNamespace(
            execute=execute,
            create_preview_session=create_preview_session,
        ),
    )

    assert verdict.passed
    assert calls == ["build", "runtime_check", "preview_session", "max_cell_probe"]


async def test_release_proof_preserves_cell_runtime_failure_detail(monkeypatch) -> None:
    project_id = uuid4()

    async def execute(action):
        if action.name == "build":
            return {"ok": True, "detail": "cell typecheck clean"}
        return {"ok": False, "detail": "cell runtime returned HTTP 503"}

    async def create_preview_session():
        raise AssertionError("optional preview must not be created")

    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        project_cell_handle=SimpleNamespace(
            execute=execute,
            create_preview_session=create_preview_session,
        ),
    )

    assert verdict.passed is False
    assert verdict.checks[1] == Check(
        "runtime", False, "cell runtime returned HTTP 503"
    )
