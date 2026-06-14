"""Catalog-realism gate — the RULE-10 demo-seeder wave as a permanent floor (V1.17).

"JS extracts, Python scores", so the whole rubric is exercised here with
hand-built observation dicts — no browser, no money. Each of the five realism
axes has a CLEAN case (it passes) and an ADVERSARIAL case (a planted defect that
must fire its axis), plus a known-good catalog that scores 5/5 and waiver /
abstain behaviour. The eight RULE-10 defect classes map onto these axes: an
absurd price (#2), a miscategorised title (#4), a generic/bare description (#5),
a broken image (#3), a past promo date (#7).
"""

from datetime import date

from omnia_api.services.catalog_coherence_gate import (
    CHECKS,
    DATE_FUTURE,
    IMAGE_RESOLVES,
    MIN_SCORE,
    PRICE_BAND,
    TITLE_CATEGORY,
    TITLE_DESCRIPTION,
    evaluate_observation,
    title_tokens,
)

TODAY = date(2026, 6, 14)


def _row(
    title="Товар",
    category="Витамины",
    description="Содержательное описание товара для покупателя.",
    price=1490,
    hasImage=True,
    imageSrc="data:image/svg+xml,%3Csvg%3E",
    imageBroken=False,
    promoDate="2026-08-15",
):
    return {
        "title": title,
        "category": category,
        "description": description,
        "price": price,
        "hasImage": hasImage,
        "imageSrc": imageSrc,
        "imageBroken": imageBroken,
        "promoDate": promoDate,
    }


def _clean_pharmacy():
    """A realistic pharmacy catalog: niche prices, coherent categories, per-item
    descriptions, resolving images, future promos. Scores 5/5, 0 findings."""
    return {
        "viewportWidth": 1440,
        "rows": [
            _row(title="Витамин C 900 мг", category="Витамины", price=590,
                 description="Антиоксидант и поддержка иммунитета."),
            _row(title="Витамин D3 2000 МЕ", category="Витамины", price=790,
                 description="Для крепких костей и хорошего настроения."),
            _row(title="Витамин E 400 МЕ", category="Витамины", price=640,
                 description="Защита клеток от окислительного стресса."),
            _row(title="Крем увлажняющий SPF30", category="Косметика", price=1290,
                 description="Дневной уход с защитой от солнца."),
            _row(title="Сыворотка с гиалуроном", category="Косметика", price=1890,
                 description="Глубокое увлажнение и сияние кожи."),
            _row(title="Маска тканевая ночная", category="Косметика", price=320,
                 description="Интенсивное восстановление за ночь."),
        ],
    }


def _evl(obs):
    return evaluate_observation(obs, today=TODAY)


# ── title_tokens helper ────────────────────────────────────────────────────────


def test_title_tokens_drops_digits_units_and_short_words():
    assert title_tokens("Витамин C 900 мг") == {"витамин"}


def test_title_tokens_lowercases_and_splits():
    toks = title_tokens("Крем увлажняющий SPF30")
    assert "крем" in toks and "увлажняющий" in toks


def test_title_tokens_empty_is_empty():
    assert title_tokens("") == set()
    assert title_tokens(None) == set()


# ── whole-gate: the clean catalog scores full ─────────────────────────────────


def test_clean_pharmacy_scores_full():
    rep = _evl(_clean_pharmacy())
    assert rep.rendered and rep.surface == "catalog"
    assert rep.score == len(CHECKS) == 5
    assert rep.passed
    assert rep.findings == ()


def test_clean_catalog_subscore_all_checks_true():
    sub = _evl(_clean_pharmacy()).subscore()
    assert sub["gate"] == "catalog"
    assert all(sub["checks"].values())
    assert sub["row_count"] == 6


# ── axis 1: price-band ─────────────────────────────────────────────────────────


def test_price_band_fires_on_absurd_outlier():
    obs = _clean_pharmacy()
    obs["rows"][0]["price"] = 197010  # the literal RULE-10 #2 regression
    rep = _evl(obs)
    assert PRICE_BAND in rep.classes
    assert rep.score == 4
    assert not rep.passed


def test_price_band_fires_on_non_positive():
    obs = _clean_pharmacy()
    obs["rows"][2]["price"] = 0
    assert PRICE_BAND in _evl(obs).classes


def test_price_band_clean_when_all_in_band():
    assert PRICE_BAND not in _evl(_clean_pharmacy()).classes


def test_price_band_no_false_positive_on_millions_catalog():
    """A real-estate catalog priced in millions is self-consistent — the median
    rides the niche, so no row is an outlier. Niche-agnostic, no taxonomy."""
    rows = [_row(title=f"Квартира {i}", category="Квартиры", price=p, promoDate="2026-09-01")
            for i, p in enumerate([4_500_000, 6_200_000, 8_900_000, 12_000_000, 5_400_000])]
    rep = _evl({"viewportWidth": 1440, "rows": rows})
    assert PRICE_BAND not in rep.classes


def test_price_band_skips_outlier_check_below_min_priced_rows():
    rows = [_row(price=500), _row(price=600), _row(price=900_000)]
    # only 3 priced rows (< _MIN_PRICED_ROWS) → no stable median → no outlier fire
    assert PRICE_BAND not in _evl({"viewportWidth": 1440, "rows": rows}).classes


# ── axis 2: title-category ─────────────────────────────────────────────────────


