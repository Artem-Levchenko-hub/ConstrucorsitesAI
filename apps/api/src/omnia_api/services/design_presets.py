"""Awwwards-tier design presets v3 — declarative blocks для Haiku-копирования.

Восемь пресетов на базе референс-сайтов (carter+co, sentempo, evy, staffan,
sonar, stryds, oblio, bureauborsche). Каждый пресет это ГОТОВЫЕ токены:
палитра HEX + 2 имени Google-шрифтов + 3-5 имён kit-классов из
``assets/omnia-kit.{css,js}`` + tone copywriting'а + anti-patterns.

Дешёвая модель (Claude Haiku 4.5) должна не СОЧИНЯТЬ дизайн, а КОПИРОВАТЬ
known-id из этого файла. Поэтому никаких generation-instructions — только
фактический набор: «возьми этот HEX, поставь этот класс, шрифт этот, copy в
таком тоне». Модель полирует, не творит.

Pipeline:
1. ``preset_classifier.classify_preset(name, template, prompt) -> preset_id`` —
   выбирает один из ключей ``PRESETS``.
2. ``prompt_builder.build_system_prompt(template, preset_id)`` —
   инжектит ``format_preset_block(preset_id)`` СРАЗУ после ``_STYLE_KIT``
   (overrides default ``REFINED MINIMAL``).
3. ``AWWWARDS_PRINCIPLES`` инжектится ВСЕГДА (даже при отсутствии preset_id) —
   это floor качества для генератора.

Источники: ``~/.claude/plans/ethereal-herding-feather.md``,
``docs/09-generated-site-presets.md``, ``docs/05-design-references.md``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesignPreset:
    """Декларативный пресет для генератора сайтов.

    Все поля — данные, не инструкции. Модель копирует HEX/имена/классы
    напрямую в результат. Поведение задаётся через ``layout_signatures`` и
    ``kit_classes`` (известные модели по ``_ANIMATION_KIT``), без сочинения
    CSS/JS.
    """

    id: str
    name: str
    reference_url: str
    one_liner: str
    industries: tuple[str, ...]
    keywords: tuple[str, ...]
    palette: dict[str, str]
    fonts: dict[str, str]
    hero_type: str
    layout_signatures: tuple[str, ...]
    kit_classes: tuple[str, ...]
    copywriting_tone: str
    copywriting_examples: tuple[str, ...]
    anti_patterns: tuple[str, ...]
    section_signature: str


PRESETS: dict[str, DesignPreset] = {
    "editorial-trust": DesignPreset(
        id="editorial-trust",
        name="Editorial Trust",
        reference_url="https://www.carterco.us/",
        one_liner="B2B-услуги, корпоративные с человеческим лицом — ч/б + section-numerals.",
        industries=(
            "B2B услуги",
            "консалтинг",
            "коммерческая недвижимость",
            "корпоративные финансы",
            "юридические услуги",
            "advisory",
            "logistics",
        ),
        keywords=(
            "консалтинг",
            "консультанты",
            "консультант",
            "услуги",
            "advisor",
            "consulting",
            "broker",
            "брокер",
            "недвижимость",
            "B2B",
            "корпоративный",
            "logistics",
            "real estate",
            "корпоратив",
        ),
        palette={
            "bg": "#FFFFFF",
            "bg_alt": "#F4F4F5",
            "fg": "#0A0A0A",
            "muted": "#6B7280",
            "accent": "#0A0A0A",
            "border": "#E5E5E5",
        },
        fonts={
            "display": "Inter Display",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            "section-numerals 0.1/0.2/0.3 над заголовками секций",
            "BW client-logo marquee без цветных лого",
            "headshot основателя круглый, 96px, рядом с цитатой",
            "max-w-4xl центрированная типография в hero",
        ),
        kit_classes=(
            "section-numeral",
            "reveal",
            "fade-up",
            "marquee",
            "divider-fade",
        ),
        copywriting_tone=(
            "Сдержанный, доверительный, от первого лица «мы». Без хайпа, "
            "без капса, без эмодзи. Личная нота в hero-tagline (как «with a Heart»). "
            "Конкретные данные: имя основателя, год создания, число клиентов."
        ),
        copywriting_examples=(
            "Commercial Real Estate with a Heart",
            "Логистика, в которой видно человека",
            "Брокеридж по-старому: рукопожатие важнее презентации",
        ),
        anti_patterns=(
            "НЕ ставить stock-фото офисов в hero",
            "НЕ использовать цветные градиенты — палитра строго ч/б + один muted-серый",
            "НЕ писать «Innovative solutions for modern businesses»",
            "НЕ использовать emoji",
        ),
        section_signature="numerals",
    ),
    "studio-showreel": DesignPreset(
        id="studio-showreel",
        name="Studio Showreel",
        reference_url="https://www.studiosentempo.com/",
        one_liner="Креативные студии, CGI/3D/motion портфолио — mosaic-grid + casual footer.",
        industries=(
            "креативная студия",
            "CGI",
            "3D",
            "motion design",
            "продакшн",
            "арт-дирекция",
            "anime studio",
        ),
        keywords=(
            "студия",
            "studio",
            "CGI",
            "3D",
            "motion",
            "арт-дирекция",
            "креативная",
            "creative",
            "анимация",
            "animation",
            "продакшн",
            "production",
            "моушн",
            "showreel",
            "реел",
            "reel",
        ),
        palette={
            "bg": "#FFFFFF",
            "bg_alt": "#0A0A0A",
            "fg": "#0A0A0A",
            "muted": "#71717A",
            "accent": "#0A0A0A",
            "border": "#D4D4D8",
        },
        fonts={
            "display": "Space Grotesk",
            "body": "DM Sans",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=DM+Sans:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "asymmetric mosaic-grid 12-колонок: чередовать col-span-7/5, col-span-5/7, full-row",
            "[PLAY REEL] видео-CTA крупная, под hero-tagline",
            "крупные кадры работ с .img-zoom при hover",
            "footer-tone разговорный с предложением кофе/коллаборации",
        ),
        kit_classes=(
            "img-zoom",
            "reveal",
            "marquee",
            "shine",
            "tilt",
        ),
        copywriting_tone=(
            "Разговорный, дружелюбный, без корпоратива. Прямой адрес «вы», "
            "приглашение к диалогу. Footer в стиле «mail us, don't be shy — "
            "always happy for coffee or collaborations». Тэги клиентов вместо рейтингов."
        ),
        copywriting_examples=(
            "WE ARE A STUDIO FOCUSED ON CGI, ART DIRECTION & 3D MOTION",
            "Студия моушн-дизайна. Делаем кино из брендов.",
            "PLAY REEL · 2026 · WORK · CONTACT",
        ),
        anti_patterns=(
            "НЕ ставить симметричный 3-card grid",
            "НЕ писать «We are passionate about creativity»",
            "НЕ использовать стоковые abstract-фото — только реальные кадры работ или .blob/.bg-mesh",
        ),
        section_signature="monogram",
    ),
    "saas-product": DesignPreset(
        id="saas-product",
        name="SaaS Product",
        reference_url="https://www.evy.eu/",
        one_liner="B2B SaaS, fintech, insurtech — light + один acid-accent + real UI-моки.",
        industries=(
            "SaaS",
            "fintech",
            "insurtech",
            "B2B продукт",
            "финтех",
            "страхование",
            "API",
            "developer tools",
            "процессинг",
        ),
        keywords=(
            "SaaS",
            "saas",
            "продукт",
            "platform",
            "платформа",
            "fintech",
            "финтех",
            "insurance",
            "страховка",
            "страхование",
            "процессинг",
            "API",
            "интеграция",
            "B2B",
            "dashboard",
        ),
        palette={
            "bg": "#FFFFFF",
            "bg_alt": "#F0FDF4",
            "fg": "#0A0A0A",
            "muted": "#52525B",
            "accent": "#10B981",
            "border": "#E4E4E7",
        },
        fonts={
            "display": "Plus Jakarta Sans",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700&family=Inter:wght@400;500&display=swap",
        },
        hero_type="text-as-hero",
        layout_signatures=(
            "real UI-моки в hero: реальные суммы (€800/₽12 400), IBAN, даты клеймов — НЕ Lorem-цифры",
            "persona-testimonials с фото + ФИО + должность + название реальной компании",
            "client-logo marquee (.marquee) с 6-10 настоящими брендами",
            "ROI-калькулятор или дашборд-скриншот в product-секции",
        ),
        kit_classes=(
            "card-soft",
            "hover-lift",
            "gradient-border",
            "reveal",
            "marquee",
        ),
        copywriting_tone=(
            "Уверенный продуктовый, измеримый. Цифры в hero: «-30% времени», "
            "«€12 400 в месяц», «4 минуты на интеграцию». Testimonials с именем "
            "CEO/CTO и компанией. Без AI-speak («revolutionary», «cutting-edge»)."
        ),
        copywriting_examples=(
            "Product protection made simple — за 4 минуты в чекаут",
            "Страховка велосипедов как часть чекаута. €12/мес, без бумаг.",
            "8 минут от интеграции до первой выплаты",
        ),
        anti_patterns=(
            "НЕ ставить «Trusted by leading brands» без логотипов",
            "НЕ использовать stock-cooperate-handshake фото",
            "НЕ писать «AI-powered next-gen solution»",
            "НЕ ставить два acid-цвета — только ОДИН emerald",
        ),
        section_signature="eyebrow-labels",
    ),
    "scandi-editorial": DesignPreset(
        id="scandi-editorial",
        name="Scandi Editorial",
        reference_url="https://staffansundstrom.com/",
        one_liner="Арт-директора, фотографы, кураторы — off-white, single-column, воздух.",
        industries=(
            "арт-директор",
            "фотограф",
            "куратор",
            "редактор",
            "стилист",
            "иллюстратор",
            "personal portfolio",
            "design portfolio",
        ),
        keywords=(
            "фотограф",
            "photographer",
            "арт-директор",
            "art director",
            "куратор",
            "curator",
            "стилист",
            "stylist",
            "редактор",
            "editor",
            "personal site",
            "личный сайт",
            "портфолио",
            "portfolio",
            "иллюстратор",
        ),
        palette={
            "bg": "#F7F5F1",
            "bg_alt": "#FFFFFF",
            "fg": "#1A1A1A",
            "muted": "#6B6B6B",
            "accent": "#1A1A1A",
            "border": "#E0DDD6",
        },
        fonts={
            "display": "Newsreader",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,400;0,500;1,400&family=Inter:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            "single-column вертикальный каталог, без grid — каждая работа отдельной строкой",
            "воздушный tracking 0.02em–0.04em на навигации",
            "team-credits «Photography by X, styling by Y» под каждой работой",
            "max-w-3xl весь контент центрирован",
        ),
        kit_classes=(
            "reveal",
            "fade-up",
            "img-zoom",
            "divider-fade",
        ),
        copywriting_tone=(
            "Сдержанный, кураторский, от первого лица. Лаконичная биография в hero. "
            "Кредиты команды на каждом проекте. Без рейтингов и звёзд."
        ),
        copywriting_examples=(
            "Иван Петров — арт-директор и фотограф из Санкт-Петербурга",
            "Kinfolk × Fritz Hansen, 2025 — Photography by Sarah Blais",
            "Selected work · 2018–2026",
        ),
        anti_patterns=(
            "НЕ использовать цветные акценты — только off-white + чёрный",
            "НЕ ставить hover-карточки с тенями — только нативный underline на ссылках",
            "НЕ использовать карусели — только вертикальный список",
            "НЕ писать «award-winning» — пусть работы говорят сами",
        ),
        section_signature="none",
    ),
    "festival-brutalist": DesignPreset(
        id="festival-brutalist",
        name="Festival Brutalist",
        reference_url="https://sonar.es/",
        one_liner="Фестивали, музыка, digital arts — тёмный + неон + kinetic-type.",
        industries=(
            "фестиваль",
            "music festival",
            "digital arts",
            "лейбл",
            "электронная музыка",
            "клуб",
            "rave",
            "media art",
            "exhibition",
        ),
        keywords=(
            "фестиваль",
            "festival",
            "музыка",
            "music",
            "электронная",
            "techno",
            "rave",
            "лейбл",
            "label",
            "клуб",
            "club",
            "exhibition",
            "выставка",
            "медиа-арт",
            "digital art",
            "performance",
        ),
        palette={
            "bg": "#0A0A0A",
            "bg_alt": "#171717",
            "fg": "#FAFAFA",
            "muted": "#A1A1AA",
            "accent": "#00FFB2",
            "border": "#262626",
        },
        fonts={
            "display": "Unbounded",
            "body": "JetBrains Mono",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Unbounded:wght@500;700;900&family=JetBrains+Mono:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            ".kinetic-marquee бесконечная лента с названиями артистов/работ",
            "gridless flowing layout — секции наезжают друг на друга через negative margins",
            "displacement/glitch на hover ссылок (через .shine + custom transform)",
            "тайминги в monospace — даты/локации справа от названия",
        ),
        kit_classes=(
            "kinetic-marquee",
            "display-fill",
            "kinetic-type",
            "grain",
            "glow-pulse",
            "bg-mesh",
        ),
        copywriting_tone=(
            "Минималистичный, манифест-стиль, без объяснений. КАПС в hero. "
            "Тайминги в формате 18:00 · 12.06 · MAIN STAGE. Цитаты артистов "
            "от первого лица. Лейблы программ как теги: «AV / LIVE / TALK»."
        ),
        copywriting_examples=(
            "SOUND · LIGHT · NOISE · 2026",
            "VOLNA FESTIVAL — три ночи электронной музыки на Балтике",
            "18:00 · 12.06 · MAIN STAGE",
        ),
        anti_patterns=(
            "НЕ использовать светлый фон — только #0A0A0A",
            "НЕ ставить serif-шрифты — только grotesque + mono",
            "НЕ писать lifestyle-копи («immerse yourself») — только манифест и расписание",
            "НЕ использовать 3-card grid для программы — лента или сетка-афиша",
        ),
        section_signature="monogram",
    ),
    "wellness-casual": DesignPreset(
        id="wellness-casual",
        name="Wellness Casual",
        reference_url="https://stryds.com/",
        one_liner="Mobile-apps, fitness, B2C wellness — light + green + emoji + custom-cursor.",
        industries=(
            "wellness",
            "fitness app",
            "медитация",
            "здоровье",
            "lifestyle app",
            "habit tracker",
            "питание",
            "yoga",
            "mental health",
        ),
        keywords=(
            "wellness",
            "fitness",
            "фитнес",
            "медитация",
            "meditation",
            "здоровье",
            "health",
            "приложение",
            "app",
            "iOS",
            "android",
            "lifestyle",
            "yoga",
            "йога",
            "питание",
            "nutrition",
            "habit",
            "трекер",
        ),
        palette={
            "bg": "#FAFAF7",
            "bg_alt": "#FFFFFF",
            "fg": "#0F172A",
            "muted": "#64748B",
            "accent": "#16A34A",
            "border": "#E2E8F0",
        },
        fonts={
            "display": "Bricolage Grotesque",
            "body": "DM Sans",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@500;700&family=DM+Sans:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            "провокативный problem/solution tagline двумя строками",
            ".cursor-blob (data-cursor=\"blob\" на body) — мягкий следующий за курсором blob",
            "emoji в feedback-сообщениях формы (🥳, 🙌, 💚)",
            "App Store + Google Play badges в hero, не «coming soon»",
        ),
        kit_classes=(
            "cursor-blob",
            "blob",
            "reveal",
            "card-soft",
            "hover-lift",
            "magnetic",
        ),
        copywriting_tone=(
            "Casual, дружелюбный, провокативный. Контраст: проблема короткой "
            "фразой → решение. Использование emoji в copy (умеренно). Tagline "
            "от первого лица «мы» или вызов читателю."
        ),
        copywriting_examples=(
            "Social apps are toxic. Health apps are boring. Мы делаем третье.",
            "Living well > living large 🌿",
            "Скачай и не возвращайся к ленте — мы прислали инвайт 🥳",
        ),
        anti_patterns=(
            "НЕ ставить stock-фото бегущих людей в hero",
            "НЕ использовать корпоративные testimonials с компаниями",
            "НЕ писать «AI-powered wellness coach»",
            "НЕ ставить gradient-кнопки — только flat green",
        ),
        section_signature="eyebrow-labels",
    ),
    "boutique-reel": DesignPreset(
        id="boutique-reel",
        name="Boutique Reel",
        reference_url="https://oblio.io/",
        one_liner="VFX, видео-продакшн, motion-агентства — full-bleed reel + двухколонник.",
        industries=(
            "VFX",
            "video production",
            "видео-продакшн",
            "motion agency",
            "post-production",
            "пост-продакшн",
            "коммерческая видеография",
            "анимация",
        ),
        keywords=(
            "VFX",
            "видео",
            "video",
            "продакшн",
            "production",
            "post-production",
            "пост-продакшн",
            "motion",
            "моушн",
            "агентство",
            "agency",
            "коммерческая",
            "реклама",
            "advertising",
            "клип",
            "ролик",
        ),
        palette={
            "bg": "#0E0E0E",
            "bg_alt": "#1A1A1A",
            "fg": "#FAFAFA",
            "muted": "#A3A3A3",
            "accent": "#FAFAFA",
            "border": "#262626",
        },
        fonts={
            "display": "Archivo",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Archivo:wght@700;900&family=Inter:wght@400;500&display=swap",
        },
        hero_type="video-reel",
        layout_signatures=(
            "full-bleed showreel autoplay muted loop в hero, поверх — крупный логотип агентства",
            "двухколонник: services (slim left) + portfolio grid (wide right) с .img-zoom",
            "капс-заголовки «WHAT WE DO / LATEST WORK / CONTACT» в .display-hero",
            "tangible contact в футере: реальный телефон + адрес + соцсети",
        ),
        kit_classes=(
            "display-hero",
            "display-fill",
            "img-zoom",
            "reveal",
            "marquee",
            "shine",
        ),
        copywriting_tone=(
            "Лаконичный, монументальный, без украшений. КАПС в секциях. "
            "Список клиентов как доказательство (узнаваемые бренды/проекты). "
            "Контакты прямые: телефон с кодом города, email, физический адрес."
        ),
        copywriting_examples=(
            "WHAT WE DO — VFX, motion, post for film and brands",
            "LATEST WORK — Top Gun: Maverick, Sonic 2, HP & The Cursed Child",
            "+7 (812) 555-01-02 · Невский пр. 42, Санкт-Петербург",
        ),
        anti_patterns=(
            "НЕ использовать светлый фон — только #0E0E0E или mono",
            "НЕ писать «We craft visual stories» — конкретные проекты или ничего",
            "НЕ использовать stock-фото камеры/clapboard",
            "НЕ ставить hover-эффекты на видео-плеер",
        ),
        section_signature="caps-monumental",
    ),
    "editorial-publication": DesignPreset(
        id="editorial-publication",
        name="Editorial Publication",
        reference_url="https://bureauborsche.com/",
        one_liner="Журналы, культурные проекты, редакция — serif display + justified + film-grain.",
        industries=(
            "журнал",
            "magazine",
            "culture",
            "культурный проект",
            "редакция",
            "publication",
            "newsletter",
            "literary",
            "art publication",
            "издание",
        ),
        keywords=(
            "журнал",
            "magazine",
            "издание",
            "publication",
            "редакция",
            "editorial",
            "newsletter",
            "рассылка",
            "литературный",
            "literary",
            "культура",
            "culture",
            "искусство",
            "art",
            "критика",
            "criticism",
            "issue",
            "выпуск",
        ),
        palette={
            "bg": "#F4F1EC",
            "bg_alt": "#FFFFFF",
            "fg": "#1A1A1A",
            "muted": "#737373",
            "accent": "#B91C1C",
            "border": "#D6D3D1",
        },
        fonts={
            "display": "Fraunces",
            "body": "Newsreader",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,900&family=Newsreader:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            ".justified-prose длинные абзацы с переносами в основном тексте",
            ".film-grain поверх hero-фото — редакционная фактура",
            "footnote-style eyebrows в формате «¹ Issue 12 · Spring 2026»",
            "табличная typeset для оглавления: номер | автор | название | страница",
        ),
        kit_classes=(
            "justified-prose",
            "film-grain",
            "reveal",
            "divider-fade",
            "img-zoom",
        ),
        copywriting_tone=(
            "Литературный, эссеистский, без обращений. Длинные предложения. "
            "Подписи авторов с именем и кратким био. Tagline-манифест в "
            "одном абзаце без буллетов."
        ),
        copywriting_examples=(
            "Журнал о современном искусстве и архитектуре. Issue 12, весна 2026.",
            "¹ Мария Иванова, искусствовед, Москва",
            "СОДЕРЖАНИЕ · 12 эссе, 4 интервью, 1 манифест",
        ),
        anti_patterns=(
            "НЕ использовать grotesque шрифты для display — только serif (Fraunces/Newsreader)",
            "НЕ ставить sticky-CTA «Subscribe now» — только тихая ссылка в футере",
            "НЕ использовать hover-карточки — нативный underline по тексту",
            "НЕ ставить градиенты — fond палитра muted",
        ),
        section_signature="footnote",
    ),
    # ────────────────────────────────────────────────────────────────────
    # Wave 2 (2026-06-01) — реальный SMB-рынок. Раньше эти вертикали падали в
    # editorial-trust (ч/б B2B-консалтинг) = чужой вайб. Теперь у каждой свой
    # характер: палитра + шрифты + layout + тематика фото + copy-тон.
    # ────────────────────────────────────────────────────────────────────
    "restaurant-warm": DesignPreset(
        id="restaurant-warm",
        name="Restaurant Warm",
        reference_url="https://www.noahrestaurant.example",
        one_liner="Рестораны, кафе, суши, бары — тёплая палитра + крупные фото блюд + меню-эдиториал.",
        industries=(
            "ресторан", "кафе", "бар", "суши", "пиццерия", "кофейня",
            "бистро", "пекарня", "доставка еды", "стейк-хаус",
        ),
        keywords=(
            "ресторан", "restaurant", "кафе", "cafe", "бар", "bar", "суши",
            "sushi", "пицц", "pizza", "кофейня", "coffee", "бистро", "bistro",
            "пекарн", "bakery", "еда", "food", "кухня", "kitchen", "меню",
            "menu", "доставка еды", "бургер", "burger", "винотека", "паб",
        ),
        palette={
            "bg": "#FBF6EE",
            "bg_alt": "#FFFFFF",
            "fg": "#1C1410",
            "muted": "#7A6A5A",
            "accent": "#B23A1E",
            "border": "#E7DDCD",
        },
        fonts={
            "display": "Fraunces",
            "body": "DM Sans",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=DM+Sans:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "full-bleed hero-фото фирменного блюда/интерьера, поверх — название + одна строка вкуса",
            "меню как эдиториал-список: блюдо · короткое описание · цена ₽ в одну строку, не карточки",
            "крупная фото-галерея блюд .img-zoom (3-6 кадров), не стоковые тарелки",
            "бронь столика / заказ — липкая CTA + часы работы и адрес рядом",
        ),
        kit_classes=("img-zoom", "reveal", "fade-up", "divider-fade", "grain"),
        copywriting_tone=(
            "Аппетитный, чувственный, конкретный. Названия блюд, ингредиенты, "
            "происхождение продуктов. Без «вкусно и недорого» — конкретика: "
            "«дальневосточный гребешок», «тесто на 48-часовой опаре»."
        ),
        copywriting_examples=(
            "Сакура — суши на рыбе с утреннего аукциона Цукидзи",
            "Паста на яйце, как в Болонье. Каждое утро — свежая.",
            "Бронь столика · Пн-Вс 12:00–23:00 · Большая Дмитровка, 7",
        ),
        anti_patterns=(
            "НЕ ставить generic-стоковые тарелки — только тематические аппетитные кадры",
            "НЕ делать 3 одинаковые карточки «о нас / меню / контакты»",
            "НЕ писать «вкусная еда в уютной атмосфере»",
            "НЕ использовать холодные сине-фиолетовые акценты — палитра тёплая",
        ),
        section_signature="eyebrow-labels",
    ),
    "retail-product": DesignPreset(
        id="retail-product",
        name="Retail Product",
        reference_url="https://www.shopflagship.example",
        one_liner="Магазины, e-commerce, бренды товаров — product-forward grid + чистый light + 1 акцент.",
        industries=(
            "магазин", "ретейл", "e-commerce", "бренд одежды", "маркетплейс",
            "интернет-магазин", "товары", "shop", "store",
        ),
        keywords=(
            "магазин", "shop", "store", "ретейл", "retail", "ecommerce",
            "e-commerce", "интернет-магазин", "бренд", "brand", "товар",
            "product", "одежда", "clothing", "обувь", "аксессуар", "каталог",
            "коллекция", "marketplace", "маркетплейс", "купить",
        ),
        palette={
            "bg": "#FFFFFF",
            "bg_alt": "#F6F6F4",
            "fg": "#111111",
            "muted": "#6B7280",
            "accent": "#1D4ED8",
            "border": "#E7E7E4",
        },
        fonts={
            "display": "Plus Jakarta Sans",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero — крупный продукт-кадр (lifestyle или packshot) + название коллекции + CTA «Купить»",
            "product-grid: карточка = фото .img-zoom + название + цена ₽ + быстрый add-to-cart",
            "free-shipping / гарантия — тонкая лента над хедером",
            "lookbook / подборка образов с asymmetric mosaic, не симметричные 3 карточки",
        ),
        kit_classes=("img-zoom", "hover-lift", "card-soft", "reveal", "marquee"),
        copywriting_tone=(
            "Чёткий, продающий, без воды. Материалы, размеры, выгода. "
            "Цены настоящие ₽. Социальное доказательство: число заказов, отзывы "
            "с именами. Без «качественные товары по выгодным ценам»."
        ),
        copywriting_examples=(
            "Худи на плотном футере 380 г/м². Российский пошив.",
            "12 400 заказов за сезон · доставка 1–2 дня по РФ",
            "Новая коллекция · Осень 2026",
        ),
        anti_patterns=(
            "НЕ ставить пустой hero без товара",
            "НЕ прятать цену — она всегда видна на карточке",
            "НЕ использовать stock-handshake / corporate-фото",
            "НЕ ставить два-три акцент-цвета — один синий",
        ),
        section_signature="eyebrow-labels",
    ),
    "beauty-elegant": DesignPreset(
        id="beauty-elegant",
        name="Beauty Elegant",
        reference_url="https://www.maisonbeaute.example",
        one_liner="Салоны красоты, спа, барбершопы, косметология — мягкая премиум-палитра + serif + воздух.",
        industries=(
            "салон красоты", "спа", "барбершоп", "косметология", "ногтевая студия",
            "парикмахерская", "массаж", "бьюти", "эпиляция", "брови",
        ),
        keywords=(
            "салон", "salon", "красот", "beauty", "спа", "spa", "барбершоп",
            "barber", "косметолог", "ногт", "nail", "маникюр", "парикмахер",
            "массаж", "massage", "эпиляц", "брови", "ресниц", "макияж", "уход",
        ),
        palette={
            "bg": "#F7F1EC",
            "bg_alt": "#FFFFFF",
            "fg": "#2A2320",
            "muted": "#9C8B7E",
            "accent": "#A8763E",
            "border": "#E8DDD3",
        },
        fonts={
            "display": "Cormorant Garamond",
            "body": "Mulish",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Mulish:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero — атмосферное фото интерьера/процедуры + тонкий serif-заголовок, много воздуха",
            "прайс услуг сгруппирован по категориям: услуга · длительность · цена ₽",
            "до/после или галерея работ .img-zoom",
            "онлайн-запись CTA + мастера с фото и специализацией",
        ),
        kit_classes=("img-zoom", "reveal", "fade-up", "divider-fade", "hover-lift"),
        copywriting_tone=(
            "Изысканный, заботливый, тактильный. Названия процедур, бренды "
            "косметики, длительность. Без «качественные услуги красоты» — "
            "конкретика: «окрашивание AirTouch», «уходовая косметика Davines»."
        ),
        copywriting_examples=(
            "Стрижка и стайлинг у мастеров с опытом 8+ лет",
            "Маникюр с покрытием · 90 мин · 2 800 ₽",
            "Запишитесь онлайн · Пн-Вс 10:00–21:00",
        ),
        anti_patterns=(
            "НЕ использовать кричащие неоновые цвета — мягкая тёплая премиум-гамма",
            "НЕ ставить stock-фото улыбающихся моделей — реальные работы/интерьер",
            "НЕ делать 3 одинаковые карточки услуг — сгруппированный прайс",
            "НЕ писать «индивидуальный подход к каждому клиенту»",
        ),
        section_signature="eyebrow-labels",
    ),
    "medical-clinic": DesignPreset(
        id="medical-clinic",
        name="Medical Clinic",
        reference_url="https://www.cliniccare.example",
        one_liner="Клиники, стоматология, медцентры, врачи — спокойный clean-trust, тёплый teal, без fitness-зелени.",
        industries=(
            "клиника", "стоматология", "медцентр", "поликлиника", "врач",
            "диагностика", "лаборатория", "медицина", "приём", "хирургия",
        ),
        keywords=(
            "клиник", "clinic", "стомат", "dental", "медцентр", "поликлин",
            "врач", "doctor", "медицин", "medical", "диагностик", "лаборатор",
            "приём", "анализ", "узи", "терапевт", "хирург", "пациент", "здоровье",
        ),
        palette={
            "bg": "#FBFDFD",
            "bg_alt": "#FFFFFF",
            "fg": "#0F2A33",
            "muted": "#5B7682",
            "accent": "#0E8C8C",
            "border": "#DCE9EA",
        },
        fonts={
            "display": "Onest",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Onest:wght@500;600;700&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero — спокойный кадр клиники/врача + чёткий оффер + CTA «Записаться»",
            "услуги с ценами ₽ сгруппированы по направлениям",
            "врачи: фото + ФИО + специализация + стаж + категория (доверие)",
            "лицензии / сертификаты / оборудование — реальные trust-сигналы",
        ),
        kit_classes=("card-soft", "hover-lift", "reveal", "fade-up", "divider-fade"),
        copywriting_tone=(
            "Спокойный, профессиональный, без давления. Конкретные процедуры, "
            "стаж врачей, оборудование. Без «качественная медицина с заботой» — "
            "факты: «КТ Siemens», «врачи высшей категории, стаж 15+ лет»."
        ),
        copywriting_examples=(
            "Имплантация под ключ за один визит. Гарантия 10 лет.",
            "Приём терапевта · от 2 000 ₽ · запись на сегодня",
            "12 врачей высшей категории · собственная лаборатория",
        ),
        anti_patterns=(
            "НЕ использовать ярко-зелёную fitness-палитру — спокойный медицинский teal",
            "НЕ ставить stock-фото врачей с пальцем вверх",
            "НЕ писать «индивидуальный подход и забота о здоровье»",
            "НЕ прятать цены приёма — указывать «от X ₽»",
        ),
        section_signature="numerals",
    ),
    "realestate-premium": DesignPreset(
        id="realestate-premium",
        name="Real Estate Premium",
        reference_url="https://www.estatehaus.example",
        one_liner="Недвижимость, новостройки, агентства — премиум, крупные фото объектов, тёплый бронзовый акцент.",
        industries=(
            "недвижимость", "новостройка", "жилой комплекс", "квартиры",
            "агентство недвижимости", "загородная недвижимость", "коттедж",
            "апартаменты", "девелопер",
        ),
        keywords=(
            "недвиж", "realestate", "real estate", "новострой", "жк ",
            "жилой комплекс", "квартир", "apartment", "коттедж", "таунхаус",
            "девелопер", "застройщик", "апартамент", "ипотек", "планировк",
            "продажа квартир", "аренда жилья",
        ),
        palette={
            "bg": "#FAF8F4",
            "bg_alt": "#FFFFFF",
            "fg": "#1A1814",
            "muted": "#807666",
            "accent": "#9A6B3F",
            "border": "#E6DFD4",
        },
        fonts={
            "display": "Spectral",
            "body": "Work Sans",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Spectral:wght@500;600;700&family=Work+Sans:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero — крупный кадр объекта/рендера + название ЖК + ключевые цифры (этаж, м², от ₽)",
            "карточки объектов: фото .img-zoom + планировка + площадь + цена от ₽/мес или итого",
            "интерактив-намёк: карта расположения + инфраструктура рядом",
            "галерея интерьеров/видов + форма заявки на просмотр с именем агента",
        ),
        kit_classes=("img-zoom", "card-soft", "hover-lift", "reveal", "parallax"),
        copywriting_tone=(
            "Премиальный, предметный, цифровой. Метражи, этажи, сроки сдачи, "
            "цены ₽. Локация как ценность. Без «дом вашей мечты» — конкретика: "
            "«потолки 3.2 м», «сдача Q4 2026», «5 минут до метро»."
        ),
        copywriting_examples=(
            "Клубный дом на 28 резиденций. Потолки 3.4 м, сдача — Q2 2027.",
            "2-комнатная, 64 м², 7 этаж — от 14,9 млн ₽",
            "Запишитесь на просмотр · агент Анна, +7 (495) 120-30-40",
        ),
        anti_patterns=(
            "НЕ ставить generic-рендер «небоскрёбы на закате»",
            "НЕ прятать цены и метражи",
            "НЕ использовать кричащие акценты — сдержанный бронзовый/тёплый нейтрал",
            "НЕ писать «квартиры вашей мечты по доступным ценам»",
        ),
        section_signature="numerals",
    ),
    "hospitality-escape": DesignPreset(
        id="hospitality-escape",
        name="Hospitality Escape",
        reference_url="https://www.lodgeretreat.example",
        one_liner="Отели, базы отдыха, глэмпинг, тревел — атмосферные крупные фото + глубокий зелёный акцент.",
        industries=(
            "отель", "гостиница", "база отдыха", "глэмпинг", "санаторий",
            "тревел", "аренда дома", "хостел", "курорт", "загородный клуб",
        ),
        keywords=(
            "отель", "hotel", "гостиниц", "база отдыха", "глэмпинг", "glamping",
            "санатори", "курорт", "resort", "тревел", "travel", "номер",
            "бронирован", "booking", "аренда дома", "хостел", "отдых", "тур",
        ),
        palette={
            "bg": "#F5F2EA",
            "bg_alt": "#FFFFFF",
            "fg": "#1B241E",
            "muted": "#6F7A6F",
            "accent": "#2F5D4C",
            "border": "#DDE2D6",
        },
        fonts={
            "display": "Fraunces",
            "body": "Karla",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Karla:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "full-bleed атмосферный кадр локации + название + одна строка настроения",
            "номера/домики: фото .img-zoom + вместимость + удобства + цена ₽/ночь",
            "удобства иконками + сезонные предложения",
            "виджет-намёк брони (даты/гости) + галерея видов + карта",
        ),
        kit_classes=("img-zoom", "reveal", "fade-up", "parallax", "divider-fade"),
        copywriting_tone=(
            "Атмосферный, чувственный, манящий. Виды, тишина, локальный колорит. "
            "Конкретика: «15 домиков у озера», «завтрак из фермерских продуктов», "
            "«2 часа от Москвы». Без «незабываемый отдых в комфорте»."
        ),
        copywriting_examples=(
            "Глэмпинг на берегу Ладоги. 12 куполов, баня на дровах, тишина.",
            "Домик у озера · 2–4 гостя · от 9 500 ₽/ночь",
            "Забронируйте даты · заезд 15:00 · 2 часа от СПб",
        ),
        anti_patterns=(
            "НЕ ставить generic-стоковый пляж/пальмы",
            "НЕ делать симметричную сетку из 3 карточек",
            "НЕ писать «незабываемый отдых для всей семьи»",
            "НЕ использовать холодные неоновые цвета — природная тёплая гамма",
        ),
        section_signature="eyebrow-labels",
    ),
    "education-bright": DesignPreset(
        id="education-bright",
        name="Education Bright",
        reference_url="https://www.learnup.example",
        one_liner="Курсы, школы, онлайн-обучение, репетиторы — дружелюбный, структурный, оптимистичный акцент.",
        industries=(
            "курсы", "онлайн-курс", "школа", "обучение", "образование",
            "репетитор", "тренинг", "академия", "университет", "буткемп",
        ),
        keywords=(
            "курс", "course", "школ", "school", "обучен", "education",
            "образован", "репетитор", "tutor", "тренинг", "training",
            "академи", "academy", "университет", "буткемп", "урок", "студент",
            "научит", "программа обучения", "вебинар",
        ),
        palette={
            "bg": "#FFFDF8",
            "bg_alt": "#FFFFFF",
            "fg": "#15233B",
            "muted": "#5E6B82",
            "accent": "#E0A100",
            "border": "#EAE6DC",
        },
        fonts={
            "display": "Sora",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700&family=Inter:wght@400;500&display=swap",
        },
        hero_type="text-as-hero",
        layout_signatures=(
            "hero — сильный оффер + результат обучения + CTA «Записаться», цифры (выпускников, трудоустройство)",
            "программа как модули/уроки списком с длительностью и темами",
            "преподаватели: фото + опыт + где работали",
            "результаты/отзывы студентов с именами + outcome-метрики",
        ),
        kit_classes=("card-soft", "hover-lift", "reveal", "fade-up", "marquee"),
        copywriting_tone=(
            "Дружелюбный, мотивирующий, конкретный по результату. Что освоит "
            "студент, за сколько, какой итог. Без «качественное образование для "
            "будущего» — факты: «12 недель», «портфолио из 5 проектов», «87% трудоустройство»."
        ),
        copywriting_examples=(
            "Стань frontend-разработчиком за 6 месяцев. С нуля до оффера.",
            "12 недель · 5 проектов в портфолио · ментор из индустрии",
            "1 200 выпускников · 87% нашли работу за 3 месяца",
        ),
        anti_patterns=(
            "НЕ писать «качественное образование для светлого будущего»",
            "НЕ ставить stock-фото студентов с ноутбуками",
            "НЕ прятать цену и длительность курса",
            "НЕ делать 3 безликие карточки «почему мы»",
        ),
        section_signature="eyebrow-labels",
    ),
    "local-services": DesignPreset(
        id="local-services",
        name="Local Services",
        reference_url="https://www.handyfix.example",
        one_liner="Локальные услуги: ремонт, клининг, сантехник, мастер, доставка — практичный trust + сильный CTA «Позвонить».",
        industries=(
            "ремонт", "клининг", "сантехник", "электрик", "мастер на час",
            "услуги", "грузоперевозки", "автосервис", "эвакуатор", "монтаж",
        ),
        keywords=(
            "ремонт", "repair", "клининг", "cleaning", "уборк", "сантехник",
            "электрик", "мастер", "услуг", "service", "грузопере", "переезд",
            "автосервис", "шиномонтаж", "эвакуатор", "монтаж", "установка",
            "вызов", "выезд", "под ключ",
        ),
        palette={
            "bg": "#FFFFFF",
            "bg_alt": "#F3F5F7",
            "fg": "#10151C",
            "muted": "#5C6975",
            "accent": "#E2620E",
            "border": "#E2E6EA",
        },
        fonts={
            "display": "Archivo",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero — оффер + цена «от ₽» + крупная CTA «Позвонить» / «Вызвать мастера», телефон виден сразу",
            "услуги с фиксированными ценами ₽ + сроки выполнения",
            "зона обслуживания / выезд + гарантия на работы",
            "реальные отзывы с именами + фото выполненных работ .img-zoom",
        ),
        kit_classes=("card-soft", "hover-lift", "reveal", "fade-up", "img-zoom"),
        copywriting_tone=(
            "Практичный, прямой, доверительный. Цена, срок, гарантия, район. "
            "Телефон везде. Без «качественные услуги по доступным ценам» — "
            "конкретика: «выезд за 30 минут», «гарантия 2 года», «работаем 24/7»."
        ),
        copywriting_examples=(
            "Прочистка канализации за 1 час. Выезд по Москве за 30 минут.",
            "Уборка квартиры от 2 500 ₽ · гарантия чистоты или переделаем",
            "Позвонить +7 (495) 000-11-22 · работаем 24/7",
        ),
        anti_patterns=(
            "НЕ прятать телефон и цены — они всегда на виду",
            "НЕ ставить stock-фото улыбающихся уборщиц",
            "НЕ писать «команда профессионалов своего дела»",
            "НЕ растягивать на длинный лендинг без CTA — звонок в каждом экране",
        ),
        section_signature="eyebrow-labels",
    ),
    "law-authority": DesignPreset(
        id="law-authority",
        name="Law Authority",
        reference_url="https://www.lexpartners.example",
        one_liner="Юр-фирмы, адвокаты, нотариусы — авторитетный serif, глубокий навы + бордовый акцент.",
        industries=(
            "юридические услуги", "адвокат", "юрист", "право", "нотариус",
            "юридическая фирма", "арбитраж", "налоговый консультант", "legal",
        ),
        keywords=(
            "юрист", "юридическ", "адвокат", "lawyer", "legal", "право",
            "нотариус", "арбитраж", "суд", "иск", "договор", "налоговый",
            "банкротство", "юрфирма", "правовой", "litigation", "attorney",
        ),
        palette={
            "bg": "#FBFAF7",
            "bg_alt": "#FFFFFF",
            "fg": "#14213D",
            "muted": "#5A6378",
            "accent": "#7A2230",
            "border": "#E2E0D8",
        },
        fonts={
            "display": "Libre Franklin",
            "body": "Lora",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@600;700;800&family=Lora:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            "hero без фото: крупный авторитетный заголовок + специализация + CTA «Консультация»",
            "практики/направления списком с section-numerals",
            "адвокаты: фото + регалии + стаж + выигранные дела",
            "кейсы/результаты с цифрами (взыскано ₽, % выигранных дел) + конфиденциальность",
        ),
        kit_classes=("section-numeral", "reveal", "fade-up", "divider-fade"),
        copywriting_tone=(
            "Сдержанный, авторитетный, доказательный. Стаж, специализация, "
            "результаты в цифрах. Без «надёжная защита ваших интересов» — "
            "факты: «взыскано 240 млн ₽», «18 лет практики», «специализация — арбитраж»."
        ),
        copywriting_examples=(
            "Защита бизнеса в арбитраже. 18 лет практики, взыскано 240 млн ₽.",
            "Банкротство физлиц под ключ. Спишем долги законно.",
            "Первая консультация · бесплатно · конфиденциально",
        ),
        anti_patterns=(
            "НЕ ставить stock-фото молотка судьи и весов Фемиды",
            "НЕ писать «надёжная правовая защита ваших интересов»",
            "НЕ использовать яркие цвета — глубокий навы + сдержанный бордовый",
            "НЕ делать кричащие CTA — спокойный авторитет",
        ),
        section_signature="numerals",
    ),
    # ────────────────────────────────────────────────────────────────────
    # Wave 3 (2026-06-01) — добор до ~30 вертикалей.
    # ────────────────────────────────────────────────────────────────────
    "auto-showroom": DesignPreset(
        id="auto-showroom",
        name="Auto Showroom",
        reference_url="https://www.drivehaus.example",
        one_liner="Автосалоны, детейлинг, тюнинг, прокат — тёмный металлик + крупные кадры авто + электрик-акцент.",
        industries=(
            "автосалон", "автомобили", "детейлинг", "тюнинг", "прокат авто",
            "автодилер", "шиномонтаж", "каршеринг", "автомойка",
        ),
        keywords=(
            "автосалон", "автомобил", "авто", "car", "детейлинг", "detailing",
            "тюнинг", "прокат авто", "дилер", "каршеринг", "автомойк",
            "тест-драйв", "мотоцикл", "электромобил",
        ),
        palette={
            "bg": "#0E0F12",
            "bg_alt": "#17191F",
            "fg": "#F4F5F7",
            "muted": "#9AA1AD",
            "accent": "#3BA7FF",
            "border": "#262932",
        },
        fonts={
            "display": "Archivo",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Archivo:wght@700;800;900&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "full-bleed кадр авто (3/4 ракурс) + модель + ключевые ТТХ цифрами",
            "карточки моделей: фото .img-zoom + двигатель/разгон/цена от ₽",
            "конфигуратор-намёк (цвет/комплектация) + тест-драйв CTA",
            "spec-таблица в mono + галерея интерьера/экстерьера",
        ),
        kit_classes=("img-zoom", "reveal", "shine", "tilt", "parallax"),
        copywriting_tone=(
            "Технологичный, уверенный, цифровой. ТТХ, разгон, расход, цена ₽. "
            "Без «автомобиль вашей мечты» — факты: «0–100 за 4.2 с», «запас хода 520 км»."
        ),
        copywriting_examples=(
            "Запас хода 520 км. Разгон 0–100 за 4.2 с. От 4,9 млн ₽.",
            "Тест-драйв сегодня · 12 моделей в наличии",
            "Trade-in за 30 минут · кредит от 2.9%",
        ),
        anti_patterns=(
            "НЕ ставить stock-фото случайных машин — конкретные модели салона",
            "НЕ прятать цену и ТТХ",
            "НЕ писать «автомобиль вашей мечты по выгодной цене»",
            "НЕ использовать светлый «бьюти»-фон — тёмный металлик",
        ),
        section_signature="caps-monumental",
    ),
    "fashion-runway": DesignPreset(
        id="fashion-runway",
        name="Fashion Runway",
        reference_url="https://www.atelier-mode.example",
        one_liner="Фэшн-бренды, дизайнеры одежды, бутики — эдиториал ч/б + один акцент + крупный lookbook.",
        industries=(
            "фэшн", "бренд одежды", "дизайнер одежды", "бутик", "модный дом",
            "ателье", "showroom одежды", "коллекция одежды",
        ),
        keywords=(
            "фэшн", "fashion", "мода", "бренд одежды", "дизайнер одежды",
            "бутик", "boutique", "коллекци", "lookbook", "подиум", "runway",
            "ателье", "couture", "стиль", "одежда бренд",
        ),
        palette={
            "bg": "#FFFFFF",
            "bg_alt": "#0A0A0A",
            "fg": "#0A0A0A",
            "muted": "#737373",
            "accent": "#C8102E",
            "border": "#E5E5E5",
        },
        fonts={
            "display": "Archivo",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Archivo:wght@600;800;900&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "full-bleed lookbook-кадр модели + название коллекции крупным кеглем",
            "asymmetric mosaic из образов .img-zoom, чередование портрет/деталь",
            "вертикальные капс-лейблы секций (SS26 / LOOKBOOK / SHOP)",
            "минимум текста — фото говорят; цены тихие, под образом",
        ),
        kit_classes=("img-zoom", "reveal", "marquee", "display-fill", "shine"),
        copywriting_tone=(
            "Лаконичный, эдиториальный, дерзкий. Название коллекции, сезон, "
            "ткани. Без «стильная одежда для современных людей» — манифест бренда."
        ),
        copywriting_examples=(
            "SS26 — НОЧНОЙ САД. Шёлк, бархат, тишина.",
            "Сделано в Москве. Лимит 50 изделий на дроп.",
            "LOOKBOOK · SHOP · ABOUT",
        ),
        anti_patterns=(
            "НЕ ставить generic-сетку товаров как у масс-маркета — это эдиториал",
            "НЕ писать «качественная одежда по доступным ценам»",
            "НЕ использовать яркую радугу — ч/б + один акцент",
            "НЕ перегружать текстом — фото доминирует",
        ),
        section_signature="caps-monumental",
    ),
    "fitness-power": DesignPreset(
        id="fitness-power",
        name="Fitness Power",
        reference_url="https://www.ironbox.example",
        one_liner="Тренажёрные залы, кроссфит, бокс, студии — тёмный энергичный + кислотный акцент + кадры тренировок.",
        industries=(
            "фитнес-зал", "тренажёрный зал", "кроссфит", "бокс", "студия тренировок",
            "качалка", "единоборства", "пилатес-студия", "танцы",
        ),
        keywords=(
            "тренажёрн", "фитнес-зал", "фитнес зал", "кроссфит", "crossfit",
            "бокс", "качалк", "единоборств", "тренировк", "зал", "gym",
            "студия", "пилатес", "растяжк", "функциональн", "силов",
        ),
        palette={
            "bg": "#0B0D0E",
            "bg_alt": "#16191B",
            "fg": "#F5F7F6",
            "muted": "#94A0A3",
            "accent": "#C6F432",
            "border": "#242a2c",
        },
        fonts={
            "display": "Unbounded",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Unbounded:wght@600;800;900&family=Inter:wght@400;600&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "энергичный hero-кадр тренировки + дерзкий капс-оффер + CTA «Первая тренировка»",
            "расписание/направления сеткой + цены абонементов ₽",
            "тренеры: фото + специализация + регалии (КМС, чемпион)",
            "трансформации до/после + отзывы с именами",
        ),
        kit_classes=("img-zoom", "kinetic-marquee", "reveal", "glow-pulse", "grain"),
        copywriting_tone=(
            "Энергичный, мотивирующий, прямой. Вызов, результат, цифры. КАПС в "
            "hero. Без «фитнес для здорового образа жизни» — «−8 кг за 2 месяца», "
            "«первая тренировка бесплатно»."
        ),
        copywriting_examples=(
            "ХВАТИТ ОТКЛАДЫВАТЬ. Первая тренировка — бесплатно.",
            "Абонемент на месяц · безлимит · 4 900 ₽",
            "12 тренеров · 40 групповых в неделю · зал 1200 м²",
        ),
        anti_patterns=(
            "НЕ использовать спокойную пастель — тёмный + кислотный драйв",
            "НЕ ставить stock-фото с идеальными моделями — реальный зал/люди",
            "НЕ писать «здоровый образ жизни для всей семьи»",
            "НЕ делать 3 безликие карточки услуг",
        ),
        section_signature="monogram",
    ),
    "kids-playful": DesignPreset(
        id="kids-playful",
        name="Kids Playful",
        reference_url="https://www.littlestars.example",
        one_liner="Детсады, детские центры, развивашки, детские товары — тёплый яркий + округлые формы + игривость.",
        industries=(
            "детский сад", "детский центр", "развивашки", "детские товары",
            "детская студия", "няня", "детский лагерь", "продлёнка",
        ),
        keywords=(
            "детск", "садик", "детский сад", "развивашк", "ребён", "малыш",
            "kids", "children", "няня", "лагерь", "продлёнк", "монтессори",
            "дошкольн", "игров", "развитие детей",
        ),
        palette={
            "bg": "#FFF8EE",
            "bg_alt": "#FFFFFF",
            "fg": "#2B2331",
            "muted": "#7A7186",
            "accent": "#FF7A45",
            "border": "#F1E6D6",
        },
        fonts={
            "display": "Bricolage Grotesque",
            "body": "Nunito",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700;800&family=Nunito:wght@400;600;700&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "тёплый hero-кадр детей/занятий + дружелюбный оффер + CTA «Записаться»",
            "программы/кружки карточками с округлыми углами и иконками",
            "blob-формы и мягкие иллюстрации вместо строгих линий",
            "расписание + цены ₽ + воспитатели/педагоги с фото",
        ),
        kit_classes=("blob", "card-soft", "hover-lift", "reveal", "cursor-blob"),
        copywriting_tone=(
            "Тёплый, заботливый, для родителей. Безопасность, программа, питание. "
            "Без «всестороннее развитие вашего ребёнка» — «группы по 12 детей», "
            "«монтессори-педагоги», «своя кухня»."
        ),
        copywriting_examples=(
            "Детский сад, куда дети бегут с утра. Группы по 12.",
            "Монтессори-среда · своя кухня · английский с 3 лет",
            "Запишитесь на пробный день · от 28 000 ₽/мес",
        ),
        anti_patterns=(
            "НЕ использовать корпоративную холодную палитру — тёплая игривая",
            "НЕ ставить stock-фото идеальных детей — реальные группы/занятия",
            "НЕ писать «всестороннее гармоничное развитие личности»",
            "НЕ делать острые брутальные формы — округлость",
        ),
        section_signature="eyebrow-labels",
    ),
    "event-celebration": DesignPreset(
        id="event-celebration",
        name="Event Celebration",
        reference_url="https://www.loveandco.example",
        one_liner="Свадьбы, ивенты, банкетные залы, организация — элегантный романтичный + serif + мягкая палитра.",
        industries=(
            "свадьба", "ивент", "организация мероприятий", "банкетный зал",
            "праздник", "event-агентство", "юбилей", "корпоратив-ивент", "декор",
        ),
        keywords=(
            "свадьб", "wedding", "ивент", "event", "мероприят", "банкет",
            "праздник", "юбилей", "торжеств", "декор", "флорист", "ведущ",
            "организац праздник", "выездн регистрац",
        ),
        palette={
            "bg": "#F8F4EF",
            "bg_alt": "#FFFFFF",
            "fg": "#33291F",
            "muted": "#9A8C7B",
            "accent": "#B08D57",
            "border": "#EBE2D6",
        },
        fonts={
            "display": "Playfair Display",
            "body": "Karla",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&family=Karla:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "full-bleed атмосферный кадр события + изящный serif-заголовок",
            "форматы/пакеты услуг с описанием и ценой от ₽",
            "галерея проведённых событий .img-zoom + отзывы пар/клиентов",
            "тайминг-намёк дня + форма заявки с датой",
        ),
        kit_classes=("img-zoom", "reveal", "fade-up", "divider-fade", "film-grain"),
        copywriting_tone=(
            "Тёплый, романтичный, изящный. Эмоция + конкретика пакетов. Без "
            "«незабываемый праздник под ключ» — «100 гостей», «выездная "
            "регистрация на берегу», «декор в стиле бохо»."
        ),
        copywriting_examples=(
            "Свадьба, которую вспоминают годами. Под ключ, без хлопот.",
            "Пакет «Камерная» · до 40 гостей · от 180 000 ₽",
            "Оставьте дату — соберём смету за 1 день",
        ),
        anti_patterns=(
            "НЕ ставить generic-стоковые букеты/кольца",
            "НЕ писать «организуем праздник вашей мечты»",
            "НЕ использовать кричащие цвета — мягкая тёплая гамма",
            "НЕ делать 3 одинаковые карточки",
        ),
        section_signature="eyebrow-labels",
    ),
    "crypto-web3": DesignPreset(
        id="crypto-web3",
        name="Crypto Web3",
        reference_url="https://www.chainflux.example",
        one_liner="Крипто, web3, blockchain, DeFi, NFT — тёмный футуризм + неон-cyan/emerald + mono-данные.",
        industries=(
            "крипто", "web3", "blockchain", "DeFi", "NFT", "токен",
            "криптобиржа", "кошелёк", "DAO", "стейкинг",
        ),
        keywords=(
            "крипт", "crypto", "web3", "blockchain", "блокчейн", "defi",
            "nft", "токен", "token", "биржа", "wallet", "кошелёк", "dao",
            "стейкинг", "staking", "майнинг", "смарт-контракт",
        ),
        palette={
            "bg": "#070B12",
            "bg_alt": "#0E1521",
            "fg": "#EAF1F8",
            "muted": "#8595AB",
            "accent": "#1FE0A8",
            "border": "#1A2436",
        },
        fonts={
            "display": "Space Grotesk",
            "body": "JetBrains Mono",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=JetBrains+Mono:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            "тёмный hero с .bg-mesh/.blob + крупный оффер + метрики (TVL, APY, объём) в mono",
            "live-данные/числа моноширинным, gradient-border карточки фич",
            "how-it-works в 3-4 шага со схемой, security/audit-бейджи реальные",
            "CTA «Подключить кошелёк» + поддерживаемые сети иконками",
        ),
        kit_classes=("bg-mesh", "blob", "gradient-border", "glow-pulse", "reveal"),
        copywriting_tone=(
            "Технологичный, точный, без хайпа. APY, комиссии, сети, аудит. Без "
            "«революционная блокчейн-платформа будущего» — «0.05% комиссия», "
            "«аудит CertiK», «12 сетей»."
        ),
        copywriting_examples=(
            "Стейкинг с APY до 9.4%. Без локапа. Аудит CertiK.",
            "TVL $240M · 12 сетей · 0.05% своп-комиссия",
            "Подключить кошелёк · MetaMask, WalletConnect",
        ),
        anti_patterns=(
            "НЕ использовать дефолтный indigo/violet (это и есть generic-AI/web3-клише) — cyan/emerald",
            "НЕ писать «революция в мире финансов»",
            "НЕ ставить фейковые графики без подписей",
            "НЕ обещать доходность без дисклеймера риска",
        ),
        section_signature="monogram",
    ),
    "gaming-arena": DesignPreset(
        id="gaming-arena",
        name="Gaming Arena",
        reference_url="https://www.nexusplay.example",
        one_liner="Игры, киберспорт, стриминг, гейм-студии — тёмный неон + угловатость + энергия.",
        industries=(
            "игры", "киберспорт", "esports", "гейм-студия", "стриминг",
            "игровая студия", "турнир", "геймдев", "твич",
        ),
        keywords=(
            "игр", "game", "gaming", "киберспорт", "esports", "стрим",
            "twitch", "турнир", "tournament", "геймдев", "gamedev", "клан",
            "матч", "лига", "battle",
        ),
        palette={
            "bg": "#0A0712",
            "bg_alt": "#140E22",
            "fg": "#F2ECFF",
            "muted": "#9A8FB8",
            "accent": "#FF2E97",
            "border": "#241A3A",
        },
        fonts={
            "display": "Unbounded",
            "body": "Space Grotesk",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Unbounded:wght@700;900&family=Space+Grotesk:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "агрессивный hero с key-art игры/команды + капс-оффер + glow-акценты",
            "турнирная сетка/расписание матчей, призовой фонд крупно",
            "состав команды/ростер карточками с никами и ролями",
            "кинетик-лента спонсоров/игр + CTA «Вступить» / «Смотреть»",
        ),
        kit_classes=("kinetic-marquee", "glow-pulse", "shine", "reveal", "tilt"),
        copywriting_tone=(
            "Драйвовый, дерзкий, комьюнити-стиль. Призовые, ранги, расписание. "
            "КАПС, сленг уместен. Без «лучшая игровая платформа» — «призовой 2 млн ₽», "
            "«сезон 5 стартует 12.06»."
        ),
        copywriting_examples=(
            "СЕЗОН 5. Призовой фонд 2 000 000 ₽. Регистрация открыта.",
            "GG · 4 200 игроков · 128 команд · 1 чемпион",
            "Смотреть финал · 18:00 МСК · Twitch",
        ),
        anti_patterns=(
            "НЕ использовать светлый корпоративный фон — тёмный неон",
            "НЕ писать «платформа для любителей игр»",
            "НЕ ставить generic-стоковые фото геймеров",
            "НЕ делать спокойный сдержанный layout — энергия и угол",
        ),
        section_signature="monogram",
    ),
    "nonprofit-cause": DesignPreset(
        id="nonprofit-cause",
        name="Nonprofit Cause",
        reference_url="https://www.kindhands.example",
        one_liner="НКО, фонды, благотворительность, волонтёры — тёплый человечный + ясный CTA пожертвования + реальные истории.",
        industries=(
            "нко", "благотворительность", "фонд", "волонтёры", "пожертвования",
            "социальный проект", "приют", "помощь", "гуманитарный",
        ),
        keywords=(
            "нко", "благотворительн", "фонд", "волонт", "пожертв", "donate",
            "charity", "помощь", "приют", "социальн проект", "гуманитарн",
            "сбор средств", "добровольц", "миссия",
        ),
        palette={
            "bg": "#FBF7F1",
            "bg_alt": "#FFFFFF",
            "fg": "#22271F",
            "muted": "#6E7468",
            "accent": "#C2632E",
            "border": "#E9E1D5",
        },
        fonts={
            "display": "Fraunces",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero с реальным человеческим кадром + ясная миссия + крупный CTA «Помочь»",
            "impact-цифры (помогли N людям, собрано ₽) + прозрачность отчётов",
            "реальные истории подопечных с фото и именами",
            "способы помочь: разовое/ежемесячное/волонтёрство",
        ),
        kit_classes=("reveal", "fade-up", "card-soft", "img-zoom", "divider-fade"),
        copywriting_tone=(
            "Тёплый, честный, побуждающий, без давления и манипуляций. Конкретные "
            "истории, прозрачные цифры. Без «вместе мы изменим мир» — «собрали "
            "1.2 млн ₽», «помогли 340 семьям», «98% идёт на программы»."
        ),
        copywriting_examples=(
            "340 семей получили помощь в этом году. Спасибо вам.",
            "98% пожертвований идёт напрямую на программы. Отчёты открыты.",
            "Помочь · разово или 300 ₽/мес",
        ),
        anti_patterns=(
            "НЕ давить на жалость манипулятивно — честность и достоинство",
            "НЕ ставить generic-стоковые фото — реальные люди и истории",
            "НЕ писать «вместе мы сделаем мир лучше»",
            "НЕ прятать, куда идут деньги — прозрачность",
        ),
        section_signature="numerals",
    ),
    "construction-solid": DesignPreset(
        id="construction-solid",
        name="Construction Solid",
        reference_url="https://www.betonstroy.example",
        one_liner="Строительство, ремонт под ключ, отделка, прорабы — индустриальный, прочный + amber-акцент + кадры объектов.",
        industries=(
            "строительство", "ремонт под ключ", "отделка", "стройка",
            "прораб", "строительная компания", "фасады", "кровля", "монолит",
        ),
        keywords=(
            "строительств", "стройк", "ремонт под ключ", "отделк", "прораб",
            "фасад", "кровл", "монолит", "construction", "застройк объект",
            "капремонт", "дизайн интерьер ремонт", "бригад",
        ),
        palette={
            "bg": "#F5F5F3",
            "bg_alt": "#FFFFFF",
            "fg": "#1A1C1E",
            "muted": "#62686E",
            "accent": "#E08A1E",
            "border": "#DEDEDA",
        },
        fonts={
            "display": "Archivo",
            "body": "Work Sans",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Archivo:wght@700;800;900&family=Work+Sans:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero-кадр реального объекта/стройки + оффер + смета/срок цифрами",
            "услуги с ценами ₽/м² + этапы работ нумерацией",
            "портфолио объектов до/после .img-zoom + гарантия",
            "лицензии/СРО + отзывы заказчиков с объектами",
        ),
        kit_classes=("section-numeral", "img-zoom", "reveal", "card-soft", "divider-fade"),
        copywriting_tone=(
            "Прочный, деловой, доказательный. Сроки, цены ₽/м², гарантия, СРО. "
            "Без «строим дома вашей мечты» — «сдаём за 90 дней», «гарантия 5 лет», "
            "«фикс-смета без доплат»."
        ),
        copywriting_examples=(
            "Ремонт под ключ за 90 дней. Фикс-смета без доплат.",
            "Отделка от 8 900 ₽/м² · гарантия 5 лет · СРО",
            "240 сданных объектов · смета за 1 день",
        ),
        anti_patterns=(
            "НЕ ставить generic-рендеры небоскрёбов — реальные объекты компании",
            "НЕ прятать цены и сроки",
            "НЕ писать «строим дома вашей мечты под ключ»",
            "НЕ использовать легкомысленную палитру — индустриальная прочность",
        ),
        section_signature="numerals",
    ),
    "agency-bold": DesignPreset(
        id="agency-bold",
        name="Agency Bold",
        reference_url="https://www.signalagency.example",
        one_liner="Маркетинг/рекламные агентства, SMM, диджитал — смелый современный + крупная типографика + кейсы с цифрами.",
        industries=(
            "маркетинговое агентство", "реклама", "smm", "диджитал агентство",
            "брендинг", "performance", "pr-агентство", "медиа", "контент-агентство",
        ),
        keywords=(
            "маркетинг", "агентство", "реклам", "smm", "диджитал", "digital",
            "брендинг", "branding", "performance", "таргет", "контекст",
            "pr", "продвижение", "медиа", "контент-маркет",
        ),
        palette={
            "bg": "#0E0E10",
            "bg_alt": "#FAFAF8",
            "fg": "#FAFAF8",
            "muted": "#A1A1AA",
            "accent": "#FF5C38",
            "border": "#26262B",
        },
        fonts={
            "display": "Syne",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=Inter:wght@400;500&display=swap",
        },
        hero_type="type-as-hero",
        layout_signatures=(
            "крупный type-as-hero манифест агентства, kinetic-акцент",
            "кейсы с РЕАЛЬНЫМИ метриками (x3 лидов, −40% CPA, +220% охват)",
            "услуги списком с section-signature, клиент-marquee",
            "команда/процесс + дерзкий CTA «Обсудить проект»",
        ),
        kit_classes=("kinetic-type", "display-fill", "marquee", "reveal", "magnetic"),
        copywriting_tone=(
            "Смелый, уверенный, результативный. Метрики кейсов, ниши, подход. Без "
            "«комплексный маркетинг для вашего бизнеса» — «x3 заявок за квартал», "
            "«CPA −40%», «вырастили бренд с 0 до 200k подписчиков»."
        ),
        copywriting_examples=(
            "Растим выручку, а не лайки. x3 заявок за квартал.",
            "Кейс: −40% CPA и +220% охвата для D2C-бренда",
            "Обсудить проект · ответим за 2 часа",
        ),
        anti_patterns=(
            "НЕ писать «комплексные маркетинговые решения под ключ»",
            "НЕ ставить кейсы без конкретных цифр",
            "НЕ использовать generic-сетку из 3 услуг — смелая типографика",
            "НЕ ставить stock-фото команды у ноутбуков",
        ),
        section_signature="caps-monumental",
    ),
    "workspace-clean": DesignPreset(
        id="workspace-clean",
        name="Workspace Clean",
        reference_url="https://www.hubspace.example",
        one_liner="Коворкинги, аренда офисов, бизнес-центры — чистый современный + тёплый профессионализм + кадры пространств.",
        industries=(
            "коворкинг", "аренда офиса", "бизнес-центр", "офисное пространство",
            "рабочее место", "переговорные", "loft-пространство",
        ),
        keywords=(
            "коворкинг", "coworking", "аренда офис", "бизнес-центр", "офис",
            "рабочее место", "переговорн", "loft", "лофт", "резидент",
            "рабочее пространство", "митинг-рум",
        ),
        palette={
            "bg": "#FAF9F6",
            "bg_alt": "#FFFFFF",
            "fg": "#1D1F22",
            "muted": "#6B7178",
            "accent": "#C2603A",
            "border": "#E7E4DD",
        },
        fonts={
            "display": "Sora",
            "body": "Inter",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700&family=Inter:wght@400;500&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "hero-кадр пространства + оффер + тарифы (день/месяц/резидент) от ₽",
            "форматы мест: хот-деск/фикс/кабинет/переговорная карточками",
            "удобства иконками + галерея зон .img-zoom + локация/карта",
            "сообщество/резиденты + CTA «Забронировать тур»",
        ),
        kit_classes=("img-zoom", "card-soft", "hover-lift", "reveal", "divider-fade"),
        copywriting_tone=(
            "Современный, профессиональный, тёплый. Тарифы, удобства, локация. "
            "Без «комфортное пространство для продуктивной работы» — «от 12 000 "
            "₽/мес», «переговорные 24/7», «2 минуты от метро»."
        ),
        copywriting_examples=(
            "Офис без забот. Хот-деск от 700 ₽/день, кабинет от 35 000 ₽/мес.",
            "120 резидентов · переговорные 24/7 · кофе безлимит",
            "Забронируйте тур · 2 минуты от м. Курская",
        ),
        anti_patterns=(
            "НЕ ставить generic-стоковый опен-спейс — реальные кадры пространства",
            "НЕ прятать тарифы",
            "НЕ писать «комфортное пространство для продуктивной работы»",
            "НЕ использовать холодную корпоративную палитру — тёплый профессионализм",
        ),
        section_signature="eyebrow-labels",
    ),
    "pet-care": DesignPreset(
        id="pet-care",
        name="Pet Care",
        reference_url="https://www.happypaws.example",
        one_liner="Ветклиники, зоомагазины, груминг, питомцы — тёплый дружелюбный + мягкий teal/coral + кадры животных.",
        industries=(
            "ветеринария", "ветклиника", "зоомагазин", "груминг", "питомцы",
            "зоосалон", "передержка", "приют животных", "корм для животных",
        ),
        keywords=(
            "ветеринар", "ветклиник", "зоомагазин", "груминг", "grooming",
            "питом", "животн", "собак", "кошк", "зоосалон", "передержк",
            "корм", "pet", "vet", "лапы", "хвост",
        ),
        palette={
            "bg": "#F6FBFA",
            "bg_alt": "#FFFFFF",
            "fg": "#1C2B2A",
            "muted": "#5F7574",
            "accent": "#13A89E",
            "border": "#DCEAE8",
        },
        fonts={
            "display": "Bricolage Grotesque",
            "body": "Nunito",
            "google_fonts_url": "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=Nunito:wght@400;600;700&display=swap",
        },
        hero_type="mixed",
        layout_signatures=(
            "тёплый hero-кадр животного + дружелюбный оффер + CTA «Записаться»",
            "услуги с ценами ₽ (приём/вакцинация/груминг) сгруппированы",
            "врачи/грумеры с фото + специализация, отзывы владельцев",
            "запись онлайн + часы работы + экстренная помощь",
        ),
        kit_classes=("img-zoom", "card-soft", "hover-lift", "reveal", "blob"),
        copywriting_tone=(
            "Тёплый, заботливый, доверительный. Услуги, цены, забота о питомце. "
            "Без «качественная забота о ваших питомцах» — «приём 1 500 ₽», "
            "«УЗИ и анализы на месте», «врачи со стажем 10+ лет»."
        ),
        copywriting_examples=(
            "Заботимся о хвостатых как о своих. Приём от 1 500 ₽.",
            "Груминг · вакцинация · УЗИ — всё в одном месте",
            "Экстренная помощь 24/7 · запись онлайн",
        ),
        anti_patterns=(
            "НЕ использовать холодную клиническую палитру — тёплый дружелюбный teal",
            "НЕ ставить stock-фото идеальных питомцев — реальные пациенты/клиенты",
            "НЕ писать «качественная забота о ваших любимцах»",
            "НЕ прятать цены приёма",
        ),
        section_signature="eyebrow-labels",
    ),
}


AWWWARDS_PRINCIPLES = """\
AWWWARDS-FLOOR — 12 сквозных правил поверх любого пресета. Они применяются
ВСЕГДА, даже если конкретный preset не выбран. Цель — клиент за вечер собирает
сайт уровня Awwwards/Godly, а не generic-AI лендинг.

