"""Design tokens for freeform generation (Phase 11, Sprint 1.3).

The old freeform prompt anchored every site to the SAME palette (the first
of the curated set) and let fonts be free text — so two projects looked
identical and the model drifted back to its indigo+violet training default.

This module fixes the *foundation* (palette + font pairing) with **spread**:
the choice is seeded by `project_id`, so a project is stable across re-prompts
but different projects land on different, curated, WCAG-checked combinations.
That is the "freedom in composition, rigidity in the foundation" principle —
we don't fix the layout, we fix the tokens the layout is built from.

Output: a `prompt_block()` (an authoritative palette+font anchor for the
system prompt) and `css_vars()` (a ready `:root{}` snippet the model can paste).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from yleum_api.sections.palettes import (
    CuratedPalette,
    all_palettes,
    palettes_for_vibe,
)
from yleum_api.services.skill_library import (
    Palette,
    all_font_pairings,
    font_pairing_candidates,
    font_supports_cyrillic,
    font_weights,
    lookup_palette,
)

# Backward-compatible source for the in-preview font picker. The value now
# comes from the vendored catalogue's Cyrillic-safe subset instead of a
# hand-written list of 16 pairs.
_FONT_PAIRINGS: tuple[tuple[str, str], ...] = tuple(
    (pair["heading"], pair["body"])
    for pair in all_font_pairings()
    if font_supports_cyrillic(pair["heading"]) and font_supports_cyrillic(pair["body"])
)


@dataclass(frozen=True)
class _DesignDirection:
    """One semantic lane shared by colour and typography selection."""

    id: str
    label: str
    aliases: tuple[str, ...]
    vibes: tuple[str, ...]
    palette_keywords: tuple[str, ...]
    font_keywords: tuple[str, ...]


@dataclass(frozen=True)
class _FontSelection:
    name: str
    heading: str
    body: str
    google_fonts_url: str | None = None


# The aliases classify the brief; the actual colour/font knowledge stays in
# the version-pinned UI/UX Pro Max tables. Each direction deliberately allows
# several compatible outcomes, so different projects vary without jumping to
# an unrelated visual language.
# Order is priority: the first direction whose alias appears in the brief wins, so
# narrow verticals stand before broad ones (a veterinary clinic must not be read as
# a medical clinic, a car showroom not as a beauty salon, a photo studio not as a
# creative agency). Every lane names a real product type from the curated palette
# table, so the colour anchor is knowledge, not invention.
_DIRECTIONS: tuple[_DesignDirection, ...] = (
    _DesignDirection(
        "dental-clean",
        "стоматологическая чистота",
        ("стоматолог", "зубн", "имплантац", "ортодонт", "брекет", "dental", "dentist"),
        ("swiss-minimal", "apple-tech", "fintech-trust"),
        ("Dental Practice",),
        ("medical", "clean", "trustworthy", "readable", "professional", "calm"),
    ),
    _DesignDirection(
        "veterinary-care",
        "забота о питомцах",
        (
            "ветеринар",
            "ветклиник",
            "зоомагазин",
            "зоосалон",
            "питомц",
            "собак",
            "кошк",
            "veterinary",
            "vet clinic",
            "pet",
        ),
        ("wellness-casual", "swiss-minimal", "apple-tech"),
        ("Veterinary Clinic",),
        ("friendly", "warm", "caring", "approachable", "readable", "rounded"),
    ),
    _DesignDirection(
        "pharmacy-care",
        "аптечная ясность",
        ("аптек", "фармаце", "лекарств", "pharmacy", "drugstore"),
        ("swiss-minimal", "fintech-trust", "apple-tech"),
        ("Pharmacy/Drug Store",),
        ("medical", "clean", "readable", "accessible", "trustworthy"),
    ),
    _DesignDirection(
        "beauty-refined",
        "ухоженная эстетика",
        (
            "салон красот",
            "космето",
            "маникюр",
            "педикюр",
            "барбер",
            "парикмахер",
            "ресниц",
            "эпиляц",
            "депиляц",
            "brow",
            "beauty salon",
            "nail salon",
        ),
        ("editorial-luxury", "wellness-casual", "apple-tech"),
        ("Beauty/Spa/Wellness Service",),
        ("elegant", "refined", "feminine", "soft", "editorial", "premium"),
    ),
    _DesignDirection(
        "automotive-showroom",
        "автомобильная мощь",
        (
            "автосалон",
            "автосервис",
            "автошкол",
            "автомобил",
            "шиномонтаж",
            "автомойк",
            "car dealership",
            "automotive",
        ),
        ("linear-dark", "apple-tech", "swiss-minimal"),
        ("Automotive/Car Dealership",),
        ("bold", "technical", "precise", "strong", "modern", "dynamic"),
    ),
    _DesignDirection(
        "photography-studio",
        "фотографическая подача",
        ("фотограф", "фотостуди", "фотосесс", "photography", "photo studio"),
        ("editorial-luxury", "swiss-minimal", "brutalist"),
        ("Photography Studio",),
        ("editorial", "elegant", "minimal", "gallery", "refined"),
    ),
    _DesignDirection(
        "travel-escape",
        "путешествие и гостеприимство",
        (
            "отел",
            "гостиниц",
            "хостел",
            "турагент",
            "туризм",
            "путешеств",
            "экскурс",
            "санатор",
            "базы отдых",
            "hotel",
            "hostel",
            "travel",
            "tourism",
            "resort",
        ),
        ("editorial-luxury", "wellness-casual", "apple-tech"),
        ("Hotel/Hospitality", "Travel/Tourism Agency"),
        ("elegant", "warm", "editorial", "inviting", "premium", "relaxing"),
    ),
    _DesignDirection(
        "hospitality-warm",
        "тёплое гостеприимство стола",
        (
            "ресторан",
            "кафе",
            "кофейн",
            "пекарн",
            "кондитер",
            "пиццер",
            "суши",
            "бургер",
            "паб",
            "пивовар",
            "доставка еды",
            "столов",
            "restaurant",
            "cafe",
            "bakery",
            "brewery",
            "bistro",
        ),
        ("editorial-luxury", "wellness-casual", "swiss-minimal"),
        ("Restaurant/Food Service",),
        ("warm", "appetizing", "editorial", "inviting", "elegant", "readable"),
    ),
    _DesignDirection(
        "events-celebration",
        "праздничное событие",
        (
            "свадьб",
            "торжеств",
            "банкет",
            "праздник",
            "организац мероприят",
            "event planning",
            "wedding",
        ),
        ("editorial-luxury", "wellness-casual", "y2k-neo"),
        ("Wedding/Event Planning",),
        ("elegant", "festive", "editorial", "refined", "expressive"),
    ),
    _DesignDirection(
        "kids-playful",
        "детская игривость",
        (
            "детск",
            "детей",
            "ребён",
            "ребен",
            "малыш",
            "садик",
            "childcare",
            "daycare",
            "kindergarten",
            "kids",
        ),
        ("wellness-casual", "y2k-neo", "apple-tech"),
        ("Childcare/Daycare",),
        ("friendly", "playful", "rounded", "warm", "approachable", "readable"),
    ),
    _DesignDirection(
        "senior-care",
        "бережный уход",
        ("пансионат", "пожил", "сиделк", "senior care", "elderly"),
        ("wellness-casual", "swiss-minimal"),
        ("Senior Care/Elderly",),
        ("accessible", "readable", "calm", "caring", "large-type"),
    ),
    _DesignDirection(
        "legal-authority",
        "юридическая весомость",
        (
            "юрист",
            "юридическ",
            "адвокат",
            "нотариус",
            "правов",
            "законн",
            "legal services",
            "law firm",
            "attorney",
            "notary",
        ),
        ("fintech-trust", "swiss-minimal", "editorial-luxury"),
        ("Legal Services",),
        ("serious", "professional", "authoritative", "corporate", "editorial", "readable"),
    ),
    _DesignDirection(
        "insurance-trust",
        "страховая надёжность",
        ("страхов", "полис осаго", "insurance"),
        ("fintech-trust", "swiss-minimal"),
        ("Insurance Platform",),
        ("trustworthy", "professional", "corporate", "readable", "precise"),
    ),
    _DesignDirection(
        "web3-crypto",
        "web3 и крипта",
        (
            "крипто",
            "блокчейн",
            "стейкинг",
            "токен",
            "nft",
            "web3",
            "defi",
            "crypto",
            "blockchain",
        ),
        ("linear-dark", "y2k-neo", "fintech-trust"),
        ("NFT/Web3 Platform",),
        ("futuristic", "technical", "dark", "bold", "innovative"),
    ),
    _DesignDirection(
        "realestate-solid",
        "недвижимость с весом",
        (
            "недвижим",
            "новостро",
            "риелтор",
            "риэлтор",
            "квартир",
            "коттедж",
            "real estate",
            "property",
            "realty",
        ),
        ("editorial-luxury", "fintech-trust", "swiss-minimal"),
        ("Real Estate/Property",),
        ("premium", "editorial", "trustworthy", "elegant", "professional"),
    ),
    _DesignDirection(
        "construction-solid",
        "строительная прочность",
        (
            "строител",
            "стройк",
            "каркасн",
            "кровл",
            "фундамент",
            "отделочн",
            "ремонт кварт",
            "ремонт помещен",
            "архитектур",
            "construction",
            "renovation",
        ),
        ("swiss-minimal", "fintech-trust", "brutalist"),
        ("Construction/Architecture",),
        ("strong", "industrial", "precise", "professional", "structured"),
    ),
    _DesignDirection(
        "home-services",
        "мастера на районе",
        (
            "сантехник",
            "электрик",
            "клининг",
            "уборк",
            "мастер на час",
            "муж на час",
            "грузчик",
            "plumber",
            "electrician",
            "cleaning",
            "handyman",
        ),
        ("swiss-minimal", "apple-tech", "fintech-trust"),
        ("Home Services (Plumber/Electrician)",),
        ("practical", "readable", "friendly", "clear", "professional"),
    ),
    _DesignDirection(
        "logistics-delivery",
        "логистическая точность",
        (
            "логистик",
            "грузоперевоз",
            "перевозк",
            "курьерск",
            "складск",
            "транспортн компан",
            "logistics",
            "freight",
            "courier",
        ),
        ("fintech-trust", "swiss-minimal", "apple-tech"),
        ("Logistics/Delivery",),
        ("precise", "professional", "clear", "technical", "structured"),
    ),
    _DesignDirection(
        "retail-commerce",
        "торговая витрина",
        (
            "интернет-магазин",
            "маркетплейс",
            "каталог товар",
            "магазин",
            "товар",
            "shop",
            "store",
            "e-commerce",
            "ecommerce",
            "marketplace",
        ),
        ("apple-tech", "swiss-minimal", "editorial-luxury"),
        ("E-commerce",),
        ("product", "clean", "modern", "commerce", "readable"),
    ),
    _DesignDirection(
        "agriculture-farm",
        "земля и хозяйство",
        ("фермер", "агро", "сельхоз", "теплиц", "урожай", "farm", "agriculture"),
        ("wellness-casual", "swiss-minimal", "editorial-luxury"),
        ("Agriculture/Farm Tech",),
        ("natural", "organic", "warm", "readable", "grounded"),
    ),
    _DesignDirection(
        "gaming-arena",
        "игровая арена",
        ("киберспорт", "гейм", "игров", "стрим", "gaming", "esports"),
        ("linear-dark", "y2k-neo", "brutalist"),
        ("Gaming",),
        ("bold", "dark", "energetic", "futuristic", "expressive"),
    ),
    _DesignDirection(
        "media-publication",
        "медийная публикация",
        (
            "новостн",
            "медиа",
            "журнал",
            "блог",
            "издани",
            "подкаст",
            "news",
            "magazine",
            "blog",
            "podcast",
        ),
        ("editorial-luxury", "swiss-minimal", "apple-tech"),
        ("Magazine/Blog",),
        ("editorial", "readable", "serif", "refined", "typographic"),
    ),
    _DesignDirection(
        "museum-culture",
        "культурная институция",
        (
            "музе",
            "галере",
            "выставк",
            "театр",
            "кинотеатр",
            "museum",
            "gallery",
            "theater",
            "cinema",
        ),
        ("editorial-luxury", "brutalist", "swiss-minimal"),
        ("Museum/Gallery",),
        ("editorial", "artistic", "refined", "expressive", "typographic"),
    ),
    _DesignDirection(
        "church-community",
        "приходская община",
        ("храм", "приход", "церков", "община верующ", "church", "parish"),
        ("editorial-luxury", "swiss-minimal", "wellness-casual"),
        ("Church/Religious Organization",),
        ("calm", "editorial", "serif", "warm", "readable"),
    ),
    _DesignDirection(
        "nonprofit-cause",
        "общественное дело",
        ("благотвор", "некоммерч", "волонт", "фонд помощи", "nonprofit", "charity", "ngo"),
        ("wellness-casual", "swiss-minimal", "editorial-luxury"),
        ("Non-profit/Charity",),
        ("warm", "human", "readable", "approachable", "trustworthy"),
    ),
    _DesignDirection(
        "recruitment-jobs",
        "подбор и работа",
        (
            "ваканс",
            "подбор персонал",
            "рекрут",
            "трудоустрой",
            "job board",
            "recruitment",
            "hiring",
        ),
        ("swiss-minimal", "fintech-trust", "apple-tech"),
        ("Job Board/Recruitment",),
        ("professional", "clear", "readable", "structured", "modern"),
    ),
    _DesignDirection(
        "coworking-workspace",
        "рабочее пространство",
        ("коворкинг", "рабочее простран", "coworking", "workspace"),
        ("swiss-minimal", "apple-tech", "wellness-casual"),
        ("Coworking Space",),
        ("clean", "modern", "friendly", "readable", "structured"),
    ),
    _DesignDirection(
        "flowers-plants",
        "цветы и растения",
        ("цветы", "букет", "флорист", "растени", "florist", "flower shop"),
        ("wellness-casual", "editorial-luxury", "swiss-minimal"),
        ("Florist/Plant Shop",),
        ("natural", "elegant", "soft", "warm", "editorial"),
    ),
    _DesignDirection(
        "fitness-performance",
        "энергичный спортивный performance",
        (
            "fitness",
            "gym",
            "workout",
            "training",
            "athletic",
            "sport",
            "фитнес",
            "трениров",
            "спорт",
            "атлет",
            "спортзал",
            "силов",
        ),
        ("linear-dark", "brutalist", "wellness-casual"),
        ("Fitness/Gym App",),
        (
            "fitness",
            "sports",
            "athletic",
            "energetic",
            "bold",
            "action",
            "performance",
            "strong",
            "dynamic",
            "dark",
            "high-energy",
        ),
    ),
    _DesignDirection(
        "medical-trust",
        "клиническая ясность и доверие",
        (
            "medical",
            "clinic",
            "healthcare",
            "doctor",
            "patient",
            "pharma",
            "медицин",
            "клиник",
            "врач",
            "пациент",
            "здоровь",
        ),
        ("swiss-minimal", "apple-tech", "fintech-trust"),
        ("Medical Clinic",),
        ("medical", "healthcare", "trustworthy", "professional", "readable", "accessible", "clean"),
    ),
    _DesignDirection(
        "wellness-calm",
        "спокойный естественный wellness",
        (
            "wellness",
            "yoga",
            "meditation",
            "mindfulness",
            "spa",
            "велнес",
            "йога",
            "медитац",
            "осознан",
            "спа",
        ),
        ("wellness-casual", "swiss-minimal", "apple-tech"),
        ("Healthcare App",),
        ("wellness", "calm", "health", "relaxing", "natural", "organic", "readable"),
    ),
    _DesignDirection(
        "finance-trust",
        "финансовая точность и доверие",
        ("fintech", "finance", "bank", "invest", "финтех", "финанс", "банк", "инвест"),
        ("fintech-trust",),
        ("Fintech/Crypto",),
        ("financial", "trustworthy", "professional", "corporate", "banking", "serious", "precise"),
    ),
    _DesignDirection(
        "education-clear",
        "дружелюбная учебная ясность",
        ("education", "school", "course", "learning", "образован", "школ", "курс", "обучен"),
        ("swiss-minimal", "apple-tech", "wellness-casual"),
        ("Online Course/E-learning",),
        ("education", "learning", "friendly", "readable", "accessible", "clean", "modern"),
    ),
    _DesignDirection(
        "product-precision",
        "продуктовая точность",
        ("saas", "b2b", "dashboard", "productivity", "сервис", "дашборд", "продуктив"),
        ("swiss-minimal", "apple-tech", "linear-dark"),
        ("SaaS (General)",),
        ("saas", "product", "modern", "professional", "clean", "precise", "readable"),
    ),
    _DesignDirection(
        "technology-forward",
        "технологичный forward",
        ("developer", "technology", " tech", " dev", "разработ", "технолог", "айти"),
        ("apple-tech", "linear-dark", "swiss-minimal"),
        ("Developer Tool / IDE",),
        ("tech", "developer", "technical", "precise", "innovative", "modern", "futuristic"),
    ),
    _DesignDirection(
        "editorial-premium",
        "редакционная премиальность",
        ("luxury", "premium", "editorial", "fashion", "люкс", "премиум", "редакц", "модн"),
        ("editorial-luxury",),
        ("Luxury/Premium Brand",),
        ("luxury", "premium", "editorial", "elegant", "refined", "sophisticated"),
    ),
    _DesignDirection(
        "expressive-creative",
        "выразительная креативность",
        ("creative", "agency", "portfolio", "studio", "креатив", "агентств", "портфолио", "студи"),
        ("brutalist", "y2k-neo", "editorial-luxury"),
        ("Creative Agency",),
        ("creative", "bold", "artistic", "expressive", "experimental", "impactful"),
    ),
)

_DEFAULT_DIRECTION = _DesignDirection(
    "balanced-modern",
    "современный читаемый баланс",
    (),
    # An unclassified brief gets the quiet lanes only. Expressive languages
    # (brutalist, y2k-neo) are a deliberate choice for a vertical that asks for
    # them, never a fallback: a law firm used to land on brutalist orange this way.
    ("swiss-minimal", "apple-tech", "fintech-trust"),
    # Без ниши нет и якоря: он сузил бы кандидатов до пяти палитр и убрал
    # разброс между проектами, ради которого вся эта раздача и делалась.
    (),
    ("modern", "readable", "clean", "professional"),
)

# Some strongest exact industry pairs (for example Sports/Fitness with Barlow)
# are Latin-only. These locale-safe cohorts are assembled from families in the
# same vendored catalogue whose metadata explicitly includes Cyrillic.
_CYRILLIC_DIRECTION_FONTS: dict[str, tuple[_FontSelection, ...]] = {
    "balanced-modern": (
        _FontSelection("Balanced UI Cyrillic", "Inter", "Inter"),
        _FontSelection("Friendly Product Cyrillic", "Plus Jakarta Sans", "Plus Jakarta Sans"),
        _FontSelection("Neutral Product Cyrillic", "Manrope", "Inter"),
        _FontSelection("Humanist Product Cyrillic", "Rubik", "Nunito Sans"),
        _FontSelection("Structured Product Cyrillic", "IBM Plex Sans", "IBM Plex Sans"),
        _FontSelection("Confident Product Cyrillic", "Montserrat", "Source Sans 3"),
        _FontSelection("Editorial Product Cyrillic", "Lora", "Raleway"),
        _FontSelection("Clean Product Cyrillic", "Jost", "Open Sans"),
    ),
    "fitness-performance": (
        _FontSelection("Athletic Impact Cyrillic", "Russo One", "Manrope"),
        _FontSelection("Technical Performance Cyrillic", "Exo 2", "Inter"),
        _FontSelection("Bold Training Cyrillic", "Montserrat", "Manrope"),
        _FontSelection("Friendly Motion Cyrillic", "Rubik", "Nunito Sans"),
        _FontSelection("Functional Sport Cyrillic", "Fira Sans", "IBM Plex Sans"),
    ),
    "medical-trust": (
        _FontSelection("Clinical Trust Cyrillic", "IBM Plex Sans", "IBM Plex Sans"),
        _FontSelection("Clinical Clear Cyrillic", "Inter", "Inter"),
        _FontSelection(
            "Clinical Accessibility Cyrillic",
            "Atkinson Hyperlegible",
            "Atkinson Hyperlegible",
        ),
        _FontSelection("Clinical Modern Cyrillic", "Plus Jakarta Sans", "Plus Jakarta Sans"),
        _FontSelection("Clinical Humanist Cyrillic", "Rubik", "Nunito Sans"),
    ),
    "finance-trust": (
        _FontSelection("Financial Trust Cyrillic", "IBM Plex Sans", "IBM Plex Sans"),
        _FontSelection("Financial Data Cyrillic", "Fira Code", "Fira Sans"),
        _FontSelection("Financial Clear Cyrillic", "Inter", "Inter"),
        _FontSelection("Financial Modern Cyrillic", "Plus Jakarta Sans", "Plus Jakarta Sans"),
        _FontSelection("Financial Humanist Cyrillic", "Rubik", "Nunito Sans"),
    ),
}

_LATIN_DIRECTION_FONT_NAMES: dict[str, tuple[str, ...]] = {
    "fitness-performance": (
        "Sports/Fitness",
        "Bold Statement",
        "Kinetic Brutalism (Space Grotesk)",
    ),
}


def _seed(project_id: str, salt: str) -> int:
    """Stable, process-independent seed from a project id.

    `hash()` is salted per-process in CPython, so it can't give a project a
    *stable* look across restarts. SHA-256 of the id + a per-axis salt does —
    and the salt decorrelates the palette pick from the font pick so they
    don't move together.
    """
    digest = hashlib.sha256(f"{salt}:{project_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _google_fonts_url(display: str, body: str) -> str:
    """Build a Google Fonts CSS2 link for the pairing (weights baked in)."""
    fams = []
    seen: set[str] = set()
    for name, requested in (
        (display, (400, 500, 600, 700)),
        (body, (400, 500, 600)),
    ):
        if name in seen:
            continue
        seen.add(name)
        available = font_weights(name)
        selected = tuple(weight for weight in requested if weight in available)
        weights = selected or available or requested
        fams.append(f"family={name.replace(' ', '+')}:wght@{';'.join(map(str, weights))}")
    return "https://fonts.googleapis.com/css2?" + "&".join(fams) + "&display=swap"


@dataclass(frozen=True)
class DesignTokens:
    """A resolved palette + font pairing for one project."""

    palette: CuratedPalette
    display_font: str
    body_font: str
    google_fonts_url: str
    direction_id: str
    direction_label: str
    font_pair_name: str

    def css_vars(self) -> str:
        """A `:root{}` block the model can paste verbatim."""
        p = self.palette
        return (
            ":root{\n"
            f"  --bg: {p.bg};\n"
            f"  --bg-alt: {p.surface};\n"
            f"  --fg: {p.text};\n"
            f"  --muted: {p.muted};\n"
            f"  --primary: {p.primary};\n"
            f"  --accent: {p.accent};\n"
            f"  --border: {p.border};\n"
            f"  --font-display: '{self.display_font}';\n"
            f"  --font-body: '{self.body_font}';\n"
            "}"
        )

    def prompt_block(self) -> str:
        """Authoritative palette+font anchor for the freeform system prompt.

        Mirrors the imperative shape of prompt_builder._format_palette_anchor
        so the model treats it as a hard constraint, not a suggestion — but
        these tokens are seeded-per-project, not a fixed preset, which is what
        gives sites their visual spread.
        """
        p = self.palette
        return f"""\
