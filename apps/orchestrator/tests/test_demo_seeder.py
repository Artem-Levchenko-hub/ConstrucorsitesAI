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


# ── number heuristics ────────────────────────────────────────────────────────


def test_money_field_is_positive_number() -> None:
    f = _fields(price={"type": "number", "required": True})
    rows = ds.generate_rows("Product", f, count=8, seed="s")
    assert all(isinstance(r["price"], int) and r["price"] > 0 for r in rows)


def test_rating_field_is_one_to_five() -> None:
    f = _fields(rating={"type": "number", "required": True})
    rows = ds.generate_rows("Review", f, count=12, seed="s")
    assert all(1 <= r["rating"] <= 5 for r in rows)


def test_percent_field_is_zero_to_hundred() -> None:
    f = _fields(progress={"type": "number", "required": True})
    rows = ds.generate_rows("Enrollment", f, count=12, seed="s")
    assert all(0 <= r["progress"] <= 100 for r in rows)


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
