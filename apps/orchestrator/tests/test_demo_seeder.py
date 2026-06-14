"""Unit suite for the model-independent demo-data generator core.

Covers the two trust properties (determinism + model-independent realism) plus
the row-count floor, field-type coverage, defensive parsing, and reference
handling.
"""

from __future__ import annotations

import re
from datetime import date

from omnia_orchestrator.services import demo_seeder as ds

# ── shared fixtures ──────────────────────────────────────────────────────────


def _fields(**specs: dict[str, object]) -> dict[str, ds.FieldShape]:
    """Build a {name: FieldShape} map from loose specs via the real parser."""
    return ds.parse_entity({"name": "X", "fields": specs}).fields


# ── row-count policy ─────────────────────────────────────────────────────────


def test_row_count_within_floor_and_ceiling() -> None:
    for entity in ("Task", "Client", "Course", "Invoice", "Product", "Deal"):
        n = ds.row_count(entity, seed="proj-1")
        assert ds.MIN_ROWS <= n <= ds.MAX_ROWS
        assert n >= 6  # the hard floor the gate depends on


def test_row_count_is_deterministic() -> None:
    assert ds.row_count("Task", "s") == ds.row_count("Task", "s")


def test_row_count_varies_by_seed_or_entity() -> None:
    counts = {ds.row_count(f"E{i}", "seed") for i in range(12)}
    assert len(counts) > 1  # not a constant


# ── determinism ──────────────────────────────────────────────────────────────


def test_generate_rows_is_byte_identical_for_same_inputs() -> None:
    f = _fields(
        title={"type": "string", "required": True},
        amount={"type": "number"},
        status={"type": "enum", "options": ["a", "b", "c"]},
        due={"type": "date"},
    )
    a = ds.generate_rows("Order", f, count=8, seed="proj-42")
    b = ds.generate_rows("Order", f, count=8, seed="proj-42")
    assert a == b


def test_generate_rows_differs_by_seed() -> None:
    f = _fields(title={"type": "string", "required": True})
    a = ds.generate_rows("Order", f, count=8, seed="proj-1")
    b = ds.generate_rows("Order", f, count=8, seed="proj-2")
    assert a != b


# ── required fields always present & non-null ────────────────────────────────


def test_required_fields_always_present_and_non_null() -> None:
    f = _fields(
        title={"type": "string", "required": True},
        price={"type": "number", "required": True},
        active={"type": "boolean", "required": True},
        kind={"type": "enum", "options": ["x", "y"], "required": True},
    )
    rows = ds.generate_rows("Product", f, count=6, seed="s")
    assert len(rows) == 6
    for r in rows:
        for key in ("title", "price", "active", "kind"):
            assert key in r and r[key] is not None


# ── enum coverage ────────────────────────────────────────────────────────────


def test_enum_values_are_from_options_and_spread() -> None:
    opts = ["low", "medium", "high"]
    f = _fields(priority={"type": "enum", "options": opts})
    rows = ds.generate_rows("Task", f, count=9, seed="s")
    seen = {r["priority"] for r in rows}
    assert seen == set(opts)  # count >= len(options) → every option appears
    assert all(r["priority"] in opts for r in rows)


def test_enum_without_options_is_omitted_when_optional() -> None:
    f = _fields(state={"type": "enum"})  # no options
    rows = ds.generate_rows("Task", f, count=6, seed="s")
    assert all("state" not in r for r in rows)


# ── date fields ──────────────────────────────────────────────────────────────


def test_date_field_is_iso_date() -> None:
    f = _fields(due={"type": "date", "required": True})
    rows = ds.generate_rows("Task", f, count=6, seed="s")
    for r in rows:
        # raises ValueError if not a valid ISO date
        date.fromisoformat(r["due"])


# The seeder anchors dates on this fixed epoch (no wall-clock → reproducible).
_DATE_EPOCH = date(2026, 6, 1)


def test_forward_looking_date_field_is_in_the_future() -> None:
    # A field whose name points forward in time (deadline / booking / delivery /
    # promo-until) must NOT seed an already-expired past date on the catalog card.
    for fname in ("deadline", "delivery_date", "акция_до", "бронь", "valid_until"):
        f = _fields(**{fname: {"type": "date", "required": True}})
        rows = ds.generate_rows("Item", f, count=8, seed="s")
        assert rows, fname
        assert all(date.fromisoformat(r[fname]) > _DATE_EPOCH for r in rows), fname


def test_backward_looking_date_field_stays_in_the_past() -> None:
    # High-precision-or-nothing: a creation/registration/birth date has no future
    # marker → byte-identical past behaviour (the common case, 0 regression).
    for fname in ("created_at", "дата_регистрации", "order_date", "дата_рождения"):
        f = _fields(**{fname: {"type": "date", "required": True}})
        rows = ds.generate_rows("Record", f, count=8, seed="s")
        assert rows, fname
        assert all(date.fromisoformat(r[fname]) <= _DATE_EPOCH for r in rows), fname