def test_title_category_fires_on_miscategorised_row():
    obs = _clean_pharmacy()
    obs["rows"][0]["category"] = "Косметика"  # "Витамин C" shelved under Косметика
    rep = _evl(obs)
    assert TITLE_CATEGORY in rep.classes
    assert not rep.passed


def test_title_category_clean_when_coherent():
    assert TITLE_CATEGORY not in _evl(_clean_pharmacy()).classes


def test_title_category_waived_below_min_rows():
    rows = [_row(title="Витамин C", category="Витамины"),
            _row(title="Крем", category="Косметика")]
    # < _MIN_CATEGORISED_ROWS → axis can't run → no finding
    assert TITLE_CATEGORY not in _evl({"viewportWidth": 1440, "rows": rows}).classes


# ── axis 3: title-description ──────────────────────────────────────────────────


def test_title_description_fires_on_bare_title():
    obs = _clean_pharmacy()
    obs["rows"][1]["description"] = ""
    assert TITLE_DESCRIPTION in _evl(obs).classes


def test_title_description_fires_on_copy_pasted_generic():
    same = "Отличный выбор."
    rows = [_row(title=f"Товар {i}", description=same, price=300 + i * 10)
            for i in range(6)]
    rep = _evl({"viewportWidth": 1440, "rows": rows})
    assert TITLE_DESCRIPTION in rep.classes


def test_title_description_clean_when_per_item():
    assert TITLE_DESCRIPTION not in _evl(_clean_pharmacy()).classes


# ── axis 4: image-resolves ─────────────────────────────────────────────────────


def test_image_resolves_fires_on_broken_decode():
    obs = _clean_pharmacy()
    obs["rows"][0]["imageBroken"] = True
    assert IMAGE_RESOLVES in _evl(obs).classes


def test_image_resolves_fires_on_empty_src():
    obs = _clean_pharmacy()
    obs["rows"][3]["imageSrc"] = ""
    assert IMAGE_RESOLVES in _evl(obs).classes


def test_image_resolves_fires_on_placeholder_marker():
    obs = _clean_pharmacy()
    obs["rows"][2]["imageSrc"] = "/_omnia/placeholder.png?data-omnia-gen=1"
    assert IMAGE_RESOLVES in _evl(obs).classes


def test_image_resolves_ignores_rows_without_image():
    obs = _clean_pharmacy()
    for r in obs["rows"]:
        r["hasImage"] = False
        r["imageSrc"] = ""
    # no <img> element at all → not "broken"; taste owns hero-imagery presence
    assert IMAGE_RESOLVES not in _evl(obs).classes


def test_image_resolves_clean_on_data_uri():
    assert IMAGE_RESOLVES not in _evl(_clean_pharmacy()).classes


# ── axis 5: date-future ────────────────────────────────────────────────────────


def test_date_future_fires_on_past_promo():
    obs = _clean_pharmacy()
    obs["rows"][0]["promoDate"] = "2026-01-01"  # before TODAY
    assert DATE_FUTURE in _evl(obs).classes


def test_date_future_clean_when_future():
    assert DATE_FUTURE not in _evl(_clean_pharmacy()).classes


def test_date_future_parses_dotted_dates():
    obs = _clean_pharmacy()
    obs["rows"][0]["promoDate"] = "01.02.2026"  # dotted, in the past
    assert DATE_FUTURE in _evl(obs).classes


def test_date_future_ignores_rows_without_date():
    obs = _clean_pharmacy()
    for r in obs["rows"]:
        r["promoDate"] = None
    assert DATE_FUTURE not in _evl(obs).classes


def test_date_future_uses_obs_now_when_today_unset():
    obs = _clean_pharmacy()
    obs["now"] = "2026-12-31"
    obs["rows"][0]["promoDate"] = "2026-08-15"  # future of TODAY, past of obs.now
    rep = evaluate_observation(obs)  # no today= → falls back to obs['now']
    assert DATE_FUTURE in rep.classes


# ── multiple defects compound ──────────────────────────────────────────────────


def test_multiple_defects_lower_score_independently():
    obs = _clean_pharmacy()
    obs["rows"][0]["price"] = 197010
    obs["rows"][1]["imageBroken"] = True
    obs["rows"][2]["promoDate"] = "2025-01-01"
    rep = _evl(obs)
    assert set(rep.classes) >= {PRICE_BAND, IMAGE_RESOLVES, DATE_FUTURE}
    assert rep.score == 2  # 5 - 3 failed axes


# ── surface / abstain semantics ────────────────────────────────────────────────


def test_no_catalog_waives():
    rep = _evl({"viewportWidth": 1440, "rows": []})
    assert rep.surface == "none"
    assert rep.passed  # waived, not failed
    assert "WAIVED" in rep.summary()


def test_single_row_is_not_a_catalog():
    rep = _evl({"viewportWidth": 1440, "rows": [_row()]})
    assert rep.surface == "none"


def test_abstain_when_not_rendered():
    rep = evaluate_observation({"rows": []}, rendered=False)
    assert not rep.rendered
    assert not rep.passed  # abstain ≠ pass
    assert "ABSTAIN" in rep.summary()


def test_subscore_round_trips_findings():
    obs = _clean_pharmacy()
    obs["rows"][0]["price"] = 197010
    sub = _evl(obs).subscore()
    assert sub["checks"][PRICE_BAND] is False
    assert any(f["check"] == PRICE_BAND for f in sub["findings"])


def test_min_score_is_full_clean_floor():
    assert MIN_SCORE == len(CHECKS)
