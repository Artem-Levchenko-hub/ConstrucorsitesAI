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
