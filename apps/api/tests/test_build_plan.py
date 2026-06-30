"""Unit tests for services.build_plan — the pre-build feature spec + fail-soft.

Pure-function coverage (no DB / no gateway) plus monkeypatched plan_build paths
proving the strict fail-soft contract: flag-off, mock, gateway error and parse
failure all degrade to an EMPTY plan (= today's behaviour).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from omnia_api.services import build_plan as bp
from omnia_api.services.build_plan import (
    BuildPlan,
    Capability,
    merge_plan_into_spec,
    parse_plan,
    plan_build,
    read_plan,
)

_VALID: dict = {
    "summary": "CRM для агентства",
    "screens": [
        {"route": "/dashboard", "name": "Обзор", "purpose": "метрики", "primary_entity": None},
        {"route": "clients", "name": "Клиенты", "purpose": "список"},
    ],
    "entities": [{"name": "Client", "fields": ["name", "phone"], "owner_scoped": True}],
    "capabilities": [
        {
            "id": "create_client",
            "actor_role": "user",
            "action": "создать клиента",
            "method": "POST",
            "path": "/api/clients",
            "expect": "2xx",
            "must_have": True,
        },
        {"id": "ui_filter", "action": "фильтр", "path": "", "must_have": False},
    ],
    "acceptance": ["клиент создаётся и виден"],
}


def test_parse_valid_json():
    p = parse_plan(json.dumps(_VALID, ensure_ascii=False))
    assert not p.is_empty
    assert len(p.screens) == 2
    assert p.screens[1].route == "/clients"  # leading slash normalised
    assert p.entities[0].name == "Client"
    assert len(p.capabilities) == 2
    blk = p.blocking_capabilities()
    assert len(blk) == 1 and blk[0].id == "create_client"


def test_parse_fenced_json():
    raw = "```json\n" + json.dumps(_VALID) + "\n```"
    assert not parse_plan(raw).is_empty


def test_parse_prose_wrapped():
    raw = "Вот план:\n" + json.dumps(_VALID) + "\nГотово."
    assert not parse_plan(raw).is_empty


def test_parse_garbage_is_empty():
    assert parse_plan("no json here").is_empty
    assert parse_plan("").is_empty
    assert parse_plan(None).is_empty
    assert parse_plan("{not valid json").is_empty


def test_truncation_caps():
    big = {
        "capabilities": [
            {"id": f"c{i}", "path": f"/api/{i}", "action": "x"} for i in range(40)
        ],
        "screens": [{"route": f"/s{i}"} for i in range(40)],
        "entities": [{"name": f"E{i}"} for i in range(40)],
    }
    p = parse_plan(json.dumps(big))
    assert len(p.capabilities) == 10
    assert len(p.screens) == 8
    assert len(p.entities) == 8


def test_capability_probeable_and_blocking():
    c = Capability.from_dict({"id": "x", "path": "notabs", "must_have": True})
    assert c is not None and c.path == "" and not c.probeable and not c.blocks_completion
    c2 = Capability.from_dict({"id": "y", "path": "/api/y", "must_have": True})
    assert c2 is not None and c2.probeable and c2.blocks_completion
    c3 = Capability.from_dict({"id": "z", "path": "/api/z", "must_have": False})
    assert c3 is not None and c3.probeable and not c3.blocks_completion
    # no id and no action → dropped
    assert Capability.from_dict({"method": "POST"}) is None


def test_roundtrip_to_from_dict():
    p = parse_plan(json.dumps(_VALID))
    p2 = BuildPlan.from_dict(p.to_dict())
    assert p2.to_dict() == p.to_dict()


def test_read_and_merge_preserve_existing_spec():
    p = parse_plan(json.dumps(_VALID))
    spec = {"dark_mode": True, "tone": "playful"}
    merged = merge_plan_into_spec(spec, p)
    assert merged["dark_mode"] is True  # FidelitySpec keys preserved
    assert merged["tone"] == "playful"
    assert "build_plan" in merged
    assert read_plan(merged).to_dict() == p.to_dict()
    # empty plan leaves the spec untouched
    assert merge_plan_into_spec(spec, BuildPlan()) == spec
    assert read_plan(None).is_empty
    assert read_plan({"foo": 1}).is_empty


def test_checklist_block():
    p = parse_plan(json.dumps(_VALID))
    blk = p.checklist_block()
    assert "ПЛАН СБОРКИ" in blk
    assert "/api/clients" in blk
    assert "/dashboard" in blk
    assert BuildPlan().checklist_block() == ""


def test_from_dict_defensive():
    assert BuildPlan.from_dict(None).is_empty
    assert BuildPlan.from_dict({"screens": "notalist", "capabilities": None}).is_empty
    p = BuildPlan.from_dict({"screens": [{"route": "/x"}, {"no": "route"}, "junk"]})
    assert len(p.screens) == 1


async def test_plan_build_flag_off(monkeypatch):
    monkeypatch.setattr(bp, "get_settings", lambda: SimpleNamespace(use_build_plan=False))
    out = await plan_build("сделай CRM", stack="fullstack", model="x")
    assert out.is_empty


async def test_plan_build_happy(monkeypatch):
    monkeypatch.setattr(bp, "get_settings", lambda: SimpleNamespace(use_build_plan=True))

    async def _fake(messages, model, **kw):
        assert isinstance(messages, list) and model == "x"
        return json.dumps(_VALID)

    monkeypatch.setattr(bp, "complete_chat", _fake)
    out = await plan_build("сделай CRM", stack="fullstack", model="x")
    assert not out.is_empty
    assert len(out.blocking_capabilities()) == 1


async def test_plan_build_gateway_error_failsoft(monkeypatch):
    monkeypatch.setattr(bp, "get_settings", lambda: SimpleNamespace(use_build_plan=True))

    async def _boom(*a, **k):
        raise bp.LLMError("gateway 500")

    monkeypatch.setattr(bp, "complete_chat", _boom)
    out = await plan_build("x", stack="spa", model="m")
    assert out.is_empty


async def test_plan_build_mock_empty(monkeypatch):
    monkeypatch.setattr(bp, "get_settings", lambda: SimpleNamespace(use_build_plan=True))

    async def _empty(*a, **k):
        return ""  # mock_llm path returns ""

    monkeypatch.setattr(bp, "complete_chat", _empty)
    out = await plan_build("x", stack="spa", model="m")
    assert out.is_empty
