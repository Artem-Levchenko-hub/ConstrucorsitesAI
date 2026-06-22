"""Tests for auto stack-routing (P1 — owner directive 2026-06-09, last mile).

When progressive discovery decides to BUILD and recommends a container stack for
a still-static project, ``stack_routing`` flips the template, re-scaffolds the
git, and provisions the dev container. The contract that matters: the mapping is
exact, the switch is idempotent (never double-switches a project that's already a
container stack, never touches a static recommendation), and provisioning is
fail-soft (R-10 — an orchestrator hiccup never blocks the build). Git + the
orchestrator are stubbed so these stay offline and deterministic.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from omnia_api.services import orchestrator_client, stack_routing
from omnia_api.services import repo as repo_svc
from omnia_api.services.discovery import (
    _infer_stack_from_text,
    infer_result_type_from_text,
    result_type_to_stack,
)

# ─── discovery_stack_to_template ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("stack", "expected"),
    [
        ("static", None),
        ("fullstack", "fullstack"),
        ("nextjs_entities", "nextjs_entities"),
        ("spa", "spa"),  # Phase 7.2 — no-backend Vite stack
        ("code", "code"),  # owner 2026-06-18 — language-agnostic source
        ("CODE", "code"),  # case-insensitive
        ("SPA", "spa"),  # case-insensitive
        ("NEXTJS_ENTITIES", "nextjs_entities"),  # case-insensitive
        ("  fullstack  ", "fullstack"),  # trimmed
        ("", None),
        ("garbage", None),
    ],
)
def test_stack_mapping(stack: str, expected: str | None) -> None:
    assert stack_routing.discovery_stack_to_template(stack) == expected


# ─── BS-5 acceptance-lock: tgbot/api stacks orphaned from discovery ──────────
#
# Blind spot (dogfood run #4, 2026-06-16): the `telegram-bot-aiogram` and
# `fastapi-postgres` templates are fully built & provisionable (orchestrator
# stack_registry + prompt_builder `_TGBOT_STACK`/`_BACKEND_TEMPLATES`), but NO
# natural-language request can ever reach them. Discovery's stack vocabulary
# (`discovery._STACKS`) and its `_SYSTEM` menu only offer {static, fullstack,
# nextjs_entities, spa}, and `_DISCOVERY_STACK_TO_TEMPLATE` only maps those. Live
# prod repro: "сделай телеграм-бота для записи в барбершоп" → discovery picked
# `nextjs_entities`; "telegram бот ... без сайта" → `fullstack`. The user asking
# for a bot silently gets a web app.
#
# The two xfails below lock the structural preconditions of the fix (see PROPOSAL
# P-BS5 in docs/plans/2026-06-16-dogfood-eval-routine.md). They XPASS once tgbot
# is added to the discovery vocabulary AND wired into the routing map. `strict=
# False` so CI never breaks on the current/broken state. NOT shipped blind: a real
# fix also needs backend-only provisioning (no web preview) + a TELEGRAM_BOT_TOKEN
# secret-collection UX, or the bot is dead-on-arrival.


@pytest.mark.xfail(
    reason="BS-5 blind spot: tgbot is absent from discovery._STACKS, so no NL "
    "request can ever recommend a Telegram-bot stack. Remove when the fix lands.",
    strict=False,
)
def test_tgbot_should_be_a_discovery_stack() -> None:
    from omnia_api.services.discovery import _STACKS

    assert "tgbot" in _STACKS


@pytest.mark.xfail(
    reason="BS-5 blind spot: the discovery→template map omits tgbot, so even a "
    "'tgbot' recommendation would fall through to static. Remove when fix lands.",
    strict=False,
)
def test_tgbot_should_route_to_template() -> None:
    assert stack_routing.discovery_stack_to_template("tgbot") == "telegram-bot-aiogram"


def test_tgbot_is_currently_unreachable_evidence() -> None:
    """Evidence lock (not desired behavior): documents that, TODAY, tgbot is
    orphaned from the NL pipeline on BOTH surfaces. If either changes, the xfails
    above start XPASSing and all three markers should be revisited together."""
    from omnia_api.services.discovery import _STACKS

    assert "tgbot" not in _STACKS
    assert stack_routing.discovery_stack_to_template("tgbot") is None


# ─── fakes ───────────────────────────────────────────────────────────────


class _FakeProject:
    def __init__(self, template: str) -> None:
        self.id = uuid4()
        self.slug = "shop-abc123"
        self.template = template
        self.current_snapshot_id = uuid4()


class _FakeSession:
    """Minimal async session: records add/flush/commit/refresh calls."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False
        self.flushed = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # The real AsyncSession populates a server/Python-default PK on flush;
        # mirror that so switch_to_stack can read snapshot.id afterwards.
        self.flushed = True
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj: object) -> None:
        pass


