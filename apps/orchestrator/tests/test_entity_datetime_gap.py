"""Acceptance-lock for BS-32 / P-DATETIME (dogfood run #29, 2026-06-17 → FIXED
2026-06-21): the entity field system originally had NO time-of-day type. Every
"when" was a calendar `date` only, so any scheduling app — barber appointment,
clinic visit, restaurant reservation, gym class, consultation slot — could store
the DAY of an appointment but never the TIME (10:00 vs 16:30); two same-day
bookings were indistinguishable in time and could not be ordered within the day.

ORIGINAL LIVE PROOF (real product generation, not a hand-built fixture):
  gen  = dogfood-barber-crm-5d864d   (prompt: "CRM для барбершопа … записи на
         стрижку (дата, услуга, мастер) …", skip_clarify=true → BS-4 escalation
         fired → real nextjs_entities app, Client + Booking entities)
  The writer KNEW the field needed a time — it named the Booking field label
  «Дата и время визита» (date AND time of visit) and gave the booking list a
  «Время» (Time) column — but the only field type it could pick was `"date"`:
        entities/Booking.json -> "date": { "type": "date", "required": true }

THE WAVE (2026-06-21) closed the gap across the three template surfaces:
  1. registry.ts  FieldType union now adds `datetime` (calendar day + clock
     time, ISO 8601 e.g. 2026-06-21T14:30) AND `time` (HH:mm clock only). The
     data model can finally declare "this field carries a time-of-day".
  2. registry.ts  zodForField validates them: `datetime` shares the `date`
     branch (`Date.parse` must succeed, keeping the …T14:30 the form sends);
     `time` has its own HH:mm(:ss) check. fieldSqlType() casts `datetime` to a
     timestamptz like `date` (real chronological sort/filter); `time` falls
     through to text (HH:mm sorts right lexically).
  3. entity-form.tsx  the form now renders `<input type="datetime-local">` for
     a `datetime` field and `<input type="time">` for a `time` field — a user
     can physically enter the hour/minute. The datetime widget formats via
     toDateTimeInput() (YYYY-MM-DDTHH:mm in LOCAL wall-clock) so the time
     ROUND-TRIPS; the day-only `date` widget keeps toDateInput()'s
     `.toISOString().slice(0, 10)` truncation, which is correct FOR A DATE.

REMAINING SEMANTIC GAP (kept as a tight xfail below): the `time` validator is a
SHAPE-only regex `^\\d{2}:\\d{2}(:\\d{2})?$` — it accepts impossible clock values
(25:99, 88:77) because it never bounds hours to 0-23 or minutes to 0-59. The
type now EXISTS and round-trips, but a garbage clock value still validates.

Distinct from the date FAMILY already in the ledger: BS-19/BS-22 (P-DATECAST)
are about a JS-valid-but-PG-invalid date STRING crashing a sort (a robustness
bug in casting); this was the ABSENCE of a time-of-day type — a feature/semantic
gap, now closed.

Deterministic file-content asserts (money-free, no container, no LLM)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_FORM = _ENTITIES / "src" / "components" / "omnia" / "entity-form.tsx"


def _field_type_union() -> str:
    """The body of `export type FieldType = … ;` in registry.ts."""
    src = _REGISTRY.read_text(encoding="utf-8")
    m = re.search(r"export type FieldType\s*=(.*?);", src, re.S)
    assert m, "FieldType union not found in registry.ts"
    return m.group(1)


def test_field_type_union_has_time_of_day_types() -> None:
    """FIXED: the field-type vocabulary now carries `date`, `datetime`, AND
    `time` — a scheduling app can declare a field that holds a time-of-day, so
    an appointment's hour (10:00 vs 16:30) is representable in the data model."""
    union = _field_type_union()
    assert '"date"' in union
    assert '"datetime"' in union
    assert '"time"' in union


def test_datetime_and_time_have_validators() -> None:
    """FIXED: zodForField has a real validator for each time-of-day type.
    `datetime` shares the `date` branch (must `Date.parse`, so the …T14:30 the
    form sends is accepted, not rejected as an unknown type); `time` has its own
    HH:mm(:ss) shape check. Neither falls through to the bare `z.string()`."""
    src = _REGISTRY.read_text(encoding="utf-8")
    # datetime is handled alongside date (case fall-through into the Date.parse refine).
    assert re.search(r'case "date":\s*\n\s*case "datetime":', src), (
        "datetime should share the date validation branch"
    )
    assert "Date.parse" in src
    # time has its own dedicated case with an HH:mm regex.
    assert re.search(r'case "time":', src)
    assert r"\d{2}:\d{2}" in src


