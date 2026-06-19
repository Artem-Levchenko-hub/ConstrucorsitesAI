"""Acceptance-lock for BS-39 (dogfood run #39, 2026-06-17).

**Blind spot:** a recurring/weekly schedule renders a SILENTLY BLANK calendar.

Live repro (prod, dogfood-shkola-7-f4cae4): the real prod prompt «Полноценное
веб-приложение для школы №7: расписание уроков … электронный журнал …» escalated
blank→nextjs_entities (BS-4 ✓) and the writer produced
`src/app/(app)/dashboard/schedule/page.tsx` with

    view="calendar"  dateField="dayOfWeek"

but `Schedule.dayOfWeek` is an ENUM ["monday".."saturday"] — a recurring weekday,
NOT a date. The calendar's `parseLocalDate` (calendar-view.tsx) sends a weekday
string to `new Date("monday")` → Invalid Date → null; every such event is
`continue`-skipped out of `placed[]`; and `noRecords = rawCount === 0` stays
False (the rows exist), so the EmptyState never shows either. Result: a month
grid with ZERO lessons and no explanation. The data and the `columns` are both
present — a usable table is one fallback away.

**Fix (shipped this run):** `CrudResource` now computes `calendarPlaceable` — if
`view="calendar"` has loaded rows but NONE carry a `parseLocalDate`-able date, it
degrades to the table the writer already configured instead of a blank grid.
Pure rendering fallback; only fires when the calendar would show nothing anyway;
generalises to any misprescribed calendar (enum / missing / malformed dateField).

This test is a source-presence guard (the fix is template TS, not importable
Python) plus a root-cause re-statement of the weekday-enum parse fact.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "orchestrator"
    / "templates"
    / "nextjs-entities"
    / "src"
    / "components"
    / "omnia"
)
_CALENDAR = _TEMPLATE / "calendar-view.tsx"
_CRUD = _TEMPLATE / "crud-resource.tsx"


def test_root_cause_weekday_enum_is_not_a_date() -> None:
    """A recurring schedule keyed on a weekday name has no absolute date.

    `dateField="dayOfWeek"` feeds "monday".."saturday" to the calendar, none of
    which is a parseable ISO/epoch/Date — so EVERY lesson drops out and the grid
    is blank. (JS `new Date("monday")` → Invalid Date; mirrored here as the fact
    that a weekday token matches no date-shaped pattern.)
    """
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    # None of these are ISO-date / epoch shaped → the calendar cannot place them.
    import re

    iso = re.compile(r"^\d{4}-\d{2}-\d{2}")
    assert all(not iso.match(d) and not d.isdigit() for d in weekdays)


def test_parse_local_date_is_exported_for_placeability_check() -> None:
    """CrudResource must be able to ask "can the calendar place any of these?"."""
    src = _CALENDAR.read_text(encoding="utf-8")
    assert "export function parseLocalDate(" in src, (
        "parseLocalDate must be exported so CrudResource can pre-check placeability"
    )


def test_crud_resource_degrades_unplaceable_calendar_to_table() -> None:
    """The fix: view='calendar' with zero date-placeable rows falls back to table."""
    src = _CRUD.read_text(encoding="utf-8")
    # Imports the placeability helper.
    assert "parseLocalDate" in src, "CrudResource must use parseLocalDate to gate calendar"
    # Computes a placeability flag from the loaded rows.
    assert "calendarPlaceable" in src
    # The calendar view is gated on placeability (not just on dateField presence),
    # so an all-unplaceable set degrades to the table instead of a blank grid.
    assert "&& calendarPlaceable" in src, (
        "useCalendar must require calendarPlaceable so a weekday-enum schedule "
        "falls back to the table the writer already configured"
    )
    # Empty/loading row sets keep the calendar (no flicker before the first fetch).
    assert "data.rows.length === 0) return true" in src
