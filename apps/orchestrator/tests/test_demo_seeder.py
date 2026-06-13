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
