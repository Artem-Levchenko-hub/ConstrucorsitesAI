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
    ProjectCellPreEffectRejection,
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
