"""Подготовка контейнера проекта перед сборкой.

Раньше здесь проверялась автоматическая маршрутизация стеков: продукт умел делать
сайты разных видов и по итогам опроса переключал шаблон проекта. Теперь создаётся
только приложение MAX, переключать не на что, и те функции сняты вместе с их
проверками.

Осталось два предмета: распознавание намерения по тексту (оно живёт в discovery и
по-прежнему решает, что человек просит) и мягкий подъём контейнера разработки —
заминка оркестратора не должна срывать сборку. Git и оркестратор подменены, так
что проверки офлайновые и предсказуемые.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from yleum_api.schemas.project import orchestrator_template
from yleum_api.services import orchestrator_client, stack_routing
from yleum_api.services.discovery import (
    _infer_max_miniapp_from_text,
    _infer_realtime_from_text,
    _infer_stack_from_text,
    _resolve_messenger_stack,
    infer_result_type_from_text,
    result_type_to_stack,
)

# ─── MAX Mini App routing ────────────────────────────────────────────────


def test_max_miniapp_stack_maps_to_template() -> None:
    """Стек MAX ведёт в шаблон оркестратора max-miniapp-nextjs.

    Соответствие переехало: раньше его держала таблица в stack_routing, вместе с
    десятком стеков старого конструктора. Стек остался один, и живёт он там же,
    где остальные знания о шаблоне проекта.
    """
    assert orchestrator_template("max_miniapp") == "max-miniapp-nextjs"


@pytest.mark.parametrize(
    "text",
    [
        "сделай мини-приложение в MAX",
        "мини-приложение внутри мессенджера MAX для программы лояльности",
        "магазин внутри мессенджера max",
        "MAX mini app для программы лояльности",
        "приложение для мессенджера Макс",
        "бот в max с витриной",
    ],
)
def test_max_miniapp_intent_detected(text: str) -> None:
    assert _infer_max_miniapp_from_text(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "задай max-width: 1200px",
        "максимально быстрый сайт",
        "обычный мессенджер для команды",
        "лендинг для магазина",
        "MAX_RESULTS = 20",
    ],
)
def test_max_miniapp_intent_does_not_overfire(text: str) -> None:
    assert _infer_max_miniapp_from_text(text) is False


def test_max_miniapp_wins_over_generic_realtime_override() -> None:
    intent = "мини-приложение внутри мессенджера MAX для программы лояльности"
    assert _infer_realtime_from_text(intent) is True
    assert _resolve_messenger_stack("realtime", intent) == "max_miniapp"
    assert _resolve_messenger_stack("nextjs_entities", intent) == "max_miniapp"


# ─── realtime routing (G001 — messenger / chat / live) ───────────────────


@pytest.mark.parametrize(
    "text",
    [
        "создай семейный мессенджер",
        "я хочу чат в реальном времени с семьёй",
        "messenger для команды",
        "приложение для общения, обмен сообщениями",
        "групповой чат с друзьями",
    ],
)
def test_realtime_intent_detected(text: str) -> None:
    """The deterministic floor routes clear live/chat intent to realtime — it
    must not depend on the model picking it (which is what failed before)."""
    assert _infer_realtime_from_text(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "лендинг для пиццерии с меню и формой заказа",
        "интернет-магазин косметики с корзиной",
        "портфолио фотографа",
        "напиши парсер на python",
        "сайт-визитка для стоматологии",
    ],
)
def test_realtime_intent_not_overfired(text: str) -> None:
    """Precision guard: a normal site / shop / tool / script must NOT be routed
    to the realtime stack."""
    assert _infer_realtime_from_text(text) is False


# ─── discovery_stack_to_template ─────────────────────────────────────────


# ─── ensure_provisioned ──────────────────────────────────────────────────


async def test_provision_skipped_for_static() -> None:
    """Static templates have no container — provisioning is a no-op."""
    assert await stack_routing.ensure_provisioned(uuid4(), "slug", "blank") is False


async def test_provision_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def _fake_provision(  # type: ignore[no-untyped-def]
        *, project_id, slug, template, tier, timeout  # noqa: ASYNC109
    ):
        seen["template"] = template
        seen["timeout"] = timeout
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "provision", _fake_provision)
    ok = await stack_routing.ensure_provisioned(uuid4(), "slug", "max_miniapp")
    assert ok is True
    assert seen["template"] == "max-miniapp-nextjs"  # mapped to orchestrator dir name
    assert seen["timeout"] == 30.0


async def test_provision_failsoft(monkeypatch: pytest.MonkeyPatch) -> None:
    """An orchestrator error is swallowed — the build must not be blocked."""

    async def _boom(**_: object) -> dict[str, object]:
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(orchestrator_client, "provision", _boom)
    assert await stack_routing.ensure_provisioned(uuid4(), "slug", "fullstack") is False


async def test_required_provision_waits_for_ready_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _fake_provision(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "provision", _fake_provision)
    assert (
        await stack_routing.ensure_provisioned(
            uuid4(),
            "slug",
            "max_miniapp",
            require_ready=True,
        )
        is True
    )
    assert seen["template"] == "max-miniapp-nextjs"
    assert seen["timeout"] == 420.0


async def test_required_provision_fails_before_agent_uses_missing_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _not_ready(**_: object) -> dict[str, object]:
        return {"state": "provisioning"}

    monkeypatch.setattr(orchestrator_client, "provision", _not_ready)
    with pytest.raises(RuntimeError, match="did not become ready"):
        await stack_routing.ensure_provisioned(
            uuid4(),
            "slug",
            "max_miniapp",
            require_ready=True,
        )


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
