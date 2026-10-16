"""Invocation-owned output recovery and explicitly authorized single-batch reuse."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import weakref
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation.recovery_contract import (
    CompilerWitness,
    IncompleteResponseBudget,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryIdentity,
    RecoveryRequirement,
    RequirementWitness,
    decode_recovery,
)
from yleum_api.services.generation.recovery_policy import (
    record_output_boundary,
    verified_compiler_progress,
)
from yleum_api.services.generation.recovery_requirements import (
    frozen_requirements,
    load_compiler_witness,
    load_requirement_witness,
)
from yleum_api.services.max_behavior_proof import RECEIPT_KEY
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error
from yleum_api.services.project_cell_proofs import ProofIdentity

if TYPE_CHECKING:
    from yleum_api.services.agent_builder import Action

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerifiedProgressReceipt:
    identity: RecoveryIdentity
    physical: ProofIdentity
    previous: CompilerWitness
    current: CompilerWitness
    sequence: int
    issuer: UUID
    reason: str = "verified_compiler_repair"


_issued: dict[int, weakref.ReferenceType[VerifiedProgressReceipt]] = {}
ProgressReader = Callable[[], Awaitable[VerifiedProgressReceipt | None]]


@dataclass
class ResponseRecovery:
    """Shared through segments and same-run repair calls; never persisted as authority."""

    budget: IncompleteResponseBudget = field(default_factory=IncompleteResponseBudget)
    _issuer: UUID | None = field(default=None, init=False)
    _sequence: int = 0

    def truncated(self) -> None:
        self.budget = record_output_boundary(self.budget, truncated=True, verified_progress=False)

    async def consume(self, reader: ProgressReader | None) -> None:
        if reader is None:
            return
        # Deliver pending cancellation before reading or spending one-shot authority.
        await asyncio.sleep(0)
        try:
            receipt = await reader()
            await asyncio.sleep(0)
        except Exception as exc:
            raise_if_terminal_cell_error(exc)
            _log.warning("Verified progress observer unavailable")
            return
        if not isinstance(receipt, VerifiedProgressReceipt):
            return
        trusted = _issued.get(id(receipt))
        if (
            trusted is None
            or trusted() is not receipt
            or receipt.sequence <= self._sequence
            or self._issuer != receipt.issuer
        ):
            return
        _issued.pop(id(receipt), None)
        self._sequence = receipt.sequence
        self.budget = record_output_boundary(self.budget, truncated=False, verified_progress=True)


def recovery_binding(physical: ProofIdentity) -> RecoveryBinding:
    if physical.base_image_digest != physical.toolchain_digest:
        raise RecoveryContractError("unsupported physical environment binding")
    return RecoveryBinding(
        source_sha256=physical.workspace_revision,
        schema_sha256=physical.schema_data_digest,
        environment_sha256=physical.base_image_digest,
        workspace_id=str(physical.workspace_id),
        fencing_epoch=physical.fencing_epoch,
    )


def verified_progress_executor(
    execute: Callable[[Action], Awaitable[dict[str, Any]]],
    recovery: ResponseRecovery,
    reader: ProgressReader | None,
) -> Callable[[Action], Awaitable[dict[str, Any]]]:
    """Observe every actual effect, including automatic checks; cache hits skip it."""
    if reader is None:
        return execute

    async def observed(action: Action) -> dict[str, Any]:
        result = await execute(action)
        await recovery.consume(reader)
        return result

    return observed


class VerifiedProgressCollector:
    """Load terminal receipts before model-visible dict collapse, retaining RED evidence."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        run_id: UUID,
        project_id: UUID,
        user_id: UUID,
        current_identity: Callable[[], Awaitable[ProofIdentity]],
        response_recovery: ResponseRecovery | None = None,
    ) -> None:
        self.factory, self.run_id, self.project_id, self.user_id = (
            factory,
            run_id,
            project_id,
            user_id,
        )
        self.current_identity = current_identity
        self.history: tuple[CompilerWitness, ...] = ()
        self._pending: VerifiedProgressReceipt | None = None
        self._sequence = 0
        self._issuer = uuid4()
        self.response_recovery = response_recovery or ResponseRecovery()
        if self.response_recovery._issuer is not None:
            raise ValueError("Output recovery already belongs to a collector")
        self.response_recovery._issuer = self._issuer
        self._functional_refs: dict[str, str] = {}

    async def _functional_witnesses(
        self,
        session: AsyncSession,
        run: GenerationRun,
        requirements: tuple[RecoveryRequirement, ...],
        identity: RecoveryIdentity,
        binding: RecoveryBinding,
    ) -> tuple[RequirementWitness, ...]:
        root = run.agent_state or {}
        saved = decode_recovery(root.get("recovery_v1"))
        if "recovery_v1" in root and saved is None:
            raise RecoveryContractError("invalid prior recovery evidence")
        if saved is not None:
            if (
                saved.identity.run_id,
                saved.identity.project_id,
                saved.identity.user_id,
                saved.identity.prompt_hash,
            ) != (
                identity.run_id,
                identity.project_id,
                identity.user_id,
                identity.prompt_hash,
            ) or saved.requirements != requirements:
                raise RecoveryContractError("conflicting prior functional evidence")
            self._functional_refs.update(
                {w.requirement_id: w.receipt_id for w in saved.witnesses if w.passed}
            )
        if RECEIPT_KEY in root:
            receipt = root[RECEIPT_KEY]
            if not isinstance(receipt, dict) or not isinstance(receipt.get("receipt_digest"), str):
                raise RecoveryContractError("invalid prior functional evidence")
            self._functional_refs.update(
                {
                    r.id: "named_behavior:" + receipt["receipt_digest"]
                    for r in requirements
                    if r.check_kind == "named_behavior"
                }
            )
        return tuple(
            [
                await load_requirement_witness(
                    session, run_id=self.run_id, requirement_id=key, receipt_id=ref, binding=binding
                )
                for key, ref in self._functional_refs.items()
            ]
        )

    async def record(self, receipt_id: UUID) -> None:
        self._pending = None
        try:
            physical = await self.current_identity()
            if physical.generation_run_id != self.run_id:
                return
            binding = recovery_binding(physical)
            async with self.factory() as session:
                current = await load_compiler_witness(
                    session, run_id=self.run_id, receipt_id=receipt_id, binding=binding
                )
                run = await session.get(GenerationRun, self.run_id)
                if (
                    run is None
                    or run.project_id != self.project_id
                    or run.user_id != self.user_id
                    or run.status != "running"
                ):
                    return
                identity = RecoveryIdentity(
                    run_id=run.id,
                    project_id=run.project_id,
                    user_id=run.user_id,
                    prompt_hash=run.prompt_hash,
                    accepted_snapshot_id=None,
                    supersedes_run_id=None,
                )
                requirements = frozen_requirements(run)
                witnesses = await self._functional_witnesses(
                    session, run, requirements, identity, binding
                )
            progress = verified_compiler_progress(
                identity=identity,
                binding=binding,
                requirements=requirements,
                witnesses=witnesses,
                previous=self.history,
                current=current,
            )
            if progress:
                previous = next(
                    w
                    for w in reversed(self.history)
                    if w.dimension == current.dimension
                    and w.comparison_sha256 == current.comparison_sha256
                )
                self._sequence += 1
                receipt = VerifiedProgressReceipt(
                    identity, physical, previous, current, self._sequence, self._issuer
                )
                key = id(receipt)
                _issued[key] = weakref.ref(receipt, lambda _: _issued.pop(key, None))
                self._pending = receipt
            if current not in self.history:
                self.history += (current,)
        except Exception as exc:
            raise_if_terminal_cell_error(exc)
            _log.warning("Verified progress receipt unavailable")

    async def take(self) -> VerifiedProgressReceipt | None:
        receipt, self._pending = self._pending, None
        if receipt is None:
            return None
        # Full current physical identity/owner/fence must still hold at consumption.
        if await self.current_identity() != receipt.physical:
            _issued.pop(id(receipt), None)
            return None
        return receipt


