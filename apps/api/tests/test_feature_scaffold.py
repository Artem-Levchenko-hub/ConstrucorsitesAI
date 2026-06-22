"""Feature-scaffold prompt block (give-it-functionality) — `_build_edit_messages`.

The container EDIT prompt gains an entity-JSON + CrudResource + nav-wiring SCAFFOLD
block ONLY when (a) the stack is a container app, (b) the request is an
add-functionality ask, and (c) `use_feature_scaffold` is on. Everything else is
byte-identical to today (DARK).
"""

from __future__ import annotations

from types import SimpleNamespace

from omnia_api.services import prompt_builder

# A unique fingerprint of the scaffold block (not present in the base prompts).
_MARKER = "ДОБАВЛЕНИЕ ФУНКЦИОНАЛА"
_SCHEMA = '"access":"owner"'


def _system(monkeypatch, *, template: str, prompt: str, flag: bool) -> str:
    monkeypatch.setattr(
        prompt_builder, "get_settings", lambda: SimpleNamespace(use_feature_scaffold=flag)
    )
    msgs = prompt_builder._build_edit_messages(
        {"src/app/(app)/layout.tsx": "export default function L(){return null}"},
        [],
        prompt,
        None,
        template=template,
    )
    return msgs[0]["content"]  # the system block


def test_scaffold_added_for_container_feature_add_when_on(monkeypatch) -> None:
    sys = _system(
        monkeypatch,
        template="nextjs_entities",
        prompt="добавь раздел бронирований с формой записи",
        flag=True,
    )
    assert _MARKER in sys
    assert _SCHEMA in sys  # the entity-JSON contract reached the model
    assert "CrudResource entity=" in sys
    assert "(app)/layout.tsx" in sys  # the nav-wiring instruction


def test_scaffold_absent_when_flag_off(monkeypatch) -> None:
    sys = _system(
        monkeypatch,
        template="nextjs_entities",
        prompt="добавь раздел бронирований с формой записи",
        flag=False,
    )
    assert _MARKER not in sys


def test_scaffold_absent_for_pure_visual_edit(monkeypatch) -> None:
    # no functionality noun → no scaffold even with the flag on (don't bloat a
    # recolour/text edit).
    sys = _system(
        monkeypatch,
        template="nextjs_entities",
        prompt="поменяй цвет кнопки на синий",
        flag=True,
    )
    assert _MARKER not in sys


def test_scaffold_absent_for_static_stack(monkeypatch) -> None:
    # the scaffold contract is container-only (entities/CrudResource/(app) routes
    # don't exist on a static HTML project).
    sys = _system(
        monkeypatch,
        template="blank",
        prompt="добавь раздел отзывов",
        flag=True,
    )
    assert _MARKER not in sys
