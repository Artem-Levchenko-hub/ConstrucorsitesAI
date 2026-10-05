"""Anthropic write-rate hints from explicit controls in the transmitted request.

OpenRouter prompt-caching docs: ephemeral writes use 1.25x input for the
default/5m TTL and 2x for 1h. This does not infer token counts or a paid receipt.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

_MAX_NODES = 4096
_MAX_DEPTH = 8


def anthropic_cache_write_factor(model_id: str, request: Mapping[str, Any]) -> Decimal | None:
    """Return a factor only for explicit supported controls with one TTL."""
    if model_id != "claude-sonnet-5":
        return None
    pending: list[tuple[Mapping[str, Any], int]] = [(request, 0)]
    visited = 0
    factor: Decimal | None = None
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > _MAX_NODES or depth > _MAX_DEPTH:
            return None
        if "cache_control" in node:
            control = node["cache_control"]
            if (
                not isinstance(control, Mapping)
                or control.get("type") != "ephemeral"
                or set(control) - {"type", "ttl"}
            ):
                return None
            ttl = control.get("ttl", "5m")
            if ttl == "5m":
                observed = Decimal("1.25")
            elif ttl == "1h":
                observed = Decimal("2")
            else:
                return None
            if factor is not None and factor != observed:
                return None
            factor = observed
        # Traverse only payload containers, never prompt text, tool parameters,
        # JSON schemas, or arbitrary user dictionaries inside those schemas.
        for key in ("messages", "content", "tools"):
            children = node.get(key)
            if isinstance(children, list):
                if len(children) + len(pending) + visited > _MAX_NODES:
                    return None
                pending.extend((child, depth + 1) for child in children if isinstance(child, Mapping))
    return factor
