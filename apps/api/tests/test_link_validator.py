from omnia_api.services.link_validator import (
    find_dead_links,
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