# ─── switch_to_stack ─────────────────────────────────────────────────────


async def test_switch_static_is_noop() -> None:
    """A static recommendation leaves the project untouched."""
    session = _FakeSession()
    project = _FakeProject(template="blank")
    result = await stack_routing.switch_to_stack(session, project, "static")
    assert result is None
    assert project.template == "blank"
    assert not session.committed


async def test_switch_already_container_is_idempotent() -> None:
    """A project that's already a container stack is never re-switched."""
    session = _FakeSession()
    project = _FakeProject(template="nextjs_entities")
    result = await stack_routing.switch_to_stack(session, project, "fullstack")
    assert result is None
    assert project.template == "nextjs_entities"
    assert not session.committed


# ─── pivot_code_to_web (owner 2026-06-19) ────────────────────────────────


async def test_pivot_code_to_web_flips_code_to_blank() -> None:
    """A code project pivots to the `blank` static-class template (runnable web
    page at /p/<slug>) — non-destructive (no re-scaffold), just template + commit.
    Must be a REAL template value (`blank`), never `static` (a discovery stack name
    that violates the projects.template CHECK → prod 500)."""
    session = _FakeSession()
    project = _FakeProject(template="code")
    flipped = await stack_routing.pivot_code_to_web(session, project)
    assert flipped is True
    assert project.template == "blank"
    assert session.committed
    # Non-destructive: no new snapshot scaffolded.
    assert session.added == []


async def test_pivot_code_to_web_noop_for_non_code() -> None:
    """Only a `code` project pivots — a web/spa/static project is left alone."""
    for tmpl in ("spa", "static", "nextjs_entities", "blank"):
        session = _FakeSession()
        project = _FakeProject(template=tmpl)
        flipped = await stack_routing.pivot_code_to_web(session, project)
        assert flipped is False
        assert project.template == tmpl
        assert not session.committed


# ─── pivot_static_to_app (P-H1, owner 2026-06-21) ────────────────────────
#
# H1 blind spot: a built static project's app-ification follow-up ("переделай в
# полноценное приложение: вход, кабинет, база") could never escalate, because
# switch_to_stack (which re-scaffolds) runs only on the first build — re-scaffolding
# a BUILT project would wipe history + break rollback. pivot_static_to_app escalates
# NON-DESTRUCTIVELY, exactly like pivot_code_to_web: flip the template only. The
# orchestrator owns the container scaffold (nextjs_entities has no api-side dir), so
# the git side needs nothing more.


async def test_pivot_static_to_app_escalates_static_to_entities() -> None:
    """Static → nextjs_entities flips the template + commits ONLY — no re-scaffold,
    no new snapshot — so the static history stays in git (rollback-able) and the
    orchestrated build writes the app on top. Mirrors pivot_code_to_web."""
    session = _FakeSession()
    project = _FakeProject(template="blank")
    old_snap = project.current_snapshot_id
    result = await stack_routing.pivot_static_to_app(
        session, project, "nextjs_entities"
    )
    assert result == "nextjs_entities"
    assert project.template == "nextjs_entities"
    assert session.committed
    # Non-destructive: no re-scaffold, no new starter snapshot, history untouched.
    assert session.added == []
    assert project.current_snapshot_id == old_snap


async def test_pivot_static_to_app_idempotent_for_container() -> None:
    """A project already on a container stack is never re-escalated."""
    for tmpl in ("nextjs_entities", "spa", "fullstack"):
        session = _FakeSession()
        project = _FakeProject(template=tmpl)
        result = await stack_routing.pivot_static_to_app(
            session, project, "nextjs_entities"
        )
        assert result is None
        assert project.template == tmpl
        assert not session.committed


