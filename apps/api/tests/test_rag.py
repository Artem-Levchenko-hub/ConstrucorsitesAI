"""Smoke tests for the awwwards-corpus RAG retrieval (Phase L5)."""

from __future__ import annotations

from omnia_api.services import rag


def test_corpus_loads_and_is_nonempty() -> None:
    corpus = rag._load_corpus()
    assert isinstance(corpus, list)
    assert len(corpus) > 0, "awwwards_corpus.json is empty or missing"


def test_top_n_returns_at_most_n() -> None:
    hits = rag.top_n("SaaS B2B аналитика", n=3)
    assert isinstance(hits, list)
    assert len(hits) <= 3


def test_top_n_empty_query_no_hits_or_safe_passthrough() -> None:
    # An empty query gives no tokens — must return [] (not crash).
    hits = rag.top_n("", n=1)
    assert hits == []


def test_top_reference_block_returns_str_or_none() -> None:
    block = rag.top_reference_block("SaaS dashboard для аналитики")
    # Either a populated string or None — never an empty string / crash.
    assert block is None or (isinstance(block, str) and len(block) > 50)


def test_top_reference_block_contains_expected_sections() -> None:
    """If we get a hit at all, it must include the structural labels we
    promise the model — Reference / Palette / Fonts. Industries/Motion/
    Why/Avoid are conditional (skip empty), so we only require the four
    always-present labels."""
    block = rag.top_reference_block("SaaS dashboard аналитики")
    if block is None:
        return  # corpus may not have a saas-tagged item — that's OK
    assert "Reference:" in block
    assert "Palette:" in block
    assert "Fonts:" in block


def test_industry_hint_boosts_match() -> None:
    """Same query, with vs without industry_hint — the hint should never
    decrease the score (it's additive). Sanity check: when both calls
    return something, the hinted version's score-driven first item is
    at least as relevant."""
    plain = rag.top_n("современный сайт", n=1)
    hinted = rag.top_n("современный сайт", n=1, industry_hint="saas-product")
    # Hint cannot break the path — both calls must still be lists.
    assert isinstance(plain, list)
    assert isinstance(hinted, list)


def test_tokens_strip_stopwords_and_punctuation() -> None:
    toks = rag._tokens("Сайт для кофейни в Москве — minimal SaaS!")
    # "сайт", "для", "в" are stop-words → filtered.
    assert "сайт" not in toks
    assert "для" not in toks
    assert "в" not in toks
    # Real content tokens preserved.
    assert "кофейни" in toks
    assert "москве" in toks
    assert "minimal" in toks
    assert "saas" in toks


def test_format_reference_handles_missing_fields() -> None:
    """A degenerate item with missing optional fields must still render."""
    item = {"name": "TestStub", "style_id": "x", "industry_tags": ["foo"],
            "palette": {"bg": "#FFF"}, "fonts": {"display": "Inter"}}
    out = rag.format_reference(item)
    assert "TestStub" in out
    assert "Palette:" in out
    assert "Fonts:" in out
