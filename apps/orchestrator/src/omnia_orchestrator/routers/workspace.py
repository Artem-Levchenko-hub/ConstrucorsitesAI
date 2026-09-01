"""Dark internal workspace-capability surface."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.internal_auth import verify_internal_token
from omnia_orchestrator.schemas.workspace import WorkspaceCapabilityResponse
from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider

router = APIRouter(prefix="/internal/projects", tags=["workspace"])


@router.get(
    "/{project_id}/workspace/capabilities",
    response_model=WorkspaceCapabilityResponse,
)
async def workspace_capabilities(
    project_id: UUID,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WorkspaceCapabilityResponse:
    verify_internal_token(x_internal_token)
    provider = build_workspace_provider(get_settings())
    status = await provider.status(project_id)
    return WorkspaceCapabilityResponse(
        project_id=status.project_id,
        provider=status.provider,
        enabled=status.enabled,
        ready=status.ready,
        state=status.state,
        detail=status.detail,
    )
