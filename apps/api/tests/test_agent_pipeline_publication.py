from unittest.mock import AsyncMock

import pytest

from omnia_api.services.generation.agent_pipeline import publish_finalized_candidate_once


@pytest.mark.parametrize("activation_consumed, expected", [(True, 0), (False, 1)])
async def test_adaptive_activation_consumes_publication_once(
    activation_consumed: bool,
    expected: int,
) -> None:
    publish = AsyncMock()

    called = await publish_finalized_candidate_once(
        activation_consumed=activation_consumed,
        publish=publish,
    )

    assert publish.await_count == expected
    assert called is (not activation_consumed)
