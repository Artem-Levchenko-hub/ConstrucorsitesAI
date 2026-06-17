from omnia_api.services.link_validator import (
    find_dead_links,
    repair_dead_links_inline,
    repair_orphaned_anchors_inline,
)


def test_flags_placeholder_hrefs() -> None:
    files = {
        "index.html": (
            '<a href="#">Кнопка</a>'
            '<a href="">Пусто</a>'
            '<a href="javascript:void(0)">JS</a>'
        )
    }
    assert len(find_dead_links(files)) == 3


def test_flags_anchor_without_matching_id() -> None:
    files = {"index.html": '<a href="#pricing">Цены</a>'}
    assert find_dead_links(files)


def test_accepts_anchor_with_matching_id() -> None:
    files = {
        "index.html": '<a href="#pricing">Цены</a><section id="pricing"></section>'
    }
    assert find_dead_links(files) == []


def test_accepts_top_fragment_and_real_targets() -> None:
    files = {
        "index.html": (
            '<a href="#top">Наверх</a>'
            '<a href="tel:+79990001122">Звонок</a>'
            '<a href="mailto:hi@example.ru">Почта</a>'
            '<a href="https://wa.me/79990001122">WA</a>'
            '<a href="about.html">О нас</a>'
        )
    }
    assert find_dead_links(files) == []


def test_ignores_non_html_files() -> None:
    files = {"styles.css": "a{color:red}", "app.js": 'const placeholder = "#";'}
    assert find_dead_links(files) == []


# --- repair_orphaned_anchors_inline (dogfood run #8, BS-10) ----------------
# A removal edit ("убери секцию отзывов") deletes id="reviews" but leaves the
# nav link href="#reviews" dangling. The surgical edit path skips the normal
# dead-link pass, so this anchor — orphaned by the very change the user asked
# for — was shipped dead. The repair redirects it to a surviving section.

_NAV = (
    '<nav><a href="#about">О нас</a>'
    '<a href="#reviews">Отзывы</a>'
    '<a href="#contacts">Контакты</a></nav>'
)


def test_orphaned_anchor_after_section_removal_is_redirected() -> None:
    old = {
        "index.html": _NAV
        + '<section id="about"></section>'
        + '<section id="reviews"></section>'
        + '<section id="contacts"></section>'
    }
    # The edit removed the reviews section (id gone) but left the nav link.
    new = {
        "index.html": _NAV
        + '<section id="about"></section>'
        + '<section id="contacts"></section>'
    }
    out = repair_orphaned_anchors_inline(old, new)
    # No dead anchors remain — the orphaned #reviews link was redirected.
    assert find_dead_links(out) == []
    assert 'href="#reviews"' not in out["index.html"]
    # The two still-valid anchors are untouched.
    assert 'href="#about"' in out["index.html"]
    assert 'href="#contacts"' in out["index.html"]


def test_preexisting_dead_anchor_is_not_touched() -> None:
    # An anchor that was ALREADY dangling before the edit (id never existed)
    # must NOT be repaired here — that's a pre-existing link the user didn't
    # mention. Only edit-orphaned anchors are in scope.
    old = {"index.html": '<a href="#ghost">X</a><section id="contacts"></section>'}
    new = {"index.html": '<a href="#ghost">X</a><section id="contacts"></section>'}
    out = repair_orphaned_anchors_inline(old, new)
    assert out["index.html"] == new["index.html"]


def test_no_fallback_target_leaves_anchor_unchanged() -> None:
    # If the page has no CTA-class section to redirect to, leave the link as-is
    # rather than inventing a target (no regression / no new dead link).
    old = {"index.html": '<a href="#reviews">Отзывы</a><section id="reviews"></section>'}
    new = {"index.html": '<a href="#reviews">Отзывы</a>'}
    out = repair_orphaned_anchors_inline(old, new)
    assert out["index.html"] == new["index.html"]


# --- repair_dead_links_inline niche-agnostic fallback (dogfood run #41, BS-41) ---
# A travel blog's sections were hero/archive/manifesto/subscribe — none of the
# commercial CTA names — so _pick_repair_target returned None and the inline
# pass repaired 0 of 28 placeholders. The fix: redirect to the page's own first
# nav-referenced section, else #top, so a placeholder never survives on any
# archetype (blog / portfolio / magazine / article detail page).


def test_content_site_dead_links_redirect_to_real_nav_section() -> None:
    # Editorial homepage: real sections, but none commercial. Old code no-oped;
    # now the dead "#" links must be repaired to a real nav-referenced section.
    files = {
        "index.html": (
            '<nav><a href="#hero">Главная</a>'
            '<a href="#archive">Архив</a>'
            '<a href="#subscribe">Подписка</a></nav>'
            '<a href="#">Логотип</a>'
            '<a href="#">Соцсеть</a>'
            '<section id="hero"></section>'
            '<section id="archive"></section>'
            '<section id="subscribe"></section>'
        )
    }
    assert find_dead_links(files)  # before: dead placeholders present
    out = repair_dead_links_inline(files)
    assert find_dead_links(out) == []  # after: none remain
    # Redirected to a real section the page navigates to (first nav anchor).
    assert 'href="#hero"' in out["index.html"]
    assert 'href="#"' not in out["index.html"]


def test_article_page_with_no_local_sections_falls_back_to_top() -> None:
    # An article/detail page: tag + footer placeholder links, and the only ids
    # are header/footer (its nav points off-page). No local section to pick →
    # universal #top fallback keeps every link valid, never dead.
    files = {
        "article.html": (
            '<header id="header"></header>'
            '<a href="#">ИСЛАНДИЯ</a><a href="#">ВУЛКАНЫ</a>'
            '<footer id="footer">'
            '<a href="#">Политика</a><a href="#">TELEGRAM</a></footer>'
        )
    }
    assert len(find_dead_links(files)) == 4
    out = repair_dead_links_inline(files)
    assert find_dead_links(out) == []
    assert 'href="#top"' in out["article.html"]


def test_commercial_cta_target_still_preferred() -> None:
    # Regression: a storefront page with a #contacts section must STILL redirect
    # dead links there (commercial intent), not to #top.
    files = {
        "index.html": (
            '<a href="#">Связаться</a>'
            '<section id="contacts"></section>'
            '<section id="hero"></section>'
        )
    }
    out = repair_dead_links_inline(files)
    assert find_dead_links(out) == []
    assert 'href="#contacts"' in out["index.html"]


def test_working_links_untouched_by_dead_link_repair() -> None:
    # Repair must only rewrite DEAD hrefs — real links stay byte-identical.
    files = {
        "index.html": (
            '<a href="article-iceland.html">Читать</a>'
            '<a href="tel:+79990001122">Звонок</a>'
            '<a href="#hero">Наверх</a><section id="hero"></section>'
        )
    }
    out = repair_dead_links_inline(files)
    assert out["index.html"] == files["index.html"]
