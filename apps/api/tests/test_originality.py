"""Originality fingerprint + cross-project dedup (Phase 11, Sprint 4)."""

import io

from PIL import Image

from omnia_api.core import redis as redis_mod
from omnia_api.services import originality


def _grad_png(reverse: bool) -> bytes:
    """A horizontal gradient PNG. reverse flips left/right → maximally different
    dHash (left>right everywhere vs left<right everywhere)."""
    img = Image.new("L", (64, 48))
    px = img.load()
    for x in range(64):
        v = (((63 - x) if reverse else x) * 4) % 256
        for y in range(48):
            px[x, y] = v
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _FakeRedis:
    def __init__(self, entries):
        self.entries = list(entries)

    async def lrange(self, key, start, stop):
        return list(self.entries)

    async def lpush(self, key, val):
        self.entries.insert(0, val)

    async def ltrim(self, key, start, stop):
        self.entries = self.entries[start : stop + 1]


def test_fingerprint_deterministic():
    a = _grad_png(False)
    assert originality.fingerprint(a) == originality.fingerprint(a)


def test_fingerprint_distinguishes_layouts():
    fa = originality.fingerprint(_grad_png(False))
    fb = originality.fingerprint(_grad_png(True))
    assert fa is not None and fb is not None
    assert fa != fb
    assert originality._hamming(fa, fb) > 20


def test_fingerprint_failsoft_on_garbage():
    assert originality.fingerprint(b"not a real png") is None


async def test_nearest_skips_same_project(monkeypatch):
    fp = 0b101010
    monkeypatch.setattr(redis_mod, "get_redis", lambda: _FakeRedis([f"projX:{fp}"]))
    # Only this project's own entry in the pool → nothing to be "too similar" to.
    assert await originality.nearest_cross_project_distance("projX", fp) == 64


async def test_nearest_finds_other_project(monkeypatch):
    monkeypatch.setattr(redis_mod, "get_redis", lambda: _FakeRedis(["projY:3"]))
    # hamming(0, 0b11) == 2
    assert await originality.nearest_cross_project_distance("projX", 0) == 2


async def test_failsoft_when_redis_down(monkeypatch):
    def _boom():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(redis_mod, "get_redis", _boom)
    assert await originality.nearest_cross_project_distance("p", 123) == 64


async def test_issue_fires_on_cross_project_duplicate(monkeypatch):
    png = _grad_png(False)
    fp = originality.fingerprint(png)
    monkeypatch.setattr(redis_mod, "get_redis", lambda: _FakeRedis([f"other:{fp}"]))
    out_fp, issue = await originality.originality_issue("mine", png, max_distance=10)
    assert out_fp == fp
    assert issue is not None
    assert "оригинальность" in issue


async def test_issue_silent_when_pool_empty(monkeypatch):
    monkeypatch.setattr(redis_mod, "get_redis", lambda: _FakeRedis([]))
    _fp, issue = await originality.originality_issue(
        "mine", _grad_png(False), max_distance=10
    )
    assert issue is None


async def test_remember_pushes_tagged_entry(monkeypatch):
    fake = _FakeRedis([])
    monkeypatch.setattr(redis_mod, "get_redis", lambda: fake)
    await originality.remember("projZ", 42)
    assert fake.entries == ["projZ:42"]
