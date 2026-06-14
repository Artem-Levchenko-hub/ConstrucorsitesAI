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
    rows: list[dict[str, Any]] = []
    for i in range(max(0, count)):
        row: dict[str, Any] = {}
        for fname, fshape in fields.items():
            value = _field_value(
                entity, fname, fshape, seed=seed, index=i, refs=refs, domain=domain
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
        # Cycle by index so a catalog spreads across every option deterministically.
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
        pool = _DESCRIPTIONS if _has_token(key, _DESC_TOKENS) else _SENTENCES
        return _pick(pool, seed, entity, fname, index)

    # string (and any unknown type, coerced to string upstream)
    return _demo_string(entity, key, seed, fname, index, domain)


def _demo_string(
    entity: str, key: str, seed: str, fname: str, index: int, domain: str | None = None
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
        first = _hash_int(seed, entity, fname, index, "u")
        return f"user{first % 9000 + 1000}@example.ru"
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
    # Spread by index (so a page of rows is distinct) off a seed-varied start.
    if domain is not None and _has_token(key, _LABEL_FIELD_TOKENS):
        pool = _DOMAIN_NOUNS[domain]
        start = _hash_int(seed, entity, fname) % len(pool)
        return pool[(start + index) % len(pool)]
    # A "thing" label: <Entity-label> «Adjective» — always on-topic, clearly demo.
    return f"{_pick(_LABELS, seed, entity, fname, index)} {index + 1}"


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
    # Spread across the last ~120 days, anchored on a FIXED epoch so output is
    # reproducible (no wall-clock — that would break determinism in tests/CI).
    base = datetime(2026, 6, 1, tzinfo=UTC)
    days = _hash_int(seed, entity, fname, index) % 120
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


def _is_person_entity(entity: str, key: str) -> bool:
    # Only treat a bare name/title as a person when the ENTITY itself is a person.
    if not _has_token(key, ("name", "имя", "title", "название", "заголовок", "фио")):
        return False
    e = entity.lower()
    return any(h in e for h in _PERSON_ENTITY_HINT)


def _slugish(text: str) -> str:
    out = "".join(c if c.isascii() and c.isalnum() else "-" for c in text.lower())
    return out.strip("-") or "item"
