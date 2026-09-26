from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NamedTuple
from uuid import UUID

from yleum_api.models.project import Project
from yleum_api.services.agent_builder import Action, AgentResult
from yleum_api.services.max_finalization import MaxFinalizationCoordinator, ProofBundle
from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle

"""Cohesive input records and invocation-owned runtime resources."""


@dataclass(frozen=True)
class GenerationIds:
    run_id: UUID
    project_id: UUID
    user_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID


@dataclass(frozen=True)
class ProjectGenerationFacts:
    template: str
    slug: str
    name: str
    design_preset_id: str | None
    discovery_spec: dict[str, Any] | None
    image_gen_enabled: bool
    language: str
    is_imported: bool
    memory_context: str
    restoration_context: str


@dataclass(frozen=True)
class SourceBaseline:
    snapshot_id: UUID | None
    sha: str | None
    files: dict[str, str]


@dataclass
class GenerationRuntime:
    handle: ProjectCellExecutorHandle | None = None
    coordinator: MaxFinalizationCoordinator | None = None
    deadline_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class AgentPromptPlan:
    system: str
    user: str
    model: str
    escalate_model: str | None
    steps: int
    seed_context: str
    stack_guide: str | None
    stack_system: str
    skills: str | None
    bare_stack: bool


@dataclass(frozen=True)
class AgentOperations:
    execute: Callable[[Action], Awaitable[dict[str, Any]]]
    emit: Callable[[str, dict[str, Any]], Awaitable[None]]
    probe_runtime: Callable[[str], Awaitable[dict[str, Any]]]
    probe_build: Callable[[], Awaitable[dict[str, Any]]]
    preview_url: Callable[[], Awaitable[str | None]]


class FinalizedAgentSource(NamedTuple):
    proof: ProofBundle | None
    files: dict[str, str]
    message: str


class AgentTurnClassification(NamedTuple):
    prompt: str
    is_continue: bool
    is_edit: bool
    has_generated_snapshot: bool


class CandidateProbeResult(NamedTuple):
    message: str
    runtime_ok: bool
    typecheck_ok: bool
    runtime_error: str
    typecheck_error: str


class VerificationRecovery(NamedTuple):
    candidate_failed: bool
    agent_result: AgentResult
    files: dict[str, str]
    runtime_ok: bool
    typecheck_ok: bool
    message: str


@dataclass
class AgentRuntimeBindings:
    """Selected execution capabilities shared with the agent action closure."""

    base_executor: Callable[[Action], Awaitable[dict[str, Any]]]
    execute: Callable[[Action], Awaitable[dict[str, Any]]]
    vision_context: str
    shell_requested: bool
    shell_enabled: bool
    locked_files: frozenset[str]
    probe_runtime: Callable[[str], Awaitable[dict[str, Any]]]
    probe_build: Callable[[], Awaitable[dict[str, Any]]]
    preview_url: Callable[[], Awaitable[str | None]]


class StackPrompt(NamedTuple):
    seed_context: str
    orchestrator_template: str | None
    guide: str | None
    skills: str | None
    system: str
    bare_stack: bool


class PublishedStreamedSource(NamedTuple):
    snapshot_id: UUID | None
    project: Project | None
    files: dict[str, str]
    commit_sha: str
    model_id: str
