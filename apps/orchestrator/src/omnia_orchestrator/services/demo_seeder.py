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

# Short, neutral sentences for `text` fields — believable notes/descriptions.
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

_STATUS_WORDS: tuple[str, ...] = (
    "Новый",
    "В работе",
    "Завершён",
    "На паузе",
    "Согласование",
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
_MONEY_TOKENS = (
    "price", "цена", "amount", "сумма", "cost", "стоим", "total", "итог",
    "salary", "окл-", "оклад", "revenue", "выручка", "budget", "бюджет",
)
_RATING_TOKENS = ("rating", "рейтинг", "score", "оценка", "stars", "звёзд", "звезд")
_AGE_TOKENS = ("age", "возраст")
_PERCENT_TOKENS = ("percent", "процент", "progress", "прогресс", "discount", "скидк")
_COUNT_TOKENS = (
    "count", "количество", "qty", "quantity", "кол-", "stock", "остаток",
    "views", "просмотр", "likes", "лайк",
)
_STATUS_TOKENS = ("status", "статус", "state", "состояние", "stage", "этап")


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
) -> list[dict[str, Any]]:
    """Generate `count` deterministic demo `data` payloads for one entity.

    Each payload is a `{field: value}` dict ready for the `records.data` JSONB
    column, with every field the schema declares filled with a plausible value.
    `reference` fields draw an id from `references[target_entity]` when supplied,
    else they're left null (the seeding order that fills those pools is a later
    slice's concern). Output is stable for identical arguments.
    """
    refs = references or {}
    rows: list[dict[str, Any]] = []
    for i in range(max(0, count)):
        row: dict[str, Any] = {}
        for fname, fshape in fields.items():
            value = _field_value(entity, fname, fshape, seed=seed, index=i, refs=refs)
            if value is None and not fshape.required:
                # Skip optional nulls (e.g. a reference with no pool) — leaving
                # the key out matches how the app would store an omitted field.
                continue
            row[fname] = value
        rows.append(row)
    return rows


# ── value derivation ─────────────────────────────────────────────────────────


def _field_value(
    entity: str,
    fname: str,
    fshape: FieldShape,
    *,
    seed: str,
    index: int,
    refs: Mapping[str, Sequence[str]],
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
        return _demo_number(key, seed, entity, fname, index)

    if fshape.type == "text":
        return _pick(_SENTENCES, seed, entity, fname, index)

    # string (and any unknown type, coerced to string upstream)
    return _demo_string(entity, key, seed, fname, index)


def _demo_string(entity: str, key: str, seed: str, fname: str, index: int) -> str:
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
    # A "thing" label: <Entity-label> «Adjective» — always on-topic, clearly demo.
    return f"{_pick(_LABELS, seed, entity, fname, index)} {index + 1}"


def _demo_number(key: str, seed: str, entity: str, fname: str, index: int) -> int:
    if _has_token(key, _MONEY_TOKENS):
        # Round-ish rouble amounts, 990 … ~199 990.
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
