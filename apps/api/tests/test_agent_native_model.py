"""The selected model reaches the actual native HTTP payload unchanged."""

import json

import httpx
import pytest

from yleum_api.services import agent_native


@pytest.mark.parametrize("model", [None, "~anthropic/claude-sonnet-latest"])
async def test_native_request_model_and_backward_compatible_default(model):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        kwargs = {} if model is None else {"model": model}
        await agent_native._call_messages(
            client, "https://gateway.test/v1/messages", [], "s", **kwargs
        )

    assert len(requests) == 1
    assert requests[0]["model"] == (model or "claude-sonnet-5")