def test_forward_date_field_is_deterministic() -> None:
    f = _fields(deadline={"type": "date", "required": True})
    a = ds.generate_rows("Item", f, count=8, seed="proj-7")
    b = ds.generate_rows("Item", f, count=8, seed="proj-7")
    assert a == b


# ── number heuristics ────────────────────────────────────────────────────────


def test_money_field_is_positive_number() -> None:
    f = _fields(price={"type": "number", "required": True})
    rows = ds.generate_rows("Product", f, count=8, seed="s")
    assert all(isinstance(r["price"], int) and r["price"] > 0 for r in rows)


def test_rating_field_skews_high_and_appealing() -> None:
    # A fresh demo catalog should look appealing: a uniform 1–5 spread put a 1★/2★
    # "bad product" on ~40% of cards (a WOW-killer on the first screen). Ratings
    # must cluster at the top — 4–5, never below 4, kept integer so a star-widget
    # that renders `Array(rating)` can't crash on a fractional value.
    for fname in ("rating", "рейтинг", "оценка", "stars", "звёзд"):
        f = _fields(**{fname: {"type": "number", "required": True}})
        rows = ds.generate_rows("Review", f, count=12, seed="s")
        assert rows, fname
        assert all(isinstance(r[fname], int) for r in rows), fname
        assert all(4 <= r[fname] <= 5 for r in rows), fname
    # …and across many seeds the top value (5) actually dominates, so the catalog
    # reads as well-reviewed rather than mediocre.
    f = _fields(rating={"type": "number", "required": True})
    spread = [r["rating"] for s in range(40)
              for r in ds.generate_rows("Review", f, count=12, seed=f"s{s}")]
    assert spread.count(5) > spread.count(4)


def test_percent_field_is_zero_to_hundred() -> None:
    f = _fields(progress={"type": "number", "required": True})
    rows = ds.generate_rows("Enrollment", f, count=12, seed="s")
    assert all(0 <= r["progress"] <= 100 for r in rows)


def test_discount_field_is_believable_band() -> None:
    # A "Скидка 0%" badge is pointless and "97%" reads as a scam — a fresh
    # catalog must show believable promos. Discount fields land on a round
    # 5–50% band (steps of 5), never 0, never absurdly high (pillar 1).
    for fname in ("discount", "скидка", "sale"):
        f = _fields(**{fname: {"type": "number", "required": True}})
        rows = ds.generate_rows("Product", f, count=12, seed="s")
        vals = [r[fname] for r in rows]
        assert all(5 <= v <= 50 and v % 5 == 0 for v in vals), (fname, vals)


def test_discount_band_does_not_touch_progress() -> None:
    # `progress`/`процент` stay a full 0–100 sweep — a progress bar legitimately
    # reaches 0 or 100, so the discount band must not bleed into it.
    spread = [r["progress"] for s in range(40)
              for r in ds.generate_rows(
                  "Enrollment",
                  _fields(progress={"type": "number", "required": True}),
                  count=12, seed=f"s{s}")]
    assert min(spread) < 5  # reaches below the discount floor
    assert any(v % 5 != 0 for v in spread)  # not snapped to the 5-step band


# ── string heuristics (model-independent realism) ────────────────────────────


def test_email_field_looks_like_email() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows("Client", f, count=6, seed="s")
    assert all("@" in r["email"] and "." in r["email"].split("@")[1] for r in rows)


def test_phone_field_looks_like_phone() -> None:
    f = _fields(phone={"type": "string", "required": True})
    rows = ds.generate_rows("Client", f, count=6, seed="s")
    assert all(r["phone"].startswith("+7") for r in rows)


def test_person_entity_name_is_a_person() -> None:
    f = _fields(name={"type": "string", "required": True})
    rows = ds.generate_rows("Client", f, count=6, seed="s")
    # A person's name has a space (Имя Фамилия) and is from the curated pool.
    assert all(r["name"] in ds._PERSON_NAMES for r in rows)


def test_thing_entity_name_is_not_a_person() -> None:
    f = _fields(name={"type": "string", "required": True})
    rows = ds.generate_rows("Product", f, count=6, seed="s")
    assert all(r["name"] not in ds._PERSON_NAMES for r in rows)


def test_city_field_from_pool() -> None:
    f = _fields(city={"type": "string", "required": True})
    rows = ds.generate_rows("Order", f, count=6, seed="s")
    assert all(r["city"] in ds._CITIES for r in rows)


def test_text_field_is_a_sentence() -> None:
    f = _fields(notes={"type": "text", "required": True})
    rows = ds.generate_rows("Task", f, count=6, seed="s")
    assert all(r["notes"] in ds._SENTENCES for r in rows)


# ── boolean ──────────────────────────────────────────────────────────────────


def test_boolean_field_is_bool_and_mixed() -> None:
    f = _fields(done={"type": "boolean", "required": True})
    rows = ds.generate_rows("Task", f, count=12, seed="s")
    vals = {r["done"] for r in rows}
    assert all(isinstance(r["done"], bool) for r in rows)
    assert vals == {True, False}  # a healthy mix, not all one value


