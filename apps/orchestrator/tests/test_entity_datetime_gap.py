"""Acceptance-lock for BS-32 (dogfood run #29, 2026-06-17): the entity field
system has NO time-of-day type. Every "when" is a calendar `date` only, so any
scheduling app — barber appointment, clinic visit, restaurant reservation, gym
class, consultation slot — can store the DAY of an appointment but never the
TIME (10:00 vs 16:30). Two same-day bookings are indistinguishable in time and
cannot be ordered within the day.

LIVE PROOF (real product generation, not a hand-built fixture):
  gen  = dogfood-barber-crm-5d864d   (prompt: "CRM для барбершопа … записи на
         стрижку (дата, услуга, мастер) …", skip_clarify=true → BS-4 escalation
         fired → real nextjs_entities app, Client + Booking entities)
  The writer KNEW the field needed a time — it named the Booking field label
  «Дата и время визита» (date AND time of visit) and gave the booking list a
  «Время» (Time) column — but the only field type it could pick is `"date"`:
        entities/Booking.json -> "date": { "type": "date", "required": true }
  Live Playwright render of the booking «Создать» dialog (worker Chromium vs the
  internal container, authed): the «Дата и время визита» control renders a bare
  `mm/dd/yyyy` date picker — FORM_DATE_INPUT_TYPES == ['date'], no time/datetime
  input anywhere in the form. The «Время» column therefore has no time to show.
  Screenshots: _routine/runs/2026-06-16T20-02Z/05_booking_form.png (date-only
  picker) + 03_dashboard.png (the «Ближайшие визиты» Time column).

Root cause (code-proven, three template surfaces):
  1. registry.ts  FieldType union = string|text|number|boolean|date|enum|
     reference — there is NO `datetime`/`time` member, so the data model cannot
     declare "this field carries a time-of-day".
  2. entity-form.tsx  the `kind === "date"` widget renders `<Input type="date">`
     — an HTML date picker with no hour/minute component, so a user physically
     cannot enter a time.
  3. entity-form.tsx  toDateInput() coerces every value with
     `.toISOString().slice(0, 10)` — it TRUNCATES to YYYY-MM-DD, dropping any
     time even if one were somehow stored. So the round-trip is lossy at the
     widget, independent of the wire.

Distinct from the date FAMILY already in the ledger: BS-19/BS-22 (P-DATECAST)
are about a JS-valid-but-PG-invalid date STRING crashing a sort (a robustness
bug in casting); this is the ABSENCE of a time-of-day type — a feature/semantic
gap. Fits the recurring "the form promises something it can't deliver" family:
BS-15 (lead form toasts success, drops the lead), BS-17 (clear toasts saved,
keeps the old value). Here the label says «время» and the widget can't capture
one.

Why this is a PROPOSAL (P-DATETIME), not a blind ship: the fix is multi-surface
and TEMPLATE-level (base-image rebuild on prod) — add a `datetime` FieldType
(registry + zodForField), render `<input type="datetime-local">` and stop the
day-truncation in the form, place events by time in CalendarView, AND steer the
writer prompt to emit `"datetime"` for appointment/slot fields without
over-emitting it for plain dates (birthday, due date). It then needs
regen-verify across niches. One fix per run; no blind template ship.

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


def test_field_type_union_has_no_time_of_day_type_today() -> None:
    """EVIDENCE (green today): the field-type vocabulary has `date` but neither
    `datetime` nor `time` — there is no way to declare a field that carries a
    time-of-day, so an appointment's hour is unrepresentable in the data model."""
    union = _field_type_union()
    assert '"date"' in union
    assert '"datetime"' not in union
    assert '"time"' not in union


def test_date_widget_is_date_only_today() -> None:
    """EVIDENCE (green today): the only date widget the form can render is an
    HTML `type="date"` picker — no hour/minute component, no `datetime-local`,
    no `type="time"`. A user cannot physically enter the time of a visit."""
    src = _FORM.read_text(encoding="utf-8")
    assert 'type="date"' in src
    assert 'type="datetime-local"' not in src
    assert 'type="time"' not in src


def test_date_round_trip_truncates_time_today() -> None:
    """EVIDENCE (green today): toDateInput() formats values with
    `.toISOString().slice(0, 10)` — it keeps only YYYY-MM-DD, so even a value
    that did carry a time loses it on the way into the form."""
    src = _FORM.read_text(encoding="utf-8")
    assert "toISOString().slice(0, 10)" in src


@pytest.mark.xfail(
    strict=False,
    reason="BS-32 / P-DATETIME not yet landed: the field system has no "
    "time-of-day type and the form is date-only, so scheduling apps cannot "
    "store an appointment's time. When a `datetime` FieldType exists AND the "
    "form renders a time-capable input (datetime-local) without truncating to "
    "the day, flip this to XPASS.",
)
def test_field_system_should_support_a_time_of_day() -> None:
    """DESIRED: a scheduling field must be able to carry a time-of-day. Two
    surfaces have to change together — the data model must offer a `datetime`
    (or `time`) field type, AND the form must render a time-capable input
    instead of a day-only picker that truncates."""
    union = _field_type_union()
    form = _FORM.read_text(encoding="utf-8")

    model_has_time_type = '"datetime"' in union or '"time"' in union
    form_has_time_input = (
        'type="datetime-local"' in form or 'type="time"' in form
    )

    assert model_has_time_type and form_has_time_input
