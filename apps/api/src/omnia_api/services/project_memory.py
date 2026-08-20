"""Versioned, project-scoped memory compiled from verified generation evidence.

The model never writes this store directly. Every terminal generation run produces
one immutable full revision. The next run receives a bounded rendering, while the
current repository and real build/runtime observations remain the source of truth.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_memory import ProjectMemoryRevision
from omnia_api.services.secret_safety import redact_provider_secrets

SCHEMA_VERSION = 1
MAX_CONTEXT_CHARS = 6_000
MAX_NOTE_TEXT = 1_200
MAX_REQUESTS = 16
MAX_RULES = 12
MAX_CHANGES = 16
MAX_FAILURES = 12
MAX_CHANGED_FILES = 60

_SPACE_RE = re.compile(r"\s+")
_VOLATILE_RE = re.compile(
    r"\b(?:[0-9a-f]{40}|[0-9a-f]{8}-[0-9a-f-]{27,}|\d{3,})\b",
    re.IGNORECASE,
)
_APP_ERROR_RE = re.compile(r"<app-error\b[^>]*>(.*?)</app-error>", re.IGNORECASE | re.DOTALL)
_RULE_RE = re.compile(
    r"(?i)(?:\b(?:запомни|никогда|всегда|обязательно|нельзя|never|always|must)\b|"
    r"\bне\s+(?:делай|добавляй|используй|меняй|переписывай)\b|"
    r"\bне\s+.+?\s+а\s+.+)",
)


def _safe_text(value: object, limit: int = MAX_NOTE_TEXT) -> str:
    redacted = redact_provider_secrets(str(value or ""))
    compact = _SPACE_RE.sub(" ", redacted.replace("\x00", " ")).strip()
    compact = compact.replace("</project_memory>", "&lt;/project_memory&gt;")
    return compact[:limit]


def _note_id(prefix: str, value: object) -> str:
    return f"{prefix}:{value!s}"


def _empty_state(project: Project) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {},
        "product_contract": {},
        "user_rules": [],
        "recent_requests": [],
        "verified_changes": [],
        "known_failures": [],
    }


def _compact_discovery_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:30]:
        key = _safe_text(raw_key, 80)
        if not key or key == "build_plan":
            continue
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            result[key] = _safe_text(raw_value, 300) if isinstance(raw_value, str) else raw_value
        elif isinstance(raw_value, list):
            result[key] = [_safe_text(item, 160) for item in raw_value[:12]]
    return result


def _append_unique(
    items: list[dict[str, Any]],
    note: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    note_id = note.get("id")
    kept = [item for item in items if item.get("id") != note_id]
    kept.append(note)
    return kept[-limit:]


def _failure_fingerprint(summary: str) -> str:
    normalized = _VOLATILE_RE.sub("#", summary.lower())
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _changed_files(run: GenerationRun) -> list[str]:
    raw = (run.agent_state or {}).get("changed_files", [])
    if not isinstance(raw, list):
        return []
    paths = {_safe_text(path, 300).replace("\\", "/") for path in raw}
    return sorted(path for path in paths if path)[:MAX_CHANGED_FILES]


def _failure_summary(run: GenerationRun, assistant: Message | None) -> str:
    if assistant is not None and isinstance(assistant.agent_steps, list):
        for step in reversed(assistant.agent_steps):
            if not isinstance(step, dict) or step.get("ok") is not False:
                continue
            detail = _safe_text(step.get("detail") or step.get("action"), 700)
            if not detail:
                continue
            tool = _safe_text(step.get("tool"), 80)
            path = _safe_text(step.get("path"), 240)
            locator = " ".join(part for part in (tool, path) if part)
            return f"{locator}: {detail}".strip(": ")
    if assistant is not None:
        card = _APP_ERROR_RE.search(assistant.content or "")
        if card is not None:
            return _safe_text(card.group(1), 800)
    return _safe_text(run.error or "generation failed without a committed snapshot", 800)


async def _user_message(session: AsyncSession, run: GenerationRun) -> Message | None:
    if run.user_message_id is not None:
        exact = await session.get(Message, run.user_message_id)
        if exact is not None:
            return exact
    if run.assistant_message_id is None:
        return None
    assistant = await session.get(Message, run.assistant_message_id)
    if assistant is None:
        return None
    return (
        await session.execute(
            select(Message)
            .where(
                Message.project_id == run.project_id,
                Message.role == "user",
                Message.created_at < assistant.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_project_memory_revision(
    session: AsyncSession,
    project_id: UUID,
) -> ProjectMemoryRevision | None:
    return (
        await session.execute(
            select(ProjectMemoryRevision)
            .where(ProjectMemoryRevision.project_id == project_id)
            .order_by(ProjectMemoryRevision.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def record_run_artifacts(
    run: GenerationRun,
    *,
    snapshot_id: UUID,
    commit_sha: str,
    changed_files: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Attach exact commit evidence to a run without committing its transaction."""

    state = dict(run.agent_state or {})
    previous = state.get("changed_files", [])
    known = {str(path) for path in previous} if isinstance(previous, list) else set()
    known.update(str(path) for path in changed_files)
    state.update(
        {
            "snapshot_id": str(snapshot_id),
            "commit_sha": commit_sha,
            "changed_files": sorted(known)[:MAX_CHANGED_FILES],
        }
    )
    run.agent_state = state


