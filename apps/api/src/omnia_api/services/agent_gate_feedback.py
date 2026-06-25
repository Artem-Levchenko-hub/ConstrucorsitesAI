"""Gate-feedback loop (layer C of the unleash-the-model design).

The agent writes the app FREELY (no input cage); then GATES verify the output —
backend guardrail (no raw-DB escape), functional (it works), security (no leak).
When a gate is red, we do not just fail: we feed the SPECIFIC failures back as the
agent's next instruction so the loop SELF-HEALS until green (bounded retries). The
model can write realtime/backend however it likes — the gates, not a template,
decide whether the result is a real app or a leaky prototype.

This module is the PURE translation of gate verdicts -> a concrete fix
instruction (and the stop/continue decision). The loop wiring lives in
messages.py; keeping the decision pure makes the risky self-heal logic unit-
testable without a container.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateOutcome:
    """One gate's result. `failures` are human-readable failing-check names the
    agent can act on."""

    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    # A guardrail/security failure is a HARD stop (must be fixed); a functional
    # failure is also blocking but distinct in messaging.
    blocking: bool = True


def all_passed(outcomes: list[GateOutcome]) -> bool:
    return all(o.passed for o in outcomes)


def blocking_failures(outcomes: list[GateOutcome]) -> list[GateOutcome]:
    return [o for o in outcomes if not o.passed and o.blocking]


def build_fix_instruction(
    outcomes: list[GateOutcome],
    attempt: int,
    max_attempts: int,
) -> str | None:
    """Next-segment instruction for the agent, or None when there is nothing to
    fix (all blocking gates green) or we are out of retry budget.

    Concrete by design: it names each red gate and its specific failures so the
    model fixes the RIGHT thing instead of rewriting working code.
    """
    red = blocking_failures(outcomes)
    if not red:
        return None
    if attempt >= max_attempts:
        return None

    lines: list[str] = []
    for o in red:
        detail = "; ".join(o.failures[:8]) if o.failures else "see gate output"
        lines.append(f"- {o.name}: {detail}")
    body = "\n".join(lines)
    return (
        "Сборка НЕ прошла проверки качества/безопасности. Почини ИМЕННО это "
        "(не переписывай работающее), затем вызови done:\n"
        f"{body}\n\n"
        "Правило безопасности: доступ к данным ТОЛЬКО через SDK/engine "
        "(@/lib/sdk, @/lib/entities/engine) — НИКОГДА напрямую @/lib/db, "
        "drizzle-orm или pg (это и есть причина отказа security-гейта)."
    )


def should_retry(outcomes: list[GateOutcome], attempt: int, max_attempts: int) -> bool:
    """True iff there is a blocking failure AND retry budget remains."""
    return bool(blocking_failures(outcomes)) and attempt < max_attempts
