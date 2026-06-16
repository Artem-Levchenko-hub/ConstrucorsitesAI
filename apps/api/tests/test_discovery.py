"""Tests for the progressive discovery interview (P1 — owner directive 2026-06-09).

Discovery replaces the blocking onboarding quiz / one-shot clarify with a
conversational, one-question-at-a-time intake that decides on its own when to
build. The contract that matters: it NEVER raises (fail-soft, R-10), respects the
hard turn cap and explicit "build now" signals, and folds a usable brief + a
sane stack into every BUILD result. The gateway is stubbed so these stay offline
and deterministic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from omnia_api.services import discovery
from omnia_api.services.discovery import (
    _PLAN_MAX_QUESTIONS,
    ASK,
    BUILD,
    MAX_DISCOVERY_QUESTIONS,
    PlannedQuestion,
    _explicit_no_backend,
    _infer_stack_from_text,
    confident_enough_to_build,
    cumulative_idea,
    gather_answers,
    infer_niche_label,
    plan_discovery_questions,
    recap_labels,
    run_discovery,
    serve_planned_question,
    wants_build_now,
    zero_question_build,
)

# ─── Gateway stub ────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Stands in for ``httpx.AsyncClient`` as an async context manager."""

    def __init__(self, resp: _FakeResp | None, exc: Exception | None) -> None:
        self._resp = resp
        self._exc = exc

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResp:
        if self._exc is not None:
            raise self._exc
        assert self._resp is not None
        return self._resp


def _gateway_returning(
    content: str, *, status_code: int = 200
) -> _FakeResp:
    """Wrap a model reply string in the gateway's chat-completion envelope."""
    return _FakeResp(
        status_code,
        {"choices": [{"message": {"content": content}}]},
    )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resp: _FakeResp | None = None,
    exc: Exception | None = None,
) -> None:
    monkeypatch.setattr(
        discovery.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient(resp, exc),
    )


# ─── wants_build_now (pure) ──────────────────────────────────────────────


def test_wants_build_now_detects_explicit_signals() -> None:
    assert wants_build_now("давай уже генерируй")
    assert wants_build_now("СТРОЙ сайт")
    assert wants_build_now("just build it")
    assert wants_build_now("ок, поехали")


def test_wants_build_now_ignores_normal_answers() -> None:
    assert not wants_build_now("это магазин косметики")
    assert not wants_build_now("аудитория — молодые мамы")
    assert not wants_build_now("")


# ─── ASK path ────────────────────────────────────────────────────────────


async def test_ask_path_streams_model_question(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "Кто ваша аудитория?"})
        ),
    )
    result = await run_discovery([], "хочу сайт кофейни", asked_count=0)
    assert result.action == ASK
    assert result.message == "Кто ваша аудитория?"
    assert result.brief == ""


async def test_gateway_error_falls_back_to_deterministic_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, exc=RuntimeError("gateway down"))
    # Never raises; degrades to the keyed fallback question for this turn index.
    first = await run_discovery([], "идея", asked_count=0)
    second = await run_discovery([], "идея", asked_count=1)
    assert first.action == ASK and first.message
    assert second.action == ASK and second.message
    assert first.message != second.message  # one question at a time, advances


async def test_gateway_5xx_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, resp=_gateway_returning("whatever", status_code=502))
    result = await run_discovery([], "идея", asked_count=0)
    assert result.action == ASK and result.message


# ─── ASK quick-reply chips (P1) ──────────────────────────────────────────


async def test_ask_returns_model_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Нужна админка?",
                    "choices": ["Да", "Нет"],
                }
            )
        ),
    )
    result = await run_discovery([], "хочу crm", asked_count=0)
    assert result.action == ASK
    assert result.choices == ("Да", "Нет")
    assert result.allow_custom is True  # free-text path always open


async def test_ask_open_question_gets_floor_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2.1: a model ASK that omits its own choices (e.g. an open question) must
    still land with tappable chips — never bare text. The stage-keyed floor fills
    them in, model-independent, so the discovery card always offers options."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "Как ты это представляешь?"})
        ),
    )
    result = await run_discovery([], "идея", asked_count=0)
    assert result.action == ASK
    assert result.choices  # floor applied — never empty
    assert "Лендинг" in result.choices  # stage-0 product archetypes
    assert result.allow_custom is True  # "Другое" keeps free text open


