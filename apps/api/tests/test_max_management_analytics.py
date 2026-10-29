import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from yleum_api.core.errors import ApiError
from yleum_api.routers import max_analytics
from yleum_api.services.integration_auth import (
    verify_business_analytics_assertion,
    verify_integration_assertion,
)


def assertion(project, body, *, purpose=b"yleum:business-analytics:v1"):
    now = int(time.time())
    path = f"/api/runtime/projects/{project}/analytics"
    claims = {
        "project_id": str(project),
        "max_user_id": "42",
        "iat": now,
        "exp": now + 60,
        "method": "POST",
        "path": path,
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signed = f"v1.{encoded}"
    key = hmac.digest(b"synthetic-token", purpose, "sha256")
    return f"{signed}.{hmac.new(key, signed.encode(), 'sha256').hexdigest()}", path


def test_business_receipt_key_cannot_mint_integration_authority():
    project, body = uuid4(), b"{}"
    signed, path = assertion(project, body)
    kwargs = dict(project_id=project, method="POST", path=path, body=body)
    assert verify_business_analytics_assertion(signed, "synthetic-token", **kwargs) == 42
    with pytest.raises(ValueError):
        verify_integration_assertion(signed, "synthetic-token", **kwargs)
    with pytest.raises(ValueError):
        verify_business_analytics_assertion(signed, "synthetic-token", **{**kwargs, "body": b"[]"})
    core, _ = assertion(project, body, purpose=b"omnia:integration-assertion:v1")
    with pytest.raises(ValueError):
        verify_business_analytics_assertion(core, "synthetic-token", **kwargs)


@pytest.mark.parametrize(
    "kind,event_type", [("open", "app_open"), ("event", "button_click"), ("action", None)]
)
async def test_business_key_attests_only_fixed_committed_business_events(
    monkeypatch, kind, event_type
):
    project = uuid4()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/analytics",
            "headers": [(b"x-yleum-business-assertion", b"signed")],
        }
    )
    context = AsyncMock()
    monkeypatch.setattr(max_analytics, "_runtime_context", context)
    with pytest.raises(ApiError) as caught:
        await max_analytics.collect_event(
            project,
            max_analytics.AnalyticsEventRequest(
                event_id=uuid4(),
                kind=kind,
                event_type=event_type,
            ),
            SimpleNamespace(),
            request,
        )
    assert caught.value.status_code == 401
    context.assert_not_called()


async def test_committed_managed_order_uses_separate_auth_and_durable_receipt(monkeypatch):
    project, event = uuid4(), uuid4()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/analytics",
            "headers": [(b"x-yleum-business-assertion", b"signed")],
        }
    )
    context = AsyncMock(return_value=SimpleNamespace(max_user_id=42))
    record = AsyncMock()
    monkeypatch.setattr(max_analytics, "_runtime_context", context)
    monkeypatch.setattr(max_analytics, "record_event", record)
    session = SimpleNamespace()
    result = await max_analytics.collect_event(
        project,
        max_analytics.AnalyticsEventRequest(
            event_id=event,
            kind="action",
            event_type="order_created",
        ),
        session,
        request,
    )
    assert result.status_code == 204
    assert context.call_args.kwargs == {"business_analytics": True}
    record.assert_awaited_once_with(session, project, 42, event, "action", "order_created")
