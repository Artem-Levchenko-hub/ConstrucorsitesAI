"""Harness-hardening: the agent is handed the EXACT signatures of the locked
nextjs-realtime primitives up front, so a weak model imports the real API instead
of hallucinating names/shapes/arity (`getChannels`, its own `Channel` type,
`useChannel()`) and looping on TS2305/TS2322/TS2554.

The card is a static string, so the real risk is DRIFT: a template rename leaves
the card lying. These tests re-read the live template files and fail if any
promised export is gone — the card can never silently diverge from truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnia_api.services import agent_builder as ab

# apps/api/tests/ -> repo root -> orchestrator realtime template
_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "orchestrator"
    / "templates"
    / "nextjs-realtime"
)


def test_template_dir_exists() -> None:
    assert _TEMPLATE.is_dir(), f"realtime template not found at {_TEMPLATE}"


@pytest.mark.parametrize(
    "rel_path,names",
    sorted(ab.REALTIME_CONTRACT_EXPORTS.items()),
)
def test_every_promised_export_exists(rel_path: str, names: tuple[str, ...]) -> None:
    """Each name the card promises must actually be exported by the template file —
    otherwise the contract handed to the agent is a lie and it'll loop again."""
    src = (_TEMPLATE / rel_path).read_text(encoding="utf-8")
    for name in names:
        assert (
            f"export async function {name}" in src
            or f"export function {name}" in src
            or f"export const {name}" in src
            or f"export type {name}" in src
            or f"export interface {name}" in src
            # destructured re-export, e.g. `export const { signIn, signOut, auth } = NextAuth(...)`
            or (f"export const {{" in src and name in src)
        ), f"{rel_path} no longer exports `{name}` — update the contract card"


def test_card_lists_the_real_names_not_the_hallucinated_one() -> None:
    card = ab.realtime_primitives_contract()
    # the real names the live failures got wrong
    assert "listUserChannels" in card
    assert "useChannel(channel: string" in card  # arity is explicit
    assert "type Channel = {" in card  # exact shape, so no home-grown Channel type
    # the hallucination must NOT be taught
    assert "getChannels" not in card


def test_card_is_nonempty_and_marks_locked() -> None:
    card = ab.realtime_primitives_contract()
    assert len(card) > 500
    assert "КОНТРАКТ" in card
    assert "@/lib/channels" in card
    assert "@/components/realtime/use-channel" in card