async def test_ask_floor_does_not_override_model_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor is a backstop only — when the model DID supply choices, those are
    kept verbatim and the deterministic set never clobbers them."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Нужна тёмная тема?",
                    "choices": ["Да", "Нет"],
                }
            )
        ),
    )
    result = await run_discovery([], "дашборд", asked_count=0)
    assert result.choices == ("Да", "Нет")  # model choices, not the archetype floor


# ─── multi-select (NORTH STAR pillar 2 — мультивыбор) ────────────────────


def test_infer_multi_select_fires_on_section_questions() -> None:
    """Inherently multi-answer questions (which sections / features / pages) read
    as multi-select; single-answer ones (tone, yes/no) do not."""
    from omnia_api.services.discovery import _infer_multi_select

    assert _infer_multi_select("Какие разделы нужны на сайте?")
    assert _infer_multi_select("Какие возможности должны быть?")
    assert _infer_multi_select("Which sections do you need?")
    assert not _infer_multi_select("Нужна тёмная тема?")
    assert not _infer_multi_select("Какой тон ближе — премиум или дружелюбный?")
    assert not _infer_multi_select("")


async def test_ask_model_flag_sets_multi_select(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model that flags ``multiSelect:true`` carries through to the result."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Что выберешь?",  # text alone wouldn't trip the floor
                    "choices": ["A", "B", "C"],
                    "multiSelect": True,
                }
            )
        ),
    )
    result = await run_discovery([], "идея", asked_count=0)
    assert result.action == ASK
    assert result.multi_select is True


async def test_ask_floor_infers_multi_select_from_question_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the model omits the flag, a sections/features question is detected
    as multi-select from its text — model-independent (the fallback question for the
    sections stage gets it too)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Какие разделы обязательно нужны?",
                    "choices": ["Каталог", "Блог", "Контакты"],
                }
            )
        ),
    )
    result = await run_discovery([], "магазин", asked_count=2)
    assert result.multi_select is True


async def test_ask_single_answer_question_is_not_multi_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A yes/no or tone question stays single-select (no flag, no keyword)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {"action": "ask", "message": "Нужна админка?", "choices": ["Да", "Нет"]}
            )
        ),
    )
    result = await run_discovery([], "crm", asked_count=0)
    assert result.multi_select is False


async def test_choices_are_clamped_and_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Untrusted model output: ≤5 chips, length-capped, de-duped, junk dropped."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Стиль?",
                    "choices": [
                        "Премиум",
                        "премиум",  # dup (case-insensitive)
                        "  ",  # blank → dropped
                        42,  # non-string → dropped
                        "x" * 80,  # over-long → truncated to 40
                        "A",
                        "B",
                        "C",
                        "D",  # would be the 6th kept → dropped by cap
                    ],
                }
            )
        ),
    )
    result = await run_discovery([], "сайт", asked_count=0)
    assert len(result.choices) <= 5
    assert "Премиум" in result.choices
    assert all(len(c) <= 40 for c in result.choices)
    # case-insensitive dedup keeps a single "премиум"
    assert sum(1 for c in result.choices if c.lower() == "премиум") == 1


async def test_fallback_question_carries_paired_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a gateway failure the deterministic question for the 'tone' turn
    (index 1) still ships its tappable chips, not just bare text."""
    _install(monkeypatch, exc=RuntimeError("gateway down"))
    result = await run_discovery([], "идея", asked_count=1)
    assert result.action == ASK
    assert "Премиум" in result.choices


async def test_fallback_first_question_carries_archetype_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2.1: the very first fallback question (gateway down on the first prompt)
    must also land with chips — product archetypes — so the discovery card is
    never bare text even when the gateway is cold."""
    _install(monkeypatch, exc=RuntimeError("gateway down"))
    result = await run_discovery([], "идея", asked_count=0)
    assert result.action == ASK
    assert result.choices  # never empty on the first turn
    assert "Интернет-магазин" in result.choices


# ─── BUILD path ──────────────────────────────────────────────────────────


