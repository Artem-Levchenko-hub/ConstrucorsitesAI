"""V3.10a — BRIEF→CLIENT PLUMBING.

Deterministic, money-free gate for the brief→client transport. Two halves:

1. ``parse_brief`` reliably extracts the structured signal the brief format
   guarantees (palette HEX, fonts, motion, sections) for BOTH the landing brief
   and the app/entity brief — so an ``omnia:brief`` event can carry it.
2. ``art_director_writer_generate`` emits exactly one ``{"brief": …}`` event
   with non-empty palette/font/section BEFORE the writer streams (i.e. before
   ``llm.done``) on a normal build, and NO brief event on the fail-soft path —
   which is precisely the adversarial "without brief" baseline the gate refutes.

Self-contained: the gateway stream is faked and the async generator is drained
via ``asyncio.run`` (no pytest-asyncio config required), mirroring
``test_art_director_writer.py``.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import omnia_api.services.art_director_writer as adw
from omnia_api.services.art_director_writer import (
    art_director_writer_generate,
    parse_brief,
)

# A representative LANDING brief in the strict Pass-1 format (truncated but
# format-faithful — the lines the parser keys on are verbatim).
_LANDING_BRIEF = """\
# 1. РАЗБОР (3 строки)
ЧУВСТВО: тепло и неспешный ритуал
ИДЕЯ: кофе как маленький ежедневный праздник
РЕФЕРЕНС: как Aesop встретил локальную обжарку

# 2. ГЛОБАЛ
ФОН #1a1410 · ТЕКСТ #f5efe6 · PRIMARY #c8753a · АКЦЕНТ #e0a060 (дозой: только CTA+цифры)
ШРИФТЫ: дисплей "Fraunces" · текст "Manrope"
MOTION-СИГНАТУРА: ОДНА — .line-rise в hero (reduced-motion-safe)
РИТМ: отступы секций py-32, контейнер max-w-6xl, радиус rounded-2xl

# 3. СЕКЦИИ
[1] hero — главный экран | id="hero"
ФОН/ОТСТУП/КОНТЕЙНЕР: #1a1410 · py-32 · max-w-6xl
[2] меню кофейни | id="menu"
[3] о нас | id="about"
[4] контакты и адрес | id="contacts"
"""

# A representative APP/ENTITY brief (template == nextjs_entities) — different
# palette labels (FOREGROUND/BACKGROUND), single ШРИФТ:, no motion line, and
# screens expressed as sidebar nav items rather than [N] sections.
_APP_BRIEF = """\
# 1. ПРОДУКТ (3 строки)
СУТЬ: CRM для стоматологической клиники
ПОЛЬЗОВАТЕЛЬ И ЗАДАЧИ: администратор ведёт пациентов и записи
ТОН: деловой, спокойный

# 2. ТЕМА
БРЕНД-НАЗВАНИЕ: "ДентоCRM"
PRIMARY #2563eb (ОДИН бренд-акцент: кнопки / активный пункт нав) ·
  FOREGROUND #0a0a0a · BACKGROUND #fafafa
ШРИФТ: Manrope
РАДИУС: 0.65rem

# 3. АРХИТЕКТУРА (навигация = сайдбар)
СУЩНОСТИ: Client, Deal, Task
НАВИГАЦИЯ (пункты сайдбара по порядку):
  - "Дашборд" → "/" (LayoutDashboard)
  - "Клиенты" → "/clients" (Users)
  - "Сделки" → "/deals" (Briefcase)
