"""Deterministic recovery decisions. No I/O, replay, lifecycle or billing changes.

Raw source novelty and diagnostics are observations, not proof. Authority comes
only from the owning server receipt loaders. All returned directives are shadow
until the separate lifecycle activation; work/truncation budgets are untouched.
"""
from __future__ import annotations

from typing import Any

from yleum_api.services.generation.recovery_contract import (
    CompilerWitness,
    RecoveryDirective,
    RecoveryObservation,
    RecoveryState,
)
from yleum_api.services.generation.recovery_requirements import (
    compiler_witness_is_trusted,
    requirement_witness_is_trusted,
)


def _equivalence(observation: RecoveryObservation) -> tuple[Any, ...]:
    binding = observation.binding
    # Source churn cannot hide a repeated failed approach; a different physical
    # environment/schema/fence does require a new pair of observations.
    return (observation.kind, observation.requirement_id, observation.action_signature,
            tuple(sorted(set(observation.diagnostic_signatures))), binding.schema_sha256,
            binding.environment_sha256, binding.workspace_id, binding.fencing_epoch)


def _functional_checks_hold(state: RecoveryState, observation: RecoveryObservation) -> bool:
    return all(requirement_witness_is_trusted(
        witness, requirements=state.requirements, binding=observation.binding,
        identity=state.identity,
    ) for witness in state.witnesses)


def _compiler_reduction(
    state: RecoveryState, observation: RecoveryObservation, current: CompilerWitness,
) -> bool:
    comparable = [w for w in state.compiler_witnesses
                  if w.dimension == current.dimension
                  and w.comparison_sha256 == current.comparison_sha256
                  and w.binding.workspace_id == current.binding.workspace_id
                  and w.binding.fencing_epoch == current.binding.fencing_epoch]
    if (not current.passed or not comparable or comparable[-1].passed
            or not compiler_witness_is_trusted(comparable[-1], identity=state.identity,
                                                binding=comparable[-1].binding)
            or any(w.passed and w.binding == current.binding for w in comparable)
            or not _functional_checks_hold(state, observation)):
        return False
    # Preserve other previously green check dimensions on the current source.
    other_checks = {w.dimension for w in state.compiler_witnesses
                    if w.passed and w.dimension != current.dimension}
    return all(any(w.passed and w.dimension == dimension and w.binding == current.binding
                   and compiler_witness_is_trusted(w, identity=state.identity,
                                                   binding=current.binding)
                   for w in state.compiler_witnesses) for dimension in other_checks)


def observe_recovery(
    state: RecoveryState, observation: RecoveryObservation,
) -> tuple[RecoveryState, RecoveryDirective]:
    """Observe once; two equivalent stalls advance one strategy, never erase history."""
    witnesses = state.witnesses
    compilers = state.compiler_witnesses
    progress = False
    witness = observation.witness
    if (observation.kind in {"proof", "source"} and witness is not None
            and observation.requirement_id == witness.requirement_id
            and requirement_witness_is_trusted(witness, requirements=state.requirements,
                binding=observation.binding, identity=state.identity)):
        requirement = next(r for r in state.requirements if r.id == witness.requirement_id)
        progress = requirement.required and not any(
            w.requirement_id == witness.requirement_id for w in witnesses)
        # A refreshed receipt updates evidence but cannot re-earn a previously
        # accepted obligation through comment churn or an ABA source cycle.
        witnesses = tuple(w for w in witnesses if w.requirement_id != witness.requirement_id)
        witnesses += (witness,)
    compiler = observation.compiler
    if (observation.kind == "diagnostics" and compiler is not None
            and compiler_witness_is_trusted(compiler, identity=state.identity,
                                            binding=observation.binding)):
        progress = _compiler_reduction(state, observation, compiler)
        # The owning finalizer may refresh one row in place. Preserve each
        # immutable evidence version, including operation/binding/outcome.
        if compiler not in compilers:
            compilers += (compiler,)

    updates: dict[str, Any] = {
        "binding": observation.binding, "observations": (*state.observations, observation),
        "witnesses": witnesses, "compiler_witnesses": compilers,
        "consecutive_no_progress": 0 if progress else state.consecutive_no_progress + 1,
    }
    if progress:
        updates["mode"] = "normal"
        return state.model_copy(update=updates), RecoveryDirective(
            action="continue", reason="New independently verified progress")

    equivalents = 1
    for previous in reversed(state.observations[-state.consecutive_no_progress:]
                             if state.consecutive_no_progress else ()):
        if _equivalence(previous) != _equivalence(observation):
            break
        equivalents += 1
    directive = RecoveryDirective(action="continue", reason="Progress is unverified")
    if state.mode == "blocked":
        directive = RecoveryDirective(action="needs_input", reason="Recovery strategies exhausted")
    elif equivalents % 2 == 0:
        action, mode = {
            "normal": ("nudge", "diagnose"),
            "diagnose": ("narrow", "narrow"),
            "narrow": ("reset_context", "fresh_context"),
            "fresh_context": ("needs_input", "blocked"),
        }[state.mode]
        directive = RecoveryDirective.model_validate({
            "action": action, "reason": "Two equivalent observations without verified progress",
        })
        updates["mode"] = mode
        updates["tried_strategies"] = (*state.tried_strategies, action)
    return state.model_copy(update=updates), directive
