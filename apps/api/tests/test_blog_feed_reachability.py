"""Acceptance-lock for BS-14 (dogfood run 2026-06-16): a multi-article blog feed
generated as freeform-static renders article cards as NON-CLICKABLE dead-ends.

Live repro (dogfood-travel-blog-029341, owner 2aa9b1a2, skip_clarify=true):
prompt «блог о путешествиях … отдельная страница для каждой статьи …». The
writer escalated nothing (template=blank / gen_mode=freeform), wrote FULL content
for exactly ONE featured article (an in-page ``#article-kyoto`` section reached
from the hero CTA), and emitted the OTHER FOUR articles as ``<article
class="article-card hover-lift">`` teaser cards with their own title / excerpt /
author / read-time but **no ``<a>`` wrapper, no href, no onclick** — so 4 of 5
requested articles are unreadable (Playwright: ``hasLink=False, cursor=auto`` on
all four). The category filter (JS) and subscribe form DO work; this lock is only
about reachability of the article content.

Two layers are pinned here:

1. The freeform writer prompt already FORBIDS this (prompt_builder.py:499-500
   «кнопка-обманка без обработчика», «пункт меню на несуществующий id») and tells
   it to make multi-page items in-page anchors (prompt_builder.py:496-497) — yet
   the writer violated its own rule. That layer is prompt/LLM-probabilistic.

2. The deterministic safety net is BLIND to it: ``find_dead_links`` only inspects
   ``<a href>`` for placeholder/missing-anchor hrefs (link_validator.py:24-46). An
   interactive-STYLED card that has no ``<a>`` at all is invisible to it, so the
   acceptance vision flagged the two footer ``href="#"`` links but never the four
   actionless article cards. This file locks that gap.

Fix is generator-archetype / prompt level (a blog/content-collection that makes
every feed item reachable, or a per-article in-page section + card→anchor link) +
base-rebuild + regen-verify across niches; a deterministic auto-repair would have
nowhere correct to point 4 cards at 1 article → false-positive risk. So this is a
PROPOSAL (P-BLOG), shipped as a lock, not a blind fix. See
docs/plans/2026-06-16-dogfood-eval-routine.md.
"""

from __future__ import annotations

import pytest

from omnia_api.services.link_validator import find_dead_links

# Faithful reduction of the live output: a featured article reachable via an
# in-page anchor CTA (compliant) + a feed of teaser cards that LOOK clickable
# (`article-card hover-lift`, heading, excerpt, byline) but carry no link/handler.
_BLOG_FEED_HTML = """
<section id="hero">
  <a href="#article-kyoto" class="btn-primary">Читать историю</a>
</section>
<section id="journal">
  <article class="article-card hover-lift" data-category="asia">
    <h3>Каппадокия вне шаров</h3>
    <p>Пещерные монастыри и каньоны без единого человека.</p>
    <span>Елена Власова</span><span>14 мин чтения</span>
  </article>
  <article class="article-card hover-lift" data-category="europe">
    <h3>Дороги Исландии: Инструкция по туманам</h3>
    <p>Как арендовать внедорожник и читать знаки погоды.</p>
    <span>Алексей Макаров</span><span>8 мин чтения</span>
  </article>
  <article class="article-card hover-lift" data-category="lifehacks">
    <h3>Искусство собирать рюкзак</h3>
    <p>7 кг на полгода.</p>
    <span>Мария Соколова</span><span>6 мин чтения</span>
  </article>
</section>
<section id="article-kyoto">
  <h2>Одиночество в Киото</h2>
  <p>Настоящий Киото пахнет сырым мхом...</p>
</section>
"""


def test_actionless_article_cards_are_invisible_to_dead_link_finder() -> None:
    """GREEN EVIDENCE (today): the deterministic net does NOT see the dead-end
    cards. The only reachable link (`#article-kyoto`) resolves to a real section,
    so `find_dead_links` reports ZERO issues — yet 4 of 5 articles are unreadable.
    This is exactly the blind spot: link-absent interactive cards never register.
    """
    issues = find_dead_links({"index.html": _BLOG_FEED_HTML})
    assert issues == [], (
        "Today find_dead_links only inspects <a href>, so actionless article "
        f"cards produce no issue. Got: {issues}"
    )

    # And there genuinely ARE multiple article teasers with no way in:
    card_count = _BLOG_FEED_HTML.count('class="article-card')
    reachable = _BLOG_FEED_HTML.count("<a ")
    assert card_count >= 3 and reachable == 1, (
        "Sanity: a multi-card feed with a single reachable article — the live shape."
    )


def _count_actionless_interactive_cards(html: str) -> int:
    """A future deterministic check would flag interactive-STYLED cards (article
    feed items) that carry no <a> and no onclick. Not yet implemented — this stub
    mirrors the intended signal so the xfail lock has something to assert against.
    """
    # Intentionally returns 0 today: there is no such check in link_validator.
    return 0


@pytest.mark.xfail(
    strict=False,
    reason="BS-14 / PROPOSAL P-BLOG: blog feed cards must be reachable. XPASS when "
    "the generator makes every article reachable OR a validator flags actionless "
    "interactive cards.",
)
def test_blog_feed_article_cards_should_be_reachable() -> None:
    """LOCK: every article teaser in a feed must be reachable (its own page, an
    in-page section, or at least a link). When the fix lands, either no actionless
    interactive cards survive generation, or the validator surfaces them.
    """
    flagged = _count_actionless_interactive_cards(_BLOG_FEED_HTML)
    # The live output had FOUR actionless cards; the reduced fixture has three.
    assert flagged >= 3, (
        "A multi-article blog feed shipped 3+ non-clickable article cards and "
        "nothing flagged them — every article must be reachable."
    )
