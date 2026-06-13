"""Stranger-link first-paint gate tests (V4.0b).

Two layers, mirroring the data/wow gates:
  * pure-scorer tests — a hand-built observation dict → verdict, no browser;
    the known-good shape PASSES, each defect (auth wall, shell, slow paint)
    FAILS with its named class.
  * render tests — a real Chromium load of a static fixture: a shell page MUST
    fail (empty-shell), a content-rich page MUST pass. This is the adversarial
    teeth the V4.0b spec demands (a gate with no fixture-that-must-fail is a vibe).

Also pins the R-04 single-source contract: ``FIRST_PAINT_BUDGET_MS`` MUST exist as
a numeric constant in the shared ``render_settle`` module (exit 1 if missing) so a
gate cannot be satisfied by an arbitrary inline number.
"""

from __future__ import annotations

import pytest

from omnia_api.services import data_gate, first_paint_gate, render_settle
from omnia_api.services.first_paint_gate import (
    AUTH_WALL,
    EMPTY_SHELL,
    SHELL_TEXT_FLOOR,
    SLOW_FIRST_PAINT,
    evaluate_observation,
)

# ── R-04 single-source constant contract ───────────────────────────────────────


def test_first_paint_budget_is_a_shared_numeric_constant() -> None:
    """The budget MUST live in the shared render_settle module as a real number.

    V4.0b: "вшить T КОНСТАНТОЙ, не «напр. 3s»; exit 1 при отсутствии константы".
    """
    assert hasattr(render_settle, "FIRST_PAINT_BUDGET_MS")
    budget = render_settle.FIRST_PAINT_BUDGET_MS
    assert isinstance(budget, (int, float))
    assert budget > 0
    # the gate must consume the shared constant, not redeclare its own.
    assert first_paint_gate.FIRST_PAINT_BUDGET_MS is budget


# ── pure scorer: known-good passes ──────────────────────────────────────────────


def _good_obs(**over: object) -> dict:
    obs = {
        "url": "https://constructor.lead-generator.ru/p/sushi-restoran-08ce5f",
        "first_paint_ms": 1200.0,
        "text_count": 80,
        "rows": 8,
        "hero_visible": True,
        "cta_visible": True,
    }
    obs.update(over)
    return obs


def test_known_good_landing_passes() -> None:
    report = evaluate_observation(_good_obs())
    assert report.passed
    assert report.classes == ()
    assert report.rendered


def test_hero_plus_cta_with_zero_rows_passes() -> None:
    """A marketing landing (no catalog) still passes if hero+CTA are visible."""
    report = evaluate_observation(_good_obs(rows=0))
    assert report.passed


def test_catalog_rows_alone_passes_without_hero() -> None:
    """A seeded catalog ≥ MIN_ROWS passes even if the hero heuristic missed."""
    report = evaluate_observation(
        _good_obs(hero_visible=False, cta_visible=False, rows=data_gate.MIN_ROWS)
    )
    assert report.passed


# ── pure scorer: each defect fails with its named class ─────────────────────────


def test_auth_redirect_fails_auth_wall() -> None:
    report = evaluate_observation(
        _good_obs(url="https://constructor.lead-generator.ru/signin?next=/p/x")
    )
    assert not report.passed
    assert AUTH_WALL in report.classes


def test_shell_page_fails_empty_shell() -> None:
    report = evaluate_observation(
        _good_obs(text_count=SHELL_TEXT_FLOOR, rows=0, hero_visible=False, cta_visible=False)
    )
    assert not report.passed
    assert EMPTY_SHELL in report.classes


def test_no_content_signal_fails_empty_shell() -> None:
    """Plenty of text but no catalog and no hero+CTA = nothing worth sharing."""
    report = evaluate_observation(
        _good_obs(text_count=40, rows=0, hero_visible=True, cta_visible=False)
    )
    assert not report.passed
    assert EMPTY_SHELL in report.classes


def test_slow_first_paint_fails() -> None:
    over_budget = render_settle.FIRST_PAINT_BUDGET_MS + 1_500
    report = evaluate_observation(_good_obs(first_paint_ms=over_budget))
    assert not report.passed
    assert SLOW_FIRST_PAINT in report.classes


def test_paint_exactly_at_budget_passes() -> None:
    """Boundary: == budget is within budget (strictly-greater fails)."""
    report = evaluate_observation(
        _good_obs(first_paint_ms=render_settle.FIRST_PAINT_BUDGET_MS)
    )
    assert report.passed


def test_missing_paint_metric_does_not_fail_slow() -> None:
    """No FCP entry (None) is 'not measured' — content checks still govern."""
    report = evaluate_observation(_good_obs(first_paint_ms=None))
    assert SLOW_FIRST_PAINT not in report.classes
    assert report.passed


def test_unrendered_abstains() -> None:
    report = evaluate_observation({}, rendered=False)
    assert not report.rendered
    assert not report.passed  # ABSTAIN is not a pass


def test_multiple_defects_all_named() -> None:
    report = evaluate_observation(
        _good_obs(
            url="https://x/login",
            text_count=2,
            first_paint_ms=render_settle.FIRST_PAINT_BUDGET_MS + 9_000,
        )
    )
    assert set(report.classes) == {AUTH_WALL, EMPTY_SHELL, SLOW_FIRST_PAINT}


# ── render tests: real Chromium, adversarial fixture MUST fail ──────────────────

_CTA = (
    '<a href="/build" style="display:inline-block;padding:14px 28px;'
    'background:#4f46e5;color:#fff">Начать</a>'
)

_CONTENT_RICH_HTML = (
    "<!doctype html><html lang=ru><head><meta charset=utf-8>"
    "<title>Суши Сакура</title></head><body>"
    "<h1 style='font-size:48px'>Суши Сакура — доставка за 40 минут</h1>"
    "<p>Свежая рыба каждый день. Роллы, сеты, горячие блюда.</p>"
    "<p>Работаем с 10:00 до 23:00 по всему городу.</p>"
    "<p>Более 120 позиций в меню и комбо для компании.</p>"
    "<p>Бесплатная доставка от 1500 рублей.</p>"
    "<p>Собственная пекарня и кондитерская.</p>"
    "<p>Отзывы клиентов: 4.9 из 5 по 2000 оценок.</p>"
    f"{_CTA}"
    "</body></html>"
)

# A holding / placeholder shell: one heading-ish line, no real content. Exactly
# the "запускается" oblochka or scale-from-zero stub a stranger must NEVER get.
_SHELL_HTML = (
    "<!doctype html><html lang=ru><head><meta charset=utf-8>"
    "<title>...</title></head><body>"
    "<div>Запускается</div>"
    "</body></html>"
)


@pytest.mark.asyncio
async def test_render_content_rich_page_passes() -> None:
    report = await first_paint_gate.audit_files({"index.html": _CONTENT_RICH_HTML})
    if not report.rendered:
        pytest.skip("Chromium unavailable in this environment")
    assert report.passed, report.summary()


@pytest.mark.asyncio
async def test_render_shell_page_fails_empty_shell() -> None:
    report = await first_paint_gate.audit_files({"index.html": _SHELL_HTML})
    if not report.rendered:
        pytest.skip("Chromium unavailable in this environment")
    assert not report.passed, report.summary()
    assert EMPTY_SHELL in report.classes, report.summary()
