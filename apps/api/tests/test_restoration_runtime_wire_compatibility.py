"""Task5 cancellation states remain compatible with the owner API contract."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from yleum_api.schemas.restoration import RuntimeRestoration


def runtime_payload(state: str, phase: str) -> dict[str, object]:
    return {
        "operation_id": UUID(int=1),
        "workspace_id": UUID(int=2),
        "project_id": UUID(int=3),
        "owner_id": UUID(int=4),
        "state": state,
        "phase": phase,
        "revision": 2,
        "candidate_id": None,
        "report": None,
        "error": None,
        "can_apply": False,
        "can_cancel": False,
        "observed": None,
        "binding": None,
        "binding_digest": None,
    }


def test_cancellation_pending_uses_wire_compatible_reconciling_state() -> None:
    value = RuntimeRestoration.model_validate(runtime_payload("reconciling", "reconciling"))
    assert value.state == "reconciling" and value.phase == "reconciling"

    with pytest.raises(ValidationError):
        RuntimeRestoration.model_validate(runtime_payload("cancelling", "cancelling"))