async def test_pivot_static_to_app_noop_for_static_target() -> None:
    """A static / unknown target is a no-op — there is nothing to escalate to."""
    for stack in ("static", "", "garbage"):
        session = _FakeSession()
        project = _FakeProject(template="blank")
        result = await stack_routing.pivot_static_to_app(session, project, stack)
        assert result is None
        assert project.template == "blank"
        assert not session.committed


async def test_pivot_static_to_app_skips_scaffolded_stack() -> None:
    """`fullstack` ships REAL api-side template files the git repo must contain, so
    a flip-only escalation (which skips the re-scaffold) would leave it broken — it
    is out of scope for the non-destructive follow-up path and left untouched."""
    session = _FakeSession()
    project = _FakeProject(template="blank")
    result = await stack_routing.pivot_static_to_app(session, project, "fullstack")
    assert result is None
    assert project.template == "blank"
    assert not session.committed


async def test_switch_static_to_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Static → nextjs_entities flips template, re-scaffolds, returns new snap."""
    calls: dict[str, object] = {}

    def _fake_init(project_id, template_dir, template_name):  # type: ignore[no-untyped-def]
        calls["init"] = (project_id, template_name)
        return "deadbeef" * 5  # 40-char fake sha

    monkeypatch.setattr(repo_svc, "init_repo", _fake_init)

    session = _FakeSession()
    project = _FakeProject(template="blank")
    old_snap = project.current_snapshot_id

    result = await stack_routing.switch_to_stack(session, project, "nextjs_entities")

    assert project.template == "nextjs_entities"
    assert calls["init"][1] == "nextjs_entities"  # scaffolded from the right tpl
    assert session.committed
    assert result is not None
    assert result != old_snap  # a fresh starter snapshot replaced the old one
    assert project.current_snapshot_id == result


# ─── ensure_provisioned ──────────────────────────────────────────────────


async def test_provision_skipped_for_static() -> None:
    """Static templates have no container — provisioning is a no-op."""
    assert await stack_routing.ensure_provisioned(uuid4(), "slug", "blank") is False


async def test_provision_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def _fake_provision(*, project_id, slug, template, tier):  # type: ignore[no-untyped-def]
        seen["template"] = template
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "provision", _fake_provision)
    ok = await stack_routing.ensure_provisioned(uuid4(), "slug", "nextjs_entities")
    assert ok is True
    assert seen["template"] == "nextjs-entities"  # mapped to orchestrator dir name


async def test_provision_spa_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 7.2 — the spa stack provisions the vite-react-spa image."""
    seen: dict[str, object] = {}

    async def _fake_provision(*, project_id, slug, template, tier):  # type: ignore[no-untyped-def]
        seen["template"] = template
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "provision", _fake_provision)
    ok = await stack_routing.ensure_provisioned(uuid4(), "slug", "spa")
    assert ok is True
    assert seen["template"] == "vite-react-spa"  # mapped to orchestrator dir name


async def test_provision_failsoft(monkeypatch: pytest.MonkeyPatch) -> None:
    """An orchestrator error is swallowed — the build must not be blocked."""

    async def _boom(**_: object) -> dict[str, object]:
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(orchestrator_client, "provision", _boom)
    assert await stack_routing.ensure_provisioned(uuid4(), "slug", "fullstack") is False


# ── BLIND SPOT BS-3 (dogfood-eval run #2, 2026-06-16) ────────────────────────
# When the discovery interview is skipped (quiz / "just generate" path sends
# skip_clarify=True, or a select-mode first build), `switch_to_stack` was never
# reached — it only ran inside the discovery BUILD branch. So an unmistakable
# first-build app request ("CRM, вход, личный кабинет, база записей") built as
# freeform STATIC with dead login buttons, violating «полноценное приложение с
# 1 генерации». The fix (routers/messages.py) reuses discovery's own
# deterministic safety-net `_infer_stack_from_text` on the FIRST build when
# discovery didn't run, escalating static→container. These specs lock the
# contract that fix depends on: app-intent first prompts must infer a container
# stack, and genuine marketing landings must stay static (no false escalation).
_FIRSTBUILD_APP_PROMPTS = [
    # the exact scenario-1 prompt the dogfood run used
    "CRM для записи клиентов: вход в систему, список клиентов, добавление "
    "нового клиента, заметки по каждому клиенту",
    "сделай настоящее приложение с авторизацией и личным кабинетом",
    "хочу чтобы пользователи могли регистрироваться и сохранять свои записи",
    "интернет-магазин с корзиной и оформлением заказа",
]