# ── references ───────────────────────────────────────────────────────────────


def test_reference_with_pool_draws_from_pool() -> None:
    f = _fields(project={"type": "reference", "entity": "Project", "required": True})
    pool = ["id-a", "id-b", "id-c"]
    rows = ds.generate_rows(
        "Task", f, count=6, seed="s", references={"Project": pool}
    )
    assert all(r["project"] in pool for r in rows)


def test_reference_without_pool_is_omitted_when_optional() -> None:
    f = _fields(project={"type": "reference", "entity": "Project"})
    rows = ds.generate_rows("Task", f, count=6, seed="s")
    assert all("project" not in r for r in rows)


def test_required_reference_without_pool_is_null_present() -> None:
    # Required ref with no pool must still surface (the key is present, null) so a
    # later seeding-order slice can detect/repair it rather than silently drop it.
    f = _fields(project={"type": "reference", "entity": "Project", "required": True})
    rows = ds.generate_rows("Task", f, count=6, seed="s")
    assert all("project" in r and r["project"] is None for r in rows)


# ── distinctness ─────────────────────────────────────────────────────────────


def test_rows_are_not_all_identical() -> None:
    f = _fields(
        title={"type": "string", "required": True},
        amount={"type": "number", "required": True},
        due={"type": "date", "required": True},
    )
    rows = ds.generate_rows("Order", f, count=6, seed="s")
    # at least the multi-field rows should not collapse to one repeated row
    uniq = {tuple(sorted(r.items())) for r in rows}
    assert len(uniq) >= 4


# ── defensive parsing ────────────────────────────────────────────────────────


def test_parse_entity_tolerates_garbage() -> None:
    shape = ds.parse_entity(
        {
            "name": "Thing",
            "access": "weird",  # invalid → owner
            "fields": {
                "ok": {"type": "number"},
                "notype": {},  # missing type → string
                "badspec": "not-a-dict",  # ignored
                123: {"type": "string"},  # non-str key ignored
            },
        }
    )
    assert shape.access == "owner"
    assert shape.fields["ok"].type == "number"
    assert shape.fields["notype"].type == "string"
    assert "badspec" not in shape.fields


def test_parse_entity_unknown_type_falls_back_to_string() -> None:
    shape = ds.parse_entity({"name": "T", "fields": {"f": {"type": "json"}}})
    assert shape.fields["f"].type == "string"


def test_empty_fields_yields_empty_rows() -> None:
    rows = ds.generate_rows("Empty", {}, count=6, seed="s")
    assert rows == [{}] * 6


def test_count_zero_or_negative_yields_no_rows() -> None:
    f = _fields(title={"type": "string", "required": True})
    assert ds.generate_rows("X", f, count=0, seed="s") == []
    assert ds.generate_rows("X", f, count=-3, seed="s") == []


# ── end-to-end on the real template entity ───────────────────────────────────


def test_real_task_schema_produces_full_rows() -> None:
    raw = {
        "name": "Task",
        "access": "owner",
        "fields": {
            "title": {"type": "string", "required": True},
            "done": {"type": "boolean", "default": False},
            "priority": {
                "type": "enum",
                "options": ["low", "medium", "high"],
                "default": "medium",
            },
            "due": {"type": "date"},
            "notes": {"type": "text"},
        },
    }
    shape = ds.parse_entity(raw)
    n = ds.row_count(shape.name, "proj-9")
    rows = ds.generate_rows(shape.name, shape.fields, count=n, seed="proj-9")
    assert len(rows) >= 6
    for r in rows:
        assert r["title"]  # non-empty required
        assert isinstance(r["done"], bool)
        assert r["priority"] in ("low", "medium", "high")
        date.fromisoformat(r["due"])
        assert r["notes"] in ds._SENTENCES


def test_phone_format_shape() -> None:
    f = _fields(phone={"type": "string", "required": True})
    rows = ds.generate_rows("Client", f, count=6, seed="s")
    pat = re.compile(r"^\+7 \(9\d\d\) \d\d\d-\d\d-\d\d$")
    assert all(pat.match(r["phone"]) for r in rows)


# ── niche-aware realism (catalog titles, descriptions, SKU codes) ─────────────


