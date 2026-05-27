"""Tiny RAG over the awwwards corpus (Phase L5).

The curated corpus at ``apps/api/data/awwwards_corpus.json`` holds
~10-30 hand-picked awwwards / studio-grade reference pages with their
palette, fonts, motion signature, and "what makes it work" notes.

The whole thing is < 30 KB → no embedding model is needed. A bag-of-
tokens scorer over ``industry_tags`` + ``style_id`` + ``name`` matches
prompts to references reliably enough at this corpus size. If the
corpus grows past a few hundred items we'll swap this for a real
embedding pipeline — the public API (``top_n`` + ``format_reference``)
stays the same.

Used by ``services/lean_prompt.build_lean_system_prompt`` to inject one
high-quality reference into the prompt under a ``<rag_reference>`` tag,
so the model has a concrete awwwards-grade pattern to mirror instead
of generating from prose alone.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# services/rag.py → parents: [0]=services, [1]=omnia_api, [2]=src, [3]=api.
# Corpus lives at apps/api/data/awwwards_corpus.json — i.e. parents[3]/data.
_CORPUS_PATH = Path(__file__).resolve().parents[3] / "data" / "awwwards_corpus.json"


# ─── Token utilities ─────────────────────────────────────────────────────

# Strip Russian + English non-word chars; lowercase; split on whitespace.
_TOKEN_SPLIT = re.compile(r"[^\wЀ-ӿ]+", re.UNICODE)

# Very common Russian stop-words that pollute matches (added on demand —
# the corpus is small so over-pruning matters more than under-pruning).
_STOP = frozenset({
    "и", "в", "на", "для", "под", "по", "с", "со", "из", "к", "ко", "у",
    "о", "об", "обо", "за", "до", "от", "при", "над", "про", "без", "через",
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with",
    "site", "сайт", "лендинг", "page", "страница",
})


def _tokens(text: str) -> set[str]:
    return {
        t for t in (s.lower() for s in _TOKEN_SPLIT.split(text or ""))
        if t and t not in _STOP and len(t) > 1
    }


# ─── Corpus loading ──────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_corpus() -> list[dict[str, Any]]:
    """Lazy-load the JSON corpus. Returns an empty list if the file is
    missing — the caller treats that as "no reference available" and
    falls through to the no-RAG path."""
    if not _CORPUS_PATH.is_file():
        log.warning("awwwards_corpus.json not found at %s — RAG disabled", _CORPUS_PATH)
        return []
    try:
        raw = _CORPUS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            log.error("awwwards_corpus.json: expected list, got %s", type(data).__name__)
            return []
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Failed to load awwwards_corpus.json: %r", exc)
        return []


def _item_haystack(item: dict[str, Any]) -> set[str]:
    """Build the token set used for matching against the user query.

    Weights are baked into the score function — here we just collect the
    distinctive tokens (industry tags + style id + name). We deliberately
    skip ``what_makes_it_work`` / ``motion_signature`` because they're
    long-form prose that produces too many incidental matches.
    """
    parts: list[str] = []
    for tag in item.get("industry_tags") or []:
        parts.append(str(tag).replace("-", " "))
    style_id = item.get("style_id") or ""
    parts.append(str(style_id).replace("-", " "))
    name = item.get("name") or ""
    parts.append(str(name))
    return _tokens(" ".join(parts))


# ─── Public API ──────────────────────────────────────────────────────────


def top_n(query: str, n: int = 1, *, industry_hint: str | None = None) -> list[dict[str, Any]]:
    """Rank corpus items by token overlap with the query.

    ``industry_hint`` (if provided) is treated as an extra high-weight
    token — the classifier already resolved a vibe, and the corpus
    item's industry_tags often agree on the word ("saas", "fintech",
    "restaurant"). Weight 3× normal token match.

    Returns up to ``n`` items, **best first**. Empty list if no item
    scored > 0 (the caller falls through to the no-RAG path)."""

    corpus = _load_corpus()
    if not corpus:
        return []

    q_tokens = _tokens(query)
    hint_tokens = _tokens(industry_hint or "")

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in corpus:
        item_tokens = _item_haystack(item)
        if not item_tokens:
            continue
        # Bag-of-words overlap. Hint tokens score 3× normal.
        overlap = len(item_tokens & q_tokens)
        hint_overlap = len(item_tokens & hint_tokens) * 3
        total = overlap + hint_overlap
        if total > 0:
            scored.append((total, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:max(1, n)]]


def format_reference(item: dict[str, Any]) -> str:
    """Render one corpus item as a compact, model-readable reference.

    Designed to be inlined inside a ``<rag_reference>`` XML tag in the
    lean system prompt. Roughly 200-400 chars per reference — small
    enough to keep the prompt under budget, dense enough to actually
    inform the design decisions.
    """
    name = item.get("name") or "—"
    style = item.get("style_id") or "—"
    industries = ", ".join((item.get("industry_tags") or [])[:4])
    palette = item.get("palette") or {}
    palette_str = ", ".join(
        f"{k}={v}" for k, v in palette.items() if isinstance(v, str)
    )[:200]
    fonts = item.get("fonts") or {}
    fonts_str = ", ".join(f"{k}={v}" for k, v in fonts.items() if isinstance(v, str))
    motion = (item.get("motion_signature") or [])[:3]
    motion_str = "; ".join(motion)
    notes = item.get("what_makes_it_work") or ""
    anti = (item.get("anti_patterns_avoided") or [])[:3]
    anti_str = "; ".join(anti)

    lines = [
        f"Reference: {name} (style={style})",
        f"  Industries: {industries}",
        f"  Palette: {palette_str}",
        f"  Fonts: {fonts_str}",
    ]
    if motion_str:
        lines.append(f"  Motion: {motion_str}")
    if notes:
        lines.append(f"  Why it works: {notes[:400]}")
    if anti_str:
        lines.append(f"  Avoid: {anti_str}")
    return "\n".join(lines)


def top_reference_block(
    query: str, *, industry_hint: str | None = None
) -> str | None:
    """One-call helper for the lean prompt: returns the formatted reference
    block (or ``None`` if no match scored > 0)."""
    hits = top_n(query, n=1, industry_hint=industry_hint)
    if not hits:
        return None
    return format_reference(hits[0])


__all__ = [
    "format_reference",
    "top_n",
    "top_reference_block",
]
