"""A cache-write quote may use only explicit, homogeneous transmitted controls."""

from decimal import Decimal

import pytest


@pytest.mark.parametrize("control,expected", [
    ({"type": "ephemeral"}, Decimal("1.25")),
    ({"type": "ephemeral", "ttl": "5m"}, Decimal("1.25")),
    ({"type": "ephemeral", "ttl": "1h"}, Decimal("2")),
    ({"type": "ephemeral", "ttl": None}, None),
    ({"type": "ephemeral", "ttl": "2h"}, None),
    ({"type": "persistent"}, None),
    ({"ttl": "1h"}, None),
    ({"type": "ephemeral", "unsupported": True}, None),
    (True, None),
])
@pytest.mark.parametrize("location", ["top", "message", "content", "tool"])
def test_supported_cache_control_locations_and_ttls(control, expected, location):
    from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor

    node = {"cache_control": control}
    request = {
        "top": node,
        "message": {"messages": [node]},
        "content": {"messages": [{"content": [node]}]},
        "tool": {"tools": [node]},
    }[location]
    assert anthropic_cache_write_factor("claude-sonnet-5", request) == expected


@pytest.mark.parametrize("model", ["gemini-3.1-pro-preview-customtools", "unknown", "anthropic/claude-sonnet-5"])
def test_other_models_have_no_anthropic_factor(model):
    from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor

    assert anthropic_cache_write_factor(model, {"cache_control": {"type": "ephemeral"}}) is None


def test_absent_and_mixed_controls_cannot_infer_a_write_factor():
    from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor

    assert anthropic_cache_write_factor("claude-sonnet-5", {}) is None
    assert anthropic_cache_write_factor("claude-sonnet-5", {
        "cache_control": {"type": "ephemeral"},
        "messages": [{"content": [{"cache_control": {"type": "ephemeral", "ttl": "1h"}}]}],
    }) is None
    assert anthropic_cache_write_factor("claude-sonnet-5", {
        "cache_control": {"type": "ephemeral"},
        "messages": [{"content": [{"cache_control": {"type": "ephemeral", "ttl": "5m"}}]}],
    }) == Decimal("1.25")


def test_json_schema_and_message_text_are_not_cache_controls():
    from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor

    assert anthropic_cache_write_factor("claude-sonnet-5", {
        "messages": [{"content": '{"cache_control":{"type":"ephemeral"}}'}],
        "tools": [{"function": {"parameters": {"properties": {
            "cache_control": {"type": "ephemeral"},
        }}}}],
    }) is None


def test_cyclic_or_excessive_content_is_not_partially_priced():
    from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor

    cyclic = {"cache_control": {"type": "ephemeral"}}
    cyclic["content"] = [cyclic]
    assert anthropic_cache_write_factor("claude-sonnet-5", cyclic) is None
    assert anthropic_cache_write_factor("claude-sonnet-5", {
        "cache_control": {"type": "ephemeral"}, "messages": [{}] * 5000,
    }) is None
