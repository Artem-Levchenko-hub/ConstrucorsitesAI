from omnia_api.services.link_validator import find_dead_links


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
