"""Baseline only: execute real generators, replacing the external LLM stream."""

import importlib
import json
from uuid import UUID

import pytest

NAMES = ["art_director_writer", "director_polish"]
DIRECT_CASES = [
    ((), {"tokens_in": 0, "tokens_out": 0, "cost_rub": 0, "passes": 0}),
    ((None, None), {"tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0, "passes": 0}),
    (({},), {"tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0, "passes": 1}),
    ((None, {}, None), {"tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0, "passes": 1}),
    (
        (
            {"tokens_in": "2", "tokens_out": "3", "cost_rub": "0.1"},
            {"tokens_in": 4, "tokens_out": 5, "cost_rub": 0.2},
        ),
        {"tokens_in": 6, "tokens_out": 8, "cost_rub": 0.30000000000000004, "passes": 2},
    ),
    (
        ({"tokens_in": 2.9, "tokens_out": -1.9},),
        {"tokens_in": 2, "tokens_out": -1, "cost_rub": 0.0, "passes": 1},
    ),
    (
        ({"cost_rub": 1e16}, {"cost_rub": 1.0}, {"cost_rub": -1e16}),
        {"tokens_in": 0, "tokens_out": 0, "cost_rub": 1.0, "passes": 3},
    ),
]


@pytest.fixture
def modules(monkeypatch):
    from omnia_api.services import llm_client, pipeline_debug

    monkeypatch.setattr(
        llm_client,
        "stream_chat_completion",
        lambda *_a, **_kw: pytest.fail("Real LLM stream is forbidden"),
    )
    monkeypatch.setattr(pipeline_debug, "enabled", lambda: False)
    monkeypatch.setattr(pipeline_debug, "dump", lambda *_a, **_kw: None)
    return {name: importlib.import_module("omnia_api.services." + name) for name in NAMES}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "usages, expected",
    DIRECT_CASES,
    ids=[
        "zero-args",
        "all-none",
        "empty-dict",
        "mixed-empty",
        "coercion-and-decimals",
        "fractional-token-coercion",
        "float-order",
    ],
)
def test_exact_helper_json(modules, name, usages, expected):
    actual = modules[name]._aggregate_usage(*usages)
    assert json.dumps(actual, separators=(",", ":")) == json.dumps(expected, separators=(",", ":"))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "usage, error",
    [
        ({"tokens_in": None}, TypeError),
        ({"tokens_out": "bad"}, ValueError),
        ({"cost_rub": None}, TypeError),
    ],
)
def test_numeric_errors_not_silently_normalized(modules, name, usage, error):
    with pytest.raises(error):
        modules[name]._aggregate_usage(usage)


def test_multipass_is_intentionally_different(modules):
    from omnia_api.services import multipass_generator

    assert multipass_generator._aggregate_usage(None, {})["passes"] == 2
    assert modules["director_polish"]._aggregate_usage(None, {})["passes"] == 1


async def generate(module, name, scripts, monkeypatch):
    calls = []

    async def stream(messages, model, user_id, project_id, message_id):
        index = len(calls)
        calls.append((model, user_id, project_id, message_id))
        assert messages and index < len(scripts)
        for event in scripts[index]:
            yield event

    monkeypatch.setattr(module, "stream_chat_completion", stream)
    identity = UUID("00000000-0000-0000-0000-000000000001")
    kwargs = {
        "base_messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
        ],
        "user_prompt": "request",
        "user_id": identity,
        "project_id": identity,
        "message_id": identity,
    }
    if name == "art_director_writer":
        generator = module.art_director_writer_generate
        kwargs.update(art_director_model="claude-opus-4-7", writer_model="claude-opus-4-7")
    else:
        generator = module.director_polish_generate
        kwargs.update(director_model="claude-opus-4-7", polish_model="claude-opus-4-7")
    events = [event async for event in generator(**kwargs)]
    assert all(call[1:] == (str(identity),) * 3 for call in calls)
    return events, calls


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "usages, expected",
    [
        ([None, None], {"tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0, "passes": 0}),
        ([{}, {}], {"tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0, "passes": 0}),
        (
            [{"tokens_in": 2, "cost_rub": 0.1}, None],
            {"tokens_in": 2, "tokens_out": 0, "cost_rub": 0.1, "passes": 1},
        ),
        (
            [{"tokens_in": 2, "cost_rub": 0.1}, {"tokens_out": 3, "cost_rub": 0.2}],
            {"tokens_in": 2, "tokens_out": 3, "cost_rub": 0.30000000000000004, "passes": 2},
        ),
    ],
    ids=["no-usage", "empty-usage-ignored", "one-usage", "float-json"],
)
async def test_real_generators_usage_contract(modules, monkeypatch, name, usages, expected):
    scripts = [
        [{"delta": "brief"}, {"usage": usages[0]}],
        [{"delta": "FINAL"}, {"usage": usages[1]}],
    ]
    events, calls = await generate(modules[name], name, scripts, monkeypatch)
    assert len(calls) == 2
    assert [event["delta"] for event in events if "delta" in event] == ["FINAL"]
    usage_events = [event for event in events if "usage" in event]
    assert len(usage_events) == 1 and events[-1] == usage_events[0]
    assert json.dumps(usage_events[0]["usage"]) == json.dumps(expected)
    assert [(event["stage"]) for event in events if "stage" in event] == [
        "start",
        "end",
        "start",
        "end",
    ]


@pytest.mark.parametrize("name", NAMES)
async def test_last_truthy_usage_wins_per_pass(modules, monkeypatch, name):
    scripts = [
        [
            {"delta": "brief"},
            {"usage": {"cost_rub": 9}},
            {"usage": {"cost_rub": 0.1}},
            {"usage": {}},
        ],
        [{"delta": "FINAL"}, {"usage": {"cost_rub": 0.2}}, {"usage": None}],
    ]
    events, _ = await generate(modules[name], name, scripts, monkeypatch)
    assert events[-1]["usage"] == {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_rub": 0.30000000000000004,
        "passes": 2,
    }


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("failed_pass", [0, 1])
async def test_real_generator_errors_keep_existing_terminal_contract(
    modules,
    monkeypatch,
    name,
    failed_pass,
):
    scripts = [[{"delta": "brief"}], [{"delta": "FINAL"}, {"usage": {"cost_rub": 0.2}}]]
    scripts[failed_pass] = [{"error": "synthetic failure"}]
    events, calls = await generate(modules[name], name, scripts, monkeypatch)
    if name == "art_director_writer" and failed_pass == 0:
        assert len(calls) == 2
        assert events[-1]["usage"]["passes"] == 1
        assert not any("error" in event for event in events)
    else:
        assert events[-1].get("error", "").endswith("synthetic failure")
        assert not any("usage" in event for event in events)
        assert len(calls) == failed_pass + 1