1. HUMAN-TONE HERO. Hero-tagline обязан содержать personal hook или
   провокацию: имя/город/число, манифест-фраза, противопоставление
   problem→solution. НИКОГДА не «Innovative AI Solutions for Modern
   Businesses», «Welcome to our website», «Empowering your future».

2. TYPE-AS-HERO BY DEFAULT. Если бриф НЕ требует визуального продукта
   (электроника, еда, мода) — hero БЕЗ stock-фото. Один гигантский
   заголовок (.display-hero/.display-fill), tracking-tight, подзаголовок
   одной строкой, ОДНА главная CTA. Воздух вокруг — не запихивать всё.

3. EDITORIAL WHITESPACE. py-24…py-32 между крупными секциями,
   max-w-3xl/4xl для прозы, max-w-7xl для сеток. Никаких py-8 —
   только воздушные ритмы.

4. ASYMMETRIC GRID > SYMMETRIC. Чередуй сетку: col-span-7/5, col-span-5/7,
   full-row, mosaic. Три одинаковые карточки подряд = generic-AI.

5. REAL PROOF, NOT STOCK. Конкретные числа (₽12 400, не «competitive
   pricing»), реальные имена (Мария Иванова, не «Happy Customer»),
   города/даты. Если данных нет — придумай ПРАВДОПОДОБНЫЕ, не общие фразы.

