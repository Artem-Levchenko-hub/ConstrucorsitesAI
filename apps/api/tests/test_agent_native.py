"""Tests for the native tool-use build loop helpers (services/agent_native).

Focus: `_module_not_found_hint` — the in-loop recovery that stops the native
agent from shipping a red build when it hallucinated a non-existent internal
module (`@/lib/entities/engine`, `@/lib/sdk/*`) that belongs to a different stack.
"""

from __future__ import annotations

from omnia_api.services.agent_native import _module_not_found_hint


def test_hint_none_on_clean_or_unrelated_error() -> None:
    assert _module_not_found_hint("") is None
    assert _module_not_found_hint("Build succeeded, 0 errors") is None
    # a real error that is NOT a missing @/ module → no hint (don't over-fire)
    assert (
        _module_not_found_hint(
            "src/app/page.tsx(3,10): error TS2345: Argument of type 'string'"
        )
        is None
    )
    # a bare package (not an @/ alias) is a dependency problem, not the
    # SDK-hallucination this hint addresses → stay silent.
    assert _module_not_found_hint("Cannot find module 'postgres'") is None


def test_hint_fires_on_ts2307_internal_alias() -> None:
    out = _module_not_found_hint(
        "src/lib/sdk/tasks.ts(4,24): error TS2307: Cannot find module "
        "'@/lib/entities/engine' or its corresponding type declarations."
    )
    assert out is not None
    assert "@/lib/entities/engine" in out
    assert "do not create" in out.lower()
    # steers away from fabricating an SDK/engine wrapper
    assert "sdk" in out.lower() and "engine" in out.lower()


def test_hint_dedupes_and_caps_modules() -> None:
    blob = "\n".join(
        f"src/a{i}.ts: error TS2307: Cannot find module '@/lib/m{i}'"
        for i in range(8)
    )
    blob += "\nsrc/z.ts: error TS2307: Cannot find module '@/lib/m0'"  # repeat
    out = _module_not_found_hint(blob)
    assert out is not None
    assert out.count("@/lib/m0") == 1  # deduped
    listed = [m for m in (f"@/lib/m{i}" for i in range(8)) if m in out]
    assert len(listed) == 5  # capped to 5
