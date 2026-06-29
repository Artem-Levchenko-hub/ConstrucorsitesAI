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


def _security_rule(stack: str | None) -> str:
    """The remediation rule appended to a fix instruction, keyed to the stack.

    On the real-backend stack (``nextjs-postgres-drizzle`` / ``fullstack``) the
    agent writes Drizzle queries DIRECTLY — so the entities "use the SDK, never
    @/lib/db" rule is WRONG there; the right rule is per-user scoping + auth on
    every route (the cause of an isolation-gate failure). Other stacks keep the
    original entities-SDK rule unchanged.
    """
    if stack in ("nextjs-postgres-drizzle", "fullstack"):
        return (
            "Правило изоляции данных: КАЖДЫЙ обработчик data-роута обязан требовать "
            "аутентификацию (auth()/getServerSession) и фильтровать строки по id "
            "текущего пользователя — НИКОГДА не возвращай и не изменяй чужие строки "
            "и не оставляй data-роут доступным анониму (это причина отказа "
            "isolation-гейта)."
        )
    return (
        "Правило безопасности: доступ к данным ТОЛЬКО через SDK/engine "
        "(@/lib/sdk, @/lib/entities/engine) — НИКОГДА напрямую @/lib/db, "
        "drizzle-orm или pg (это и есть причина отказа security-гейта)."
    )


def build_fix_instruction(
    outcomes: list[GateOutcome],
    attempt: int,
    max_attempts: int,
    stack: str | None = None,
) -> str | None:
    """Next-segment instruction for the agent, or None when there is nothing to
    fix (all blocking gates green) or we are out of retry budget.

    Concrete by design: it names each red gate and its specific failures so the
    model fixes the RIGHT thing instead of rewriting working code. ``stack`` keys
    the remediation rule (see :func:`_security_rule`); omitted → entities default.
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
        f"{_security_rule(stack)}"
    )


def should_retry(outcomes: list[GateOutcome], attempt: int, max_attempts: int) -> bool:
    """True iff there is a blocking failure AND retry budget remains."""
    return bool(blocking_failures(outcomes)) and attempt < max_attempts


def outcome_from_checks(name: str, passed: bool, checks: object) -> GateOutcome:
    """Map a live gate verdict to a GateOutcome. Works for any verdict whose
    `checks` is an iterable of objects exposing ``ok``/``name``/``detail``
    (functional_gate.Check, role_gate.RoleCheck) — so the same self-heal loop
    consumes functional, role and guardrail results uniformly."""
    failures: list[str] = []
    for c in checks or []:  # type: ignore[union-attr]
        if getattr(c, "ok", True):
            continue
        cname = getattr(c, "name", "check")
        detail = getattr(c, "detail", "") or ""
        failures.append(f"{cname}: {detail}".rstrip(": ").rstrip())
    return GateOutcome(name=name, passed=bool(passed), failures=failures)
