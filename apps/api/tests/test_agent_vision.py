from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from omnia_api.services import agent_vision


@pytest.mark.asyncio
async def test_max_see_bootstraps_signed_preview_before_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import vision_audit
    from omnia_api.workers import preview

    bootstrap = "https://fitness-dev.example/api/omnia/preview-session?expires=1&signature=x"
    captured: dict[str, Any] = {}

    async def fake_capture(url: str, widths: Any, **kwargs: Any) -> dict[int, bytes]:
        captured.update(url=url, widths=tuple(widths), **kwargs)
        return {1440: b"png", 360: b"png"}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(skipped=True)

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)

    result = await agent_vision.see_page(
        uuid4(),
        path="/profile",
        bootstrap_url=bootstrap,
    )

    assert result["ok"] is True
    assert captured == {
        "url": "https://fitness-dev.example/profile",
        "widths": (1440, 360),
        "bootstrap_url": bootstrap,
    }


@pytest.mark.asyncio
async def test_max_see_rejects_invalid_bootstrap_origin() -> None:
    result = await agent_vision.see_page(uuid4(), bootstrap_url="javascript:bad")

    assert result == {"ok": False, "error": "invalid preview bootstrap URL"}