6. SECTION SIGNATURE. Используй маркировку секций согласно
   ``preset.section_signature``: numerals («0.1 / 0.2 / 0.3»), monogram,
   eyebrow-labels («ВОЗМОЖНОСТИ»), caps-monumental, footnote, none.
   Один тип на сайт — не смешивать.

7. ONE CHARACTERISTIC MOTION. ОДИН выразительный motion-приём на сайт:
   ИЛИ .cursor-blob (wellness-casual), ИЛИ .kinetic-marquee
   (festival-brutalist), ИЛИ .kinetic-type, ИЛИ .img-zoom + parallax.
   НЕ всё сразу — иначе перегруз.

8. ONE ACCENT, NEVER RAINBOW. 0 или 1 hue-accent на сайт (из палитры
   пресета). Никаких двух-трёх «бренд-цветов» одновременно. CTA в
   accent, всё остальное — neutral. Тёмная тема — accent светлее
   muted, иначе текст превращается в кашу.

9. NO DARK PATTERNS (Malewicz). Лейблы переключателей и кнопок отражают
   реальный результат действия — без двойных отрицаний, без opt-out по
   умолчанию, без замаскированной серой «cancel»-кнопки. Юзер должен
   понимать что произойдёт после клика, прочитав ТОЛЬКО лейбл. Любая
   тёмная схема убивает доверие — а доверие = единственная валюта.

