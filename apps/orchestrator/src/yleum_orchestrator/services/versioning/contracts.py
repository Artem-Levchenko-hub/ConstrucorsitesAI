"""Wire contracts of the structured restoration report (format 2).

Format 1 fields (mode, database_state, blockers, ...) stay exactly as before so
older API/web clients keep working; format 2 only adds fields. A missing
measurement is ``unknown``/``not_measured`` — never zero and never success.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Presence = Literal["empty", "present", "unknown"]
Coverage = Literal["complete", "partial", "unavailable"]
CheckStatus = Literal["compatible", "incompatible", "unknown", "not_applicable"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InventoryObject(Strict):
    """One stored object; counts only, never row contents."""

    object: str = Field(max_length=300)
    kind: Literal["table", "partitioned_table", "view", "materialized_view", "foreign_table",
                  "large_objects"]
    classification: Literal["business", "technical", "derived", "unknown"]
    presence: Presence
    row_count: int | None = Field(default=None, ge=0)
    count_kind: Literal["exact", "estimate", "not_measured"]
    diagnostic: str | None = Field(default=None, max_length=120)


class InventoryReport(Strict):
    presence: Presence
    coverage: Coverage
    schema_analysis: Coverage
    objects: list[InventoryObject] = Field(default_factory=list, max_length=500)
    # Where the numbers were taken: the live source (read-only) or the isolated copy.
    observed_on: Literal["source", "candidate_copy"]


class CompatibilityCheck(Strict):
    code: str = Field(max_length=80)
    status: CheckStatus
    severity: Literal["blocking", "warning", "info"]
    operation: str = Field(max_length=200)
    object: str = Field(max_length=300)
    evidence: Literal["structural_rule", "observed_catalog", "source_scan"]
    explanation: str = Field(max_length=600)
    resolution: str | None = Field(default=None, max_length=600)


class Capability(Strict):
    """An HTTP route of the application (method + path)."""

    method: str = Field(max_length=10)
    path: str = Field(max_length=300)


class CapabilityDiff(Strict):
    # Present now, absent in the selected version: restoring as-is drops them.
    lost: list[Capability] = Field(default_factory=list, max_length=500)
    # Present in the selected version, absent now: restoring brings them back.
    restored: list[Capability] = Field(default_factory=list, max_length=500)
