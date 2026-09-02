"""Keep the API adapter coupled to the orchestrator's actual wire models."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import SecretStr

from omnia_api.services import orchestrator_client


@pytest.fixture
def wire_schemas(monkeypatch):
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "orchestrator/src/omnia_orchestrator/schemas/workspace.py"
    )
    spec = importlib.util.spec_from_file_location("cell_wire_contract", schema_path)
    assert spec is not None and spec.loader is not None
    schemas = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, schemas)
    spec.loader.exec_module(schemas)
    return schemas


async def test_ensure_adapter_passes_actual_orchestrator_http_schema(monkeypatch, wire_schemas):
    schemas = wire_schemas
    workspace_id, generation_run_id = uuid4(), uuid4()
    observed = []

    async def ensure(payload, request: Request):
        assert request.headers["X-Internal-Token"] == "wire-contract-test"
        observed.append(payload)
        return schemas.WorkspaceResourceResponse(
            workspace_id=payload.workspace_id,
            state="resources_ready",
            provider_ref="cell-wire-contract",
            fencing_epoch=payload.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    ensure.__annotations__["payload"] = schemas.WorkspaceEnsureRequest
    app = FastAPI()
    app.add_api_route("/internal/workspaces/ensure", ensure, methods=["POST"])
    original_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=app)
    monkeypatch.setattr(
        orchestrator_client.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(orchestrator_client, "get_settings", lambda: SimpleNamespace(
        orchestrator_url="http://orchestrator.test",
        orchestrator_internal_token=SecretStr("wire-contract-test"),
    ))
    request = orchestrator_client.EnsureProjectCellResourcesRequest(
        workspace_id=workspace_id,
        project_id=uuid4(),
        owner_id=uuid4(),
        generation_run_id=generation_run_id,
        profile_version="docker-owner-cell-resources-v1",
        operation_id=uuid4(),
        fencing_epoch=2,
        request_digest="a" * 64,
    )
    result = await orchestrator_client.HttpProjectCellOrchestratorClient().ensure(request)
    assert result.workspace_id == workspace_id
    assert result.state == "resources_ready"
    assert result.fencing_epoch == 2
    assert len(observed) == 1
    assert observed[0].generation_run_id == generation_run_id

    # This omission caused the first real generation to fail before provider dispatch.
    without_lease = request.to_wire_json()
    without_lease.pop("generation_run_id")
    async with original_client(transport=transport, base_url="http://orchestrator.test") as client:
        rejected = await client.post("/internal/workspaces/ensure", json=without_lease)
    assert rejected.status_code == 422
    assert len(observed) == 1