10. MODERN ≠ PURELY FLAT (Malewicz). Чисто плоский дизайн (Material 1.0,
    flat 2014) — пользователи решают задачи на 22% медленнее: нет
    affordance-сигналов, что кликабельно. Modern = flat + ОДИН слой
    тени (.depth-1/2) + лёгкий 3D-намёк (gradient на кнопке, мягкая
    тень под карточкой). Не возвращай скевоморфизм, но и не оставляй
    голую плоскость без иерархии глубины.

11. LESS IS MORE (Malewicz). UI «выцветает» в сознании после адаптации —
    юзер перестаёт замечать декорации. Меньше = больше: декоративные
    элементы (orbs, паттерны, motion-приёмы) НЕ должны перебивать
    основной контент. Если приходится выбирать между «ещё одна
    декоративная блямба» и «больше воздуха» — выбирай воздух. Каждая
    деталь должна работать на иерархию или удаляется.

12. DRIBBBLE ≠ PRODUCTION (Malewicz). Awwwards и Dribbble — это
    галерея визуальной inspiration, не shipping-ready продукт.
    Перенося приём — валидируй: работает ли он на 375px, читается
    ли при контрасте 4.5:1, не убивает ли usability ради эстетики.
    Если приём «красивый, но кликать неудобно» — снижай агрессию
    приёма, не жертвуй удобством. Production-сайт > красивого скриншота.