def test_pharmacy_catalog_titles_are_real_products_not_placeholders() -> None:
    """A pharmacy Product catalog must read like real products, not "Максимум 1".

    The niche is inferred from the entity's own enum vocabulary (Препараты /
    Витамины / …) — no LLM, no slug needed. Titles come from the pharmacy pool;
    none of the generic placeholder labels (`Максимум 5`) survive.
    """
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {
                "type": "enum",
                "options": ["Препараты", "Витамины", "Косметика"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=8, seed="s")
    assert all(r["title"] in ds._DOMAIN_NOUNS["pharmacy"] for r in rows)
    # none is the bare placeholder form "<Label> <n>" (e.g. "Максимум 5")
    assert all(not re.fullmatch(r"\S+ \d+", r["title"]) for r in rows)


def test_niche_from_slug_drives_titles_when_fields_are_generic() -> None:
    """When the entity fields carry no niche vocabulary, the slug still does."""
    f = _fields(title={"type": "string", "required": True})
    rows = ds.generate_rows(
        "Item", f, count=8, seed="s", niche="sait-avtoservis-v-moskve"
    )
    assert all(r["title"] in ds._DOMAIN_NOUNS["auto"] for r in rows)


def test_transliterated_slugs_are_detected() -> None:
    """App slugs are Latin transliterations of a Russian niche — detection must
    fire on those, not only on the Cyrillic field vocabulary."""
    f = _fields(title={"type": "string", "required": True})
    cases = {
        "sait-apteki-v-barnaule": "pharmacy",
        "kalibrovka-klinika-crm": "clinic",
        "kalibrovka-kofeinia": "cafe",
        "sushi-restoran": "restaurant",
        "mebelnyi-shourum": "furniture",
        "turagentstvo": "travel",
        "avtoservis-qa": "auto",
        "sait-sportzala": "fitness",
        "salon-krasoty-moskva": "beauty",
        "kursy-anglijskogo": "education",
        "agentstvo-nedvizhimosti": "realestate",
    }
    for slug, expected in cases.items():
        assert ds._detect_domain("Item", f, slug) == expected, slug


def test_unknown_niche_falls_back_to_safe_demo_label() -> None:
    """No detected domain → keep the existing clearly-demo label (no regression,
    never a confidently-wrong noun)."""
    f = _fields(title={"type": "string", "required": True})
    rows = ds.generate_rows("Widget", f, count=6, seed="s", niche="zzz-unknownixx")
    # falls back to "<Label> <n>" form — a label from the generic pool + index
    assert all(r["title"].split()[-1].isdigit() for r in rows)


def test_catalog_titles_are_distinct_across_a_page() -> None:
    f = _fields(title={"type": "string", "required": True})
    rows = ds.generate_rows("Dish", f, count=8, seed="s", niche="sushi-restoran")
    titles = [r["title"] for r in rows]
    # a curated catalog should not repeat the same product 8 times
    assert len(set(titles)) >= 6


def test_description_field_is_a_catalog_blurb_not_a_crm_task() -> None:
    """A `description` field reads as catalog copy; the CRM-task sentences belong
    only to operational note fields."""
    f = _fields(description={"type": "text", "required": True})
    rows = ds.generate_rows("Product", f, count=8, seed="s")
    assert all(r["description"] in ds._DESCRIPTIONS for r in rows)
    assert all(r["description"] not in ds._SENTENCES for r in rows)


def test_notes_field_still_uses_operational_sentences() -> None:
    """Routing must not touch operational note fields (no regression)."""
    f = _fields(notes={"type": "text", "required": True})
    rows = ds.generate_rows("Task", f, count=8, seed="s")
    assert all(r["notes"] in ds._SENTENCES for r in rows)


# ── title ↔ description coherence (a niche card describes the actual item) ──────


def test_every_domain_noun_has_a_description() -> None:
    """Sync guard: every catalog noun maps to a product-describing blurb, so a
    freshly added noun can never silently fall back to the generic praise pool."""
    assert set(ds._DOMAIN_NOUN_DESCRIPTION) == set(ds._DOMAIN_NOUNS)
    for domain, nouns in ds._DOMAIN_NOUNS.items():
        mapping = ds._DOMAIN_NOUN_DESCRIPTION[domain]
        for noun in nouns:
            assert noun in mapping, (domain, noun)
            assert mapping[noun], (domain, noun)  # non-empty
            # a real description, not a recycled generic-praise line
            assert mapping[noun] not in ds._DESCRIPTIONS, (domain, noun)


def test_pharmacy_description_correlates_with_title() -> None:
    """In a recognised niche the `description` describes the row's own product —
    a Vitamin C card reads its real blurb, never the niche-blind praise pool."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "description": {"type": "text", "required": True},
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(
        shape.name, shape.fields, count=12, seed="s", niche="apteka-online"
    )
    saw_known = False
    for r in rows:
        expected = ds._DOMAIN_NOUN_DESCRIPTION["pharmacy"].get(r["title"])
        assert expected is not None, r["title"]  # every pharmacy noun is mapped
        assert r["description"] == expected, (r["title"], r["description"])
        assert r["description"] not in ds._DESCRIPTIONS  # not generic praise
        saw_known = True
    assert saw_known


def test_description_falls_back_to_generic_when_niche_unknown() -> None:
    """Unknown niche → byte-identical to the pre-existing generic-pool behaviour
    (no regression for catalogs whose domain we can't confidently name)."""
    f = _fields(description={"type": "text", "required": True})
    rows = ds.generate_rows(
        "Widget", f, count=8, seed="s", niche="zzz-unknownixx"
    )
    assert all(r["description"] in ds._DESCRIPTIONS for r in rows)


def test_notes_field_uses_operational_sentences_even_in_a_niche() -> None:
    """The per-noun description only routes `description`-style fields — an
    operational `notes` field keeps the back-office sentences in every niche."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "notes": {"type": "text", "required": True},
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(
        shape.name, shape.fields, count=8, seed="s", niche="apteka-online"
    )
    assert all(r["notes"] in ds._SENTENCES for r in rows)
    assert all(r["notes"] not in ds._DOMAIN_NOUN_DESCRIPTION["pharmacy"].values()
               for r in rows)


def test_description_correlation_is_deterministic() -> None:
    raw = {
        "name": "Product",
        "fields": {
            "title": {"type": "string", "required": True},
            "description": {"type": "text", "required": True},
        },
    }
    shape = ds.parse_entity(raw)
    a = ds.generate_rows(shape.name, shape.fields, count=10, seed="p1", niche="cafe")
    b = ds.generate_rows(shape.name, shape.fields, count=10, seed="p1", niche="cafe")
    assert a == b


def test_sku_field_is_a_code_not_a_label() -> None:
    f = _fields(sku={"type": "string", "required": True})
    rows = ds.generate_rows("Product", f, count=8, seed="s")
    pat = re.compile(r"^[A-Z]{2,4}-\d{3,5}$")
    assert all(pat.match(r["sku"]) for r in rows), [r["sku"] for r in rows]


def test_niche_titles_are_deterministic() -> None:
    f = _fields(title={"type": "string", "required": True})
    a = ds.generate_rows("Item", f, count=8, seed="p1", niche="kofeinia")
    b = ds.generate_rows("Item", f, count=8, seed="p1", niche="kofeinia")
    assert a == b


def test_pharmacy_not_misdetected_as_beauty_by_cosmetics_enum() -> None:
    """`Косметика` is a pharmacy category AND a beauty word — pharmacy wins, so an
    apteka catalog never shows manicures."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {
                "type": "enum",
                "options": ["Препараты", "Витамины", "Косметика", "Органика"],
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=6, seed="s")
    assert all(r["title"] in ds._DOMAIN_NOUNS["pharmacy"] for r in rows)
    assert all(r["title"] not in ds._DOMAIN_NOUNS["beauty"] for r in rows)


def test_non_label_string_field_keeps_label_form_not_a_product_noun() -> None:
    """Domain nouns only fill the item's *title/name*; an unrelated string field
    in a niche app must not become a product noun."""
    f = _fields(
        title={"type": "string", "required": True},
        color={"type": "string", "required": True},
    )
    rows = ds.generate_rows("Item", f, count=6, seed="s", niche="apteka")
    # title is a real product, color stays a neutral demo label
    assert all(r["title"] in ds._DOMAIN_NOUNS["pharmacy"] for r in rows)
    assert all(r["color"] not in ds._DOMAIN_NOUNS["pharmacy"] for r in rows)


# ── niche-aware price realism (a vitamin must not cost 197 010 ₽) ─────────────


def test_every_noun_domain_has_a_price_band() -> None:
    """No catalog domain may ship with the generic 990…199 990 band — that is the
    exact defect (a supplement priced at 197 010 ₽) this slice exists to kill."""
    assert set(ds._DOMAIN_NOUNS) <= set(ds._DOMAIN_PRICE)


def test_pharmacy_price_is_realistic_not_absurd() -> None:
    f = _fields(price={"type": "number", "required": True})
    rows = ds.generate_rows("Product", f, count=12, seed="s", niche="apteka")
    lo, hi, _ = ds._DOMAIN_PRICE["pharmacy"]
    assert all(lo <= r["price"] <= hi for r in rows), [r["price"] for r in rows]
    # the headline bug: nothing in a pharmacy catalog reaches five-figure rubles
    assert all(r["price"] < 10_000 for r in rows), [r["price"] for r in rows]


def test_realestate_price_is_in_the_millions() -> None:
    f = _fields(price={"type": "number", "required": True})
    rows = ds.generate_rows("Flat", f, count=12, seed="s", niche="недвижимость")
    assert all(r["price"] >= 1_000_000 for r in rows), [r["price"] for r in rows]


def test_domain_price_respects_its_step() -> None:
    f = _fields(price={"type": "number", "required": True})
    rows = ds.generate_rows("Tour", f, count=12, seed="s", niche="турагентство")
    lo, hi, step = ds._DOMAIN_PRICE["travel"]
    assert all((r["price"] - lo) % step == 0 for r in rows), [r["price"] for r in rows]
    assert all(lo <= r["price"] <= hi for r in rows)


def test_price_without_domain_is_byte_identical_to_legacy_formula() -> None:
    """Unknown niche → no band → the original 990-multiple formula, unchanged."""
    f = _fields(price={"type": "number", "required": True})
    rows = ds.generate_rows("Widget", f, count=8, seed="s", niche="zzz-unknownixx")
    assert all(r["price"] % 990 == 0 and r["price"] > 0 for r in rows)
    # and identical to the truly niche-less call
    plain = ds.generate_rows("Widget", f, count=8, seed="s")
    assert [r["price"] for r in rows] == [r["price"] for r in plain]


def test_business_metric_money_keeps_generic_band_even_in_a_niche() -> None:
    """A `salary`/`revenue` field is not a catalog item price — it must NOT inherit
    a niche item band (a realtor's salary is not 30 000 000 ₽)."""
    f = _fields(salary={"type": "number", "required": True})
    rows = ds.generate_rows("Employee", f, count=8, seed="s", niche="недвижимость")
    assert all(r["salary"] % 990 == 0 for r in rows), [r["salary"] for r in rows]


def test_domain_price_is_deterministic() -> None:
    f = _fields(price={"type": "number", "required": True})
    a = ds.generate_rows("Dish", f, count=8, seed="p1", niche="sushi-restoran")
    b = ds.generate_rows("Dish", f, count=8, seed="p1", niche="sushi-restoran")
    assert [r["price"] for r in a] == [r["price"] for r in b]


# ── image fields render a real tile, never a broken <img> ────────────────────


def test_image_field_is_a_data_uri_not_a_placeholder() -> None:
    """An `image` field must be a real, renderable src — not "<Label> 1" the
    browser would load as an image and show broken."""
    f = _fields(image={"type": "string", "required": True})
    rows = ds.generate_rows("Product", f, count=6, seed="s")
    assert all(r["image"].startswith("data:image/svg+xml,") for r in rows)
    assert all("Элемент" not in r["image"] for r in rows)


def test_image_field_decodes_to_valid_svg() -> None:
    from urllib.parse import unquote

    f = _fields(photo={"type": "string", "required": True})
    rows = ds.generate_rows("Product", f, count=3, seed="s")
    for r in rows:
        svg = unquote(r["photo"].split(",", 1)[1])
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "linearGradient" in svg


def test_image_aliases_all_detected() -> None:
    """photo / avatar / cover / фото / image_url all route to a tile."""
    f = _fields(
        photo={"type": "string"},
        avatar={"type": "string"},
        cover={"type": "string"},
        фото={"type": "string"},
        image_url={"type": "string"},
    )
    rows = ds.generate_rows("Item", f, count=4, seed="s", niche="cafe")
    for r in rows:
        for key in ("photo", "avatar", "cover", "фото", "image_url"):
            assert r[key].startswith("data:image/svg+xml,"), (key, r[key])


def test_image_dedicated_type_coerces_and_gets_a_tile() -> None:
    """A field declared `type: image` (not in the valid set) coerces to string and
    still gets a tile, not a placeholder."""
    f = _fields(picture={"type": "image", "required": True})
    rows = ds.generate_rows("Product", f, count=3, seed="s")
    assert all(r["picture"].startswith("data:image/svg+xml,") for r in rows)


def test_image_tiles_are_deterministic() -> None:
    f = _fields(image={"type": "string", "required": True})
    a = ds.generate_rows("Product", f, count=6, seed="s", niche="apteka")
    b = ds.generate_rows("Product", f, count=6, seed="s", niche="apteka")
    assert [r["image"] for r in a] == [r["image"] for r in b]


def test_image_tiles_vary_across_a_page() -> None:
    """A catalog page must not show eight identical tiles."""
    f = _fields(image={"type": "string", "required": True})
    rows = ds.generate_rows("Product", f, count=8, seed="s")
    assert len({r["image"] for r in rows}) > 1


# ── title ↔ category coherence (a niche catalog's category must match the item) ─


def test_every_domain_noun_has_a_category() -> None:
    """Sync guard: every catalog noun maps to a category, so a freshly added noun
    can never silently fall through to a decorrelated category."""
    assert set(ds._DOMAIN_NOUN_CATEGORY) == set(ds._DOMAIN_NOUNS)
    for domain, nouns in ds._DOMAIN_NOUNS.items():
        mapping = ds._DOMAIN_NOUN_CATEGORY[domain]
        for noun in nouns:
            assert noun in mapping, (domain, noun)
            assert mapping[noun], (domain, noun)  # non-empty


def test_pharmacy_category_correlates_with_title() -> None:
    """A vitamin must never land in the cosmetics category, and a cream must never
    land in the vitamins category — the single most obviously-wrong catalog defect."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {
                "type": "enum",
                "options": ["Витамины", "БАДы", "Косметика"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=12, seed="s")
    saw_vitamin = saw_cosmetic = False
    for r in rows:
        title, cat = r["title"], r["category"]
        assert cat in ("Витамины", "БАДы", "Косметика")
        if title.startswith("Витамин"):
            assert cat == "Витамины", (title, cat)
            saw_vitamin = True
        if "Крем" in title or "Маска" in title:
            assert cat == "Косметика", (title, cat)
            saw_cosmetic = True
    assert saw_vitamin and saw_cosmetic  # the page actually exercised both


def test_cafe_coffee_drinks_land_in_coffee_category() -> None:
    raw = {
        "name": "Position",
        "access": "public",
        "fields": {
            "name": {"type": "string", "required": True},
            "категория": {
                "type": "enum",
                "options": ["Кофе", "Десерты", "Выпечка", "Напитки"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=12, seed="c", niche="kofeinia")
    coffee = {"Капучино", "Латте", "Раф ванильный", "Эспрессо", "Флэт уайт"}
    for r in rows:
        if r["name"] in coffee:
            assert r["категория"] == "Кофе", (r["name"], r["категория"])


def test_furniture_sofa_lands_in_sofa_category() -> None:
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "тип": {
                "type": "enum",
                "options": ["Диваны", "Шкафы", "Столы", "Хранение"],
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=12, seed="f", niche="mebel-shourum")
    for r in rows:
        if r["title"].startswith("Угловой диван"):
            assert r["тип"] == "Диваны", (r["title"], r["тип"])


def test_category_falls_back_to_index_cycle_when_no_option_matches() -> None:
    """Enum options that match no noun-category keep the deterministic index spread
    (zero regression for catalogs whose categories aren't product-type buckets)."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {
                "type": "enum",
                "options": ["Новинки", "Хиты продаж", "Распродажа"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=6, seed="s")  # pharmacy via title? no
    opts = ["Новинки", "Хиты продаж", "Распродажа"]
    # title here has no niche vocab/slug → no domain → pure index cycle preserved
    assert [r["category"] for r in rows] == [opts[i % 3] for i in range(6)]


def test_status_enum_is_not_correlated_even_in_a_niche() -> None:
    """Only category-like fields correlate; a status/state enum keeps the index
    spread even when its option words happen to look like categories."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {  # gives the niche signal (pharmacy)
                "type": "enum",
                "options": ["Витамины", "БАДы", "Косметика"],
            },
            "статус": {
                "type": "enum",
                "options": ["В наличии", "Под заказ"],
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=6, seed="s")
    opts = ["В наличии", "Под заказ"]
    assert [r["статус"] for r in rows] == [opts[i % 2] for i in range(6)]


def test_category_correlation_is_deterministic() -> None:
    raw = {
        "name": "Product",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {"type": "enum", "options": ["Витамины", "БАДы", "Косметика"]},
        },
    }
    shape = ds.parse_entity(raw)
    a = ds.generate_rows(shape.name, shape.fields, count=10, seed="p1")
    b = ds.generate_rows(shape.name, shape.fields, count=10, seed="p1")
    assert a == b


def test_match_category_option_handles_stem_and_substring() -> None:
    m = ds._match_category_option
    assert m(("Витамины", "БАДы"), "Витамины") == "Витамины"
    assert m(("Десерты и выпечка", "Кофе"), "Десерты") == "Десерты и выпечка"  # substring
    assert m(("Десерт", "Кофе"), "Десерты") == "Десерт"  # stem (5-char prefix)
    assert m(("Хиты", "Новинки"), "Витамины") is None  # no match → fall back


# ── category synonyms (RULE-10 #6a): real LLM enums use «Препараты», not the ─────
# curated «Витамины» — synonyms bridge the gap so coherence survives the real
# vocabulary, not just the curated one.


def test_pharmacy_real_world_category_enum_matches_via_synonyms() -> None:
    """A real apteka enum reads «Препараты / Косметика / Приборы» — none equal the
    curated «Витамины»/«БАДы», yet a vitamin must still land in «Препараты» and
    never in the «Приборы» (devices) bucket no product targets."""
    raw = {
        "name": "Product",
        "access": "public",
        "fields": {
            "title": {"type": "string", "required": True},
            "category": {
                "type": "enum",
                "options": ["Препараты", "Косметика", "Приборы"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=14, seed="s")
    saw_drug = saw_cosmetic = saw_device = False
    for r in rows:
        title, cat = r["title"], r["category"]
        assert cat in ("Препараты", "Косметика", "Приборы")
        if title.startswith("Витамин") or "Омега" in title or "Магний" in title:
            assert cat == "Препараты", (title, cat)
            saw_drug = True
        if "Крем" in title or "Маска" in title:
            assert cat == "Косметика", (title, cat)
            saw_cosmetic = True
        if cat == "Приборы":
            saw_device = True
    assert saw_drug and saw_cosmetic
    assert not saw_device  # no catalog item is a device → that bucket never shows


def test_synonym_used_only_when_no_direct_option_matches() -> None:
    """Coffee drinks fall to «Напитки» (a «Кофе» synonym) when the enum has no
    «Кофе» option — but the most specific option still wins when present."""
    no_coffee = {
        "name": "Position",
        "access": "public",
        "fields": {
            "name": {"type": "string", "required": True},
            "категория": {
                "type": "enum",
                "options": ["Напитки", "Десерты", "Выпечка"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(no_coffee)
    rows = ds.generate_rows(shape.name, shape.fields, count=12, seed="c", niche="kofeinia")
    coffee = {"Капучино", "Латте", "Раф ванильный", "Эспрессо", "Флэт уайт"}
    for r in rows:
        if r["name"] in coffee:
            assert r["категория"] == "Напитки", (r["name"], r["категория"])


def test_synonyms_never_override_a_direct_primary_match() -> None:
    """When the curated «Кофе» option exists, coffee lands there — the «Напитки»
    synonym must not steal it (primary tried first)."""
    raw = {
        "name": "Position",
        "access": "public",
        "fields": {
            "name": {"type": "string", "required": True},
            "категория": {
                "type": "enum",
                "options": ["Кофе", "Напитки", "Десерты"],
                "required": True,
            },
        },
    }
    shape = ds.parse_entity(raw)
    rows = ds.generate_rows(shape.name, shape.fields, count=12, seed="c", niche="kofeinia")
    coffee = {"Капучино", "Латте", "Раф ванильный", "Эспрессо", "Флэт уайт"}
    for r in rows:
        if r["name"] in coffee:
            assert r["категория"] == "Кофе", (r["name"], r["категория"])


def test_category_synonyms_are_well_formed() -> None:
    """Sync guard: every domain has a synonym map; every key is a real curated
    category; every synonym is ≥5 chars so it can never collide with an unrelated
    option as a stray substring (the matcher uses substring/stem)."""
    assert set(ds._CATEGORY_SYNONYMS) == set(ds._DOMAIN_NOUN_CATEGORY)
    for domain, syn in ds._CATEGORY_SYNONYMS.items():
        valid = set(ds._DOMAIN_NOUN_CATEGORY[domain].values())
        for primary, words in syn.items():
            assert primary in valid, (domain, primary)
            assert words, (domain, primary)  # no empty tuples — drop the key instead
            for w in words:
                assert len(w) >= 5, (domain, primary, w)


# ── niche-aware email (RULE-10 #8) ───────────────────────────────────────────
# A throwaway `user1234@example.ru` is the most obviously fake value left on a
# catalog/contact card. When the app's own slug is known the email's domain is
# the brand (`anna@salon-krasoty.ru`); person entities get a name-like handle,
# others a business mailbox. No ASCII slug → byte-identical legacy fallback.


def test_email_uses_slug_domain_for_known_niche() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows(
        "Doctor", f, count=8, seed="s", niche="salon-krasoty-moskva"
    )
    assert all(r["email"].endswith("@salon-krasoty-moskva.ru") for r in rows)
    assert all("example.ru" not in r["email"] for r in rows)  # no fake domain


def test_email_person_entity_has_name_like_handle() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows("Doctor", f, count=10, seed="s", niche="klinika-zdorovya")
    locals_ = [r["email"].split("@")[0] for r in rows]
    assert all(loc in ds._EMAIL_HANDLES_PERSON for loc in locals_)


def test_email_business_entity_has_mailbox_handle() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows("Product", f, count=10, seed="s", niche="apteka-online")
    locals_ = [r["email"].split("@")[0] for r in rows]
    assert all(loc in ds._EMAIL_HANDLES_BIZ for loc in locals_)


def test_email_falls_back_to_legacy_when_no_slug() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows("Client", f, count=6, seed="s")  # niche=None
    assert all(re.fullmatch(r"user\d{4}@example\.ru", r["email"]) for r in rows)


def test_email_falls_back_when_slug_has_no_ascii_brand() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows("Client", f, count=6, seed="s", niche="недвижимость")
    assert all(r["email"].endswith("@example.ru") for r in rows)


def test_email_is_valid_shape_and_deterministic() -> None:
    f = _fields(email={"type": "string", "required": True})
    a = ds.generate_rows("Doctor", f, count=8, seed="p1", niche="cafe-aroma")
    b = ds.generate_rows("Doctor", f, count=8, seed="p1", niche="cafe-aroma")
    assert a == b
    for r in a:
        assert re.fullmatch(r"[a-z0-9.-]+@[a-z0-9.-]+\.ru", r["email"]), r["email"]


def test_email_domain_caps_overlong_slug() -> None:
    f = _fields(email={"type": "string", "required": True})
    rows = ds.generate_rows(
        "Client", f, count=4, seed="s",
        niche="ochen-dlinnyy-slug-internet-magazina-tovarov-dlya-doma",
    )
    dom = rows[0]["email"].split("@")[1]
    assert dom.endswith(".ru")
    assert len(dom) <= 27  # 24-char slug cap + ".ru"
    assert "--" not in dom


def test_email_handle_pools_are_disjoint_and_ascii() -> None:
    """Person and business handles never overlap (so the entity-kind test is
    meaningful) and are pure lowercase ASCII (valid local-parts)."""
    assert not (set(ds._EMAIL_HANDLES_PERSON) & set(ds._EMAIL_HANDLES_BIZ))
    for h in (*ds._EMAIL_HANDLES_PERSON, *ds._EMAIL_HANDLES_BIZ):
        assert h == h.lower() and h.isascii() and h.isalnum()
