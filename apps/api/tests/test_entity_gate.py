"""V1.6 16/5 — entity/fullstack live-container composition gate.

Covers the pure gate service (resolve internal URL + composition gauntlet),
the worker job (compile-settle → gate → quality card), and the de-orphan
wiring asserts (the entity path actually calls ``run(composition=True)`` and
the queue points at the worker job).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from omnia_api.services import entity_gate
from omnia_api.workers import quality

_SRC = Path(__file__).resolve().parents[1] / "src" / "omnia_api"


# ── pure service: gate_live_app ───────────────────────────────────────────────


async def test_gate_live_app_disabled_returns_none(monkeypatch) -> None:
    """Flag OFF → no resolve, no render, None."""
    called = {"resolve": False}

    async def _resolve(_pid, route="/"):
        called["resolve"] = True
        return "http://omnia-dev-x:3000"

    monkeypatch.setattr(
        entity_gate, "get_settings",
        lambda: SimpleNamespace(acceptance_entity_composition_gate=False),
    )
    monkeypatch.setattr(entity_gate, "resolve_live_url", _resolve)
    assert await entity_gate.gate_live_app(uuid4(), "shop") is None
    assert called["resolve"] is False  # short-circuits before resolving


async def test_gate_live_app_unreachable_returns_none(monkeypatch) -> None:
    """Container not running → resolve None → gate None (no gauntlet call)."""
    ran = {"gauntlet": False}

    async def _resolve(_pid, route="/"):
        return None

    async def _run(**_kw):
        ran["gauntlet"] = True

    monkeypatch.setattr(
        entity_gate, "get_settings",
        lambda: SimpleNamespace(acceptance_entity_composition_gate=True),
    )
    monkeypatch.setattr(entity_gate, "resolve_live_url", _resolve)
    monkeypatch.setattr(entity_gate.accept_gauntlet, "run", _run)
    assert await entity_gate.gate_live_app(uuid4(), "shop") is None
    assert ran["gauntlet"] is False


async def test_gate_live_app_runs_composition_only(monkeypatch) -> None:
    """Happy path: composition legs over the live URL, no touch leg, no files."""
    seen: dict = {}
    sentinel = object()

    async def _resolve(_pid, route="/"):
        seen["route"] = route
        base = "http://omnia-dev-sushi-abc:3000"
        return base if route == "/" else base + route

    async def _run(**kw):
        seen.update(kw)
        return sentinel

    monkeypatch.setattr(
        entity_gate, "get_settings",
        lambda: SimpleNamespace(acceptance_entity_composition_gate=True),
    )
    monkeypatch.setattr(entity_gate, "resolve_live_url", _resolve)
    monkeypatch.setattr(entity_gate.accept_gauntlet, "run", _run)

    # the gate forwards the caller's route to resolve_live_url and gates that URL
    out = await entity_gate.gate_live_app(uuid4(), "sushi", "/dashboard")
    assert out is sentinel
    assert seen["route"] == "/dashboard"
    assert seen["url"] == "http://omnia-dev-sushi-abc:3000/dashboard"
    assert seen["composition"] is True
    assert seen["include_rendered"] is False  # 44px touch leg stays out


async def test_gate_live_app_render_error_returns_none(monkeypatch) -> None:
    """A browser/render hiccup never sinks the build — fail-soft to None."""
    async def _resolve(_pid, route="/"):
        return "http://omnia-dev-x:3000"

    async def _boom(**_kw):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(
        entity_gate, "get_settings",
        lambda: SimpleNamespace(acceptance_entity_composition_gate=True),
    )
    monkeypatch.setattr(entity_gate, "resolve_live_url", _resolve)
    monkeypatch.setattr(entity_gate.accept_gauntlet, "run", _boom)
    assert await entity_gate.gate_live_app(uuid4(), "shop") is None


def test_describe_failure_lists_classes() -> None:
    verdict = SimpleNamespace(failed_classes=("taste", "hierarchy"))
    detail = entity_gate.describe_failure(verdict)
    assert "taste" in detail and "hierarchy" in detail
    assert "иерархи" in detail.lower()


def test_describe_failure_empty_classes_has_fallback() -> None:
    detail = entity_gate.describe_failure(SimpleNamespace(failed_classes=()))
    assert "композиция" in detail


# ── worker job: compile-settle + card ─────────────────────────────────────────


def _fast_settle(monkeypatch) -> None:
    monkeypatch.setattr(quality, "_COMPILE_SETTLE_TRIES", 1)
    monkeypatch.setattr(quality, "_COMPILE_SETTLE_DELAY", 0)


async def test_compile_clean_true_when_ok(monkeypatch) -> None:
    _fast_settle(monkeypatch)

    async def _status(_pid, *, slug):
        return {"ok": True}

    monkeypatch.setattr(quality.orchestrator_client, "compile_status", _status)
    assert await quality._compile_clean(uuid4(), "shop") is True


async def test_compile_clean_false_when_broken(monkeypatch) -> None:
    _fast_settle(monkeypatch)

    async def _status(_pid, *, slug):
        return {"ok": False, "error": "boom"}

    monkeypatch.setattr(quality.orchestrator_client, "compile_status", _status)
    assert await quality._compile_clean(uuid4(), "shop") is False


async def test_compile_clean_false_on_orchestrator_error(monkeypatch) -> None:
    _fast_settle(monkeypatch)

    async def _boom(_pid, *, slug):
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(quality.orchestrator_client, "compile_status", _boom)
    assert await quality._compile_clean(uuid4(), "shop") is False


class _FakeEngine:
    async def dispose(self) -> None:
        return None


def _patch_publish(monkeypatch) -> list[dict]:
    """Capture app_errors.publish calls and stub the engine/factory plumbing."""
    published: list[dict] = []

    async def _publish(_factory, project_id, message_id, **kw):
        published.append({"project_id": project_id, "message_id": message_id, **kw})

    monkeypatch.setattr(quality.app_errors, "publish", _publish)
    monkeypatch.setattr(quality, "create_async_engine", lambda _url: _FakeEngine())
    monkeypatch.setattr(quality, "async_sessionmaker", lambda *a, **k: object())
    return published


def _patch_route(monkeypatch, *, route: str = "/") -> None:
    """Stub the 16/5d route-resolve so the worker job never touches the
    orchestrator / a browser: base URL resolves, route resolves to ``route``."""

    async def _base(_pid, route="/"):
        return "http://omnia-dev-x:3000"

    async def _route(_base_url, *, candidate_route="/dashboard"):
        return route

    monkeypatch.setattr(quality.dev_container, "resolve_live_url", _base)
    monkeypatch.setattr(quality.route_target, "resolve_target_route", _route)


async def test_gate_async_disabled_no_publish(monkeypatch) -> None:
    published = _patch_publish(monkeypatch)
    monkeypatch.setattr(
        quality, "get_settings",
        lambda: SimpleNamespace(
            acceptance_entity_composition_gate=False, database_url="x"
        ),
    )
    await quality._gate_async(str(uuid4()), str(uuid4()), "shop")
    assert published == []


async def test_gate_async_compile_broken_no_publish(monkeypatch) -> None:
    _fast_settle(monkeypatch)
    published = _patch_publish(monkeypatch)
    monkeypatch.setattr(
        quality, "get_settings",
        lambda: SimpleNamespace(
            acceptance_entity_composition_gate=True, database_url="x"
        ),
    )

    async def _status(_pid, *, slug):
        return {"ok": False}

    monkeypatch.setattr(quality.orchestrator_client, "compile_status", _status)
    await quality._gate_async(str(uuid4()), str(uuid4()), "shop")
    assert published == []


async def test_gate_async_passing_verdict_no_publish(monkeypatch) -> None:
    _fast_settle(monkeypatch)
    published = _patch_publish(monkeypatch)
    monkeypatch.setattr(
        quality, "get_settings",
        lambda: SimpleNamespace(
            acceptance_entity_composition_gate=True, database_url="x"
        ),
    )

    async def _status(_pid, *, slug):
        return {"ok": True}

    async def _gate(_pid, _slug, _route="/"):
        return SimpleNamespace(hard_failed=(), failed_classes=())

    _patch_route(monkeypatch)
    monkeypatch.setattr(quality.orchestrator_client, "compile_status", _status)
    monkeypatch.setattr(quality.entity_gate, "gate_live_app", _gate)
    await quality._gate_async(str(uuid4()), str(uuid4()), "shop")
    assert published == []


async def test_gate_async_hard_fail_publishes_card(monkeypatch) -> None:
    _fast_settle(monkeypatch)
    published = _patch_publish(monkeypatch)
    captured: dict = {}
    mid, pid = uuid4(), uuid4()
    monkeypatch.setattr(
        quality, "get_settings",
        lambda: SimpleNamespace(
            acceptance_entity_composition_gate=True, database_url="x"
        ),
    )

    async def _status(_pid, *, slug):
        return {"ok": True}

    async def _gate(_pid, _slug, route="/"):
        captured["route"] = route
        return SimpleNamespace(
            hard_failed=(object(),), failed_classes=("taste", "hierarchy")
        )

    _patch_route(monkeypatch, route="/dashboard")
    monkeypatch.setattr(quality.orchestrator_client, "compile_status", _status)
    monkeypatch.setattr(quality.entity_gate, "gate_live_app", _gate)
    await quality._gate_async(str(mid), str(pid), "sushi")
    assert captured["route"] == "/dashboard"  # resolved route reaches the gate

    assert len(published) == 1
    card = published[0]
    assert card["project_id"] == pid and card["message_id"] == mid
    assert card["category"] == "runtime"
    assert "taste" in card["detail"] and "hierarchy" in card["detail"]


# ── de-orphan wiring asserts (R-04 / ratchet) ─────────────────────────────────


def test_entity_gate_calls_run_with_composition() -> None:
    """The entity path MUST fan accept_gauntlet with composition=True and keep
    the touch leg out — falsifiable source assert (16/5 headline contract)."""
    src = (_SRC / "services" / "entity_gate.py").read_text(encoding="utf-8")
    assert "composition=True" in src
    assert "include_rendered=False" in src


def test_worker_resolves_target_route() -> None:
    """The worker MUST resolve the WOW/content route (16/5d) before gating —
    falsifiable de-orphan assert: route_target is wired into the gate path, not
    left orphaned. Without this the gate scores the bare `/` login wall."""
    src = (_SRC / "workers" / "quality.py").read_text(encoding="utf-8")
    assert "route_target.resolve_target_route" in src
    assert "gate_live_app(pid, slug, route)" in src


def test_queue_points_entity_gate_at_worker_job() -> None:
    src = (_SRC / "services" / "queue.py").read_text(encoding="utf-8")
    assert "omnia_api.workers.quality.gate_entity_app" in src
    assert "def enqueue_entity_gate" in src


def test_messages_enqueues_entity_gate_on_entity_template() -> None:
    """messages.py must enqueue the gate (de-orphaned) and scope it to the
    entity/fullstack templates that skip acceptance.evaluate."""
    src = (_SRC / "routers" / "messages.py").read_text(encoding="utf-8")
    assert "enqueue_entity_gate" in src
    # enqueue is scoped to the entity templates that bypass the acceptance gate
    assert re.search(
        r'project\.template in \("nextjs_entities", "fullstack"\)', src
    )
