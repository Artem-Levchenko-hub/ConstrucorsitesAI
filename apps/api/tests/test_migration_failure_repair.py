from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yleum_api.services.max_finalization import (
    GenerationPhase,
    MaxFinalizationCoordinator,
    MaxFinalizationStatus,
)


@pytest.mark.parametrize(
    "detail,repairable",
    [
        ("[build-contract:version] [project-migration-source-error:42P01] absent table", True),
        ("[project-migration-source-error:42601] syntax", True),
        ("[project-migration-source-error:57014] timeout", False),
        ("project migration verification failed", False),
        ("controller database operation failed", False),
        ("project migration checksum changed", False),
    ],
)
async def test_only_typed_transactional_source_failure_returns_to_editor(detail, repairable):
    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator._checkpoint = lambda *args: "checkpoint"
    coordinator._outcome = AsyncMock(side_effect=lambda status, *args: status)
    coordinator._log_terminal = AsyncMock()
    outcome = await coordinator._failed(
        SimpleNamespace(),
        SimpleNamespace(),
        GenerationPhase.FINAL_BUILD,
        SimpleNamespace(operation_id=None),
        detail,
    )
    assert outcome is (
        MaxFinalizationStatus.NEEDS_EDIT if repairable else MaxFinalizationStatus.FAILED
    )
