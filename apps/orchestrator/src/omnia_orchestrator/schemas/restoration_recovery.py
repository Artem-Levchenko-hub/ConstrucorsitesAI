"""Что именно разрешено восстановить — и ничего сверх того.

После оборвавшегося отката проект остаётся расщеплённым: авторитетный снимок
говорит одно, редактируемое дерево — другое, а база отвязана, но цела. Починка
такого состояния опасна ровно тем, что «починить» легко не то: другой том, чужой
проект, устаревшая ограда.

Поэтому восстановление не принимает имён и флагов. Оно принимает один замороженный
объект, в котором заранее названы проект, владелец, ограда, снимок, коммит и оба
привязанных тома вместе с их отпечатками. Любое расхождение с наблюдаемым
состоянием — остановка, а не «похоже, это оно».
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Фазы идут строго в этом порядке: сначала свидетель на копии, потом правка
# исходника, потом запуск, потом проверка. Инспекция вне порядка — она не меняет
# ничего и допустима когда угодно.
RecoveryPhase = Literal[
    "inspect", "clone_witness", "source_sync", "start_current", "verify", "complete"
]
MUTATING_PHASES: tuple[RecoveryPhase, ...] = (
    "clone_witness",
    "source_sync",
    "start_current",
    "verify",
    "complete",
)

_VOLUME_PATTERN = r"^omnia-(?:cell|machine)-[0-9a-f]{32}-[a-z0-9][a-z0-9-]{0,80}$"
_DIGEST = r"^[0-9a-f]{64}$"


class RestorationRecoveryIntent(BaseModel):
    """Единственный вход восстановления: всё названо заранее и неизменяемо."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    owner_id: UUID
    workspace_id: UUID
    current_snapshot_id: UUID
    current_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_fencing_epoch: int = Field(gt=0)
    registry_binding_digest: str = Field(pattern=_DIGEST)
    active_code_volume: str = Field(pattern=_VOLUME_PATTERN)
    active_database_volume: str = Field(pattern=_VOLUME_PATTERN)
    expected_workspace_inventory_digest: str = Field(pattern=_DIGEST)
    target_inventory_digest: str = Field(pattern=_DIGEST)
    original_db_content_manifest_digest: str = Field(pattern=_DIGEST)
    witness_digest: str | None = Field(default=None, pattern=_DIGEST)

    @model_validator(mode="after")
    def distinct_bound_volumes(self) -> Self:
        if self.active_code_volume == self.active_database_volume:
            raise ValueError("code and database volumes must differ")
        # Оба тома принадлежат одной рабочей области: разные префиксы означают, что
        # намерение собрано из двух разных проектов.
        code_stem = self.active_code_volume.rsplit("-", 1)[0].rsplit("-code", 1)[0]
        db_stem = self.active_database_volume.rsplit("-", 1)[0].rsplit("-db", 1)[0]
        if code_stem.split("-")[:3] != db_stem.split("-")[:3]:
            raise ValueError("code and database volumes belong to different workspaces")
        if self.expected_workspace_inventory_digest == self.target_inventory_digest:
            raise ValueError("recovery intent claims nothing to reconcile")
        return self


class RecoveryFinding(BaseModel):
    """Одно наблюдение: что проверяли, сошлось ли и чем это доказано."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str = Field(min_length=1, max_length=80)
    ok: bool
    detail: str = Field(default="", max_length=240)


class RecoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: RecoveryPhase
    ok: bool
    findings: tuple[RecoveryFinding, ...] = ()
    # Заполняется только после настоящего SQL на одноразовой копии.
    witness_digest: str | None = Field(default=None, pattern=_DIGEST)

    @model_validator(mode="after")
    def failure_names_its_reason(self) -> Self:
        if not self.ok and not any(not finding.ok for finding in self.findings):
            raise ValueError("a failed report must carry the finding that failed")
        return self


__all__ = [
    "MUTATING_PHASES",
    "RecoveryFinding",
    "RecoveryPhase",
    "RecoveryReport",
    "RestorationRecoveryIntent",
]
