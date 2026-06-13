"""V2.5d — REGEN-CONVERGENCE-PROOF (chip→design causality, generation leg).

The chip→pixel gate (V2.5.2) HARD-blocks ship when a render contradicts the
onboarding chips. On its own that is *net-negative*: a "тёмная тема" chip that
never reached the writer makes the writer render light, the gate catches the
mismatch, regeneration runs **on the same raw prompt**, the new render is light
again — a deterministic reject loop that burns attempts and ships nothing. V2.5c
closed the generation leg (the writer now SEES ``discovery_spec`` via
:func:`chip_pixel_gate.spec_prompt_directive`), so the regenerate prompt is
chip-honouring and the loop must now *converge*.

This module is the money-free, browser-free, LLM-free proof of that convergence.
It wires three pieces, two of them the REAL production code:

* **builder** — a caller-supplied ``build_prompt(attempt)`` that returns the
  prompt for each loop iteration. The tests pass the REAL
  :func:`lean_prompt.build_lean_system_prompt` (the dominant entity-writer path),
  forwarding ``discovery_spec`` on the regenerate attempt exactly as
  ``messages._catalog_fallback_generate`` does.
* **render model** — :func:`honour_render`, a deterministic stand-in for the
  writer: if the prompt carries the chip directive it emits a spec-COMPLIANT DOM
  observation; if it does not, it emits the unsteered *training default* that
  contradicts the user's chips — the precise mismatch the gate exists to catch.
* **gate** — the REAL :func:`chip_pixel_gate.evaluate_fidelity`.

Because :func:`honour_render` keys off the *actual* builder output (substring
``spec_prompt_directive(spec) in prompt``), the proof is coupled to the real
wiring: drop ``discovery_spec`` from the regenerate path and the directive
vanishes → the loop stops converging → the positive test goes red. That is the
ratchet: green while the generation leg is closed, red the moment it reopens.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from omnia_api.services.chip_pixel_gate import (
    _FAMILY_HEX,
    _SECTION_KEYWORDS,
    FidelitySpec,
    Obs,
    evaluate_fidelity,
    spec_prompt_directive,
)

# A non-violet, light training default: the unsteered writer's house style. It
# contradicts a dark / violet / [catalog…] spec on every asserted axis, which is
# what triggers the reject loop V2.5c is meant to break.
_DEFAULT_BG: list[int] = [250, 250, 250, 1]
_DEFAULT_FILL_HEX = _FAMILY_HEX["emerald"]
_DEFAULT_IDS: tuple[str, ...] = ("hero", "features")
_DEFAULT_HEADINGS: tuple[str, ...] = ("Возможности", "О нас")


def _hex_to_rgba(hexv: str) -> list[int]:
    h = hexv.lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1]


def _compliant_obs(spec: FidelitySpec) -> Obs:
    """A DOM observation that honours every asserted axis of ``spec``.

    The CTA fill uses :data:`chip_pixel_gate._FAMILY_HEX` — the exact swatch the
    directive hands the writer — so :func:`family_of_hue` reads back the same
    family (no honour-but-still-fail; proven by
    ``test_spec_directive_hex_is_gate_consistent_for_every_family``).
    """
    page_bg = [10, 10, 10, 1] if spec.dark_mode is True else _DEFAULT_BG
    fill_hex = _FAMILY_HEX.get(spec.primary_family or "", _DEFAULT_FILL_HEX)
    fills = [{"bg": _hex_to_rgba(fill_hex), "tag": "button", "area": 6000}]
    ids = [
        (_SECTION_KEYWORDS[s]["anchor"][0] if s in _SECTION_KEYWORDS else s)
        for s in spec.sections
    ]
    return {
        "pageBg": page_bg,
        "fills": fills,
        "ids": ids,
        "navHrefs": [],
        "headings": [],
        "declaredTone": spec.tone,
    }


def _default_obs() -> Obs:
    """The unsteered render — fixed house style, blind to the chips."""
    return {
        "pageBg": list(_DEFAULT_BG),
        "fills": [{"bg": _hex_to_rgba(_DEFAULT_FILL_HEX), "tag": "button", "area": 6000}],
        "ids": list(_DEFAULT_IDS),
        "navHrefs": [],
        "headings": list(_DEFAULT_HEADINGS),
        "declaredTone": None,
    }


def honour_render(prompt: str, spec: FidelitySpec) -> Obs:
    """Deterministic model of the writer.

    The writer honours the chips **iff** the prompt actually carries the chip
    directive (V2.5c). A prompt without it leaves the writer unsteered → the
    training default, which contradicts the user's chips → the gate fails. This
    is what makes the loop's convergence depend on the real generation wiring.
    """
    directive = spec_prompt_directive(spec)
    if directive and directive in prompt:
        return _compliant_obs(spec)
    return _default_obs()


@dataclass(frozen=True)
class ConvergenceResult:
    """Outcome of the simulated reject→regenerate loop."""

    converged: bool
    attempts: int
    final_classes: tuple[str, ...]


def simulate_regen_loop(
    build_prompt: Callable[[int], str],
    spec: FidelitySpec,
    *,
    render: Callable[[str, FidelitySpec], Obs] = honour_render,
    max_attempts: int = 2,
) -> ConvergenceResult:
    """Run build→render→gate up to ``max_attempts``; stop the moment the gate passes.

    ``build_prompt(attempt)`` is called with the 1-based attempt number so the
    caller can mirror the real loop (initial raw build on attempt 1, spec-bearing
    regenerate on attempt 2). Returns whether the loop converged, how many
    attempts it took, and the gate findings of the final (failing) render.
    """
    last_classes: tuple[str, ...] = ()
    for attempt in range(1, max_attempts + 1):
        prompt = build_prompt(attempt)
        report = evaluate_fidelity(render(prompt, spec), spec)
        if report.passed:
            return ConvergenceResult(converged=True, attempts=attempt, final_classes=())
        last_classes = report.classes
    return ConvergenceResult(converged=False, attempts=max_attempts, final_classes=last_classes)


__all__ = [
    "ConvergenceResult",
    "honour_render",
    "simulate_regen_loop",
]