@dataclass(frozen=True)
class PureCheckCapability:
    """Executor opt-in: exact command is read-only, independent of DB/network state.

    Role names, bootstrap, arbitrary tests and model JSON do not grant this.
    The reader must enforce current ownership/fence on every call, including hits.
    """

    identity: ProofIdentity
    command: str


PureCheckReader = Callable[["Action"], Awaitable[PureCheckCapability | None]]


class PureCheckBatch:
    """Discard after one fully validated native response; unknown actions clear reuse."""

    def __init__(self, reader: PureCheckReader | None) -> None:
        self.reader = reader
        self._results: dict[tuple[Any, ...], dict[str, Any]] = {}

    def clear(self) -> None:
        self._results.clear()

    async def execute(
        self, action: Action, execute: Callable[[Action], Awaitable[dict[str, Any]]]
    ) -> tuple[dict[str, Any], bool]:
        try:
            return await self._execute(action, execute)
        except BaseException:
            self.clear()
            raise

    async def _execute(
        self, action: Action, execute: Callable[[Action], Awaitable[dict[str, Any]]]
    ) -> tuple[dict[str, Any], bool]:
        await asyncio.sleep(0)
        capability = await self.reader(action) if self.reader is not None else None
        await asyncio.sleep(0)
        if (
            action.name != "build"
            or not isinstance(capability, PureCheckCapability)
            or not capability.command.strip()
        ):
            self._results.clear()
            return await execute(action), False
        args = hashlib.sha256(
            json.dumps(action.args, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        key = (action.name, args, capability.identity, capability.command)
        if key in self._results:
            return deepcopy(self._results[key]), True
        result = await execute(action)
        after = await self.reader(action) if self.reader is not None else None
        if (
            after == capability
            and type(result.get("ok")) is bool
            and not result.get("environment_mutated")
            and not result.get("indeterminate")
            and not result.get("error")
        ):
            self._results[key] = deepcopy(result)
        else:
            self._results.clear()
        return result, False
