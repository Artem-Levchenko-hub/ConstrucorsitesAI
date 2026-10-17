from uuid import uuid4

from yleum_orchestrator.schemas.workspace import WorkspaceAgentWriteRequest


def test_server_operation_identity_survives_write_request_validation():
    operation_id = uuid4()
    request = WorkspaceAgentWriteRequest(
        generation_run_id=uuid4(),
        fencing_epoch=4,
        expected_revision="a" * 64,
        files={"src/app.ts": "confirmed"},
        operation_id=operation_id,
    )
    assert request.operation_id == operation_id
