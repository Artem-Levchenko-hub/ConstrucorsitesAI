from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from omnia_api.services import orchestrator_client
from omnia_api.services.orchestrator_client import (
    ControlProjectCellResourcesRequest,
    EnsureProjectCellResourcesRequest,
    HttpProjectCellOrchestratorClient,
    ObserveProjectCellResourcesRequest,
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellAgentExecResponse,
    ProjectCellAgentWorkspaceSnapshot,
    ProjectCellAgentWriteResponse,
    ProjectCellDraftApplyResponse,
    ProjectCellPreEffectRejection,
    ProjectCellPreviewSession,
    ProjectCellResourceResponse,
)


async def test_project_cell_capability_client_calls_exact_internal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    project_id = UUID("00000000-0000-0000-0000-000000000005")
    expected = {
        "project_id": str(project_id),
        "provider": "disabled",
        "enabled": False,
        "ready": False,
        "state": "disabled",
        "detail": "workspace provider is disabled",
    }

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return expected

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.get_project_cell_capabilities(project_id)

    assert result is expected
    assert observed == {
        "method": "GET",
        "path": f"/internal/projects/{project_id}/workspace/capabilities",
    }


async def test_project_cell_agent_bootstrap_calls_exact_internal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    workspace_id = UUID("00000000-0000-0000-0000-000000000006")

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return {
            "files": {
                "src/app/page.tsx": "export default function Page(){return null}\n",
            },
            "seeded_from_project": True,
            "generation_run_id": "00000000-0000-0000-0000-000000000099",
            "fencing_epoch": 4,
            "workspace_revision": "a" * 64,
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.project_cell_agent_bootstrap(
        workspace_id,
        generation_run_id=UUID("00000000-0000-0000-0000-000000000099"),
        fencing_epoch=4,
    )

    assert result == ProjectCellAgentWorkspaceSnapshot(
        files={"src/app/page.tsx": "export default function Page(){return null}\n"},
        seeded_from_project=True,
        generation_run_id=UUID("00000000-0000-0000-0000-000000000099"),
        fencing_epoch=4,
        workspace_revision="a" * 64,
    )
    assert observed == {
        "method": "POST",
        "path": f"/internal/workspaces/{workspace_id}/agent/bootstrap",
        "json": {
            "generation_run_id": "00000000-0000-0000-0000-000000000099",
            "fencing_epoch": 4,
        },
    }


async def test_project_cell_agent_write_files_validates_and_calls_exact_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    workspace_id = UUID("00000000-0000-0000-0000-000000000007")
    run_id = UUID("00000000-0000-0000-0000-000000000008")

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return {"written": 1, "deleted": 1, "workspace_revision": "b" * 64}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.project_cell_agent_write_files(
        workspace_id,
        generation_run_id=run_id,
        fencing_epoch=5,
        expected_revision="a" * 64,
        files={
            "src/app/page.tsx": "updated\n",
        },
        deletes=("obsolete.txt",),
    )

    assert result == ProjectCellAgentWriteResponse(
        written=1,
        deleted=1,
        workspace_revision="b" * 64,
    )
    assert observed == {
        "method": "POST",
        "path": f"/internal/workspaces/{workspace_id}/agent/write-files",
        "json": {
            "generation_run_id": str(run_id),
            "fencing_epoch": 5,
            "expected_revision": "a" * 64,
            "files": {
                "src/app/page.tsx": "updated\n",
            },
            "deletes": ["obsolete.txt"],
        },
    }

    with pytest.raises(ValueError):
        await orchestrator_client.project_cell_agent_write_files(
            workspace_id,
            generation_run_id=run_id,
            fencing_epoch=5,
            expected_revision="a" * 64,
            files={"broken.txt": 7},  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError):
        await orchestrator_client.project_cell_agent_write_files(
            workspace_id,
            generation_run_id=run_id,
            fencing_epoch=5,
            expected_revision="a" * 64,
            files={"same.txt": "x"},
            deletes=("same.txt",),
        )


async def test_project_cell_agent_exec_validates_and_calls_exact_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    workspace_id = UUID("00000000-0000-0000-0000-000000000008")
    run_id = UUID("00000000-0000-0000-0000-000000000009")

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return {
            "ok": True,
            "exit_code": 0,
            "detail": "build clean",
            "timed_out": False,
            "workspace_revision": "c" * 64,
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.project_cell_agent_exec(
        workspace_id,
        "pnpm typecheck",
        generation_run_id=run_id,
        fencing_epoch=6,
        expected_revision="b" * 64,
        timeout_seconds=321,
    )

    assert result == ProjectCellAgentExecResponse(
        ok=True,
        exit_code=0,
        detail="build clean",
        timed_out=False,
        workspace_revision="c" * 64,
    )
    assert observed == {
        "method": "POST",
        "path": f"/internal/workspaces/{workspace_id}/agent/exec",
        "json": {
            "generation_run_id": str(run_id),
            "fencing_epoch": 6,
            "expected_revision": "b" * 64,
            "cmd": "pnpm typecheck",
            "timeout_seconds": 321,
        },
        "timeout": 351.0,
    }

    with pytest.raises(ValueError):
        await orchestrator_client.project_cell_agent_exec(
            workspace_id,
            "",
            generation_run_id=run_id,
            fencing_epoch=6,
            expected_revision="b" * 64,
        )


async def test_provision_waits_for_a_cold_template_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.provision(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        slug="max-preview",
        template="max-miniapp-nextjs",
    )

    assert result == {"state": "running"}
    assert observed["timeout"] == 1320.0
    assert observed["path"] == "/internal/projects/provision"


async def test_cell_draft_apply_uses_lease_revision_and_explicit_deletes(monkeypatch) -> None:
    observed: dict[str, object] = {}
    workspace_id, run_id = uuid4(), uuid4()

    async def fake_request(method, path, **kwargs):
        observed.update(method=method, path=path, **kwargs)
        return {
            "state": "draft_running", "workspace_revision": "b" * 64,
            "preview_url": "https://cell.preview.example.test", "runtime_log_tail": "ready",
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    result = await orchestrator_client.project_cell_apply_draft(
        workspace_id, generation_run_id=run_id, fencing_epoch=7,
        expected_revision="a" * 64, files={"empty.txt": ""}, deletes=("old.txt",),
    )
    assert result.workspace_revision == "b" * 64
    assert result.runtime_log_tail == "ready"
    assert observed == {
        "method": "POST", "path": f"/internal/workspaces/{workspace_id}/draft/apply",
        "json": {"generation_run_id": str(run_id), "fencing_epoch": 7,
                 "expected_revision": "a" * 64, "files": {"empty.txt": ""},
                 "deletes": ["old.txt"]}, "timeout": 660.0,
    }


def _cell_preview_payload(workspace_id: UUID) -> dict[str, str]:
    origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
    return {
        "workspace_id": str(workspace_id), "state": "draft_running", "preview_url": origin,
        "bootstrap_url": f"{origin}/api/omnia/preview-session"
                         "?expires=1893456000&signature=" + "a" * 43,
        "expires_at": "2030-01-01T00:00:00Z",
    }


async def test_cell_preview_session_is_bound_to_workspace_and_lease(monkeypatch) -> None:
    observed: dict[str, object] = {}
    workspace_id, run_id = uuid4(), uuid4()
    payload = _cell_preview_payload(workspace_id)

    async def fake_request(method, path, **kwargs):
        observed.update(method=method, path=path, **kwargs)
        return payload

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    response = await orchestrator_client.project_cell_create_preview_session(
        workspace_id, generation_run_id=run_id, fencing_epoch=7,
    )
    assert response.workspace_id == workspace_id
    assert observed == {
        "method": "POST", "path": f"/internal/workspaces/{workspace_id}/draft/preview-session",
        "json": {"generation_run_id": str(run_id), "fencing_epoch": 7},
    }
    other_id = uuid4()
    payload.update(_cell_preview_payload(other_id))
    with pytest.raises(OrchestratorUnavailable, match="different cell"):
        await orchestrator_client.project_cell_create_preview_session(
            workspace_id, generation_run_id=run_id, fencing_epoch=7,
        )


@pytest.mark.parametrize("patch", [
    {"bootstrap_url": "https://elsewhere.example.test/api/omnia/preview-session"},
    {"preview_url": "https://cell-000000000000-dev.attacker.example"},
    {"preview_url": "http://cell.preview.example.test"},
    {"expires_at": "invalid"}, {"expires_at": "2030-01-01"},
    {"state": "running"}, {"workspace_id": "not-uuid"}, {"unexpected": "secret"},
])
def test_cell_preview_rejects_malformed_or_cross_origin_session(patch) -> None:
    with pytest.raises(OrchestratorUnavailable):
        ProjectCellPreviewSession.from_json(_cell_preview_payload(uuid4()) | patch)


def test_cell_preview_constructor_cannot_bypass_host_validation() -> None:
    with pytest.raises(ValueError, match="invalid Project Cell preview session"):
        ProjectCellPreviewSession(
            workspace_id=uuid4(), preview_url="https://attacker.example",
            bootstrap_url="https://attacker.example/api/omnia/preview-session"
            "?expires=1893456000&signature=" + "a" * 43,
            expires_at="2030-01-01T00:00:00Z",
        )


@pytest.mark.parametrize("patch", [
    {"state": "failed"}, {"workspace_revision": "bad"}, {"package_exit_code": True},
    {"migration_exit_code": "0"}, {"runtime_log_tail": []}, {"extra": "secret"},
    {"state": "draft_failed", "migration_exit_code": 0},
    {"state": "draft_running", "migration_exit_code": 1},
])
def test_cell_draft_rejects_invalid_response(patch) -> None:
    with pytest.raises(OrchestratorUnavailable):
        ProjectCellDraftApplyResponse.from_json({
            "state": "draft_running", "workspace_revision": "a" * 64,
            "preview_url": "https://cell.preview.example.test", **patch,
        })


def test_cell_draft_accepts_honest_failed_migration_response() -> None:
    result = ProjectCellDraftApplyResponse.from_json({
        "state": "draft_failed",
        "workspace_revision": "a" * 64,
        "preview_url": "https://cell.preview.example.test",
        "migration_exit_code": 1,
        "migration_stderr_tail": "migration failed",
    })
    assert result.migration_exit_code == 1


async def test_project_shell_and_dependency_sync_use_long_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.append({"method": method, "path": path, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    project_id = UUID("00000000-0000-0000-0000-000000000001")

    await orchestrator_client.agent_build(project_id, "max-preview")
    await orchestrator_client.agent_exec(project_id, "max-preview", "pnpm test")
    await orchestrator_client.agent_exec_sandbox(project_id, "max-preview", "pnpm test")
    await orchestrator_client.hot_reload(
        project_id,
        "max-preview",
        {"package.json": '{"name":"app"}'},
    )

    assert [call["timeout"] for call in observed] == [600.0, 210.0, 1500.0, 1800.0]
    assert observed[2]["json"] == {"slug": "max-preview", "cmd": "pnpm test"}
    assert "params" not in observed[2]


async def test_hot_reload_forwards_explicit_empty_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.hot_reload(
        UUID("00000000-0000-0000-0000-000000000010"),
        "max-preview",
        {"blank.txt": "", "removed.txt": ""},
        empty_files=("blank.txt",),
    )

    assert result == {"state": "hot_reloaded"}
    assert observed["json"] == {
        "project_id": "00000000-0000-0000-0000-000000000010",
        "files": {"blank.txt": "", "removed.txt": ""},
        "empty_files": ["blank.txt"],
    }


async def test_control_client_sends_only_fenced_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_calls: list[dict[str, object]] = []
    workspace_id = UUID("00000000-0000-0000-0000-000000000011")

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        raw_calls.append({"method": method, "path": path, **kwargs})
        return {
            "workspace_id": str(workspace_id),
            "state": "resources_ready",
            "provider_ref": "cell-1",
            "fencing_epoch": 4,
            "checkpoint_ref": None,
            "has_workspace": True,
            "has_agent_home": True,
            "has_postgres": True,
            "has_redis": True,
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    client = HttpProjectCellOrchestratorClient()
    dto = ControlProjectCellResourcesRequest(
        workspace_id=workspace_id,
        kind="wake",
        checkpoint_ref=None,
        operation_id=uuid4(),
        fencing_epoch=4,
        request_digest="a" * 64,
    )

    result = await client.control(dto)

    assert result == ProjectCellResourceResponse(
        workspace_id=workspace_id,
        state="resources_ready",
        provider_ref="cell-1",
        fencing_epoch=4,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )
    assert raw_calls == [
        {
            "method": "POST",
            "path": f"/internal/workspaces/{workspace_id}/control",
            "json": {
                "workspace_id": str(workspace_id),
                "kind": "wake",
                "checkpoint_ref": None,
                "operation_id": str(dto.operation_id),
                "fencing_epoch": 4,
                "request_digest": "a" * 64,
            },
        }
    ]


@pytest.mark.parametrize("method_name", ["ensure", "observe_resources"])
async def test_every_client_body_preserves_mutation_identity(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    raw_calls: list[dict[str, object]] = []
    workspace_id = UUID("00000000-0000-0000-0000-000000000021")
    operation_id = uuid4()
    digest = "b" * 64

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        raw_calls.append({"method": method, "path": path, **kwargs})
        return {
            "workspace_id": str(workspace_id),
            "state": "resources_ready",
            "provider_ref": "cell-2",
            "fencing_epoch": 9,
            "checkpoint_ref": None,
            "has_workspace": True,
            "has_agent_home": True,
            "has_postgres": True,
            "has_redis": True,
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    client = HttpProjectCellOrchestratorClient()
    if method_name == "ensure":
        dto = EnsureProjectCellResourcesRequest(
            workspace_id=workspace_id,
            project_id=uuid4(),
            owner_id=uuid4(),
            profile_version="docker-owner-cell-resources-v1",
            operation_id=operation_id,
            fencing_epoch=9,
            request_digest=digest,
        )
        result = await client.ensure(dto)
    else:
        dto = ObserveProjectCellResourcesRequest(
            workspace_id=workspace_id,
            operation_id=operation_id,
            fencing_epoch=9,
            request_digest=digest,
        )
        result = await client.observe_resources(dto)

    sent_json = raw_calls[0]["json"]
    assert isinstance(sent_json, dict)
    assert result.fencing_epoch == 9
    assert sent_json["operation_id"] == str(operation_id)
    assert sent_json["fencing_epoch"] == 9
    assert sent_json["request_digest"] == digest
    assert sent_json == dto.to_wire_json()


def test_control_request_enforces_checkpoint_rules() -> None:
    with pytest.raises(ValueError):
        ControlProjectCellResourcesRequest(
            workspace_id=uuid4(),
            kind="restore",
            checkpoint_ref=None,
            operation_id=uuid4(),
            fencing_epoch=1,
            request_digest="c" * 64,
        )

    with pytest.raises(ValueError):
        ControlProjectCellResourcesRequest(
            workspace_id=uuid4(),
            kind="pause",
            checkpoint_ref="../escape",
            operation_id=uuid4(),
            fencing_epoch=1,
            request_digest="c" * 64,
        )


async def test_real_orchestrator_error_envelope_exposes_pre_effect_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = uuid4()
    rejection = {
        "operation_id": str(operation_id),
        "fencing_epoch": 7,
        "request_digest": "d" * 64,
        "effect_applied": False,
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            409,
            request=request,
            json={
                "error": {
                    "code": "conflict",
                    "message": "fence rejected",
                    "details": rejection,
                }
            },
        )
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        orchestrator_client.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        orchestrator_client,
        "get_settings",
        lambda: SimpleNamespace(
            orchestrator_url="http://orchestrator",
            orchestrator_internal_token=SimpleNamespace(
                get_secret_value=lambda: "test-internal-token"
            ),
        ),
    )

    with pytest.raises(OrchestratorBadRequest) as caught:
        await orchestrator_client._request_raw("POST", "/internal/workspaces/ensure")

    assert caught.value.status_code == 409
    assert caught.value.details == rejection
    parsed = ProjectCellPreEffectRejection.from_json(caught.value.details)
    assert parsed.operation_id == operation_id

    with pytest.raises(ValueError):
        ControlProjectCellResourcesRequest(
            workspace_id=uuid4(),
            kind="wake",
            checkpoint_ref="accepted-1",
            operation_id=uuid4(),
            fencing_epoch=1,
            request_digest="c" * 64,
        )


def test_pre_effect_rejection_requires_exact_typed_shape() -> None:
    payload = {
        "operation_id": "00000000-0000-0000-0000-000000000041",
        "fencing_epoch": 4,
        "request_digest": "e" * 64,
        "effect_applied": False,
    }

    rejection = ProjectCellPreEffectRejection.from_json(payload)

    assert rejection.operation_id == UUID(payload["operation_id"])
    assert rejection.fencing_epoch == 4
    assert rejection.request_digest == "e" * 64
    assert rejection.effect_applied is False

    with pytest.raises(ValueError):
        ProjectCellPreEffectRejection.from_json(payload | {"detail": "extra"})

    with pytest.raises(ValueError):
        ProjectCellPreEffectRejection.from_json(payload | {"effect_applied": True})


async def test_resource_response_rejects_invalid_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        return {
            "workspace_id": "00000000-0000-0000-0000-000000000031",
            "state": "resources_ready",
            "provider_ref": "cell-3",
            "fencing_epoch": 2,
            "checkpoint_ref": None,
            "has_workspace": True,
            "has_agent_home": True,
            "has_postgres": True,
            "has_redis": True,
            "raw_labels": {"leak": True},
        }

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    client = HttpProjectCellOrchestratorClient()

    with pytest.raises(OrchestratorUnavailable):
        await client.observe_resources(
            ObserveProjectCellResourcesRequest(
                workspace_id=UUID("00000000-0000-0000-0000-000000000031"),
                operation_id=uuid4(),
                fencing_epoch=2,
                request_digest="d" * 64,
            )
        )


def test_agent_workspace_snapshot_requires_exact_typed_shape() -> None:
    payload = {
        "files": {"src/app/page.tsx": "ok\n"},
        "seeded_from_project": False,
        "generation_run_id": None,
        "fencing_epoch": 2,
        "workspace_revision": "f" * 64,
    }

    snapshot = ProjectCellAgentWorkspaceSnapshot.from_json(payload)

    assert snapshot == ProjectCellAgentWorkspaceSnapshot(
        files={"src/app/page.tsx": "ok\n"},
        seeded_from_project=False,
        generation_run_id=None,
        fencing_epoch=2,
        workspace_revision="f" * 64,
    )

    with pytest.raises(OrchestratorUnavailable):
        ProjectCellAgentWorkspaceSnapshot.from_json(payload | {"detail": "extra"})

    with pytest.raises(OrchestratorUnavailable):
        ProjectCellAgentWorkspaceSnapshot.from_json(
            {
                "files": {"src/app/page.tsx": 7},
                "seeded_from_project": False,
                "generation_run_id": None,
                "fencing_epoch": 2,
                "workspace_revision": "f" * 64,
            }
        )


def test_resource_response_accepts_optional_draft_status_but_no_unknown_fields() -> None:
    payload: dict[str, object] = {
        "workspace_id": "00000000-0000-0000-0000-000000000031",
        "state": "resources_ready", "provider_ref": "cell-3", "fencing_epoch": 2,
        "checkpoint_ref": None, "has_workspace": True, "has_agent_home": True,
        "has_postgres": True, "has_redis": True, "has_draft_runtime": True,
        "draft_state": "running", "preview_url": "https://cell.preview.example.test",
    }
    response = ProjectCellResourceResponse.from_json(payload)
    assert response.has_draft_runtime is True
    assert response.draft_state == "running"
    assert response.preview_url == "https://cell.preview.example.test"
    assert response.to_wire_json() == payload
    with pytest.raises(OrchestratorUnavailable):
        ProjectCellResourceResponse.from_json(payload | {"raw_container": "secret"})
    with pytest.raises(OrchestratorUnavailable):
        ProjectCellResourceResponse.from_json(payload | {"draft_state": "unknown"})