"""


def format_preset_block(preset_id: str) -> str:
    """Собрать declarative-блок пресета для инжекта в system prompt.

    Возвращает текст в стиле остальных секций ``prompt_builder``: KAPS-заголовок,
    маркированные списки с конкретикой. Модель копирует HEX/имена/классы
    напрямую — не сочиняет CSS.
    """
    preset = PRESETS.get(preset_id)
    if preset is None:
        return ""

    p = preset.palette
    f = preset.fonts

    layout_lines = "\n".join(f"  • {sig}" for sig in preset.layout_signatures)
    classes = ", ".join(f".{cls}" for cls in preset.kit_classes)
    examples = "\n".join(f'  • «{ex}»' for ex in preset.copywriting_examples)
    anti = "\n".join(f"  • {a}" for a in preset.anti_patterns)

    return f"""\
ВЫБРАННЫЙ ПРЕСЕТ — {preset.name} ({preset.id})
Референс (ПРИЁМ, не вёрстку): {preset.reference_url}
Короткое описание: {preset.one_liner}

Эта секция OVERRIDES default REFINED MINIMAL из _STYLE_KIT. Бери ТОЛЬКО приём.
Конкретные HEX/имена/классы ниже — копируй ДОСЛОВНО, не подменяй на «похожие».

ПАЛИТРА (готовые токены, ставь их в Tailwind config или :root CSS-переменные):
  bg:        {p['bg']}
  bg-alt:    {p['bg_alt']}
  fg:        {p['fg']}
  muted:     {p['muted']}
  accent:    {p['accent']}
  border:    {p['border']}

ТИПОГРАФИКА (подключай Google Fonts именно этот URL, не подменяй на Inter):
  display:   {f['display']}
  body:      {f['body']}
  <link rel="stylesheet" href="{f['google_fonts_url']}">

HERO-ТИП: {preset.hero_type}
SECTION-SIGNATURE: {preset.section_signature}

LAYOUT-СИГНАТУРЫ (обязательны минимум 3 из списка):
{layout_lines}

KIT-КЛАССЫ (использовать минимум по одному из):
  {classes}

COPYWRITING TONE:
  {preset.copywriting_tone}

ПРИМЕРЫ HERO-TAGLINE (стиль, не копировать дословно):
{examples}

ANTI-PATTERNS (категорически НЕ делать):
{anti}
"""


__all__ = ["DesignPreset", "PRESETS", "AWWWARDS_PRINCIPLES", "format_preset_block"]