async def compile_project_memory_revision(
    session: AsyncSession,
    run: GenerationRun,
) -> ProjectMemoryRevision | None:
    """Create exactly one corrected full memory revision for a terminal run."""

    if run.status not in {"completed", "failed", "cancelled"}:
        return None
    existing = (
        await session.execute(
            select(ProjectMemoryRevision).where(ProjectMemoryRevision.run_id == run.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
        {"scope": f"project-memory:{run.project_id}"},
    )
    existing = (
        await session.execute(
            select(ProjectMemoryRevision).where(ProjectMemoryRevision.run_id == run.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    project = await session.get(Project, run.project_id)
    if project is None:
        return None
    parent = await latest_project_memory_revision(session, run.project_id)
    state = deepcopy(parent.memory) if parent is not None else _empty_state(project)
    state["schema_version"] = SCHEMA_VERSION
    state["project"] = {
        "id": str(project.id),
        "name": _safe_text(project.name, 200),
        "template": project.template,
        "language": project.language,
    }
    state["product_contract"] = _compact_discovery_spec(project.discovery_spec)

    user = await _user_message(session, run)
    assistant = (
        await session.get(Message, run.assistant_message_id)
        if run.assistant_message_id is not None
        else None
    )
    prompt = _safe_text(user.content if user is not None else "")
    snapshot_id = assistant.snapshot_id if assistant is not None else None
    finished = run.finished_at or datetime.now(UTC)
    request_note = {
        "id": _note_id("request", run.id),
        "run_id": str(run.id),
        "message_id": str(user.id) if user is not None else None,
        "text": prompt,
        "mode": run.response_mode or "unknown",
        "outcome": run.status,
        "snapshot_id": str(snapshot_id) if snapshot_id is not None else None,
        "at": finished.isoformat(),
    }
    state["recent_requests"] = _append_unique(
        list(state.get("recent_requests") or []),
        request_note,
        limit=MAX_REQUESTS,
    )

    if run.status != "cancelled" and prompt and _RULE_RE.search(prompt):
        rule_key = hashlib.sha256(prompt.casefold().encode("utf-8")).hexdigest()[:16]
        rule_note = {
            "id": _note_id("rule", rule_key),
            "text": prompt,
            "source_run_id": str(run.id),
            "at": finished.isoformat(),
        }
        state["user_rules"] = _append_unique(
            list(state.get("user_rules") or []),
            rule_note,
            limit=MAX_RULES,
        )

    failures = list(state.get("known_failures") or [])
    if run.status == "completed" and snapshot_id is not None:
        for failure in failures:
            if failure.get("status") == "open":
                failure["status"] = "resolved"
                failure["resolved_by_run_id"] = str(run.id)
                failure["resolved_at"] = finished.isoformat()
        change_note = {
            "id": _note_id("change", snapshot_id),
            "run_id": str(run.id),
            "snapshot_id": str(snapshot_id),
            "commit_sha": _safe_text((run.agent_state or {}).get("commit_sha"), 40),
            "request": prompt,
            "changed_files": _changed_files(run),
            "evidence": "committed_snapshot",
            "at": finished.isoformat(),
        }
        state["verified_changes"] = _append_unique(
            list(state.get("verified_changes") or []),
            change_note,
            limit=MAX_CHANGES,
        )
    elif run.status == "failed":
        summary = _failure_summary(run, assistant)
        fingerprint = _failure_fingerprint(summary)
        existing_failure = next(
            (item for item in failures if item.get("fingerprint") == fingerprint),
            None,
        )
        if existing_failure is None:
            failures.append(
                {
                    "id": _note_id("failure", fingerprint),
                    "fingerprint": fingerprint,
                    "summary": summary,
                    "status": "open",
                    "occurrences": 1,
                    "first_seen_run_id": str(run.id),
                    "last_seen_run_id": str(run.id),
                    "last_seen_at": finished.isoformat(),
                }
            )
        else:
            existing_failure.update(
                {
                    "summary": summary,
                    "status": "open",
                    "occurrences": int(existing_failure.get("occurrences") or 0) + 1,
                    "last_seen_run_id": str(run.id),
                    "last_seen_at": finished.isoformat(),
                }
            )
        failures = failures[-MAX_FAILURES:]
    state["known_failures"] = failures

    revision = ProjectMemoryRevision(
        project_id=run.project_id,
        run_id=run.id,
        user_message_id=user.id if user is not None else None,
        assistant_message_id=run.assistant_message_id,
        snapshot_id=snapshot_id,
        parent_id=parent.id if parent is not None else None,
        version=(parent.version + 1) if parent is not None else 1,
        outcome=run.status,
        memory=state,
    )
    session.add(revision)
    await session.flush()
    return revision


def _short_ref(value: object) -> str:
    return str(value or "-")[:8]


async def render_project_memory_context(
    session: AsyncSession,
    project_id: UUID,
    *,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """Render latest full revision as bounded, evidence-labelled agent context."""

    revision = await latest_project_memory_revision(session, project_id)
    if revision is None:
        return ""
    state = revision.memory or {}
    project = state.get("project") or {}
    lines = [
        f"PROJECT MEMORY v{revision.version} (historical data; current code/build wins):",
        (
            f"Project: {project.get('name', '')}; stack={project.get('template', '')}; "
            f"language={project.get('language', '')}."
        ),
        "Latest explicit user instruction has priority over older notes.",
    ]

    contract = state.get("product_contract") or {}
    if contract:
        compact_contract = ", ".join(f"{key}={value}" for key, value in list(contract.items())[:12])
        lines.extend(["", "Product contract:", f"- {compact_contract}"])

    rules = list(state.get("user_rules") or [])[-6:]
    if rules:
        lines.extend(["", "Explicit user rules (oldest → newest):"])
        lines.extend(
            f"- [{_short_ref(item.get('source_run_id'))}] {_safe_text(item.get('text'), 500)}"
            for item in rules
        )

    requests = list(state.get("recent_requests") or [])[-6:]
    if requests:
        lines.extend(["", "Recent prompts (oldest → newest):"])
        lines.extend(
            (
                f"- [{_short_ref(item.get('run_id'))}] {item.get('outcome')}/"
                f"{item.get('mode')}: {_safe_text(item.get('text'), 420)}"
            )
            for item in requests
            if item.get("text")
        )

    changes = list(state.get("verified_changes") or [])[-6:]
    if changes:
        lines.extend(["", "Verified snapshot changes (oldest → newest):"])
        for item in changes:
            paths = ", ".join(item.get("changed_files") or []) or "files not recorded"
            lines.append(
                f"- snapshot={_short_ref(item.get('snapshot_id'))}; files={paths}; "
                f"request={_safe_text(item.get('request'), 320)}"
            )

    open_failures = [
        item for item in list(state.get("known_failures") or []) if item.get("status") == "open"
    ][-6:]
    if open_failures:
        lines.extend(["", "Known unresolved failures — do not repeat the same approach:"])
        lines.extend(
            (
                f"- [{item.get('fingerprint')}] {_safe_text(item.get('summary'), 500)} "
                f"(seen {item.get('occurrences', 1)}x)"
            )
            for item in open_failures
        )

    rendered = "\n".join(lines).strip()
    if len(rendered) > max_chars:
        rendered = rendered[: max(0, max_chars - 24)].rstrip() + "\n…[memory truncated]"
    return f"<project_memory>\n{rendered}\n</project_memory>"


__all__ = [
    "compile_project_memory_revision",
    "latest_project_memory_revision",
    "record_run_artifacts",
    "render_project_memory_context",
]
