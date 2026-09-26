"""Каждая ниша получает своё арт-направление, а не палитру наугад.

До этого набора направлений было девять, а у направления по умолчанию —
пустой список вайбов. Пустой список ничего не сужал, поэтому в кандидаты
попадали все 66 кураторских палитр, и ниша без своей полосы получала любую:
юридическая фирма — брутализм с оранжевым, салон красоты — брутализм с жёлтым,
а клиника, стройка и ветклиника — одну и ту же палитру.

Здесь закреплено три вещи:
  * узкая ниша срабатывает раньше широкой (ветклиника — не «клиника»,
    автосалон — не «салон», фотостудия — не «студия»);
  * ниши доверия никогда не получают выразительный язык, а детские — тёмный
    корпоративный;
  * разброс внутри одной нишы сохраняется: два проекта одной ниши не выглядят
    одинаково.
"""

from yleum_api.sections.palettes import contrast_ratio
from yleum_api.services.design_tokens import (
    _DEFAULT_DIRECTION,
    _DIRECTIONS,
    _direction_for_hint,
    tokens_for_project,
)
from yleum_api.services.skill_library import lookup_palette

# Выразительные языки — осознанный выбор ниши, а не то, куда попадают случайно.
EXPRESSIVE_VIBES = {"brutalist", "y2k-neo"}
QUIET_VIBES = {"swiss-minimal", "apple-tech", "fintech-trust"}

# Ниша → бриф на языке владельца. Ровно то, что человек пишет в первом запросе.
VERTICAL_BRIEFS: dict[str, str] = {
    "dental-clean": "сайт стоматологической клиники, запись к врачу и цены на имплантацию",
    "veterinary-care": "ветеринарная клиника и зоомагазин, приём и корма",
    "pharmacy-care": "сеть аптек, поиск лекарств и бронирование",
    "beauty-refined": "салон красоты: маникюр, косметология, ресницы",
    "automotive-showroom": "автосалон премиальных автомобилей с тест-драйвом",
    "photography-studio": "фотограф, портфолио и запись на фотосессию",
    "travel-escape": "отель у моря, номера и бронирование",
    "hospitality-warm": "ресторан авторской кухни, меню и бронь столика",
    "events-celebration": "организация свадеб и торжеств под ключ",
    "kids-playful": "частный детский сад, группы и расписание",
    "senior-care": "пансионат для пожилых, уход и проживание",
    "legal-authority": "юридическая фирма, корпоративное право и консультации",
    "insurance-trust": "страховая компания, расчёт полиса онлайн",
    "web3-crypto": "web3 кошелёк со стейкингом токенов",
    "realestate-solid": "агентство недвижимости, квартиры в новостройках",
    "construction-solid": "строительная компания, каркасные дома и кровля",
    "home-services": "вызов сантехника и электрика на дом",
    "logistics-delivery": "грузоперевозки по России, расчёт доставки",
    "retail-commerce": "интернет-магазин товаров для дома",
    "agriculture-farm": "фермерское хозяйство, молоко и сыры",
    "gaming-arena": "киберспортивная команда, состав и матчи",
    "media-publication": "онлайн-журнал о городе, статьи и подкаст",
    "museum-culture": "музей современного искусства, выставки и билеты",
    "church-community": "приход храма, расписание богослужений",
    "nonprofit-cause": "благотворительный фонд помощи детям, сбор средств",
    "recruitment-jobs": "подбор персонала, вакансии и отклики",
    "coworking-workspace": "коворкинг в центре, переговорные и тарифы",
    "flowers-plants": "доставка букетов, флорист собирает в день заказа",
    "fitness-performance": "фитнес-клуб, расписание тренировок и абонементы",
    "medical-trust": "медицинский центр, приём терапевта и анализы",
    "legal-authority-en": "law firm specialising in corporate legal services",
}

# Ловушки порядка: узкая ниша обязана победить широкую, иначе бриф уезжает
# в чужую эстетику. Каждая пара — настоящая подстрока из живого языка.
ORDER_TRAPS: tuple[tuple[str, str, str], ...] = (
    ("ветеринарная клиника на районе", "veterinary-care", "medical-trust"),
    ("стоматологическая клиника, брекеты", "dental-clean", "medical-trust"),
    ("автосалон с сервисом", "automotive-showroom", "beauty-refined"),
    ("зоомагазин с доставкой корма", "veterinary-care", "retail-commerce"),
    ("фотостудия с залами в аренду", "photography-studio", "expressive-creative"),
    ("турагентство, туры по Азии", "travel-escape", "expressive-creative"),
    ("автошкола, обучение вождению", "automotive-showroom", "education-clear"),
    ("салон красоты и студия загара", "beauty-refined", "expressive-creative"),
    ("крипто-кошелёк для инвестиций", "web3-crypto", "finance-trust"),
    ("детский центр развития и обучения", "kids-playful", "education-clear"),
)

TRUST_VERTICALS = (
    "dental-clean",
    "pharmacy-care",
    "legal-authority",
    "insurance-trust",
    "medical-trust",
    "logistics-delivery",
    "home-services",
    "recruitment-jobs",
)


