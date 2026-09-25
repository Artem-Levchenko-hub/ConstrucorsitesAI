"""Warm-artifact hand-off for Kubernetes publications.

A seeding init container in the runtime cluster restores a project's warm volume
(Postgres data, workspace, home) from an archive the orchestrator already holds.
Instead of pushing archives into the registry or sharing the internal token with
every pod, the placement issues a single-use, expiring capability link; the pod
fetches it over WireGuard and the link dies with the download.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.services.k8s_publication import artifact_capabilities

router = APIRouter(prefix="/internal/publication-artifacts", tags=["cell-publication"])


@router.get("/{token}")
async def download_publication_artifact(token: str) -> FileResponse:
    path = artifact_capabilities().resolve(token)
    if path is None or not path.is_file():
        # Unknown, expired or exhausted link — indistinguishable on purpose.
        raise OrchestratorError(
            code="not_found", message="artifact link is not valid", status_code=404
        )
    return FileResponse(path, media_type="application/x-tar", filename=path.name)