async def test_build_path_honours_model_brief_and_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "build",
                    "message": "Отлично, собираю…",
                    "brief": "Магазин косметики с корзиной и личным кабинетом.",
                    "stack": "nextjs_entities",
                }
            )
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "магазин помады"}],
        "с корзиной и входом",
        asked_count=2,
    )
    assert result.action == BUILD
    assert "корзин" in result.brief.lower()
    assert result.stack == "nextjs_entities"


async def test_build_path_honours_spa_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 7.2 — a no-backend interactive tool recommended as ``spa`` is kept.

    The backend safety-net only upgrades to ``nextjs_entities`` on account/data
    signals; a pure client-side calculator carries none, so ``spa`` survives.
    """
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "build",
                    "message": "Отлично, собираю…",
                    "brief": "Интерактивный калькулятор ипотеки на демо-данных.",
                    "stack": "spa",
                }
            )
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "калькулятор ипотеки"}],
        "со слайдерами, чисто на фронтенде",
        asked_count=2,
    )
    assert result.action == BUILD
    assert result.stack == "spa"


async def test_no_account_tool_vetoes_entities_to_spa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dogfood run #2 repro — the negative stack safety-net.

    The model OVER-escalated an explicit no-account calculator to
    ``nextjs_entities`` (which would gate the tool behind /signin via the (app)
    route group). The user said «без регистрации / без входа / без аккаунтов» and
    no positive backend signal survives the negation check, so the build is vetoed
    down to ``spa`` — a no-backend interactive React tool that can't gate."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "build",
                    "message": "Отлично, собираю…",
                    "brief": "Ипотечный калькулятор с графиком амортизации.",
                    "stack": "nextjs_entities",
                }
            )
        ),
    )
    result = await run_discovery(
        [
            {"role": "user", "content": "калькулятор ипотеки с интерактивным графиком"},
            {"role": "user", "content": "просто онлайн-калькулятор, без регистрации и без входа"},
            {"role": "user", "content": "нужен только калькулятор. Без аккаунтов."},
        ],
        "просто сделай уже",
        asked_count=3,
    )
    assert result.action == BUILD
    assert result.stack == "spa"


async def test_no_account_veto_spares_real_backend_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The veto must NOT downgrade a genuine app that just skips user accounts.

    «магазин без регистрации» still carries commerce signals (корзина/каталог) →
    ``_infer_stack_from_text`` stays truthy → the negative net is gated off and
    the model's ``nextjs_entities`` pick survives."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "build",
                    "message": "ок",
                    "brief": "Интернет-магазин с корзиной и каталогом товаров.",
                    "stack": "nextjs_entities",
                }
            )
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "магазин с корзиной и каталогом товаров, без регистрации"}],
        "просто сделай",
        asked_count=2,
    )
    assert result.action == BUILD
    assert result.stack == "nextjs_entities"


def test_explicit_no_backend_predicate() -> None:
    """Whole-phrase refusal detector — fires only on an explicit "no accounts"."""
    assert _explicit_no_backend("просто калькулятор, без регистрации")
    assert _explicit_no_backend("конвертер валют без аккаунтов")
    assert _explicit_no_backend("a puzzle game, no login no accounts")
    # A plain tool description with no refusal phrase does not fire.
    assert not _explicit_no_backend("калькулятор ипотеки с графиком")
    # A positive backend ask does not fire (no negation phrase).
    assert not _explicit_no_backend("магазин с регистрацией и корзиной")


async def test_invalid_stack_defaults_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {"action": "build", "message": "ок", "brief": "лендинг", "stack": "django"}
            )
        ),
    )
    result = await run_discovery([], "лендинг", asked_count=1)
    assert result.action == BUILD
    assert result.stack == "static"


async def test_force_build_overrides_model_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """User said "генерируй" — even if the model wants to keep asking, we build,
    compiling the brief from the conversation so the build has full context."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "ещё вопрос?"})
        ),
    )
    history = [
        {"role": "user", "content": "сайт пекарни"},
        {"role": "assistant", "content": "Какой тон?"},
        {"role": "user", "content": "тёплый, уютный"},
    ]
    result = await run_discovery(
        history, "генерируй уже", asked_count=1, force_build=True
    )
    assert result.action == BUILD
    # Fallback brief carries the whole conversation forward.
    assert "пекарни" in result.brief and "уютный" in result.brief


