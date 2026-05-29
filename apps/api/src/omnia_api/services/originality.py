"""Anti-generic originality fingerprint (Phase 11, Sprint 4).

Goal: raise the *spread between sites*. We fingerprint every accepted freeform
page (a 64-bit perceptual dHash of its screenshot) into a global pool, and when
a new page comes out near-identical to a DIFFERENT project's page we feed that
back as a repair issue ("make a genuinely different composition"). Similarity
to the SAME project is expected (iterative editing) and never penalised.

Everything fails SOFT (R-10): no Pillow, no Redis, or a decode error → no
fingerprint and no penalty, so generation never breaks on this signal.
"""

from __future__ import annotations

import logging
from io import BytesIO

log = logging.getLogger(__name__)

# One flat Redis list of "<project_id>:<dhash_int>" entries, newest first,
# capped. Tagging by project lets us skip a project's own history when scoring
# cross-project originality.
_POOL_KEY = "omnia:fp:pool"
_POOL_CAP = 800
_HASH_BITS = 64


def fingerprint(png: bytes) -> int | None:
    """64-bit difference-hash (dHash) of a PNG screenshot, or None (fail-soft).

    dHash: greyscale → resize to 9×8 → for each row, bit = (left pixel >
    right pixel). Robust to scale/compression, sensitive to layout/composition
    — exactly what "do these two pages look alike?" needs.
    """
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        img = Image.open(BytesIO(png)).convert("L").resize((9, 8))
        px = list(img.getdata())
        bits = 0
        for row in range(8):
            base = row * 9
            for col in range(8):
                bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
        return bits
    except Exception as exc:
        log.warning("originality: fingerprint failed (fail-soft): %r", exc)
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


async def nearest_cross_project_distance(project_id: str, fp: int) -> int:
    """Min Hamming distance from `fp` to any OTHER project's remembered page.

    Returns `_HASH_BITS` (max) when the pool is empty/unavailable or holds only
    this project's own entries — i.e. "nothing to be too similar to".
    """
    try:
        from omnia_api.core.redis import get_redis

        entries = await get_redis().lrange(_POOL_KEY, 0, _POOL_CAP)
    except Exception as exc:
        log.warning("originality: pool read failed (fail-soft): %r", exc)
        return _HASH_BITS

    pid = str(project_id)
    best = _HASH_BITS
    for entry in entries:
        tag, _, val = str(entry).partition(":")
        if tag == pid:
            continue  # same project — iterating on it is allowed, not "generic"
        try:
            other = int(val)
        except ValueError:
            continue
        d = _hamming(fp, other)
        if d < best:
            best = d
            if best == 0:
                break
    return best


async def remember(project_id: str, fp: int) -> None:
    """Add an accepted page's fingerprint to the global pool (fail-soft)."""
    try:
        from omnia_api.core.redis import get_redis

        r = get_redis()
        await r.lpush(_POOL_KEY, f"{project_id}:{fp}")
        await r.ltrim(_POOL_KEY, 0, _POOL_CAP - 1)
    except Exception as exc:
        log.warning("originality: remember failed (ignored): %r", exc)


async def originality_issue(
    project_id: str, png: bytes, *, max_distance: int
) -> tuple[int | None, str | None]:
    """Fingerprint `png` and return (fingerprint, repair_issue_or_None).

    The issue fires only when the page is within `max_distance` of a different
    project's page — a concrete "you produced a near-duplicate" signal the
    repair loop can act on. Returns (None, None) when fingerprinting is
    unavailable.
    """
    fp = fingerprint(png)
    if fp is None:
        return None, None
    dist = await nearest_cross_project_distance(project_id, fp)
    if dist <= max_distance:
        return fp, (
            f"[оригинальность] вёрстка слишком похожа на ранее сгенерированный сайт "
            f"другого проекта (визуальное расстояние {dist}/{_HASH_BITS}). Сделай "
            f"принципиально другую композицию: другой герой, другой ритм и порядок "
            f"секций, другие визуальные приёмы — не повторяй типовой шаблон."
        )
    return fp, None


__all__ = [
    "fingerprint",
    "nearest_cross_project_distance",
    "originality_issue",
    "remember",
]
