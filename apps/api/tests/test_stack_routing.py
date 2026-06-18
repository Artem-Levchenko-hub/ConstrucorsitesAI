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
from omnia_api.services.discovery import _infer_stack_from_text

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


@pytest.mark.xfail(
    reason="BS-7 blind spot: a consumer lead-capture booking landing (no account "
    "ask) is force-escalated to a customer-auth entity-app, gating booking behind "
    "/signin. Remove this marker when the fix lands.",
    strict=False,
)
@pytest.mark.parametrize("prompt", _CONSUMER_BOOKING_LANDINGS)
def test_consumer_booking_landing_should_not_force_customer_auth(prompt: str) -> None:
    # Desired: a "запись/бронирование" landing where the user never asked for
    # accounts must NOT be escalated to an auth-gated stack — the booking is a
    # lead-capture form, not user registration.
    assert _infer_stack_from_text(prompt) != "nextjs_entities"


@pytest.mark.parametrize("prompt", _CONSUMER_BOOKING_LANDINGS)
def test_consumer_booking_landing_is_currently_force_escalated_evidence(
    prompt: str,
) -> None:
    """Evidence lock (not desired behavior): documents that, TODAY, every consumer
    booking landing is force-escalated to nextjs_entities. If this changes, the
    xfail above starts XPASSing and both markers should be revisited together."""
    assert _infer_stack_from_text(prompt) == "nextjs_entities"