async def test_turn_cap_forces_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the hard cap we build no matter what the model returns — discovery can
    never loop forever (R-10)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "бесконечный вопрос"})
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "что-то"}],
        "ещё ответ",
        asked_count=MAX_DISCOVERY_QUESTIONS,
    )
    assert result.action == BUILD
    assert result.brief  # never empty on a build


async def test_build_with_unparseable_reply_still_builds_from_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, resp=_gateway_returning("не json вообще, просто текст"))
    result = await run_discovery(
        [{"role": "user", "content": "барбершоп"}],
        "делай",
        asked_count=0,
        force_build=True,
    )
    assert result.action == BUILD
    assert "барбершоп" in result.brief


# ─── Zero-question intent compile (V2.12) ────────────────────────────────────


async def test_zero_question_rich_first_prompt_builds_without_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """North Star pillar 2: a first prompt that already pins ≥2 design axes builds
    immediately — the popup never appears. The gateway is stubbed to ASK, so a
    BUILD result proves we short-circuited BEFORE the round-trip (deterministic,
    LLM-free)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "не должно дойти сюда"})
        ),
    )
    result = await run_discovery(
        [],
        "тёмный минималистичный лендинг с каталогом и отзывами на фиолетовом",
        asked_count=0,
    )
    assert result.action == BUILD
    assert result.brief  # compiled from the raw prompt, carries it forward
    assert "каталог" in result.brief


async def test_zero_question_vague_first_prompt_still_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adversarial symmetry: an unsteerable prompt stays in the interview — the
    compiler must not over-trigger and skip a genuinely needed question."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "Что за сайт?"})
        ),
    )
    result = await run_discovery([], "сделай сайт", asked_count=0)
    assert result.action == ASK
    assert result.message == "Что за сайт?"


async def test_zero_question_only_fires_on_first_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rich prompt mid-interview (asked_count>0) does NOT skip — the user is
    already in a conversation, the model owns the ask/build call there."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "уточняющий вопрос"})
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "идея"}, {"role": "assistant", "content": "?"}],
        "тёмный минималистичный лендинг с каталогом и отзывами на фиолетовом",
        asked_count=1,
    )
    assert result.action == ASK


async def test_zero_question_routes_backend_intent_to_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deterministic skip re-derives the stack from intent like the forced
    path does — a rich prompt that also needs accounts/data lands on a container
    stack, not a dead static landing."""
    _install(
        monkeypatch,
        resp=_gateway_returning(json.dumps({"action": "ask", "message": "x"})),
    )
    result = await run_discovery(
        [],
        "тёмный минималистичный магазин с каталогом товаров, корзиной и отзывами",
        asked_count=0,
    )
    assert result.action == BUILD
    assert result.stack == "nextjs_entities"


# ─── Stack safety-net (P0★ — owner directive 2026-06-10) ─────────────────────


def test_infer_stack_detects_backend_intent() -> None:
    """Accounts / saved-data / commerce intent → a container stack."""
    assert _infer_stack_from_text("нужна регистрация и личный кабинет") == "nextjs_entities"
    assert _infer_stack_from_text("habit tracker with login and dashboard") == "nextjs_entities"
    assert _infer_stack_from_text("магазин с корзиной и каталогом товаров") == "nextjs_entities"
    assert _infer_stack_from_text("CRM с ролями пользователей") == "nextjs_entities"


def test_infer_stack_leaves_pure_landing_alone() -> None:
    """A genuine marketing landing must NOT be upgraded to a backend app."""
    assert _infer_stack_from_text("лендинг кофейни в питере с меню и фото") is None
    assert _infer_stack_from_text("портфолио фотографа, галерея работ") is None
    assert _infer_stack_from_text("") is None


def test_infer_stack_ignores_negated_backend_words() -> None:
    """Phase 7.2 — a NEGATED backend mention must not trip the safety-net.

    A no-backend interactive tool is naturally described as «без регистрации»,
    «без аккаунтов», «no login». Naive substring matching saw "регистрац" inside
    "без регистрации" and wrongly upgraded the model's correct ``spa`` pick to
    ``nextjs_entities`` — killing the whole spa stack for the exact phrasing users
    use. The negated mention must be ignored (→ ``None`` → spa survives)."""
    assert _infer_stack_from_text("калькулятор ипотеки, без регистрации") is None
    assert _infer_stack_from_text("конвертер валют без аккаунтов") is None
    assert _infer_stack_from_text("интерактивный визуализатор, не нужна авторизация") is None
    assert _infer_stack_from_text("игра-головоломка, no login no accounts") is None
    # …but a genuine, non-negated backend need STILL fires (no over-suppression).
    saved = _infer_stack_from_text("калькулятор с сохранением в личном кабинете")
    assert saved == "nextjs_entities"
    assert _infer_stack_from_text("магазин с регистрацией и корзиной") == "nextjs_entities"


