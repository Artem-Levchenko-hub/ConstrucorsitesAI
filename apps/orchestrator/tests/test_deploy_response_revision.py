from uuid import uuid4

from omnia_orchestrator.routers.runtime import _deploy_record_to_response
from omnia_orchestrator.services.deploy_state import DeployRecord


def test_deploy_response_exposes_exact_built_revision() -> None:
    commit_sha = "a" * 40
    record = DeployRecord(
        project_id=str(uuid4()),
        commit_sha=commit_sha,
        phase="done",
        prod_url="https://example.test",
    )

    response = _deploy_record_to_response(record)

    assert response.commit_sha == commit_sha
    assert response.model_dump(mode="json")["commit_sha"] == commit_sha
