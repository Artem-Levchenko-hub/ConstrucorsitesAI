"""Acceptance-lock for BS-38 (run #38, 2026-06-17).

**Blind spot:** a follow-up prompt that EXTENDS the data model of an existing
entity/container app ("добавь раздел «Услуги»: отдельная сущность … своя
страница … пункт в меню") is classified by the triage as a CHEAP *surgical
edit* — not a BUILD — because none of the `_REBUILD`/`_STRUCTURAL` keywords
match a section-shaped "добавь раздел" ask (intent_triage.py:62-69, by design to
avoid over-orchestration). The cheap model is then handed the container
edit-prompt (`_EDIT_IDENTITY_NEXT` + `_EDIT_RESPONSE_NEXT`, prompt_builder.py:
3864-3908), which teaches page.tsx editing/creation but NEVER the entity-engine
conventions, and — crucially — there is **NO syntax/compile validation of the
.tsx it writes before commit+hot_reload**. Container/fullstack apps SKIP the
acceptance gate (messages.py:3227) and the dead-link pass is skipped on surgical
edits, so a malformed .tsx ships silently and the build reports success
(`[PP] hot_reload OK written=N`).

**Live repro (run #38, prod, dogfood-crm-followup-37c973):** first build
escalated blank→nextjs_entities (BS-4) into a working CRM (clients / appointments
/ notes / dashboard). The follow-up above routed `mode=edit` / surgical=True /
model=deepseek-chat and produced THREE files: a *valid* `entities/services.json`
(shape copied from context) but TWO broken TSX files —

  1. `src/app/(app)/dashboard/services/page.tsx` — the `columns={[ … ]}` array is
     never closed and `fields={[` is nested INSIDE it → "Expression expected".
  2. `src/app/(app)/layout.tsx` — a surgical SEARCH/REPLACE corrupted the nav
     href: `href="/dashboard/services"` came out as `hrefdashboard/services"`
     (the `="/` was eaten) → "Expected ',', got 'string literal'".

`(app)/layout.tsx` wraps the WHOLE route group, so a single mangled attribute
took every previously-working route from 200 → HTTP 500 (verified live on the
deployed dev container: /dashboard, /dashboard/clients, /dashboard/appointments,
/dashboard/notes, /dashboard/services all 500). The owner's "5 prompts and the
generator did nothing / broke things" complaint reproduced as a follow-up that
BRICKS a working app with zero signal. Family: BS-12/BS-28/BS-31 "false success"
(build says OK, app is silently dead).

**PROPOSAL P-TSXEDIT-VALIDATE** (NOT shipped this run — multi-surface, needs a
real parser/compiler the api/worker image does not have (NO_NODE), plus a
revert/fallback so a broken edit keeps the last-good snapshot live, plus
regen-verify). A delimiter-balance heuristic in Python is FP-prone on valid TSX
(braces inside strings/regex/template-literals) AND would miss the severe
attribute-corruption class, so it is not shipped as a flaky half-gate.

These tests LOCK the root cause (routing) + the evidence (the two corruption
signatures) green, and keep the build-time guarantee (a syntax gate exists and
rejects un-parseable container .tsx before commit) RED as a strict-xfail.
"""
from __future__ import annotations

import pytest

from omnia_api.services.intent_triage import CHEAP, ORCHESTRATE, decide_intent


# The exact follow-up that triggered the live repro.
_FOLLOWUP = (
    "Добавь раздел «Услуги»: отдельная сущность с названием услуги, ценой и "
    "длительностью в минутах. Своя страница в личном кабинете со списком и "
    "добавлением, и пункт «Услуги» в меню."
)

# The two malformed files the cheap surgical edit committed (verbatim from the
# live repro, _routine/runs/2026-06-16T22-53-09Z/broken_followup_files.txt).
_BROKEN_SERVICES_PAGE = """\
"use client";
import { CrudResource } from "@/components/omnia";
import { formatRub } from "@/lib/utils";

export default function ServicesPage() {
  return (
    <CrudResource
      entity="services"
      title="Услуги"
      description="Список услуг с ценой и длительностью"
      columns={[
        { key: "name", header: "Название услуги" },
        { key: "price", header: "Цена", render: (r) => formatRub(Number(r.price)) },
        { key: "duration", header: "Длительность (мин)", render: (r) => `${String(r.duration)} мин` },
           fields={[
        { name: "name", label: "Название услуги", kind: "text", required: true },
        { name: "price", label: "Цена (₽)", kind: "number", required: true },
        { name: "duration", label: "Длительность (мин)", kind: "number", required: true },
      ]}
    />
  );
}
"""

_BROKEN_LAYOUT_NAV_LINE = (
    '  { label: "Услуги", hrefdashboard/services", icon: <Scissors />, '
    'section: "Управление" },'
)


def test_new_entity_followup_routes_to_cheap_surgical_edit() -> None:
    """ROOT CAUSE: a data-model-extending follow-up on an existing app is a CHEAP
    surgical edit, not a BUILD — so it runs the cheap container edit-prompt with
    no entity conventions and no .tsx validation."""
    assert decide_intent(_FOLLOWUP, is_first_prompt=False) == CHEAP
    # Control: the SAME ask on a brand-new project is the initial BUILD.
    assert decide_intent(_FOLLOWUP, is_first_prompt=True) == ORCHESTRATE
    # Control: an explicit full-stack/architecture ask DOES earn the build path.
    assert (
        decide_intent("переделай в полноценное fullstack приложение", is_first_prompt=False)
        == ORCHESTRATE
    )


def test_committed_followup_tsx_is_structurally_corrupt_evidence() -> None:
    """EVIDENCE: the two committed files carry the exact corruption signatures
    that 500'd the live app. Encoded so a future gate has a concrete target."""
    # services/page.tsx — `fields={[` appears while the `columns={[` array is
    # still open (no `]}` between them) → the columns literal is unterminated.
    cols = _BROKEN_SERVICES_PAGE.index("columns={[")
    fields = _BROKEN_SERVICES_PAGE.index("fields={[")
    between = _BROKEN_SERVICES_PAGE[cols:fields]
    assert "]}" not in between, "columns array unexpectedly closed before fields"
    # layout.tsx — the nav href attribute lost its `="/`: a JSX attribute name is
    # fused with its value (`hrefdashboard/...` instead of `href="/dashboard/...`).
    assert 'hrefdashboard' in _BROKEN_LAYOUT_NAV_LINE
    assert 'href="/dashboard' not in _BROKEN_LAYOUT_NAV_LINE


@pytest.mark.xfail(
    strict=True,
    reason="BS-38 / P-TSXEDIT-VALIDATE not shipped: no syntax/compile gate "
    "validates container .tsx before commit+hot_reload, so a malformed surgical "
    "edit bricks the live app with the build reporting success.",
)
def test_container_surgical_edit_rejects_unparseable_tsx() -> None:
    """BUILD-TIME GUARANTEE (currently RED): a validation gate must detect that a
    committed container `.tsx` does not parse and reject the edit (keep the
    last-good snapshot) instead of shipping it. No such gate exists yet, so the
    import below fails and the strict-xfail records the open gap."""
    from omnia_api.services.file_extractor import (  # noqa: F401
        validate_container_tsx,
    )

    bad = validate_container_tsx(
        {"src/app/(app)/dashboard/services/page.tsx": _BROKEN_SERVICES_PAGE}
    )
    assert "src/app/(app)/dashboard/services/page.tsx" in bad