async def test_forced_build_with_backend_intent_routes_to_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE P0★ bug: parse-fail + forced build for an auth/cabinet product used to
    default to static (dead login buttons). The safety-net now routes it to a
    real container stack from the gathered intent."""
    _install(monkeypatch, resp=_gateway_returning("не json — парс упадёт"))
    result = await run_discovery(
        [{"role": "user", "content": "SaaS-трекер привычек"}],
        "регистрация, вход и личный кабинет на /dashboard, генерируй сразу",
        asked_count=0,
        force_build=True,
    )
    assert result.action == BUILD
    assert result.stack == "nextjs_entities"


async def test_forced_build_pure_landing_stays_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A landing with no backend intent still builds static — the net is precise."""
    _install(monkeypatch, resp=_gateway_returning("не json"))
    result = await run_discovery(
        [{"role": "user", "content": "лендинг для кофейни"}],
        "просто красивая визитка с меню, генерируй",
        asked_count=0,
        force_build=True,
    )
    assert result.action == BUILD
    assert result.stack == "static"


# ─── Batch discovery plan (owner rule 13 #1 — NORTH STAR pillar 2) ───────────


def _plan_envelope(questions: list[dict[str, Any]]) -> str:
    """Wrap a list of question dicts in the model's `{"questions":[…]}` reply."""
    return json.dumps({"questions": questions})


async def test_plan_returns_tailored_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """One upfront pass yields the WHOLE set of product-tailored questions, each
    cleaned (chips kept, multi-select honoured)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            _plan_envelope(
                [
                    {
                        "message": "Какие ступени обучения в школе?",
                        "choices": ["Начальная", "Средняя", "Старшая"],
                        "multiSelect": True,
                    },
                    {
                        "message": "Какой тон сайта ближе?",
                        "choices": ["Строгий", "Дружелюбный"],
                        "multiSelect": False,
                    },
                ]
            )
        ),
    )
    plan = await plan_discovery_questions("сайт школы МБОУ СОШ 15")
    assert [q.message for q in plan] == [
        "Какие ступени обучения в школе?",
        "Какой тон сайта ближе?",
    ]
    assert plan[0].choices == ("Начальная", "Средняя", "Старшая")
    assert plan[0].multi_select is True  # model flag honoured
    assert plan[1].multi_select is False
    assert all(q.allow_custom for q in plan)  # «Другое» never trapped


async def test_plan_infers_multi_select_when_model_omits_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A "какие разделы" question gets multi-select even if the model forgot the
    flag — the same deterministic floor as the per-question path."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            _plan_envelope(
                [{"message": "Какие разделы нужны на сайте?", "choices": ["Новости"]}]
            )
        ),
    )
    plan = await plan_discovery_questions("сайт школы")
    assert plan[0].multi_select is True


async def test_plan_question_without_chips_gets_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A question the model returned without choices still lands with chips — the
    discovery card is never bare text (V2.1)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            _plan_envelope([{"message": "Расскажите про проект?", "choices": []}])
        ),
    )
    plan = await plan_discovery_questions("стартап")
    assert plan[0].choices  # non-empty floor


async def test_plan_caps_at_max_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model that floods 8 questions is capped — onboarding is not an
    inquisition."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            _plan_envelope(
                [{"message": f"Вопрос {i}?", "choices": ["Да", "Нет"]} for i in range(8)]
            )
        ),
    )
    plan = await plan_discovery_questions("любая идея")
    assert len(plan) == _PLAN_MAX_QUESTIONS


async def test_plan_falls_back_to_meaningful_batch_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway failure degrades to a MEANINGFUL batch (general→detail), not a
    single generic question (owner rule 13 #1 fail-soft)."""
    _install(monkeypatch, exc=RuntimeError("gateway down"))
    plan = await plan_discovery_questions("сайт школы")
    assert len(plan) >= 3  # a real batch, not one lone question
    assert all(q.message for q in plan)
    assert all(q.choices for q in plan)  # each carries chips
    # general→detail: the questions are distinct, not one repeated.
    assert len({q.message for q in plan}) == len(plan)