"""


# ─── parse_brief — landing ───────────────────────────────────────────────────


def test_parse_landing_palette_fonts_sections_motion() -> None:
    b = parse_brief(_LANDING_BRIEF)
    assert b is not None
    # Labelled palette → canonical client keys.
    assert b["palette"] == {
        "bg": "#1a1410",
        "text": "#f5efe6",
        "primary": "#c8753a",
        "accent": "#e0a060",
    }
    assert b["fonts"] == {"display": "Fraunces", "text": "Manrope"}
    assert "line-rise" in b["motion"]
    ids = [s["id"] for s in b["sections"]]
    assert ids == ["hero", "menu", "about", "contacts"]
    assert b["sections"][0]["name"].startswith("hero")


# ─── parse_brief — app / entity ──────────────────────────────────────────────


def test_parse_app_palette_font_nav_as_sections() -> None:
    b = parse_brief(_APP_BRIEF)
    assert b is not None
    assert b["palette"] == {
        "primary": "#2563eb",
        "foreground": "#0a0a0a",
        "background": "#fafafa",
    }
    # Single ШРИФТ: mirrored into both keys so a non-empty font is present.
    assert b["fonts"] == {"display": "Manrope", "text": "Manrope"}
    # No MOTION-СИГНАТУРА in an app brief — motion is optional, not required.
    assert b["motion"] == ""
    # Sidebar nav stands in as the screen list.
    assert [s["id"] for s in b["sections"]] == ["/", "/clients", "/deals"]
    assert b["sections"][1]["name"] == "Клиенты"


def test_parse_brief_fields_are_non_empty_for_both_formats() -> None:
    # The V3.10a client-side assertion: a real brief always carries non-empty
    # HEX + font + section. Guard both formats so a parser regression on either
    # surfaces as a red gate.
    for brief in (_LANDING_BRIEF, _APP_BRIEF):
        b = parse_brief(brief)
        assert b is not None
        assert b["palette"], "palette must carry ≥1 HEX"
        assert b["fonts"].get("display"), "fonts must carry ≥1 family"
        assert b["sections"], "sections must carry ≥1 entry"


# ─── parse_brief — empty / fallback ──────────────────────────────────────────


def test_parse_empty_brief_returns_none() -> None:
    # Fail-soft path: an empty brief yields None → caller emits NO event. This
    # IS the adversarial "without brief" baseline.
    assert parse_brief("") is None
    assert parse_brief("   \n\t ") is None
    assert parse_brief(None) is None


def test_parse_unlabelled_palette_falls_back_to_raw_hexes() -> None:
    # A malformed brief with no palette labels still surfaces its colours.
    b = parse_brief("какой-то текст #112233 и #445566 без меток\n[1] x | id=\"x\"")
    assert b is not None
    assert b["palette"] == {"color1": "#112233", "color2": "#445566"}


# ─── generator emit — positive + adversarial ─────────────────────────────────


def _fake_stream(*, brief_text: str, fail_brief: bool = False):
    """Pass 1 (art-director) → ``brief_text``; pass 2 (writer) → HTML. The passes
    are told apart by the "проход 1 из 2" marker in the art-director directive."""

    async def fake(msgs, model, *a, **k):
        last = msgs[-1]["content"] if msgs else ""
        if "проход 1 из 2" in last:  # art-director pass
            if fail_brief:
                yield {"error": "boom"}
                return
            yield {"delta": brief_text}
            yield {"usage": {"tokens_in": 10, "tokens_out": 5, "cost_rub": 0.1}}
        else:  # writer pass
            yield {"delta": "<html>PAGE</html>"}
            yield {"usage": {"tokens_in": 50, "tokens_out": 200, "cost_rub": 0.2}}

    return fake


async def _drain(gen):
    return [ev async for ev in gen]


def _run_generate():
    return asyncio.run(
        _drain(
            art_director_writer_generate(
                base_messages=[
                    {"role": "system", "content": "SYS (palette anchor + kit)"},
                    {"role": "user", "content": "сделай лендинг кофейни"},
                ],
                user_prompt="сделай лендинг кофейни",
                user_id=uuid4(),
                project_id=uuid4(),
                message_id=uuid4(),
            )
        )
    )


def test_generator_emits_one_brief_event_before_html() -> None:
    # V3.10a positive gate: exactly one brief event, non-empty palette/font/
    # section, emitted BEFORE the writer's HTML deltas (hence before llm.done).
    adw.stream_chat_completion = _fake_stream(brief_text=_LANDING_BRIEF)
    events = _run_generate()

    brief_events = [e for e in events if "brief" in e]
    assert len(brief_events) == 1
    payload = brief_events[0]["brief"]
    assert payload["palette"]
    assert payload["fonts"].get("display")
    assert payload["sections"]

    # Ordering: the brief reaches the client before any HTML delta.
    brief_idx = next(i for i, e in enumerate(events) if "brief" in e)
    first_html_idx = next(i for i, e in enumerate(events) if "delta" in e)
    assert brief_idx < first_html_idx


def test_no_brief_event_on_failsoft_brief() -> None:
    # Adversarial baseline: the art-director failed → no brief assembled → NO
    # omnia:brief event. The page still builds (fail-soft), but the gate that
    # asserts "≥1 brief event" correctly fails here — proving the event is the
    # signal, not always-on noise.
    adw.stream_chat_completion = _fake_stream(brief_text="", fail_brief=True)
    events = _run_generate()
    assert not any("brief" in e for e in events)
    # Build still produced a page.
    assert [e["delta"] for e in events if "delta" in e] == ["<html>PAGE</html>"]
