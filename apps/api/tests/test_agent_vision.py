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

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"failed_requests": [], "console_errors": []}

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
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


@pytest.mark.asyncio
async def test_see_passes_design_contract_to_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import dev_container, vision_audit
    from omnia_api.workers import preview

    captured: dict[str, Any] = {}

    async def fake_url(_project_id: Any) -> str:
        return "https://preview.example"

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        return {1440: b"png", 360: b"png"}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(skipped=True)

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"failed_requests": [], "console_errors": []}

    monkeypatch.setattr(dev_container, "resolve_live_url", fake_url)
    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)

    result = await agent_vision.see_page(uuid4(), prompt_context="design-contract-v1")

    assert result["ok"] is True
    assert captured["prompt_context"] == "design-contract-v1"


@pytest.mark.asyncio
async def test_max_see_keeps_failed_requests_blocking_when_vision_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import dev_container, vision_audit
    from omnia_api.workers import preview

    async def fake_url(_project_id: Any) -> str:
        return "https://preview.example"

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        return {1440: b"png", 360: b"png"}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(skipped=True)

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {
            "failed_requests": ["401 GET https://preview.example/api/omnia/actions"],
            "console_errors": [],
        }

    monkeypatch.setattr(dev_container, "resolve_live_url", fake_url)
    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)

    result = await agent_vision.see_page(uuid4())

    assert result["ok"] is False
    assert "401 GET" in result["detail"]
    assert agent_vision.normalize_max_see_observation(result)["ok"] is False


def test_max_see_softens_only_missing_visual_infrastructure() -> None:
    result = agent_vision.normalize_max_see_observation(
        {"ok": False, "error": "could not render /: TimeoutError"}
    )

    assert result == {
        "ok": True,
        "detail": "could not render /: TimeoutError",
        "proof_unavailable": True,
    }