def _project_ids(count: int = 12) -> list[str]:
    return [f"vertical-probe-{i}" for i in range(count)]


def test_every_vertical_brief_lands_on_its_own_lane():
    for expected, brief in VERTICAL_BRIEFS.items():
        direction = _direction_for_hint(brief)
        assert direction.id == expected.removesuffix("-en"), (
            f"бриф «{brief}» ушёл в направление {direction.id}, ожидалось {expected}"
        )


def test_narrow_vertical_wins_over_the_broad_one():
    for brief, expected, shadowed in ORDER_TRAPS:
        direction = _direction_for_hint(brief)
        assert direction.id == expected, (
            f"бриф «{brief}» ушёл в {direction.id}; узкая ниша {expected} обязана "
            f"срабатывать раньше широкой {shadowed}"
        )


def test_trust_verticals_never_get_an_expressive_language():
    # Проектов берём много намеренно: при пустом наборе вайбов в кандидатах были
    # все 66 палитр, и на части посевов ниша доверия получала брутализм. На
    # двенадцати проектах такое можно не заметить — поэтому сорок.
    for vertical in TRUST_VERTICALS:
        brief = VERTICAL_BRIEFS[vertical]
        for project_id in _project_ids(40):
            tokens = tokens_for_project(project_id, industry_hint=brief)
            assert tokens.palette.vibe not in EXPRESSIVE_VIBES, (
                f"{vertical} получил {tokens.palette.vibe} «{tokens.palette.name}» — "
                "выразительный язык для ниши доверия это брак"
            )


def test_children_never_get_a_dark_corporate_language():
    brief = VERTICAL_BRIEFS["kids-playful"]
    forbidden = {"linear-dark", "brutalist", "fintech-trust"}
    for project_id in _project_ids():
        tokens = tokens_for_project(project_id, industry_hint=brief)
        assert tokens.palette.vibe not in forbidden, (
            f"детская ниша получила {tokens.palette.vibe} «{tokens.palette.name}»"
        )


def test_unclassified_brief_stays_on_a_quiet_lane():
    assert _DEFAULT_DIRECTION.vibes, "у полосы по умолчанию не может быть пустого набора вайбов"
    assert set(_DEFAULT_DIRECTION.vibes) <= QUIET_VIBES
    brief = "хочу просто сайт про всё сразу"
    assert _direction_for_hint(brief).id == _DEFAULT_DIRECTION.id
    for project_id in _project_ids():
        tokens = tokens_for_project(project_id, industry_hint=brief)
        assert tokens.palette.vibe in QUIET_VIBES, (
            f"неопознанный бриф получил {tokens.palette.vibe} — по умолчанию только тихие полосы"
        )


def test_same_vertical_still_varies_between_projects():
    # Ниша задаёт язык, но не превращает все проекты ниши в один сайт.
    for vertical in ("hospitality-warm", "legal-authority", "retail-commerce"):
        brief = VERTICAL_BRIEFS[vertical]
        palettes = {
            tokens_for_project(project_id, industry_hint=brief).palette.id
            for project_id in _project_ids(16)
        }
        assert len(palettes) >= 3, (
            f"{vertical}: 16 проектов дали {len(palettes)} палитр(ы) — разброса нет"
        )


def test_every_lane_anchor_resolves_in_the_curated_table():
    # Опечатка в названии типа продукта тихо отключила бы якорь палитры.
    for direction in (*_DIRECTIONS, _DEFAULT_DIRECTION):
        if not direction.palette_keywords:
            continue
        assert lookup_palette(*direction.palette_keywords) is not None, (
            f"{direction.id}: якорь {direction.palette_keywords} не находится в таблице палитр"
        )


def test_every_lane_declares_a_non_empty_vibe_set():
    for direction in (*_DIRECTIONS, _DEFAULT_DIRECTION):
        assert direction.vibes, (
            f"{direction.id}: пустой набор вайбов пускает в кандидаты все палитры"
        )


def test_every_lane_produces_readable_tokens():
    for direction in _DIRECTIONS:
        brief = direction.aliases[0]
        tokens = tokens_for_project(f"readability-{direction.id}", industry_hint=brief)
        palette = tokens.palette
        assert contrast_ratio(palette.text, palette.bg) >= 4.5, (
            f"{direction.id}: основной текст «{palette.name}» не проходит WCAG AA"
        )
        assert contrast_ratio(palette.muted, palette.bg) >= 4.5, (
            f"{direction.id}: приглушённый текст «{palette.name}» не проходит WCAG AA"
        )


def test_no_alias_is_claimed_by_two_lanes():
    seen: dict[str, str] = {}
    for direction in _DIRECTIONS:
        for alias in direction.aliases:
            assert alias not in seen, (
                f"алиас «{alias}» объявлен и в {seen[alias]}, и в {direction.id} — "
                "вторая полоса никогда не сработает"
            )
            seen[alias] = direction.id