ОБЯЗАТЕЛЬНАЯ ПАЛИТРА И ШРИФТЫ (design tokens проекта) — anchor, читай ПЕРЕД остальным промптом.
Это РАЗНЫЕ токены для разных проектов — НЕ скатывайся в дефолтный indigo/violet.

ЕДИНОЕ АРТ-НАПРАВЛЕНИЕ: {self.direction_label} ({self.direction_id}).
Палитра и типографика выбраны ВМЕСТЕ внутри этого направления — не смешивай
их с эстетикой другой отрасли.

Вайб палитры: {p.vibe} · «{p.name}»

ЦВЕТА — используй ТОЛЬКО эти HEX (вынеси в :root и Tailwind config), никаких других:
  bg     = {p.bg}     bg-alt = {p.surface}
  fg     = {p.text}     muted  = {p.muted}
  primary= {p.primary}     accent = {p.accent}     border = {p.border}

ШРИФТЫ — подключи ИМЕННО эти Google Fonts, без подмен:
  пара: {self.font_pair_name}
  display: {self.display_font}   ·   body: {self.body_font}
  <link rel="stylesheet" href="{self.google_fonts_url}">

Готовый :root (вставь как есть и используй переменные по всей странице):
{self.css_vars()}

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ (это training-data дефолты, здесь они БРАК):
  • indigo (#4f46e5 / #6366f1 / #818cf8 / indigo-500/600/700)
  • violet / purple (#7c3aed / #8b5cf6 / #a855f7)
  • градиенты from-indigo-* to-violet-*, from-purple-* to-pink-*
  • Inter+Space Grotesk если их нет в паре выше
Любой цвет/шрифт вне этого блока без явной просьбы пользователя — брак."""


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def _direction_for_hint(industry_hint: str | None) -> _DesignDirection:
    hint = _normalize(industry_hint)
    if not hint:
        return _DEFAULT_DIRECTION
    return next(
        (
            direction
            for direction in _DIRECTIONS
            if any(alias in hint for alias in direction.aliases)
        ),
        _DEFAULT_DIRECTION,
    )


def _rgb(value: str) -> tuple[int, int, int] | None:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        return None
    try:
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _colour_distance(left: str, right: str) -> int:
    a = _rgb(left)
    b = _rgb(right)
    if a is None or b is None:
        return 100_000
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _palette_cohort(
    pool: list[CuratedPalette], direction: _DesignDirection
) -> list[CuratedPalette]:
    """Keep the closest validated palettes to the product-type colour anchor."""
    anchor: Palette | None = (
        lookup_palette(*direction.palette_keywords) if direction.palette_keywords else None
    )
    if anchor is None or len(pool) <= 5:
        return pool

    def score(palette: CuratedPalette) -> int:
        # Current curated palettes use `accent` as the vivid brand/action role;
        # the skill table calls the same role either primary or accent.
        brand = min(
            _colour_distance(palette.accent, anchor["primary"]),
            _colour_distance(palette.accent, anchor["accent"]),
        )
        return (
            4 * _colour_distance(palette.bg, anchor["background"])
            + 2 * _colour_distance(palette.surface, anchor["card"])
            + 2 * _colour_distance(palette.text, anchor["foreground"])
            + 3 * brand
        )

    return sorted(pool, key=lambda palette: (score(palette), palette.id))[:5]


def _font_cohort(
    direction: _DesignDirection, *, require_cyrillic: bool
) -> tuple[_FontSelection, ...]:
    if require_cyrillic and direction.id in _CYRILLIC_DIRECTION_FONTS:
        cohort = _CYRILLIC_DIRECTION_FONTS[direction.id]
        if all(
            font_supports_cyrillic(pair.heading) and font_supports_cyrillic(pair.body)
            for pair in cohort
        ):
            return cohort

    if not require_cyrillic and direction.id in _LATIN_DIRECTION_FONT_NAMES:
        by_name = {pair["name"]: pair for pair in all_font_pairings()}
        cohort = tuple(
            _FontSelection(
                pair["name"],
                pair["heading"],
                pair["body"],
                pair["google_fonts_url"],
            )
            for name in _LATIN_DIRECTION_FONT_NAMES[direction.id]
            if (pair := by_name.get(name)) is not None
        )
        if cohort:
            return cohort

    candidates = font_pairing_candidates(
        *direction.font_keywords,
        limit=6,
        require_cyrillic=require_cyrillic,
    )
    if not candidates:
        # Data snapshot/schema failure should not stop generation.
        candidates = font_pairing_candidates(
            "readable",
            "clean",
            limit=6,
            require_cyrillic=require_cyrillic,
        )
    if not candidates:
        return (_FontSelection("Fail-safe UI", "Inter", "Inter"),)

    anchors = direction.font_keywords[:2]
    semantic = tuple(
        pair
        for pair in candidates
        if any(
            anchor in f"{pair['name']} {pair['keywords']} {pair['best_for']}".lower()
            for anchor in anchors
        )
    )
    candidates = semantic or candidates
    return tuple(
        _FontSelection(
            pair["name"],
            pair["heading"],
            pair["body"],
            pair["google_fonts_url"],
        )
        for pair in candidates
    )


def tokens_for_project(
    project_id: str,
    *,
    vibe: str | None = None,
    dark_mode: bool | None = None,
    industry_hint: str | None = None,
    require_cyrillic: bool | None = None,
) -> DesignTokens:
    """Resolve seeded design tokens for a project.

    Selection is deterministic per `project_id` (stable across re-prompts and
    restarts) but spread across the curated pool so different projects differ.
    Optional `vibe` / `dark_mode` / `industry_hint` narrow the candidate pool
    first. Typography defaults to Cyrillic-safe because current production
    call-sites often pass an English preset id even for a Russian brief; an
    explicitly English flow may opt out with `require_cyrillic=False`.
    Anything that would empty the pool is ignored (R-10 fail-safe).
    """
    direction = _direction_for_hint(industry_hint)
    pool: list[CuratedPalette] = list(all_palettes())

    if vibe:
        narrowed = list(palettes_for_vibe(vibe))
        if narrowed:
            pool = narrowed
    elif direction.vibes:
        narrowed = [p for p in pool if p.vibe in direction.vibes]
        if narrowed:
            pool = narrowed

    if dark_mode is not None:
        narrowed = [p for p in pool if p.dark_mode == dark_mode]
        if narrowed:
            pool = narrowed

    pool = _palette_cohort(pool, direction)
    palette = pool[_seed(project_id, f"palette:{direction.id}") % len(pool)]

    needs_cyrillic = True if require_cyrillic is None else require_cyrillic
    font_pool = _font_cohort(direction, require_cyrillic=needs_cyrillic)
    font_pair = font_pool[_seed(project_id, f"font:{direction.id}") % len(font_pool)]
    display = font_pair.heading
    body = font_pair.body
    return DesignTokens(
        palette=palette,
        display_font=display,
        body_font=body,
        google_fonts_url=font_pair.google_fonts_url or _google_fonts_url(display, body),
        direction_id=direction.id,
        direction_label=direction.label,
        font_pair_name=font_pair.name,
    )


__all__ = ["DesignTokens", "tokens_for_project"]
