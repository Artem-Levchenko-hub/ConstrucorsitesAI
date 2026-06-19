"""Acceptance-lock for BS-9 (dogfood run #7, 2026-06-16): the entity runtime
has NO multi-role / RBAC model, so a multi-actor app whose actors need
DIFFERENT views (school journal, clinic doctor/patient, marketplace
buyer/seller) is structurally impossible — the art-director brief designs
per-role access policies the engine can neither express nor enforce.

Scenario: a real prod prompt — «Полноценное веб-приложение для школы №7:
… личные кабинеты для трёх ролей — ученик, родитель и учитель (учитель
ставит оценки, ученик и родитель только смотрят)». Sandbox
dogfood-school-zhurnal-da6a5c (template escalated blank→nextjs_entities ✓).

The AD brief (02_brief.md) faithfully modelled three roles with compound
access:
  - Student      — access: admin/teacher (write), parent/student (read)
  - JournalEntry — access: teacher (write), student/parent (read-own)
  - Parent       — access: admin (write), parent (read)   [field: phone]
But `AccessPolicy` is only `owner | public | admin` (registry.ts:46), so the
writer was forced to FLATTEN every compound spec into one of three buckets
(03_writer_raw.html):
  - Student, Teacher, Parent, Lesson, Homework, Announcement → "public"
  - JournalEntry → "owner"

Two live failures, proven against the running sandbox:

1. PRIVACY LEAK. A `public` entity is readable by ANYONE incl. anonymous
   (engine.ts:60-61: public read = open, never owner-scoped). Live:
       GET https://…-da6a5c-dev.preview…/api/entities/Parent  → HTTP 200
       {"data":[{"phone":"+7 (993) 584-93-56","fullName":"Мария Соколова",…}]}
   Parent phone numbers (personal data) served to the open internet, no auth.
   The AD's intent "Parent — read by the *parent* role" collapsed to "public =
   everyone on earth" because there is no narrower role to map it to.

2. CORE FEATURE DEAD. `JournalEntry` → "owner" → every read scoped to
   `created_by` (engine.ts:166-169). The teacher (first signup = the only
   "admin", auth.ts:149) creates the grades. A parent or student is just role
   "user", owner-scoped to rows THEY created → they read an EMPTY journal and
   can never see the child's grades. Live: anon `GET /api/entities/JournalEntry`
   → 401; a logged-in non-creator gets `[]`. "Ученик и родитель только смотрят"
   is unreachable.

Root cause (all code-proven, no LLM variance):
  - registry.ts:46  AccessPolicy = "owner" | "public" | "admin"  (3 modes only)
  - registry.ts:107-108  normalize() coerces any other access value → "owner"
  - auth.ts:149  roleForNewUser() mints only "admin" | "user" — no teacher /
    student / parent role can ever exist, so a role-conditional control like the
    brief's «кнопка "Выставить оценку" только для роли "Учитель"» can never fire.
  - engine.ts authorize() only ever checks `user.role !== "admin"`.

The fix — real per-app roles + a role-assignment UX + relationship-based
"read-own" scoping (parent → their child) — spans engine + registry + auth +
role-assignment UI (apps/web, cross-zone) + writer prompt, and is
security-sensitive. → PROPOSAL P-RBAC, NOT a blind ship.

These are deterministic file-content asserts (money-free, no container).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_REGISTRY = _ENTITIES / "src" / "lib" / "entities" / "registry.ts"
_AUTH = _ENTITIES / "src" / "lib" / "auth.ts"
_ENGINE = _ENTITIES / "src" / "lib" / "entities" / "engine.ts"


def test_access_policy_has_only_three_modes_today() -> None:
    """EVIDENCE (green today): the access vocabulary is exactly
    owner|public|admin — there is no room for an app-specific role like
    teacher/student/parent, so any role-scoped brief must be flattened."""
    src = _REGISTRY.read_text(encoding="utf-8")
    assert 'export type AccessPolicy = "owner" | "public" | "admin";' in src


def test_unknown_access_is_silently_coerced_to_owner_today() -> None:
    """EVIDENCE (green today): normalize() turns any access value that isn't
    "public"/"admin" into "owner" — so a writer that emits a role name (or the
    brief's compound "teacher (write), student/parent (read-own)") gets a silent
    owner-scope, not an error. This is exactly how JournalEntry became
    owner-scoped → empty for parents."""
    src = _REGISTRY.read_text(encoding="utf-8")
    assert 'raw.access === "public" || raw.access === "admin" ? raw.access : "owner"' in src


def test_new_users_can_only_be_admin_or_user_today() -> None:
    """EVIDENCE (green today): a brand-new signup is stamped "admin" (first
    account) or "user" — never "teacher"/"student"/"parent". Role-conditional UI
    and role-gated writes designed by the AD cannot be honoured for real
    actors."""
    src = _AUTH.read_text(encoding="utf-8")
    assert 'export async function roleForNewUser(): Promise<"admin" | "user">' in src


def test_engine_only_distinguishes_admin_today() -> None:
    """EVIDENCE (green today): authorize() never compares user.role to anything
    other than "admin" — there is no per-role write/read gate the brief could
    bind to."""
    src = _ENGINE.read_text(encoding="utf-8")
    role_checks = re.findall(r"user\.role\s*!==\s*\"([a-z]+)\"", src)
    role_checks += re.findall(r"user\.role\s*===\s*\"([a-z]+)\"", src)
    assert role_checks, "expected at least one user.role comparison"
    assert set(role_checks) == {"admin"}, (
        f"engine compares user.role only to 'admin' today; found {set(role_checks)}"
    )


@pytest.mark.xfail(
    strict=False,
    reason="BS-9 / P-RBAC not yet landed: the entity runtime supports no "
    "app-specific roles. When real per-app roles (e.g. teacher/student/parent) "
    "with relationship-scoped read-own land, AccessPolicy / roleForNewUser will "
    "grow beyond owner|public|admin and admin|user — flip this to XPASS.",
)
def test_entity_runtime_should_support_app_specific_roles() -> None:
    """DESIRED: a multi-actor app must be able to declare app-specific roles and
    scope reads to a relationship (parent → their child), not just
    owner|public|admin + admin|user. Until then, a school journal cannot let a
    parent see only their child's grades while keeping them private from the
    public — the central requirement of the genre."""
    registry = _REGISTRY.read_text(encoding="utf-8")
    auth = _AUTH.read_text(encoding="utf-8")
    has_custom_roles = (
        "roles" in registry.lower()
        and 'Promise<"admin" | "user">' not in auth
    )
    has_relationship_scope = bool(
        re.search(r"read.?own|scopeRelation|relationScope|viaReference", registry, re.I)
    )
    assert has_custom_roles and has_relationship_scope