async def test_plan_falls_back_on_unparseable_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, resp=_gateway_returning("не json вообще"))
    plan = await plan_discovery_questions("идея")
    assert len(plan) >= 3


async def test_plan_falls_back_on_wrong_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid JSON object without a `questions` list degrades to the batch."""
    _install(
        monkeypatch,
        resp=_gateway_returning(json.dumps({"foo": "bar"})),
    )
    plan = await plan_discovery_questions("идея")
    assert len(plan) >= 3


# ─── serve_planned_question (pure, no gateway) ───────────────────────────────


def _sample_plan() -> list[dict[str, Any]]:
    return [
        PlannedQuestion(
            message="Что за продукт?", choices=("A", "B"), multi_select=False
        ).to_dict(),
        PlannedQuestion(
            message="Какие разделы нужны?", choices=("Новости", "Контакты")
        ).to_dict(),
    ]


def test_serve_advances_through_plan_without_gateway() -> None:
    """Each turn serves the next pre-computed question — no gateway call needed."""
    plan = _sample_plan()
    first = serve_planned_question(plan, 0)
    second = serve_planned_question(plan, 1)
    assert first is not None and first.action == ASK
    assert first.message == "Что за продукт?"
    assert first.choices == ("A", "B")
    assert second is not None and second.message == "Какие разделы нужны?"
    assert first.message != second.message  # advances


def test_serve_returns_none_when_plan_exhausted() -> None:
    """Past the last question → None, so the caller builds from the answers."""
    plan = _sample_plan()
    assert serve_planned_question(plan, 2) is None
    assert serve_planned_question(plan, 99) is None


def test_serve_reapplies_multi_select_floor() -> None:
    """A persisted "разделы" question is served multi-select even if it was
    stored before the inference rule existed."""
    plan = [{"message": "Какие разделы нужны?", "choices": ["Новости"]}]
    served = serve_planned_question(plan, 0)
    assert served is not None and served.multi_select is True


def test_serve_handles_malformed_plan() -> None:
    """Junk in the plan slot degrades to None rather than raising (R-10)."""
    assert serve_planned_question(None, 0) is None
    assert serve_planned_question("not a list", 0) is None
    assert serve_planned_question([{"no_message": True}], 0) is None


def test_serve_carries_question_index_and_total() -> None:
    """Each served question knows its 1-based position and the batch size so the
    workspace can frame «Вопрос N из M» (NORTH STAR pillar 2 onboarding popup)."""
    plan = _sample_plan()  # two questions
    first = serve_planned_question(plan, 0)
    second = serve_planned_question(plan, 1)
    assert first is not None and (first.question_index, first.question_total) == (1, 2)
    assert second is not None and (second.question_index, second.question_total) == (
        2,
        2,
    )


# ─── infer_niche_label (pure, model-free framing banner) ─────────────────────


def test_infer_niche_label_recognises_common_niches() -> None:
    """The framing banner names the niche from the product idea — deterministic
    substring lookup, no gateway call (pillar 2 onboarding frame)."""
    assert infer_niche_label("сайт школы МБОУ СОШ 15") == "школа / образование"
    assert infer_niche_label("интернет-магазин товаров для дома") == "интернет-магазин"
    assert infer_niche_label("сайт стоматологической клиники") == "клиника / медицина"
    assert infer_niche_label("CRM для отдела продаж") == "CRM / управление"


def test_infer_niche_label_empty_for_unknown_idea() -> None:
    """An unrecognised idea yields "" so the banner shows no dangling suffix
    (cosmetic only — never a dead-end)."""
    assert infer_niche_label("что-то совершенно непонятное и абстрактное") == ""
    assert infer_niche_label("") == ""


# ─── zero_question_build (shared floor) ──────────────────────────────────────


def test_zero_question_build_skips_popup_for_rich_prompt() -> None:
    """A prompt that pins ≥2 design axes builds immediately (shared with the batch
    path, so the popup never appears in either)."""
    result = zero_question_build(
        [], "тёмный минималистичный лендинг с каталогом и отзывами на фиолетовом"
    )
    assert result is not None and result.action == BUILD
    assert "каталог" in result.brief


def test_zero_question_build_returns_none_for_thin_prompt() -> None:
    """A thin prompt needs a question — the floor must not over-trigger."""
    assert zero_question_build([], "сделай сайт") is None


# ─── Onboarding LIVE-causality (pillar 2): recap + live niche + confidence-skip ─


def test_cumulative_idea_joins_all_user_text() -> None:
    """The cumulative idea is the product idea PLUS every answer (+ latest), so a
    later answer can sharpen the niche the first prompt left vague."""
    history = [
        {"role": "user", "content": "сайт для бизнеса"},
        {"role": "assistant", "content": "Чем занимаетесь?"},
        {"role": "user", "content": "доставка еды"},
    ]
    text = cumulative_idea(history, "ещё нужно меню")
    assert "сайт для бизнеса" in text
    assert "доставка еды" in text
    assert "меню" in text


def test_live_niche_sharpens_on_later_answer() -> None:
    """A vague first prompt yields no niche; once «доставка еды» arrives the
    cumulative re-inference surfaces «кафе / ресторан» — the badge shifts live."""
    assert infer_niche_label("сайт для бизнеса") == ""
    history = [
        {"role": "user", "content": "сайт для бизнеса"},
        {"role": "assistant", "content": "Чем занимаетесь?"},
    ]
    sharpened = infer_niche_label(cumulative_idea(history, "доставка еды"))
    assert sharpened == "кафе / ресторан"


def test_gather_answers_excludes_idea_and_includes_latest() -> None:
    """Answers gathered so far = user turns after the idea, plus the latest prompt
    (the answer to the previous question, not yet in history)."""
    history = [
        {"role": "user", "content": "магазин косметики"},
        {"role": "assistant", "content": "Какой тон?"},
        {"role": "user", "content": "Премиум"},
        {"role": "assistant", "content": "Какие разделы?"},
    ]
    answers = gather_answers(history, "Каталог, Корзина", asked_count=2)
    assert answers == ("Премиум", "Каталог, Корзина")


def test_gather_answers_empty_on_first_turn() -> None:
    """On the very first turn (idea only) nothing has been answered yet."""
    assert gather_answers([], "магазин косметики", asked_count=0) == ()


def test_recap_labels_clip_and_cap() -> None:
    """Recap chips collapse whitespace, clip overly long answers, and keep only
    the newest ≤3 so the narrow chat panel never floods."""
    labels = recap_labels(("a", "b", "c", "d"))
    assert labels == ("b", "c", "d")  # newest 3
    long = recap_labels(("очень длинный ответ который точно не влезает в чип",))
    assert long[0].endswith("…") and len(long[0]) <= 28


def test_confidence_skip_fires_on_decisive_answers() -> None:
    """≥2 answers pinning a recognised niche + ≥2 design axes → build early."""
    history = [
        {"role": "user", "content": "магазин косметики"},
        {"role": "assistant", "content": "Тон?"},
        {"role": "user", "content": "тёмный премиум"},
        {"role": "assistant", "content": "Разделы?"},
    ]
    assert confident_enough_to_build(
        history, "каталог и отзывы", asked_count=2, niche="интернет-магазин"
    )


def test_confidence_skip_holds_when_niche_unknown() -> None:
    """No recognised niche → keep asking, even with pinned axes (fail-soft)."""
    history = [
        {"role": "user", "content": "что-то непонятное"},
        {"role": "assistant", "content": "Тон?"},
        {"role": "user", "content": "тёмный премиум"},
        {"role": "assistant", "content": "Разделы?"},
    ]
    assert not confident_enough_to_build(
        history, "каталог и отзывы", asked_count=2, niche=""
    )


def test_confidence_skip_holds_early_in_interview() -> None:
    """Never cut an interview the user has barely begun (asked_count < 2)."""
    history = [{"role": "user", "content": "магазин косметики"}]
    assert not confident_enough_to_build(
        history, "тёмный премиум каталог", asked_count=1, niche="интернет-магазин"
    )