_FIRSTBUILD_LANDING_PROMPTS = [
    "лендинг для кофейни с меню и фотографиями",
    "портфолио фотографа",
    "одностраничный сайт для барбершопа без регистрации",  # negated → no backend
]


@pytest.mark.parametrize("prompt", _FIRSTBUILD_APP_PROMPTS)
def test_firstbuild_app_prompt_infers_container_stack(prompt: str) -> None:
    # Skipped-interview first build must still escalate to a real app stack.
    assert _infer_stack_from_text(prompt) == "nextjs_entities"


@pytest.mark.parametrize("prompt", _FIRSTBUILD_LANDING_PROMPTS)
def test_firstbuild_landing_prompt_stays_static(prompt: str) -> None:
    # A genuine marketing landing must NOT be escalated (no false positives).
    assert _infer_stack_from_text(prompt) is None


# ── BLIND SPOT BS-7 (dogfood-eval run #5, 2026-06-16) ────────────────────────
# Live prod repro: a user asked for a лендинг автосервиса with "запись на ремонт
# онлайн" and explicitly picked "Лендинг" in the discovery quiz. They still got a
# `nextjs_entities` container app whose EVERY conversion CTA ("Записаться онлайн",
# "Записаться") points to /signin — i.e. a customer must REGISTER AN ACCOUNT to
# book an oil change. Rendered evidence: dogfood-autoservice-turbofix-342143
# /app/src/app/page.tsx → 5× href="/signin"; screenshot in _routine/runs/.
#
# Root cause: `_BACKEND_SIGNALS` treats consumer lead-capture booking words
# ("запись на", "бронирован") as proof the product needs accounts/CRUD
# (discovery.py:90). `_infer_stack_from_text` therefore escalates ANY booking
# landing → nextjs_entities (auth-gated), and the negative safety-net
# `_explicit_no_backend` only rescues prompts that literally say "без
# регистрации" — a plain "запись на X" carries no such phrase, so nothing vetoes
# it. The explicit "Лендинг" quiz pick has zero weight in the stack decision.
#
# This is a NEW trigger for the BS-3 class (over-escalation → /signin wall) that
# BS-3's downgrade cannot catch. NOT shipped blind: the fix is a bidirectional-
# risk routing/UX policy change (removing/narrowing "запись на"/"бронирован"
# would under-escalate genuine booking *apps*) AND it exposes an architectural
# gap — Omnia has no "static landing + lead-capture form, no customer auth" path
# between static and a full entity-app. See PROPOSAL P-BS7 in
# docs/plans/2026-06-16-dogfood-eval-routine.md.
_CONSUMER_BOOKING_LANDINGS = [
    "сделай сайт для автосервиса: услуги, запись на ремонт онлайн, цены, контакты",
    "лендинг барбершопа: запись на стрижку, услуги, цены",
    "сайт ресторана с бронированием столика и меню",
]


@pytest.mark.parametrize("prompt", _CONSUMER_BOOKING_LANDINGS)
def test_consumer_booking_landing_resolves_to_landing_spa(prompt: str) -> None:
    """BS-7 FIXED (RT-1): a "запись/бронирование" landing with no account ask is a
    `landing` result-type → spa (public lead-form), NOT a customer-auth entity-app
    behind /signin. The result-type router is the fix; the legacy stack net stays
    as a safety-net (see the evidence test below)."""
    assert infer_result_type_from_text(prompt) == "landing"
    assert result_type_to_stack(infer_result_type_from_text(prompt)) == "spa"


@pytest.mark.parametrize("prompt", _CONSUMER_BOOKING_LANDINGS)
def test_consumer_booking_landing_legacy_net_still_escalates_evidence(
    prompt: str,
) -> None:
    """Safety-net evidence: the LEGACY keyword net (`_infer_stack_from_text`) still
    reads booking words as backend intent and would escalate to nextjs_entities —
    which is exactly why the result-type router OVERRIDES it for a no-account
    landing (the `result_type_landing_lead_sink` slice). If this ever stops
    escalating, the router's landing override becomes a no-op and both tests should
    be revisited together."""
    assert _infer_stack_from_text(prompt) == "nextjs_entities"
