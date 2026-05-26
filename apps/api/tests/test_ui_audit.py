"""Tests for the Phase H UI audit runner.

Each fixture is crafted to isolate ONE failing check (with a small
tolerance for co-occurring failures, since e.g. a Lorem-heavy fixture
may also drag color or typography counts). The GOOD fixture must pass
all 10 checks unambiguously.
"""

from __future__ import annotations

import pytest

from omnia_api.services.ui_audit import AuditReport, audit, parse_styles


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Good fixture — must score 10/10.
# Two fonts (Inter + Playfair), 4 sizes, 3 weights, 6 colors, one gradient
# (cool), one styled button with px-6 py-3 (asymmetric, 44 px+), all <svg>
# share stroke-width="2", all images have alt, real prose copy, no toggles.
HTML_GOOD = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=Playfair+Display:wght@700&display=swap">
  <style>
    body { color: #1a1a1a; background: #ffffff; }
    .accent { color: #2563eb; }
    .muted { color: #64748b; }
    .surface { background: #f8fafc; }
    .gradient-hero { background: linear-gradient(135deg, #2563eb 0%, #4f46e5 100%); }
  </style>
</head>
<body>
  <header class="py-16">
    <h1 class="text-5xl font-bold">Современный конструктор сайтов</h1>
    <p class="text-base font-normal">
      Опишите бизнес — получите готовый сайт за минуту. Платите только за
      реально использованные токены. Бэкенд, домен и деплой включены.
    </p>
    <button class="bg-accent text-white px-6 py-3 rounded-lg font-medium">
      Начать бесплатно
    </button>
    <img src="hero.jpg" alt="Скриншот интерфейса конструктора Omnia.AI">
  </header>
  <section class="py-16">
    <h2 class="text-3xl font-bold">Как это работает</h2>
    <p class="text-sm">Три шага от идеи до запуска.</p>
    <svg width="24" height="24" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>
    <svg width="24" height="24" stroke-width="2"><path d="M5 12l5 5L20 7"/></svg>
    <form>
      <label for="email">Email</label>
      <input id="email" type="email" class="h-12 px-4">
      <button class="bg-accent text-white px-6 py-3 rounded-lg font-medium">
        Подписаться
      </button>
    </form>
  </section>
  <footer class="py-16">
    <p class="text-xs">© 2026 Omnia.AI</p>
  </footer>
</body>
</html>
"""

# Lorem-only failure (everything else stays clean).
HTML_LOREM_FAIL = HTML_GOOD.replace(
    "Опишите бизнес — получите готовый сайт за минуту. Платите только за\n"
    "      реально использованные токены. Бэкенд, домен и деплой включены.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
    "Sed do eiusmod tempor incididunt ut labore.",
)

# Typography failure: 3 distinct font families via <link>.
HTML_TYPO_FAIL = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Playfair+Display&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto+Mono&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Merriweather&display=swap">
  <style>
    body { color: #1a1a1a; background: #ffffff; }
  </style>
</head>
<body>
  <header class="py-16">
    <h1 class="text-5xl font-bold">Заголовок страницы</h1>
    <p class="text-base">Нормальный человеческий текст без плейсхолдеров.</p>
    <button class="bg-accent text-white px-6 py-3 rounded-lg">CTA</button>
    <img src="x.jpg" alt="Картинка">
  </header>
  <section class="py-16"><p>Контент</p></section>
  <footer class="py-16"><p>Подвал</p></footer>
</body>
</html>
"""

# Color failure: 10+ distinct hex colors.
HTML_COLOR_FAIL = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&display=swap">
  <style>
    .c1 { color: #ff0000; }
    .c2 { color: #00ff00; }
    .c3 { color: #0000ff; }
    .c4 { color: #ffff00; }
    .c5 { color: #ff00ff; }
    .c6 { color: #00ffff; }
    .c7 { color: #800080; }
    .c8 { color: #808000; }
    .c9 { color: #008080; }
    .c10 { color: #800000; }
    .c11 { color: #008000; }
    .c12 { color: #000080; }
  </style>
</head>
<body>
  <header class="py-16">
    <h1 class="text-5xl font-bold">Заголовок</h1>
    <p class="text-base">Нормальный человеческий текст без плейсхолдеров.</p>
    <button class="bg-accent text-white px-6 py-3 rounded-lg">CTA</button>
    <img src="x.jpg" alt="К">
  </header>
  <section class="py-16"><p>Контент</p></section>
  <footer class="py-16"><p>Подвал</p></footer>
</body>
</html>
"""

# Button failure: symmetric padding (px-2 py-3), too-short height (h-6).
HTML_BUTTON_FAIL = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&display=swap">
  <style>
    body { color: #1a1a1a; background: #ffffff; }
    .accent { color: #2563eb; }
  </style>
</head>
<body>
  <header class="py-16">
    <h1 class="text-5xl font-bold">Заголовок</h1>
    <p class="text-base">Нормальный человеческий текст без плейсхолдеров.</p>
    <button class="bg-accent text-white px-2 py-3 h-6 rounded-lg">CTA</button>
    <img src="x.jpg" alt="К">
  </header>
  <section class="py-16"><p>Контент</p></section>
  <footer class="py-16"><p>Подвал</p></footer>
</body>
</html>
"""

# Accessibility failure: <img> without alt.
HTML_A11Y_FAIL = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&display=swap">
  <style>
    body { color: #1a1a1a; background: #ffffff; }
    .accent { color: #2563eb; }
  </style>
</head>
<body>
  <header class="py-16">
    <h1 class="text-5xl font-bold">Заголовок</h1>
    <p class="text-base">Нормальный человеческий текст без плейсхолдеров.</p>
    <button class="bg-accent text-white px-6 py-3 rounded-lg">CTA</button>
    <img src="x.jpg">
  </header>
  <section class="py-16"><p>Контент</p></section>
  <footer class="py-16"><p>Подвал</p></footer>
</body>
</html>
"""

# Compound failure: 4-5 issues stacked.
# - Lorem ipsum copy
# - 3+ font families
# - 12+ colors
# - <img> without alt
# - Symmetric button padding
HTML_ALL_FAIL = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Playfair+Display&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto+Mono&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Merriweather&display=swap">
  <style>
    .c1 { color: #ff0000; }
    .c2 { color: #00ff00; }
    .c3 { color: #0000ff; }
    .c4 { color: #ffff00; }
    .c5 { color: #ff00ff; }
    .c6 { color: #00ffff; }
    .c7 { color: #800080; }
    .c8 { color: #808000; }
    .c9 { color: #008080; }
    .c10 { color: #800000; }
    .c11 { color: #008000; }
  </style>
</head>
<body>
  <header class="py-16">
    <h1 class="text-5xl font-bold">Lorem ipsum dolor sit amet</h1>
    <p class="text-base">Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
    <button class="bg-accent text-white px-2 py-2 h-6 rounded-lg">CTA</button>
    <img src="x.jpg">
  </header>
  <section class="py-16"><p>Контент</p></section>
  <footer class="py-16"><p>Подвал</p></footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_audit_good_html_scores_perfect() -> None:
    report = audit({"index.html": HTML_GOOD})
    assert isinstance(report, AuditReport)
    assert report.score == 10, (
        f"Expected 10/10 on GOOD fixture; got {report.score}. "
        f"Failures: {[(f.check_id, f.evidence[:80]) for f in report.failures]}"
    )
    assert report.failures == ()
    assert report.max == 10


@pytest.mark.parametrize(
    "html,expected_failing_check",
    [
        (HTML_LOREM_FAIL, "no_lorem_ipsum"),
        (HTML_TYPO_FAIL, "typography_count"),
        (HTML_COLOR_FAIL, "color_count"),
        (HTML_BUTTON_FAIL, "button_rules"),
        (HTML_A11Y_FAIL, "accessibility"),
    ],
)
def test_audit_isolates_single_failure(
    html: str, expected_failing_check: str
) -> None:
    report = audit({"index.html": html})
    failing_ids = {f.check_id for f in report.failures}
    assert expected_failing_check in failing_ids, (
        f"Expected {expected_failing_check} in failures; got {failing_ids}. "
        f"Per-check: {report.per_check}"
    )
    # Tolerance: at most 2 failures in an "isolated-failure" fixture.
    assert len(failing_ids) <= 2, (
        f"Isolated fixture leaked into other checks: {failing_ids}. "
        f"Evidence: {[(f.check_id, f.evidence[:60]) for f in report.failures]}"
    )


def test_audit_score_compounds() -> None:
    report = audit({"index.html": HTML_ALL_FAIL})
    assert report.score <= 6, (
        f"Stacked-violation fixture should score ≤ 6; got {report.score}. "
        f"Per-check: {report.per_check}"
    )


def test_per_check_dict_keys_match_10() -> None:
    report = audit({"index.html": HTML_GOOD})
    assert len(report.per_check) == 10
    expected = {
        "typography_count",
        "interactive_sizes_consistency",
        "grid_alignment",
        "color_count",
        "gradient_discipline",
        "button_rules",
        "icon_family_discipline",
        "accessibility",
        "no_lorem_ipsum",
        "no_dark_patterns",
    }
    assert set(report.per_check.keys()) == expected


def test_failure_severity_is_classified() -> None:
    """Critical (a11y, lorem) > major > minor — sanity check the labels."""
    report = audit({"index.html": HTML_LOREM_FAIL})
    lorem_failure = next(
        (f for f in report.failures if f.check_id == "no_lorem_ipsum"), None
    )
    assert lorem_failure is not None
    assert lorem_failure.severity == "critical"


def test_parse_styles_finds_fonts_and_colors() -> None:
    parsed = parse_styles(HTML_GOOD)
    fams_lower = {f.lower() for f in parsed["font_families"]}
    assert any("inter" in f for f in fams_lower)
    assert any("playfair" in f for f in fams_lower)
    # Should have picked up some hex colors
    assert len(parsed["colors"]) >= 4


def test_dark_pattern_pre_checked_optout() -> None:
    html = """
    <html><body>
      <link href="https://fonts.googleapis.com/css2?family=Inter">
      <header class="py-16"><h1 class="text-3xl font-bold">Заголовок</h1>
        <button class="bg-accent text-white px-6 py-3">CTA</button>
        <img src="x" alt="К">
      </header>
      <section class="py-16">
        <form>
          <input id="news" type="checkbox" checked>
          <label for="news">Отказаться от рассылки (opt-out)</label>
        </form>
      </section>
      <footer class="py-16"><p>©</p></footer>
    </body></html>
    """
    report = audit({"index.html": html})
    failing_ids = {f.check_id for f in report.failures}
    assert "no_dark_patterns" in failing_ids


def test_no_buttons_doesnt_fail_button_rules() -> None:
    """A page without any buttons is vacuously passing button_rules."""
    html = """
    <html><head><link href="https://fonts.googleapis.com/css2?family=Inter"></head>
    <body>
      <header class="py-16">
        <h1 class="text-3xl font-bold">Просто статья</h1>
        <p class="text-base">Текст без кнопок.</p>
        <img src="x" alt="К">
      </header>
      <section class="py-16"><p>Параграф</p></section>
      <footer class="py-16"><p>©</p></footer>
    </body></html>
    """
    report = audit({"index.html": html})
    assert report.per_check["button_rules"] is True
