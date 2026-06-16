"""Acceptance-lock for BS-16 (dogfood run #13, 2026-06-16): a list of records
that belong to a parent can ship with the parent INVISIBLE in the list, because
`<CrudResource>` auto-EXPANDS reference fields but never auto-RENDERS a column
for them — and no SYSTEM_PROMPT directive requires the relation to be shown.

What IS healthy (verified, not the bug): the relation *picker* on create/edit.
Two independent real prod sandboxes confirm the writer correctly maps a
schema `reference` field to a populated `<Select>`:
  - dogfood-crm-verify-2a9337  Booking.clientId / Note.clientId
      → fields=[{name:"clientId", kind:"reference", refEntity:"Client", …}]
  - dogfood-dent-crm-bdc3f3    Appointment.patientId / .doctorId
      → fields=[{… kind:"reference", refEntity:"Patient"/"Doctor"}]
and EntityForm loads options via entities[refEntity].list({limit:200}) and
renders SelectItems (entity-form.tsx:96-120,291-313). No UUID text box. Good.

What is NOT healthy (the blind spot): surfacing the relation in the LIST. The
two sandboxes diverge under the SAME generator — pure writer variance:
  - dent-crm appointments/page.tsx — columns render the related name:
      {key:"patientId", header:"Пациент", render:(r)=>(r._expanded?.patientId)?.name ?? "—"}
      {key:"doctorId",  header:"Лечащий врач", render:(r)=>{const d=r._expanded?.doctorId …}}
    → you can see WHOSE appointment each row is.  ✓
  - crm-verify bookings/page.tsx — columns = Дата/Время/Услуга/Стоимость/Статус.
    clientId is in `fields` (the form) but is NOT a column. The booking list
    never shows the client → "whose booking is this?" is unanswerable in-list.
  - crm-verify notes/page.tsx ("Текстовые примечания к клиентам") — columns =
    Текст/Дата создания. clientId is not a column. A notes-ABOUT-clients screen
    that never shows the client.

Root cause (code-proven, no live container):
  - crud-resource.tsx:171-184 auto-expands reference fields into list params
    (`expand = fields.filter(f => f.kind === "reference").map(f => f.name)`),
    so `row._expanded.<ref>` IS fetched on every list call.
  - crud-resource.tsx:168 the column list is taken from props verbatim (only a
    `sortable` default is added); nothing ever derives a column from the
    reference fields. The auto-expanded data is silently thrown away whenever
    the writer omits the column.
  - SYSTEM_PROMPT.md has NO directive requiring a `reference` field to appear as
    a list column, so the writer is free to drop it (and sometimes does).

Class wider than CRM: any feed of records owned by a parent (order→customer,
ticket→project, appointment→patient, note→client) can ship a list where the
parent is invisible while the create form captures it fine.

Why this is a PROPOSAL, not a blind ship: the clean deterministic fix —
CrudResource auto-appends a column for each reference field not already present
in `columns`, rendering `_expanded[name]?.name ?? "—"` — is a TEMPLATE change
(requires a base-image rebuild on prod) and must be regen-verified across niches
(a writer could legitimately omit a self-referential/secondary ref). Min one
fix per run, no blind template ship. → PROPOSAL P-REFCOL.

These are deterministic file-content asserts (money-free, no container, no LLM).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_CRUD = _ENTITIES / "src" / "components" / "omnia" / "crud-resource.tsx"
_FORM = _ENTITIES / "src" / "components" / "omnia" / "entity-form.tsx"
_SYSTEM_PROMPT = _ENTITIES / "SYSTEM_PROMPT.md"


def test_reference_picker_is_healthy_today() -> None:
    """EVIDENCE (green today): the create/edit FORM does the right thing — a
    reference field loads its options from the related entity and renders a
    real <Select>, never a raw-UUID text box. This is the part that works; the
    list-column surfacing (below) is what does not."""
    src = _FORM.read_text(encoding="utf-8")
    # options are fetched from the referenced entity, not typed by the user
    assert "entities[f.refEntity as string].list({ limit: 200 })" in src
    # and rendered as a Select (reference shares the select branch)
    assert 'f.kind === "select" || f.kind === "reference"' in src


def test_crud_resource_expands_reference_data_today() -> None:
    """EVIDENCE (green today): CrudResource DOES fetch the related row for every
    reference field — `row._expanded.<ref>` is available — so the data needed to
    show the relation in the list is already on hand."""
    src = _CRUD.read_text(encoding="utf-8")
    assert 'fields.filter((f) => f.kind === "reference").map((f) => f.name)' in src
    assert "expand: [...(listParams?.expand ?? []), ...expand]" in src


def test_crud_resource_never_auto_renders_a_reference_column_today() -> None:
    """EVIDENCE (green today): columns are taken from props verbatim (only a
    `sortable` default is added); the word "reference" appears in CrudResource
    ONLY in the auto-expand memo, never in any column-derivation. So when the
    writer forgets the reference column, the expanded data is fetched and thrown
    away — the relation is invisible in the list (crm-verify bookings & notes)."""
    src = _CRUD.read_text(encoding="utf-8")
    assert "columns.map((c) => ({ ...c, sortable: c.sortable ?? true }))" in src
    # "reference" lives only in the expand path, not in any column injection.
    ref_lines = [
        ln.strip()
        for ln in src.splitlines()
        if "reference" in ln and not ln.strip().startswith("//")
    ]
    assert ref_lines == [
        '() => fields.filter((f) => f.kind === "reference").map((f) => f.name),'
    ], f"reference is used outside the expand memo now: {ref_lines}"


def test_system_prompt_does_not_require_reference_columns_today() -> None:
    """EVIDENCE (green today): nothing in the writer's system prompt obliges a
    reference field to appear as a list column, so dropping it (crm-verify) is
    not a prompt violation — it is an unguarded gap."""
    src = _SYSTEM_PROMPT.read_text(encoding="utf-8")
    # No directive tying reference/relation fields to a visible list column.
    assert not re.search(
        r"reference.{0,80}(column|колон|в списке|list)", src, re.I | re.S
    )


@pytest.mark.xfail(
    strict=False,
    reason="BS-16 / P-REFCOL not yet landed: a record's parent can be invisible "
    "in the list because CrudResource auto-expands reference data but never "
    "auto-renders a column for it, and the prompt does not require one. When "
    "CrudResource derives a reference column from `fields` (or the prompt "
    "mandates it), flip this to XPASS.",
)
def test_reference_fields_should_be_surfaced_in_the_list() -> None:
    """DESIRED: a reference field must be visible in the list by default — either
    CrudResource auto-appends a column for each reference field absent from
    `columns` (rendering `_expanded[name]?.name ?? "—"`), or the system prompt
    mandates such a column. Until then, a bookings/notes list can omit the
    client entirely while still capturing it on create."""
    crud = _CRUD.read_text(encoding="utf-8")
    prompt = _SYSTEM_PROMPT.read_text(encoding="utf-8")
    # CrudResource derives a column from the reference fields…
    crud_auto_column = bool(
        re.search(r"_expanded", crud)
        and re.search(r"\.kind === \"reference\"", crud) is not None
        and re.search(r"(push|concat|\.\.\.).{0,40}_expanded", crud, re.S)
    )
    # …or the prompt requires the writer to add one.
    prompt_mandates = bool(
        re.search(r"reference.{0,120}(column|колон|в списке)", prompt, re.I | re.S)
    )
    assert crud_auto_column or prompt_mandates
