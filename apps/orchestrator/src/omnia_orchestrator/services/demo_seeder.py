"""Model-independent demo-data generator for freshly provisioned entity apps.

The single worst first-impression in a generated app is an **empty catalog**: a
user types one prompt, gets a polished dashboard, opens the primary list screen
— and it's a blank empty-state. That kills pillar 1 (WOW from the first
generation) and pillar 4 (nothing worth sharing). The fix is a deterministic,
*model-independent* seeder that fills every entity with 6–12 realistic demo rows
at provision time, so the first screen the user (and their colleague) sees is
alive.

This module is the **generator core**: a pure function that turns an entity's
JSON schema (the same `entities/<Name>.json` the engine reads) into a list of
`data` payloads ready to drop into the generic `records` table. It does NOT
touch Postgres, the provisioner, or the network — that wiring is a separate
slice. Keeping the value logic pure makes it exhaustively unit-testable and
keeps it off the live provisioning hot-path until it's proven.

Two design rules make it trustworthy:

  * **Deterministic.** Every value derives from a SHA-256 of (seed, entity,
    field, row-index). Same inputs → byte-identical output, on any machine and
    across runs. We never use `random`/`hash()` (salted, machine-variant).
  * **Model-independent realism.** The generator never sees the LLM brief. It
    infers plausible values from the *field name* (a `price` field → a money
    amount, an `email` field → an address) and the *entity name* (a `Client`'s
    `name` → a person, a `Course`'s `title` → a labelled thing), falling back to
    the declared `type`. The same code produces sensible rows for any schema the
    generator emits.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

# Row-count policy: every entity gets at least MIN_ROWS so the browse screen is
# never empty (the gate floor), capped at MAX_ROWS so a seeded catalog still
# looks curated rather than dumped.
MIN_ROWS = 6
MAX_ROWS = 12

# ── Curated value pools (Russian market — the product targets RU users) ──────

_PERSON_NAMES: tuple[str, ...] = (
    "Анна Смирнова",
    "Дмитрий Кузнецов",
    "Елена Попова",
    "Сергей Васильев",
    "Мария Соколова",
    "Алексей Морозов",
    "Ольга Новикова",
    "Иван Фёдоров",
    "Наталья Михайлова",
    "Андрей Волков",
    "Екатерина Лебедева",
    "Павел Семёнов",
    "Юлия Егорова",
    "Михаил Павлов",
    "Татьяна Козлова",
    "Николай Степанов",
)

# Email local-parts (see `_demo_email`). A person entity's email reads as a
# personal mailbox (a translit of the `_PERSON_NAMES` first names); any other
# entity gets a business mailbox. Both are pure-ASCII so they're valid local
# parts on the slug-derived domain. The two pools are kept disjoint (asserted in
# tests) so the entity-kind branch is observable.
_EMAIL_HANDLES_PERSON: tuple[str, ...] = (
    "anna", "dmitriy", "elena", "sergey", "mariya", "aleksey",
    "olga", "ivan", "natalya", "andrey", "ekaterina", "pavel",
    "yuliya", "mihail", "tatyana", "nikolay",
)
_EMAIL_HANDLES_BIZ: tuple[str, ...] = (
    "info", "zakaz", "sales", "office", "hello", "shop", "client", "support",
)

_COMPANIES: tuple[str, ...] = (
    "ООО «Вектор»",
    "Группа «Альфа»",
    "Студия «Контур»",
    "ИП Орлов А.В.",
    "ООО «Прайм»",
    "Агентство «Сфера»",
    "ООО «Меридиан»",
    "Бюро «Ось»",
    "ООО «Грань»",
    "Холдинг «Атлас»",
)

_CITIES: tuple[str, ...] = (
    "Москва",
    "Санкт-Петербург",
    "Казань",
    "Екатеринбург",
    "Новосибирск",
    "Нижний Новгород",
    "Краснодар",
    "Сочи",
)

_STREETS: tuple[str, ...] = (
    "ул. Ленина",
    "пр. Мира",
    "ул. Гагарина",
    "наб. Фонтанки",
    "ул. Пушкина",
    "пр. Победы",
)

# Adjective-ish labels used to name "thing" entities (Project «Альфа», …) so a
# title is always on-topic for whatever the entity is, and clearly demo.
_LABELS: tuple[str, ...] = (
    "Альфа",
    "Премиум",
    "Базовый",
    "Старт",
    "Бета",
    "Профи",
    "Лайт",
    "Максимум",
    "Экспресс",
    "Стандарт",
    "Гамма",
    "Дельта",
)

# Operational sentences for note/comment `text` fields — believable internal
# notes on a record (a task, an order). These read as back-office workflow copy,
# so they're WRONG on a public catalog card; `description`-style fields route to
# `_DESCRIPTIONS` instead (see `_demo_text`).
_SENTENCES: tuple[str, ...] = (
    "Уточнить детали с клиентом до конца недели.",
    "Подготовлено по стандартному регламенту, готово к работе.",
    "Требуется согласование с ответственным менеджером.",
    "Все материалы загружены, ожидаем подтверждения.",
    "Приоритетная задача, держим на контроле.",
    "Назначена встреча, повестка согласована.",
    "Документы проверены, замечаний нет.",
    "Запланирован повторный контакт через несколько дней.",
)

# Catalog blurbs for `description`-style `text` fields — copy that reads right on
# a *public, browsable* card (product / dish / service / listing) and is never
# absurd for any niche, unlike the operational `_SENTENCES`.
_DESCRIPTIONS: tuple[str, ...] = (
    "Популярный выбор — часто заказывают.",
    "Проверенное качество, всегда в наличии.",
    "Рекомендуем обратить внимание на этот вариант.",
    "Отличное соотношение цены и качества.",
    "Хит продаж в своей категории.",
    "Доступно к заказу прямо сейчас.",
    "Свежее поступление этой недели.",
    "Ограниченное предложение — успейте оформить.",
)

_STATUS_WORDS: tuple[str, ...] = (
    "Новый",
    "В работе",
    "Завершён",
    "На паузе",
    "Согласование",
)

# ── Niche-aware catalog nouns ────────────────────────────────────────────────
# A model-independent seeder can still be *niche-realistic*: when an entity's own
# vocabulary (name, field names, enum options) or the project slug confidently
# names a domain, the catalog's primary label (title/name) draws a real product
# from that domain instead of the placeholder "<Label> <n>" form. Detection is
# high-precision-or-nothing: an unrecognised niche falls back to the safe demo
# label rather than risk a confidently-wrong noun (a pharmacy showing "Капучино").

_DOMAIN_NOUNS: dict[str, tuple[str, ...]] = {
    "pharmacy": (
        "Витамин D3 2000 МЕ", "Омега-3 1000 мг", "Магний B6", "Цинк пиколинат",
        "Морской коллаген", "Пробиотик комплекс", "Мелатонин 3 мг",
        "Гиалуроновая кислота", "Витамин C 900 мг", "Железо хелат",
        "Куркумин экстракт", "Коэнзим Q10", "Крем увлажняющий SPF30",
        "Маска тканевая",
    ),
    "clinic": (
        "Консультация терапевта", "УЗИ брюшной полости", "Общий анализ крови",
        "Приём стоматолога", "Вакцинация", "ЭКГ с расшифровкой",
        "Лечебный массаж", "Физиотерапия", "Приём кардиолога", "МРТ позвоночника",
        "Осмотр офтальмолога", "Профессиональная чистка зубов",
    ),
    "beauty": (
        "Женская стрижка", "Окрашивание в один тон", "Маникюр с покрытием",
        "Педикюр аппаратный", "Укладка", "Ламинирование ресниц",
        "Коррекция бровей", "Спа-уход для лица", "Массаж лица", "Депиляция воском",
        "Дневной макияж", "Мужская барбер-стрижка",
    ),
    "fitness": (
        "Персональная тренировка", "Групповое занятие", "Йога для начинающих",
        "Пилатес", "Сайкл", "Кроссфит", "Бокс", "Стретчинг",
        "Абонемент на месяц", "Функциональный тренинг", "Плавание", "TRX",
    ),
    "auto": (
        "Замена масла и фильтров", "Шиномонтаж", "Диагностика двигателя",
        "Развал-схождение", "Замена тормозных колодок", "Полировка кузова",
        "Химчистка салона", "Замена ремня ГРМ", "Заправка кондиционера",
        "Компьютерная диагностика", "Замена аккумулятора", "Антикоррозийная обработка",
    ),
    "cafe": (
        "Капучино", "Латте", "Раф ванильный", "Эспрессо", "Флэт уайт",
        "Чизкейк Нью-Йорк", "Тирамису", "Круассан с миндалём", "Сэндвич клаб",
        "Поке-боул", "Смузи манго-маракуйя", "Матча латте",
    ),
    "restaurant": (
        "Ролл «Филадельфия»", "Ролл «Калифорния»", "Унаги маки", "Темпура ролл",
        "Сяке нигири", "Том ям с креветками", "Удон с курицей", "Гёдза",
        "Спайси лосось", "Чука салат", "Мисо суп", "Сет «Токио»",
    ),
    "furniture": (
        "Угловой диван «Осло»", "Кресло «Лофт»", "Шкаф-купе", "Кровать двуспальная",
        "Комод на 4 ящика", "Обеденный стол", "Стеллаж открытый", "Тумба под ТВ",
        "Пуф мягкий", "Кухонный гарнитур", "Полка навесная", "Зеркало напольное",
    ),
    "travel": (
        "Тур в Турцию, всё включено", "Тур в ОАЭ", "Обзорная экскурсия",
        "Авиабилеты туда-обратно", "Отель 5★ на берегу", "Морской круиз",
        "Тур в Грузию", "Сафари-тур", "Горнолыжный тур", "Виза под ключ",
        "Индивидуальный трансфер", "Страховка путешественника",
    ),
    "education": (
        "Курс «Python с нуля»", "Разговорный английский", "Подготовка к ЕГЭ",
        "Математика для школьников", "Курс рисования", "Робототехника",
        "Шахматы для детей", "Основы дизайна", "Уроки вокала",
        "Программирование для детей", "Скорочтение", "Занятия с логопедом",
    ),
    "realestate": (
        "1-комн. квартира", "2-комн. квартира", "Студия", "Таунхаус",
        "Загородный дом", "Апартаменты", "Коммерческое помещение",
        "Земельный участок", "Пентхаус", "3-комн. квартира", "Машино-место",
        "Офис в центре",
    ),
}

# Each catalog noun's category, used to keep a row's `category` enum coherent with
# its title (a "Витамин C" row must not land in "Косметика"). The value is a
# representative category word; it's matched against the entity's *own* enum
# options by substring/stem (`_match_category_option`), so it need not equal them
# exactly — and when no option matches, the seeder falls back to the index-cycle
# (high-precision-or-nothing). Every noun in `_DOMAIN_NOUNS` MUST appear here
# (asserted in tests) so a newly added noun can't silently decorrelate.
_DOMAIN_NOUN_CATEGORY: dict[str, dict[str, str]] = {
    "pharmacy": {
        "Витамин D3 2000 МЕ": "Витамины", "Омега-3 1000 мг": "БАДы",
        "Магний B6": "Витамины", "Цинк пиколинат": "БАДы",
        "Морской коллаген": "БАДы", "Пробиотик комплекс": "БАДы",
        "Мелатонин 3 мг": "БАДы", "Гиалуроновая кислота": "БАДы",
        "Витамин C 900 мг": "Витамины", "Железо хелат": "БАДы",
        "Куркумин экстракт": "БАДы", "Коэнзим Q10": "БАДы",
        "Крем увлажняющий SPF30": "Косметика", "Маска тканевая": "Косметика",
    },
    "clinic": {
        "Консультация терапевта": "Консультации", "УЗИ брюшной полости": "Диагностика",
        "Общий анализ крови": "Анализы", "Приём стоматолога": "Стоматология",
        "Вакцинация": "Процедуры", "ЭКГ с расшифровкой": "Диагностика",
        "Лечебный массаж": "Процедуры", "Физиотерапия": "Процедуры",
        "Приём кардиолога": "Консультации", "МРТ позвоночника": "Диагностика",
        "Осмотр офтальмолога": "Консультации",
        "Профессиональная чистка зубов": "Стоматология",
    },
    "beauty": {
        "Женская стрижка": "Волосы", "Окрашивание в один тон": "Волосы",
        "Маникюр с покрытием": "Ногти", "Педикюр аппаратный": "Ногти",
        "Укладка": "Волосы", "Ламинирование ресниц": "Брови и ресницы",
        "Коррекция бровей": "Брови и ресницы", "Спа-уход для лица": "Лицо",
        "Массаж лица": "Лицо", "Депиляция воском": "Тело",
        "Дневной макияж": "Макияж", "Мужская барбер-стрижка": "Волосы",
    },
    "fitness": {
        "Персональная тренировка": "Тренировки", "Групповое занятие": "Групповые",
        "Йога для начинающих": "Йога", "Пилатес": "Групповые", "Сайкл": "Групповые",
        "Кроссфит": "Тренировки", "Бокс": "Единоборства", "Стретчинг": "Групповые",
        "Абонемент на месяц": "Абонементы", "Функциональный тренинг": "Тренировки",
        "Плавание": "Бассейн", "TRX": "Тренировки",
    },
    "auto": {
        "Замена масла и фильтров": "Техобслуживание", "Шиномонтаж": "Шины",
        "Диагностика двигателя": "Диагностика", "Развал-схождение": "Шины",
        "Замена тормозных колодок": "Техобслуживание", "Полировка кузова": "Кузов",
        "Химчистка салона": "Мойка", "Замена ремня ГРМ": "Техобслуживание",
        "Заправка кондиционера": "Техобслуживание",
        "Компьютерная диагностика": "Диагностика",
        "Замена аккумулятора": "Техобслуживание", "Антикоррозийная обработка": "Кузов",
    },
    "cafe": {
        "Капучино": "Кофе", "Латте": "Кофе", "Раф ванильный": "Кофе",
        "Эспрессо": "Кофе", "Флэт уайт": "Кофе", "Чизкейк Нью-Йорк": "Десерты",
        "Тирамису": "Десерты", "Круассан с миндалём": "Выпечка",
        "Сэндвич клаб": "Еда", "Поке-боул": "Еда",
        "Смузи манго-маракуйя": "Напитки", "Матча латте": "Напитки",
    },
    "restaurant": {
        "Ролл «Филадельфия»": "Роллы", "Ролл «Калифорния»": "Роллы",
        "Унаги маки": "Роллы", "Темпура ролл": "Роллы", "Сяке нигири": "Суши",
        "Том ям с креветками": "Супы", "Удон с курицей": "Горячее",
        "Гёдза": "Горячее", "Спайси лосось": "Роллы", "Чука салат": "Салаты",
        "Мисо суп": "Супы", "Сет «Токио»": "Сеты",
    },
    "furniture": {
        "Угловой диван «Осло»": "Диваны", "Кресло «Лофт»": "Кресла",
        "Шкаф-купе": "Шкафы", "Кровать двуспальная": "Кровати",
        "Комод на 4 ящика": "Хранение", "Обеденный стол": "Столы",
        "Стеллаж открытый": "Хранение", "Тумба под ТВ": "Хранение",
        "Пуф мягкий": "Кресла", "Кухонный гарнитур": "Кухни",
        "Полка навесная": "Хранение", "Зеркало напольное": "Декор",
    },
    "travel": {
        "Тур в Турцию, всё включено": "Туры", "Тур в ОАЭ": "Туры",
        "Обзорная экскурсия": "Экскурсии", "Авиабилеты туда-обратно": "Билеты",
        "Отель 5★ на берегу": "Отели", "Морской круиз": "Круизы",
        "Тур в Грузию": "Туры", "Сафари-тур": "Туры", "Горнолыжный тур": "Туры",
        "Виза под ключ": "Услуги", "Индивидуальный трансфер": "Услуги",
        "Страховка путешественника": "Услуги",
    },
    "education": {
        "Курс «Python с нуля»": "Программирование", "Разговорный английский": "Языки",
        "Подготовка к ЕГЭ": "Школьникам", "Математика для школьников": "Школьникам",
        "Курс рисования": "Творчество", "Робототехника": "Программирование",
        "Шахматы для детей": "Детям", "Основы дизайна": "Творчество",
        "Уроки вокала": "Творчество", "Программирование для детей": "Программирование",
        "Скорочтение": "Детям", "Занятия с логопедом": "Детям",
    },
    "realestate": {
        "1-комн. квартира": "Квартиры", "2-комн. квартира": "Квартиры",
        "Студия": "Студии", "Таунхаус": "Дома", "Загородный дом": "Дома",
        "Апартаменты": "Апартаменты", "Коммерческое помещение": "Коммерческая",
        "Земельный участок": "Участки", "Пентхаус": "Апартаменты",
        "3-комн. квартира": "Квартиры", "Машино-место": "Коммерческая",
        "Офис в центре": "Коммерческая",
    },
}

# Real LLM-generated category enums rarely use the exact curated word — an apteka
# enum reads «Препараты / Органика», not «Витамины / БАДы». For each curated
# category (the values of `_DOMAIN_NOUN_CATEGORY`) this maps domain-scoped
# alternative words a real enum is likely to use instead, tried *after* the
# curated word so the most specific option still wins when present. Synonyms are
# kept ≥5 chars (asserted in tests) so they can never collide with an unrelated
# option as a stray substring. A curated category with no safe synonym simply has
# no key here — it then relies on the curated word matching directly, else the
# index-cycle fallback (high-precision-or-nothing, zero regression).
_CATEGORY_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "pharmacy": {
        "Витамины": ("Препараты", "Лекарства", "Медикаменты"),
        "БАДы": ("Добавки", "Препараты", "Органика", "Лекарства"),
        "Косметика": ("Красота", "Гигиена"),
    },
    "clinic": {
        "Консультации": ("Приёмы", "Специалисты"),
        "Диагностика": ("Обследования", "Исследования"),
        "Анализы": ("Лаборатория", "Лабораторные"),
        "Стоматология": ("Зубные",),
        "Процедуры": ("Лечение", "Терапия"),
    },
    "beauty": {
        "Волосы": ("Парикмахерские", "Стрижки", "Окрашивание", "Причёски"),
        "Ногти": ("Маникюр", "Педикюр"),
        "Лицо": ("Косметология", "Уходовые"),
        "Тело": ("Депиляция", "Массаж"),
        "Макияж": ("Визаж",),
    },
    "fitness": {
        "Тренировки": ("Персональные", "Силовые", "Тренинг"),
        "Групповые": ("Занятия", "Классы"),
        "Йога": ("Пилатес", "Растяжка"),
        "Единоборства": ("Боевые",),
        "Абонементы": ("Членство", "Абонемент"),
        "Бассейн": ("Плавание", "Аквазона"),
    },
    "auto": {
        "Техобслуживание": ("Ремонт", "Обслуживание", "Сервис"),
        "Шины": ("Шиномонтаж", "Колёса", "Диски"),
        "Диагностика": ("Проверка", "Компьютерная"),
        "Кузов": ("Покраска", "Кузовной", "Детейлинг"),
        "Мойка": ("Автомойка", "Химчистка", "Уборка"),
    },
    "cafe": {
        "Кофе": ("Напитки", "Бариста"),
        "Десерты": ("Сладкое", "Сладости"),
        "Выпечка": ("Пекарня", "Багеты"),
        "Еда": ("Завтраки", "Закуски", "Сэндвичи", "Обеды", "Бранч"),
        "Напитки": ("Смузи", "Лимонады"),
    },
    "restaurant": {
        "Супы": ("Бульоны", "Первые"),
        "Горячее": ("Горячие", "Вторые", "Основные"),
        "Салаты": ("Закуски", "Холодные"),
        "Сеты": ("Комбо", "Наборы"),
    },
    "furniture": {
        "Диваны": ("Мягкая",),
        "Кресла": ("Стулья",),
        "Шкафы": ("Хранение", "Гардеробные"),
        "Кровати": ("Спальня", "Спальни"),
        "Хранение": ("Стеллажи", "Комоды", "Тумбы"),
        "Столы": ("Столовая",),
        "Кухни": ("Кухонная",),
        "Декор": ("Аксессуары", "Зеркала", "Освещение"),
    },
    "travel": {
        "Туры": ("Путёвки", "Пакетные", "Отдых"),
        "Экскурсии": ("Прогулки",),
        "Билеты": ("Авиабилеты", "Перелёты"),
        "Отели": ("Размещение", "Проживание", "Гостиницы"),
        "Круизы": ("Морские",),
        "Услуги": ("Страхование", "Дополнительно", "Трансфер"),
    },
    "education": {
        "Программирование": ("Разработка", "Технологии"),
        "Языки": ("Английский", "Лингвистика"),
        "Школьникам": ("Подготовка", "Экзамены"),
        "Творчество": ("Искусство", "Рисование"),
        "Детям": ("Дошкольникам", "Развитие"),
    },
    "realestate": {
        "Квартиры": ("Новостройки", "Вторичка"),
        "Студии": ("Квартиры", "Малометражки"),
        "Дома": ("Коттеджи", "Загородная"),
        "Апартаменты": ("Элитное", "Премиум"),
        "Коммерческая": ("Офисы", "Коммерция"),
        "Участки": ("Земельные", "Участок"),
    },
}

# Each catalog noun's short, *product-describing* blurb, used to keep a row's
# `description` coherent with its title (a "Витамин C 900 мг" card must read
# "Антиоксидант и поддержка иммунитета", not the generic "Хит продаж"). When the
# domain is known and the row carries a niche noun, a `description`-style text
# field draws this real one-liner instead of the niche-blind `_DESCRIPTIONS`
# praise pool; an unknown niche keeps the safe generic copy (byte-identical
# fallback). Every noun in `_DOMAIN_NOUNS` MUST appear here (asserted in tests)
# so a newly added noun can't silently fall back to generic praise.
_DOMAIN_NOUN_DESCRIPTION: dict[str, dict[str, str]] = {
    "pharmacy": {
        "Витамин D3 2000 МЕ": "Поддержка иммунитета и крепких костей.",
        "Омега-3 1000 мг": "Жирные кислоты для сердца и сосудов.",
        "Магний B6": "Снимает усталость, поддерживает нервную систему.",
        "Цинк пиколинат": "Для иммунитета, кожи и волос.",
        "Морской коллаген": "Упругость кожи и здоровье суставов.",
        "Пробиотик комплекс": "Живые бактерии для здоровой микрофлоры.",
        "Мелатонин 3 мг": "Помогает быстрее засыпать и высыпаться.",
        "Гиалуроновая кислота": "Глубокое увлажнение кожи изнутри.",
        "Витамин C 900 мг": "Антиоксидант и поддержка иммунитета.",
        "Железо хелат": "Легко усваивается, против анемии.",
        "Куркумин экстракт": "Природная поддержка суставов и печени.",
        "Коэнзим Q10": "Энергия клеток и здоровье сердца.",
        "Крем увлажняющий SPF30": "Увлажнение и защита от солнца.",
        "Маска тканевая": "Экспресс-увлажнение кожи за 15 минут.",
    },
    "clinic": {
        "Консультация терапевта": "Первичный осмотр и план лечения.",
        "УЗИ брюшной полости": "Безболезненная диагностика внутренних органов.",
        "Общий анализ крови": "Базовая проверка состояния здоровья.",
        "Приём стоматолога": "Осмотр, чистка и план лечения зубов.",
        "Вакцинация": "Сезонная и плановая защита от инфекций.",
        "ЭКГ с расшифровкой": "Проверка работы сердца за 10 минут.",
        "Лечебный массаж": "Снимает напряжение в спине и шее.",
        "Физиотерапия": "Ускоряет восстановление после травм.",
        "Приём кардиолога": "Диагностика и лечение сердца и сосудов.",
        "МРТ позвоночника": "Точная диагностика без облучения.",
        "Осмотр офтальмолога": "Проверка зрения и здоровья глаз.",
        "Профессиональная чистка зубов": "Удаление налёта и камня ультразвуком.",
    },
    "beauty": {
        "Женская стрижка": "Стрижка и укладка под ваш стиль.",
        "Окрашивание в один тон": "Стойкий ровный цвет и уход.",
        "Маникюр с покрытием": "Аккуратные ногти и стойкий гель-лак.",
        "Педикюр аппаратный": "Ухоженные стопы без распаривания.",
        "Укладка": "Праздничная или повседневная укладка.",
        "Ламинирование ресниц": "Изгиб и объём без наращивания.",
        "Коррекция бровей": "Форма по типу лица и окрашивание.",
        "Спа-уход для лица": "Глубокое очищение и увлажнение кожи.",
        "Массаж лица": "Тонус кожи и здоровый цвет лица.",
        "Депиляция воском": "Гладкая кожа на несколько недель.",
        "Дневной макияж": "Естественный образ на каждый день.",
        "Мужская барбер-стрижка": "Стрижка и моделирование бороды.",
    },
    "fitness": {
        "Персональная тренировка": "Индивидуальная программа с тренером.",
        "Групповое занятие": "Энергичная тренировка в компании.",
        "Йога для начинающих": "Гибкость, дыхание и спокойствие.",
        "Пилатес": "Укрепление мышц кора и осанки.",
        "Сайкл": "Кардио на велотренажёрах под музыку.",
        "Кроссфит": "Функциональная нагрузка высокой интенсивности.",
        "Бокс": "Техника ударов и хорошая форма.",
        "Стретчинг": "Растяжка для гибкости и расслабления.",
        "Абонемент на месяц": "Безлимитный доступ в зал на 30 дней.",
        "Функциональный тренинг": "Сила и выносливость для жизни.",
        "Плавание": "Тренировка всего тела в бассейне.",
        "TRX": "Тренировка с собственным весом на петлях.",
    },
    "auto": {
        "Замена масла и фильтров": "Плановое ТО за 40 минут.",
        "Шиномонтаж": "Сезонная смена и балансировка колёс.",
        "Диагностика двигателя": "Поиск неисправностей по всем системам.",
        "Развал-схождение": "Точная регулировка углов колёс.",
        "Замена тормозных колодок": "Безопасное торможение, надёжные детали.",
        "Полировка кузова": "Восстановление блеска и защита лака.",
        "Химчистка салона": "Глубокая чистка обивки и пластика.",
        "Замена ремня ГРМ": "Профилактика дорогого ремонта двигателя.",
        "Заправка кондиционера": "Прохлада в салоне даже в жару.",
        "Компьютерная диагностика": "Считывание ошибок электронных блоков.",
        "Замена аккумулятора": "Подбор и установка нового АКБ.",
        "Антикоррозийная обработка": "Защита кузова от ржавчины.",
    },
    "cafe": {
        "Капучино": "Эспрессо с нежной молочной пенкой.",
        "Латте": "Мягкий кофе с большим количеством молока.",
        "Раф ванильный": "Сливочный кофе с ванилью.",
        "Эспрессо": "Насыщенный классический шот.",
        "Флэт уайт": "Двойной эспрессо и тонкий слой пенки.",
        "Чизкейк Нью-Йорк": "Нежный сырный десерт на песочной основе.",
        "Тирамису": "Кофейный десерт с маскарпоне.",
        "Круассан с миндалём": "Хрустящая выпечка с миндальным кремом.",
        "Сэндвич клаб": "Сытный сэндвич с курицей и беконом.",
        "Поке-боул": "Свежая миска с рыбой и овощами.",
        "Смузи манго-маракуйя": "Освежающий фруктовый микс.",
        "Матча латте": "Японский зелёный чай с молоком.",
    },
    "restaurant": {
        "Ролл «Филадельфия»": "Лосось, сливочный сыр и рис.",
        "Ролл «Калифорния»": "Краб, авокадо и икра тобико.",
        "Унаги маки": "Ролл с копчёным угрём и соусом.",
        "Темпура ролл": "Хрустящий обжаренный ролл.",
        "Сяке нигири": "Свежий лосось на рисе.",
        "Том ям с креветками": "Острый тайский суп на кокосе.",
        "Удон с курицей": "Толстая лапша вок с овощами.",
        "Гёдза": "Японские жареные пельмени.",
        "Спайси лосось": "Острый ролл с лососем и соусом.",
        "Чука салат": "Маринованные водоросли с ореховым соусом.",
        "Мисо суп": "Классический японский суп с тофу.",
        "Сет «Токио»": "Ассорти роллов для компании.",
    },
    "furniture": {
        "Угловой диван «Осло»": "Вместительный диван с механизмом сна.",
        "Кресло «Лофт»": "Мягкое кресло для гостиной.",
        "Шкаф-купе": "Вместительный шкаф с зеркальными дверями.",
        "Кровать двуспальная": "Кровать с ортопедическим основанием.",
        "Комод на 4 ящика": "Компактное хранение для спальни.",
        "Обеденный стол": "Прочный стол для всей семьи.",
        "Стеллаж открытый": "Полки для книг и декора.",
        "Тумба под ТВ": "Подставка с местом для техники.",
        "Пуф мягкий": "Доп. место и подставка для ног.",
        "Кухонный гарнитур": "Полный комплект мебели для кухни.",
        "Полка навесная": "Стильное хранение на стене.",
        "Зеркало напольное": "Большое зеркало в полный рост.",
    },
    "travel": {
        "Тур в Турцию, всё включено": "Отдых на море с питанием.",
        "Тур в ОАЭ": "Пляжи, шопинг и небоскрёбы.",
        "Обзорная экскурсия": "Главные достопримечательности за один день.",
        "Авиабилеты туда-обратно": "Удобные рейсы по выгодной цене.",
        "Отель 5★ на берегу": "Первая линия и сервис премиум-класса.",
        "Морской круиз": "Несколько стран за одно путешествие.",
        "Тур в Грузию": "Горы, вино и гостеприимство.",
        "Сафари-тур": "Дикая природа и фотоохота.",
        "Горнолыжный тур": "Склоны, прокат и проживание.",
        "Виза под ключ": "Оформление документов без хлопот.",
        "Индивидуальный трансфер": "Встреча в аэропорту и доставка.",
        "Страховка путешественника": "Защита здоровья в поездке.",
    },
    "education": {
        "Курс «Python с нуля»": "Программирование с нуля до проектов.",
        "Разговорный английский": "Свободная речь в реальных ситуациях.",
        "Подготовка к ЕГЭ": "Системная подготовка к экзаменам.",
        "Математика для школьников": "Понятные объяснения и практика.",
        "Курс рисования": "От базовых техник к своим работам.",
        "Робототехника": "Конструирование и программирование роботов.",
        "Шахматы для детей": "Логика и концентрация через игру.",
        "Основы дизайна": "Композиция, цвет и типографика.",
        "Уроки вокала": "Постановка голоса и дыхания.",
        "Программирование для детей": "Первые программы и игры.",
        "Скорочтение": "Читать быстрее и запоминать больше.",
        "Занятия с логопедом": "Коррекция речи и произношения.",
    },
    "realestate": {
        "1-комн. квартира": "Уютная квартира для пары или одного.",
        "2-комн. квартира": "Просторное жильё для семьи.",
        "Студия": "Компактная планировка без лишних стен.",
        "Таунхаус": "Дом в ряду с собственным входом.",
        "Загородный дом": "Свой дом с участком за городом.",
        "Апартаменты": "Современное жильё с инфраструктурой.",
        "Коммерческое помещение": "Готово под магазин или офис.",
        "Земельный участок": "Земля под строительство дома.",
        "Пентхаус": "Видовая квартира на верхнем этаже.",
        "3-комн. квартира": "Просторная квартира для большой семьи.",
        "Машино-место": "Парковка в крытом паркинге.",
        "Офис в центре": "Помещение для бизнеса в центре города.",
    },
}

# Per-domain catalog-price band as (low, high, step) in whole roubles. A generic
# money formula (990 … 199 990) is absurd per-niche — a vitamin at 197 010 ₽, a
# coffee at 150 000 ₽. When the domain is known, an item's *price* is drawn from
# a hand-calibrated band rounded to `step`, so the first catalog a user sees is
# priced like the real market. Only true per-item price fields use this (see
# `_PRICE_TOKENS`); business-metric money (salary, revenue) keeps the generic
# band. Every domain in `_DOMAIN_NOUNS` MUST appear here (asserted in tests).
_DOMAIN_PRICE: dict[str, tuple[int, int, int]] = {
    "pharmacy": (190, 3490, 10),       # supplements, cosmetics
    "clinic": (700, 9000, 50),         # consultations … MRI
    "beauty": (500, 6000, 50),         # salon services
    "fitness": (450, 6000, 50),        # sessions, monthly pass
    "auto": (600, 18000, 100),         # oil change … timing belt
    "cafe": (120, 690, 10),            # coffee, desserts
    "restaurant": (220, 1900, 10),     # rolls, hot dishes, sets
    "furniture": (2900, 129000, 100),  # shelf … kitchen unit
    "travel": (9900, 250000, 1000),    # excursion … all-inclusive tour
    "education": (1900, 49000, 100),   # single lesson … full course
    "realestate": (1990000, 32000000, 10000),  # studio … penthouse
}

# Base gradient hue (0–360) per domain for image-field tiles (see `_demo_image`),
# so a pharmacy's photos trend clinical teal, a cafe's warm amber, a salon's pink.
# Unknown domain → a hash-derived hue. Domains mirror `_DOMAIN_NOUNS`.
_DOMAIN_HUE: dict[str, int] = {
    "pharmacy": 158, "clinic": 200, "beauty": 330, "fitness": 22,
    "auto": 215, "cafe": 32, "restaurant": 8, "furniture": 35,
    "travel": 192, "education": 250, "realestate": 210,
}

# (domain, substring tokens) in PRIORITY order — first hit wins. Order resolves
# overlaps deliberately: pharmacy before beauty (so the pharmacy enum word
# "Косметика" doesn't read as a salon), pharmacy/clinic split on stock vs. visit
# vocabulary. Tokens are deliberately specific — bare "авто" (matches "автор") or
# bare "космет" (matches both niches) are avoided to keep detection high-precision.
_DOMAIN_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pharmacy", ("aptek", "аптек", "pharma", "фарма", "препарат", "витамин",
                  "лекарств", "medication", "prescription", "dosage", "бад ", "бады")),
    ("clinic", ("klinik", "клиник", "clinic", "медцентр", "поликлиник",
                "стоматолог", "dental", "терапевт", "пациент", "patient", "диагноз")),
    ("beauty", ("салон красот", "beauty", "маникюр", "педикюр", "барбер", "barber",
                "парикмахер", "стрижк", "ногт", "бьюти",
                "krasot", "manikur", "pedikur", "parikmaher", "strizhk", "nail")),
    ("fitness", ("фитнес", "fitness", "спортзал", "sportzal", "gym", "тренировк",
                 "workout", "йог", "пилатес", "кроссфит", "trenirov", "yoga")),
    ("auto", ("автосервис", "avtoservis", "шиномонтаж", "автомойк", "avtomoyk",
              " сто ", "запчаст", "car wash", "моторист", "детейлинг")),
    ("restaurant", ("ресторан", "restaurant", "restoran", "суши", "sushi", "пицц",
                    "pizza", "бургер", "burger", "роллы", "доставка еды")),
    ("cafe", ("кофейн", "kofein", "кофе", "coffee", "cafe", "кафе", "бариста",
              "латте", "капучино", "пекарн", "кондитер", "десерт")),
    ("furniture", ("мебел", "mebel", "furniture", "диван", "шкаф", "шоурум",
                   "shourum", "showroom", "интерьер")),
    ("travel", ("турагент", "turagent", "travel", "путешеств", "экскурс",
                "авиабилет", "туроператор", "тур ")),
    ("education", ("школа", "школ ", "курсы", "course", "обучен", "education",
                   "репетитор", "ученик", "преподавател", "уроки", "академи",
                   "kursy", " kurs ", "shkol", "obuchen", "repetitor", "uchebn")),
    ("realestate", ("недвиж", "nedvizh", "realestate", "realty", "квартир",
                    "апартамент", "новостройк", "застройщик")),
)

# Field-name token groups (lowercased substring match). Order matters: the first
# group whose token appears in the field name wins.
_PERSON_TOKENS = (
    "fullname", "имя", "фамилия", "firstname", "lastname",
    "client", "клиент", "customer", "заказчик", "contact", "контакт",
    "manager", "менеджер", "author", "автор", "student", "студент", "ученик",
    "employee", "сотрудник", "patient", "пациент", "doctor", "врач",
    "member", "участник", "owner", "владелец", "lead", "лид",
)
_COMPANY_TOKENS = ("company", "компания", "организац", "firm", "фирма", "бренд", "brand")
_EMAIL_TOKENS = ("email", "e-mail", "почта", "mail")
_PHONE_TOKENS = ("phone", "телефон", "тел", "mobile", "моб")
_CITY_TOKENS = ("city", "город", "town")
_ADDRESS_TOKENS = ("address", "адрес", "улица", "street")
_URL_TOKENS = ("url", "link", "ссылка", "website", "сайт")
# An image/photo field — emit a self-contained SVG data-URI tile (`_demo_image`),
# never a text placeholder that renders as a broken <img> on the first screen.
# Checked before `_URL_TOKENS` so an `image_url` field becomes an image, not a link.
_IMAGE_TOKENS = (
    "image", "img", "photo", "picture", "avatar", "cover", "thumbnail",
    "thumb", "фото", "изображен", "картинк", "обложк", "аватар", "превью",
)
_MONEY_TOKENS = (
    "price", "цена", "amount", "сумма", "cost", "стоим", "total", "итог",
    "salary", "окл-", "оклад", "revenue", "выручка", "budget", "бюджет",
)
# The per-item *price* subset of money fields — these get the niche price band
# (`_DOMAIN_PRICE`). Order/inventory totals and business metrics (amount, total,
# salary, revenue, budget) are deliberately excluded: they're not catalog prices
# and a niche item band would misprice them.
_PRICE_TOKENS = ("price", "цена", "cost", "стоим", "тариф")
_RATING_TOKENS = ("rating", "рейтинг", "score", "оценка", "stars", "звёзд", "звезд")
_AGE_TOKENS = ("age", "возраст")
_PERCENT_TOKENS = ("percent", "процент", "progress", "прогресс", "discount", "скидк")
_COUNT_TOKENS = (
    "count", "количество", "qty", "quantity", "кол-", "stock", "остаток",
    "views", "просмотр", "likes", "лайк",
)
_STATUS_TOKENS = ("status", "статус", "state", "состояние", "stage", "этап")
# A product/article code — render a real SKU ("ART-4821"), not a placeholder word.
_CODE_TOKENS = ("sku", "артикул", "barcode", "штрихкод", "vendor_code", "vendorcode")
# A public-catalog description (vs. an internal operational note) — routes the
# `text` value to catalog blurbs instead of back-office sentences.
_DESC_TOKENS = (
    "description", "описание", "about", "о товаре", "о продукте", "summary",
    "краткое", "подробн", "характеристик", "состав", "tagline", "слоган",
)
# Fields that hold an entity's primary human label — these get a niche product
# noun when the domain is known. A non-label string field keeps the safe demo
# label so an unrelated field never turns into a product noun.
_LABEL_FIELD_TOKENS = (
    "title", "name", "название", "наименование", "заголовок", "товар",
    "продукт", "product", "услуга", "service", "блюдо", "позиция", "model",
    "модель",
)
# A catalog *category/type* enum field — its value should match the row's title
# noun (see `_DOMAIN_NOUN_CATEGORY`). Deliberately excludes status/state vocabulary
# so a status enum keeps the plain index spread. Correlation only fires when an
# option actually matches, so a broad token like "тип" is safe (no match → cycle).
_CATEGORY_FIELD_TOKENS = (
    "categ", "категор", "group", "группа", "раздел", "рубрик", "rubric",
    "вид", "kind", "section", "тип", "type",
)
# Date-field names that point FORWARD in time — a deadline, booking, delivery,
# promo-expiry, upcoming event. A past date there reads as already expired on the
# first catalog screen ("Акция до 12.02" when it's June), so we seed a near-future
# date instead. High-precision-or-nothing: no marker → byte-identical past
# behaviour (creation/registration/birth dates stay in the past), 0 regression.
_FUTURE_DATE_TOKENS = (
    "expir", "deadline", "delivery", "shipping", "arriv", "booking", "reserv",
    "appoint", "schedul", "upcoming", "valid", "until", "event",
    "истек", "истёк", "срок", "годност", "дедлайн", "доставк", "отгрузк",
    "прибыт", "поступл", "брон", "сеанс", "меропр", "событ", "концерт",
    "предстоящ", "заплан", "акци", "промо", "действ",
)
# String-field token groups that outrank the niche-noun branch in `_demo_string`.
# A label field only becomes a product noun when none of these match first — the
# same precedence is reused to find the row's primary label field for category
# correlation, so the two paths can never disagree about which field is the title.
_PRE_LABEL_TOKEN_GROUPS = (
    _IMAGE_TOKENS, _CODE_TOKENS, _EMAIL_TOKENS, _PHONE_TOKENS, _CITY_TOKENS,
    _ADDRESS_TOKENS, _URL_TOKENS, _COMPANY_TOKENS, _STATUS_TOKENS, _PERSON_TOKENS,
)


@dataclass(frozen=True)
class FieldShape:
    """One entity field, parsed defensively from loose JSON."""

    type: str
    required: bool = False
    options: tuple[str, ...] = ()
    reference: str | None = None


@dataclass(frozen=True)
class EntityShape:
    """A parsed `entities/<Name>.json` schema."""

    name: str
    access: str
    fields: dict[str, FieldShape] = dataclass_field(default_factory=dict)


_VALID_TYPES = frozenset(
    {"string", "text", "number", "boolean", "date", "enum", "reference"}
)
_PERSON_ENTITY_HINT = (
    "client", "customer", "student", "patient", "employee", "manager",
    "user", "contact", "lead", "author", "member", "doctor", "person",
    "клиент", "пациент", "сотрудник", "ученик", "студент", "пользователь",
)


def parse_entity(raw: Mapping[str, Any]) -> EntityShape:
    """Coerce a loose entity-JSON mapping into a validated EntityShape.

    Mirrors the engine's tolerant `normalize`: unknown/missing types fall back to
    "string", a bad access policy falls back to "owner". Never raises on
    malformed input — a broken schema yields a usable (if plain) shape.
    """
    name = str(raw.get("name") or "Item")
    access_raw = raw.get("access")
    access = access_raw if access_raw in ("public", "admin", "owner") else "owner"

    fields: dict[str, FieldShape] = {}
    raw_fields = raw.get("fields")
    if isinstance(raw_fields, Mapping):
        for key, spec in raw_fields.items():
            if not isinstance(key, str) or not isinstance(spec, Mapping):
                continue
            ftype = spec.get("type")
            ftype = ftype if ftype in _VALID_TYPES else "string"
            opts_raw = spec.get("options")
            options = (
                tuple(str(o) for o in opts_raw)
                if isinstance(opts_raw, Sequence) and not isinstance(opts_raw, str)
                else ()
            )
            ref = spec.get("entity")
            fields[key] = FieldShape(
                type=ftype,
                required=bool(spec.get("required", False)),
                options=options,
                reference=str(ref) if isinstance(ref, str) else None,
            )
    return EntityShape(name=name, access=access, fields=fields)


def row_count(entity: str, seed: str) -> int:
    """Deterministic row count in [MIN_ROWS, MAX_ROWS] for an entity."""
    span = MAX_ROWS - MIN_ROWS + 1
    return MIN_ROWS + _hash_int(seed, "count", entity) % span


def generate_rows(
    entity: str,
    fields: Mapping[str, FieldShape],
    *,
    count: int,
    seed: str,
    references: Mapping[str, Sequence[str]] | None = None,
    niche: str | None = None,
) -> list[dict[str, Any]]:
    """Generate `count` deterministic demo `data` payloads for one entity.

    Each payload is a `{field: value}` dict ready for the `records.data` JSONB
    column, with every field the schema declares filled with a plausible value.
    `reference` fields draw an id from `references[target_entity]` when supplied,
    else they're left null (the seeding order that fills those pools is a later
    slice's concern). Output is stable for identical arguments.

    `niche` is an optional free-text hint (the project slug or prompt) that, with
    the entity's own vocabulary, lets the seeder pick *niche-realistic* catalog
    titles. When no domain is recognised the output is byte-identical to the
    niche-less path — so this never regresses an unknown niche.
    """
    refs = references or {}
    domain = _detect_domain(entity, fields, niche)
    # The app's own brand domain for email fields (`anna@salon-krasoty.ru`),
    # derived from the slug. None for a niche-less / non-ASCII-slug call → email
    # keeps its byte-identical legacy form (0 regression).
    email_domain = _email_domain(niche)
    # The field whose title noun a `category` enum should agree with (only when a
    # niche is known — otherwise there's no noun to correlate against).
    label_field = _primary_label_field(entity, fields) if domain else None
    rows: list[dict[str, Any]] = []
    for i in range(max(0, count)):
        row_category: str | None = None
        row_description: str | None = None
        if domain is not None and label_field is not None:
            noun = _niche_noun(domain, seed, entity, label_field, i)
            row_category = _DOMAIN_NOUN_CATEGORY.get(domain, {}).get(noun)
            row_description = _DOMAIN_NOUN_DESCRIPTION.get(domain, {}).get(noun)
        row: dict[str, Any] = {}
        for fname, fshape in fields.items():
            value = _field_value(
                entity, fname, fshape, seed=seed, index=i, refs=refs,
                domain=domain, row_category=row_category,
                row_description=row_description, email_domain=email_domain,
            )
            if value is None and not fshape.required:
                # Skip optional nulls (e.g. a reference with no pool) — leaving
                # the key out matches how the app would store an omitted field.
                continue
            row[fname] = value
        rows.append(row)
    return rows


def _detect_domain(
    entity: str, fields: Mapping[str, FieldShape], niche: str | None
) -> str | None:
    """Infer a catalog domain from the niche hint plus the entity's own
    vocabulary (name, field names, enum options). High-precision-or-nothing:
    returns the first domain whose token appears, else None (safe fallback)."""
    parts: list[str] = [niche or "", entity]
    for fname, fshape in fields.items():
        parts.append(fname)
        parts.extend(fshape.options)
    signal = " " + " ".join(parts).lower() + " "
    for domain, tokens in _DOMAIN_TOKENS:
        if any(t in signal for t in tokens):
            return domain
    return None


def _takes_niche_noun(entity: str, key: str) -> bool:
    """Whether a string field named `key` resolves to a niche catalog noun in
    `_demo_string` — true only when it's a label field that no higher-precedence
    group (image/code/email/…/person) claims first. Lowercased `key` expected."""
    if any(_has_token(key, group) for group in _PRE_LABEL_TOKEN_GROUPS):
        return False
    if _is_person_entity(entity, key):
        return False
    return _has_token(key, _LABEL_FIELD_TOKENS)


def _primary_label_field(
    entity: str, fields: Mapping[str, FieldShape]
) -> str | None:
    """The first string field that carries the entity's catalog title — the one
    whose niche noun a `category` enum should agree with. None when no field
    qualifies (e.g. a person entity, or no title-like field)."""
    for fname, fshape in fields.items():
        if fshape.type == "string" and _takes_niche_noun(entity, fname.lower()):
            return fname
    return None


def _niche_noun(
    domain: str, seed: str, entity: str, fname: str, index: int
) -> str:
    """The catalog noun for one row: spread by index off a seed-varied start so a
    page of rows is distinct. Single source for both the title value and the
    category it correlates with."""
    pool = _DOMAIN_NOUNS[domain]
    start = _hash_int(seed, entity, fname) % len(pool)
    return pool[(start + index) % len(pool)]


def _category_candidates(domain: str | None, primary: str) -> tuple[str, ...]:
    """The row's curated category word plus domain-scoped synonyms a real enum is
    likely to use instead (a pharmacy «Витамины» also reads as «Препараты»). The
    curated word comes first so the most specific option wins when present."""
    if not domain:
        return (primary,)
    return (primary, *_CATEGORY_SYNONYMS.get(domain, {}).get(primary, ()))


def _match_category_option(options: Sequence[str], category: str) -> str | None:
    """The enum option that best names `category`, or None if none does. Matches
    case-insensitively by containment (either way) or a shared 5-char stem (so
    "Десерты" ≈ "Десерт"), keeping correlation high-precision — an unrelated set
    of options yields None and the caller keeps the plain index spread."""
    c = category.lower()
    for opt in options:
        o = opt.lower()
        if c in o or o in c:
            return opt
        if len(c) >= 5 and len(o) >= 5 and c[:5] == o[:5]:
            return opt
    return None


# ── value derivation ─────────────────────────────────────────────────────────


def _field_value(
    entity: str,
    fname: str,
    fshape: FieldShape,
    *,
    seed: str,
    index: int,
    refs: Mapping[str, Sequence[str]],
    domain: str | None = None,
    row_category: str | None = None,
    row_description: str | None = None,
    email_domain: str | None = None,
) -> Any:
    key = fname.lower()

    if fshape.type == "reference":
        pool = refs.get(fshape.reference or "", ())
        if not pool:
            return None
        return pool[_hash_int(seed, entity, fname, index) % len(pool)]

    if fshape.type == "enum":
        if not fshape.options:
            return None
        # A category/type enum agrees with the row's title noun when an option
        # matches it — so a "Витамин C" row reads "Витамины", not "Косметика".
        if row_category and _has_token(key, _CATEGORY_FIELD_TOKENS):
            for cand in _category_candidates(domain, row_category):
                match = _match_category_option(fshape.options, cand)
                if match is not None:
                    return match
        # Otherwise cycle by index so a catalog spreads across every option.
        return fshape.options[index % len(fshape.options)]

    if fshape.type == "boolean":
        # ~⅓ true, deterministic — a mix reads more real than all-false.
        return _hash_int(seed, entity, fname, index) % 3 == 0

    if fshape.type == "date":
        return _demo_date(seed, entity, fname, index)

    if fshape.type == "number":
        return _demo_number(key, seed, entity, fname, index, domain)

    if fshape.type == "text":
        # A public-catalog `description` reads as catalog copy; an operational
        # `notes`/`comment` keeps the back-office sentences.
        if _has_token(key, _DESC_TOKENS):
            # A recognised niche → a real product-describing line that agrees
            # with this row's title noun ("Антиоксидант…" for a Vitamin C row),
            # not the niche-blind praise pool. Unknown niche / no noun → generic.
            if row_description is not None:
                return row_description
            return _pick(_DESCRIPTIONS, seed, entity, fname, index)
        return _pick(_SENTENCES, seed, entity, fname, index)

    # string (and any unknown type, coerced to string upstream)
    return _demo_string(entity, key, seed, fname, index, domain, email_domain)


def _demo_string(
    entity: str,
    key: str,
    seed: str,
    fname: str,
    index: int,
    domain: str | None = None,
    email_domain: str | None = None,
) -> str:
    if _has_token(key, _IMAGE_TOKENS):
        # A real, always-rendering tile — not a placeholder word the browser would
        # try to load as an image src and show broken.
        return _demo_image(entity, seed, fname, index, domain)
    if _has_token(key, _CODE_TOKENS):
        # A real-looking SKU/article code, not a placeholder word.
        prefix = "ART" if _has_token(key, ("артикул", "vendor")) else "SKU"
        return f"{prefix}-{_hash_int(seed, entity, fname, index, 'c') % 9000 + 1000}"
    if _has_token(key, _EMAIL_TOKENS):
        return _demo_email(entity, seed, fname, index, email_domain)
    if _has_token(key, _PHONE_TOKENS):
        n = _hash_int(seed, entity, fname, index, "p")
        return f"+7 (9{n % 100:02d}) {n // 100 % 1000:03d}-{n % 100:02d}-{n // 7 % 100:02d}"
    if _has_token(key, _CITY_TOKENS):
        return _pick(_CITIES, seed, entity, fname, index)
    if _has_token(key, _ADDRESS_TOKENS):
        street = _pick(_STREETS, seed, entity, fname, index)
        num = _hash_int(seed, entity, fname, index, "n") % 80 + 1
        return f"{_pick(_CITIES, seed, entity, fname, index, 'c')}, {street}, {num}"
    if _has_token(key, _URL_TOKENS):
        return f"https://example.ru/{_slugish(entity)}-{index + 1}"
    if _has_token(key, _COMPANY_TOKENS):
        return _pick(_COMPANIES, seed, entity, fname, index)
    if _has_token(key, _STATUS_TOKENS):
        return _pick(_STATUS_WORDS, seed, entity, fname, index)
    if _has_token(key, _PERSON_TOKENS) or _is_person_entity(entity, key):
        return _pick(_PERSON_NAMES, seed, entity, fname, index)
    # The entity's primary label in a recognised niche → a real catalog product.
    if domain is not None and _takes_niche_noun(entity, key):
        return _niche_noun(domain, seed, entity, fname, index)
    # A "thing" label: <Entity-label> «Adjective» — always on-topic, clearly demo.
    return f"{_pick(_LABELS, seed, entity, fname, index)} {index + 1}"


def _demo_email(
    entity: str, seed: str, fname: str, index: int, email_domain: str | None
) -> str:
    """A believable demo email. A `user1234@example.ru` placeholder is the most
    obviously fake value left on a contact card; when the app's slug gives a
    brand domain we use it (`anna@salon-krasoty.ru`), with a name-like handle for
    person entities and a business mailbox otherwise. No brand domain (niche-less
    or non-ASCII slug) → the byte-identical legacy form, so nothing regresses."""
    if email_domain is None:
        first = _hash_int(seed, entity, fname, index, "u")
        return f"user{first % 9000 + 1000}@example.ru"
    pool = _EMAIL_HANDLES_PERSON if _entity_is_person(entity) else _EMAIL_HANDLES_BIZ
    return f"{_pick(pool, seed, entity, fname, index)}@{email_domain}"


def _demo_image(
    entity: str, seed: str, fname: str, index: int, domain: str | None
) -> str:
    """A self-contained SVG data-URI tile for an image/photo field.

    An image field left as a text placeholder ("Элемент 1") renders as a *broken
    image* on the first catalog screen — the single most WOW-killing defect a
    fresh app can show. A random external stock photo is no better: an unrelated
    landscape on a vitamin card reads as wrong as the placeholder, and adds a
    network dependency. Instead we emit a deterministic, niche-tinted gradient
    tile inline as a `data:` URI — it always renders (plain <img> and next/image,
    no network, no `remotePatterns`), looks intentional, and varies per row so a
    page isn't eight identical tiles.
    """
    base = _DOMAIN_HUE.get(domain or "", _hash_int(seed, entity, fname) % 360)
    h1 = (base + index * 23) % 360
    h2 = (h1 + 28) % 360
    cx = 120 + _hash_int(seed, entity, fname, index, "x") % 360
    cy = 60 + _hash_int(seed, entity, fname, index, "y") % 280
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='600' height='400'>"
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='hsl({h1},58%,56%)'/>"
        f"<stop offset='1' stop-color='hsl({h2},64%,42%)'/>"
        "</linearGradient></defs>"
        "<rect width='600' height='400' fill='url(#g)'/>"
        f"<circle cx='{cx}' cy='{cy}' r='150' fill='#ffffff' fill-opacity='0.12'/>"
        "</svg>"
    )
    return "data:image/svg+xml," + quote(svg, safe="")


def _demo_number(
    key: str, seed: str, entity: str, fname: str, index: int, domain: str | None = None
) -> int:
    if domain is not None and _has_token(key, _PRICE_TOKENS):
        # A real-market price for this niche, rounded to the band's step.
        lo, hi, step = _DOMAIN_PRICE[domain]
        buckets = (hi - lo) // step + 1
        return lo + _hash_int(seed, entity, fname, index) % buckets * step
    if _has_token(key, _MONEY_TOKENS):
        # Round-ish rouble amounts, 990 … ~199 990 (generic / unknown niche).
        return (_hash_int(seed, entity, fname, index) % 200 + 1) * 990
    if _has_token(key, _RATING_TOKENS):
        return _hash_int(seed, entity, fname, index) % 5 + 1
    if _has_token(key, _AGE_TOKENS):
        return _hash_int(seed, entity, fname, index) % 53 + 18
    if _has_token(key, _PERCENT_TOKENS):
        return _hash_int(seed, entity, fname, index) % 101
    if _has_token(key, _COUNT_TOKENS):
        return _hash_int(seed, entity, fname, index) % 200 + 1
    return _hash_int(seed, entity, fname, index) % 1000 + 1


def _demo_date(seed: str, entity: str, fname: str, index: int) -> str:
    # Spread across a ~120-day window, anchored on a FIXED epoch so output is
    # reproducible (no wall-clock — that would break determinism in tests/CI).
    base = datetime(2026, 6, 1, tzinfo=UTC)
    h = _hash_int(seed, entity, fname, index)
    if _has_token(fname.lower(), _FUTURE_DATE_TOKENS):
        # Forward-looking field (deadline / booking / delivery / promo-until):
        # a past date reads as expired, so seed a near-future date (~2 weeks to
        # ~3.5 months out) that stays ahead of the demo period.
        return (base + timedelta(days=14 + h % 90)).date().isoformat()
    days = h % 120
    return (base - timedelta(days=days)).date().isoformat()


# ── deterministic primitives ────────────────────────────────────────────────


def _hash_int(*parts: object) -> int:
    """Stable non-negative int from the parts (SHA-256, machine-independent)."""
    raw = "".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _pick(pool: Sequence[str], *parts: object) -> str:
    return pool[_hash_int(*parts) % len(pool)]


def _has_token(key: str, tokens: tuple[str, ...]) -> bool:
    return any(t in key for t in tokens)


def _entity_is_person(entity: str) -> bool:
    """Whether the entity itself models a person (Client, Doctor, Employee …) —
    used to pick a personal vs. business email mailbox."""
    e = entity.lower()
    return any(h in e for h in _PERSON_ENTITY_HINT)


def _email_domain(niche: str | None) -> str | None:
    """The app's brand email domain (`salon-krasoty.ru`) from its slug, or None
    when no usable ASCII brand is present (Cyrillic-only hint, empty, or absent)
    — the caller then keeps the legacy `example.ru` form. Collapses repeated
    hyphens and caps the label at 24 chars on a hyphen boundary so the domain
    stays readable."""
    if not niche:
        return None
    slug = _slugish(niche)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    # `_slugish` returns "item" for an all-non-ASCII string — treat that (and any
    # too-short / letter-less result) as "no brand" and fall back.
    if slug == "item" or len(slug) < 3 or not any(c.isalpha() for c in slug):
        return None
    if len(slug) > 24:
        slug = slug[:24].rsplit("-", 1)[0] or slug[:24]
    return f"{slug}.ru"


def _is_person_entity(entity: str, key: str) -> bool:
    # Only treat a bare name/title as a person when the ENTITY itself is a person.
    if not _has_token(key, ("name", "имя", "title", "название", "заголовок", "фио")):
        return False
    e = entity.lower()
    return any(h in e for h in _PERSON_ENTITY_HINT)


def _slugish(text: str) -> str:
    out = "".join(c if c.isascii() and c.isalnum() else "-" for c in text.lower())
    return out.strip("-") or "item"