def test_datetime_casts_to_timestamp_for_sort() -> None:
    """FIXED: fieldSqlType() treats a `datetime` field like a `date` — it casts
    to a timestamp so ?sort/?filter give real chronological order (two same-day
    bookings now order by their time, not collapse). `time` falls through to the
    text cast, which sorts HH:mm correctly lexically."""
    src = _REGISTRY.read_text(encoding="utf-8")
    assert re.search(r'f\.type === "date" \|\| f\.type === "datetime"', src), (
        "datetime should map to the 'date' SQL cast in fieldSqlType"
    )


def test_datetime_widget_renders_a_time_capable_input() -> None:
    """FIXED: the form can now render a time-capable control — `datetime` →
    `<input type="datetime-local">` (calendar day + hour/minute) and `time` →
    `<input type="time">`. A user can physically enter the time of a visit; the
    plain `date` widget still exists for day-only fields (birthday, due date)."""
    src = _FORM.read_text(encoding="utf-8")
    assert 'type="date"' in src
    assert 'type="datetime-local"' in src
    assert 'type="time"' in src


def test_datetime_round_trip_preserves_time() -> None:
    """FIXED: the datetime widget no longer truncates the time. Its value comes
    from toDateTimeInput(), which formats to YYYY-MM-DDTHH:mm in LOCAL wall-clock
    (keeping hours+minutes) — NOT toDateInput()'s `.toISOString().slice(0, 10)`,
    which is reserved for the day-only `date` widget. So the time survives the
    round-trip into the form."""
    src = _FORM.read_text(encoding="utf-8")
    # The datetime control is fed by the time-preserving formatter…
    assert "toDateTimeInput" in src
    assert re.search(r'type="datetime-local"\s+value=\{toDateTimeInput\(', src), (
        "datetime-local input must use the time-preserving toDateTimeInput()"
    )
    # …and toDateTimeInput keeps hours+minutes (no .slice(0, 10) day-truncation).
    m = re.search(r"function toDateTimeInput\([^)]*\)[^{]*\{(.*?)\n\}", src, re.S)
    assert m, "toDateTimeInput() not found"
    body = m.group(1)
    assert "getHours" in body and "getMinutes" in body
    assert "slice(0, 10)" not in body
    # The day-only truncation still lives in toDateInput (correct for a `date`).
    assert "toISOString().slice(0, 10)" in src


def test_field_system_supports_a_time_of_day() -> None:
    """DESIRED → MET: a scheduling field can carry a time-of-day. Both surfaces
    changed together — the data model offers `datetime`/`time` field types, AND
    the form renders a time-capable input (datetime-local / time) instead of a
    day-only picker that truncates."""
    union = _field_type_union()
    form = _FORM.read_text(encoding="utf-8")

    model_has_time_type = '"datetime"' in union or '"time"' in union
    form_has_time_input = (
        'type="datetime-local"' in form or 'type="time"' in form
    )

    assert model_has_time_type and form_has_time_input


@pytest.mark.xfail(
    strict=False,
    reason="BS-32 semantic remainder: the `time` FieldType exists and round-trips, "
    "but its validator is the SHAPE-only regex /^\\d{2}:\\d{2}(:\\d{2})?$/ — it "
    "never bounds hours to 0-23 or minutes to 0-59, so impossible clock values "
    "(25:99, 88:77) still validate. When zodForField rejects an out-of-range "
    "clock, flip this to XPASS.",
)
def test_time_validator_should_reject_impossible_clock_values() -> None:
    """DESIRED: a `time` value of 25:99 (hour 25, minute 99) is not a real clock
    time and should be rejected. Today the registry regex only checks the
    two-digit shape, so we detect the gap structurally: there is no 0-23 / 0-59
    bound (e.g. an alternation like (2[0-3]|[01]\\d) for hours) on the `time`
    pattern."""
    src = _REGISTRY.read_text(encoding="utf-8")
    # Pull the regex literal used inside the `time` validator.
    m = re.search(r'case "time":(.*?)message: "invalid time"', src, re.S)
    assert m, "time validator branch not found"
    branch = m.group(1)
    rx = re.search(r"/\^([^/]+)/\.test", branch)
    assert rx, "time regex literal not found"
    pattern = rx.group(1)
    # A bounds-aware pattern restricts the FIRST digit of hours/minutes (e.g.
    # 2[0-3] for hours, [0-5]\\d for minutes). The shipped shape-only pattern uses
    # a blanket \\d{2}, which is the gap.
    hour_bounded = "2[0-3]" in pattern or "[01]" in pattern
    minute_bounded = "[0-5]" in pattern
    assert hour_bounded and minute_bounded, (
        "time regex is shape-only (\\d{2}:\\d{2}); it accepts impossible clocks"
    )
