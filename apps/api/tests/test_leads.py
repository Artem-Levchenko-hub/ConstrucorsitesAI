"""Lead-capture endpoint (P-LEAD) — the public form SINK.

Closes the bug where a generated lead form showed «Спасибо» and silently
discarded the data: the public site now POSTs to ``/p/<slug>/lead`` and the
submission is stored (owner reads it from the «Заявки» inbox). These tests drive
``submit_lead`` directly with fakes (no Postgres) — the route's validation,
field/size caps, throttle, and fail-open behaviour.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.routers import public


class _Result:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def scalar_one_or_none(self) -> object:
        return self._obj


class _Session:
    def __init__(self, project: object) -> None:
        self._project = project
        self.added: list[object] = []
        self.committed = False

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._project)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


class _Redis:
    def __init__(self, count: int = 1) -> None:
        self._count = count

    async def incr(self, _k: str) -> int:
        return self._count

    async def expire(self, _k: str, _t: int) -> None:
        return None


class _Request:
    def __init__(self, body: bytes, host: str = "1.2.3.4") -> None:
        self._body = body
        self.client = SimpleNamespace(host=host)

    async def body(self) -> bytes:
        return self._body


def _json(payload: object) -> bytes:
    return json.dumps(payload).encode()


def _project() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), slug="cafe-abc123")


async def test_lead_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(1))
    sess = _Session(_project())
    req = _Request(_json({"name": "Иван", "phone": "+79001234567", "_source": "hero"}))
    resp = await public.submit_lead("cafe-abc123", req, sess)  # type: ignore[arg-type]
    assert resp.status_code == 204
    assert sess.committed and len(sess.added) == 1
    lead = sess.added[0]
    assert lead.data == {"name": "Иван", "phone": "+79001234567"}
    assert lead.source == "hero"


async def test_lead_unknown_project_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(1))
    sess = _Session(None)
    with pytest.raises(ApiError) as ei:
        await public.submit_lead("nope", _Request(_json({"name": "x"})), sess)  # type: ignore[arg-type]
    assert ei.value.status_code == 404
    assert not sess.committed


async def test_lead_empty_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(1))
    sess = _Session(_project())
    with pytest.raises(ApiError) as ei:
        await public.submit_lead("cafe-abc123", _Request(_json({})), sess)  # type: ignore[arg-type]
    assert ei.value.status_code == 400
    assert not sess.committed


async def test_lead_non_dict_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(1))
    sess = _Session(_project())
    with pytest.raises(ApiError) as ei:
        await public.submit_lead("cafe-abc123", _Request(_json(["a", "b"])), sess)  # type: ignore[arg-type]
    assert ei.value.status_code == 400


async def test_lead_oversized_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(1))
    sess = _Session(_project())
    big = _Request(b"x" * (public._LEAD_MAX_BYTES + 1))
    with pytest.raises(ApiError) as ei:
        await public.submit_lead("cafe-abc123", big, sess)  # type: ignore[arg-type]
    assert ei.value.status_code == 413


async def test_lead_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(public._LEAD_RATE_MAX + 1))
    sess = _Session(_project())
    with pytest.raises(ApiError) as ei:
        await public.submit_lead("cafe-abc123", _Request(_json({"name": "x"})), sess)  # type: ignore[arg-type]
    assert ei.value.status_code == 429
    assert not sess.committed


async def test_lead_field_and_value_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public, "get_redis", lambda: _Redis(1))
    sess = _Session(_project())
    payload = {"long": "z" * (public._LEAD_MAX_VALUE_LEN + 50)}
    payload.update({f"f{i}": "v" for i in range(public._LEAD_MAX_FIELDS + 10)})
    await public.submit_lead("cafe-abc123", _Request(_json(payload)), sess)  # type: ignore[arg-type]
    lead = sess.added[0]
    assert len(lead.data) <= public._LEAD_MAX_FIELDS
    assert len(lead.data["long"]) == public._LEAD_MAX_VALUE_LEN


async def test_lead_redis_failopen(monkeypatch: pytest.MonkeyPatch) -> None:
    # Redis down → the throttle must NOT block a real lead (fail-open).
    def _boom() -> object:
        raise RuntimeError("redis down")

    monkeypatch.setattr(public, "get_redis", _boom)
    sess = _Session(_project())
    resp = await public.submit_lead("cafe-abc123", _Request(_json({"name": "x"})), sess)  # type: ignore[arg-type]
    assert resp.status_code == 204
    assert sess.committed
