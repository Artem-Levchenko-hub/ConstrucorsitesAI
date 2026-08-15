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
    audit_calls = 0
    diagnostic_calls = 0
    retry_delays: list[int] = []

    async def fake_capture(url: str, widths: Any, **kwargs: Any) -> dict[int, bytes]:
        captured.update(url=url, widths=tuple(widths), **kwargs)
        return {390: b"png", 360: b"png"}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        nonlocal audit_calls
        audit_calls += 1
        return SimpleNamespace(skipped=True, skip_reason="gateway_error")

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        nonlocal diagnostic_calls
        diagnostic_calls += 1
        return {"failed_requests": [], "console_errors": []}

    async def fake_sleep(delay: int) -> None:
        retry_delays.append(delay)

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)
    monkeypatch.setattr(agent_vision.asyncio, "sleep", fake_sleep)

    result = await agent_vision.see_page(
        uuid4(),
        path="/profile",
        bootstrap_url=bootstrap,
        product_kind="max_miniapp",
    )

    assert result["ok"] is True
    assert result["verdict"] == "unscored"
    assert result["visual_audit_unavailable"] is True
    assert result["quality_advisory"] is True
    assert "proof_unavailable" not in result
    assert audit_calls == 2
    assert diagnostic_calls == 1
    assert retry_delays == [3]
    assert result["audit_attempts"] == 2
    assert captured == {
        "url": "https://fitness-dev.example/profile",
        "widths": (390, 360),
        "bootstrap_url": bootstrap,
        "hide_platform_chrome": True,
    }


@pytest.mark.asyncio
async def test_see_reuses_capture_for_one_transient_judge_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import vision_audit
    from omnia_api.workers import preview

    capture_calls = 0
    audit_calls = 0

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        nonlocal capture_calls
        capture_calls += 1
        return {390: b"png", 360: b"png"}

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"failed_requests": [], "console_errors": []}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls == 1:
            return SimpleNamespace(skipped=True, skip_reason="gateway_error")
        return SimpleNamespace(skipped=False, verdict="beautiful", score=9, issues=())

    async def fake_sleep(delay: int) -> None:
        assert delay == 3

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)
    monkeypatch.setattr(agent_vision.asyncio, "sleep", fake_sleep)

    result = await agent_vision.see_page(
        uuid4(),
        bootstrap_url="https://fitness-dev.example/api/omnia/preview-session?signature=x",
        product_kind="max_miniapp",
    )

    assert result["ok"] is True
    assert result["verdict"] == "beautiful"
    assert result["needs_fix"] is False
    assert capture_calls == 1
    assert audit_calls == 2


@pytest.mark.asyncio
async def test_max_see_rejects_invalid_bootstrap_origin() -> None:
    result = await agent_vision.see_page(uuid4(), bootstrap_url="javascript:bad")

    assert result == {"ok": False, "error": "invalid preview bootstrap URL"}


@pytest.mark.asyncio
async def test_max_see_requires_both_mobile_viewport_screenshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import vision_audit
    from omnia_api.workers import preview

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        return {390: b"png"}

    async def unexpected_audit(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("partial MAX capture must fail before vision audit")

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(vision_audit, "audit_screenshots", unexpected_audit)

    result = await agent_vision.see_page(
        uuid4(),
        bootstrap_url="https://fitness-dev.example/api/omnia/preview-session?signature=x",
        product_kind="max_miniapp",
    )

    assert result["ok"] is False
    assert "360" in result["error"]


@pytest.mark.asyncio
async def test_see_exposes_actionable_visual_repair_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import vision_audit
    from omnia_api.workers import preview

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        return {390: b"png", 360: b"png"}

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"failed_requests": [], "console_errors": []}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            skipped=False,
            verdict="generic",
            score=6,
            issues=("Hero: enlarge the title",),
        )

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)

    result = await agent_vision.see_page(
        uuid4(),
        bootstrap_url="https://fitness-dev.example/api/omnia/preview-session?signature=x",
        product_kind="max_miniapp",
    )

    assert result["ok"] is True
    assert result["verdict"] == "generic"
    assert result["score"] == 6
    assert result["needs_fix"] is False
    assert result["quality_advisory"] is True
    assert "Optional visual polish notes" in result["detail"]


@pytest.mark.asyncio
async def test_max_see_keeps_sub_eight_score_advisory_when_runtime_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import vision_audit
    from omnia_api.workers import preview

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        return {390: b"png", 360: b"png"}

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"failed_requests": [], "console_errors": []}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(skipped=False, verdict="beautiful", score=7, issues=())

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)

    result = await agent_vision.see_page(
        uuid4(),
        bootstrap_url="https://fitness-dev.example/api/omnia/preview-session?signature=x",
        product_kind="max_miniapp",
    )

    assert result["ok"] is True
    assert result["needs_fix"] is False
    assert result["quality_advisory"] is True
    assert "Optional visual polish notes" in result["detail"]
    assert "production-grade" in result["detail"]


@pytest.mark.asyncio
async def test_max_see_keeps_objectively_broken_layout_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services import vision_audit
    from omnia_api.workers import preview

    async def fake_capture(*args: Any, **kwargs: Any) -> dict[int, bytes]:
        return {390: b"png", 360: b"png"}

    async def fake_diagnostics(*args: Any, **kwargs: Any) -> dict[str, list[str]]:
        return {"failed_requests": [], "console_errors": []}

    async def fake_audit(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            skipped=False,
            verdict="broken",
            score=4,
            issues=("Primary action is clipped below the viewport.",),
        )

    monkeypatch.setattr(preview, "capture_live_url", fake_capture)
    monkeypatch.setattr(preview, "capture_diagnostics", fake_diagnostics)
    monkeypatch.setattr(vision_audit, "audit_screenshots", fake_audit)

    result = await agent_vision.see_page(
        uuid4(),
        bootstrap_url="https://fitness-dev.example/api/omnia/preview-session?signature=x",
        product_kind="max_miniapp",
    )

    assert result["ok"] is True
    assert result["needs_fix"] is True
    assert result["quality_advisory"] is False
    assert "Apply these concrete fixes" in result["detail"]
