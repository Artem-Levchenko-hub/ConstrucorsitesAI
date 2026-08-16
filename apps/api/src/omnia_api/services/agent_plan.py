"""Observable planning state for long-running native-agent builds.

This is intentionally not a chain-of-thought recorder. It stores only the
user-visible execution contract: objective, finite steps, acceptance criteria,
tool evidence, artifacts and the next action. The state is small enough to
persist after every meaningful tool call and precise enough to resume after an
API restart without asking the model to rediscover completed work.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

_MAX_STEPS = 12
_MAX_CRITERIA = 12
_MAX_EVIDENCE = 30
_MAX_ARTIFACTS = 40
_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked"})
_PROOF_TOOLS = frozenset({"build", "runtime_check", "probe", "verify_isolation", "see"})


def _required_tools(title: str, index: int) -> tuple[str, ...]:
    folded = title.casefold()
    if re.search(r"подписан|signed|browser acceptance|functional gate", folded):
        return ("see",)
    if re.search(r"собр|сбор|build|compile|typecheck", folded):
        return ("build",)
    if re.search(r"runtime|жив|маршрут|route|изоляц", folded):
        return ("runtime_check", "probe", "verify_isolation")
    if re.search(r"визуал|visual|дизайн", folded):
        return ("see",)
    if re.search(r"реализ|интерфейс|функцион", folded):
        return ("write_files", "write_file", "edit_file", "bash")
    if index == 0:
        return ("write_files", "write_file", "edit_file", "read_skill")
    return ()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _strings(value: Any, *, limit: int, item_limit: int = 500) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, limit=item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _revision(state: Mapping[str, Any]) -> int:
    try:
        return max(0, int(state.get("workspace_revision") or 0))
    except (TypeError, ValueError):
        return 0


def _evidence_revision(item: Mapping[str, Any]) -> int:
    try:
        return max(0, int(item.get("revision") or 0))
    except (TypeError, ValueError):
        return 0


def _invalidate_steps(state: dict[str, Any], tools: set[str]) -> None:
    steps = state.get("steps")
    if not isinstance(steps, list):
        return
    for index, item in enumerate(steps):
        if not isinstance(item, dict):
            continue
        if not set(_required_tools(str(item.get("title") or ""), index)) & tools:
            continue
        item["status"] = "pending"
        item["summary"] = ""
        item["evidence"] = []


def initial_plan(objective: str, *, max_product: bool = False) -> dict[str, Any]:
    """Return the deterministic lifecycle plan every build can refine."""

    steps = [
        "Зафиксировать продуктовую концепцию и пользовательские сценарии",
        "Реализовать целостный интерфейс и рабочую функциональность",
        "Собрать проект и устранить реальные ошибки",
        "Проверить живой runtime и ключевые состояния",
        "Провести визуальную проверку и применить конкретные улучшения",
    ]
    criteria = [
        "Пользовательский запрос реализован без TODO и заглушек",
        "Сборка после последней записи проходит чисто",
        "Главный маршрут открывается в живом приложении",
        "Визуальная проверка не содержит нерешённых блокирующих замечаний",
    ]
    if not max_product:
        criteria.append("Интерактив и изоляция данных доказаны подходящими runtime-проверками")
    return make_plan(objective=objective, steps=steps, acceptance_criteria=criteria)


def make_plan(
    *,
    objective: Any,
    steps: Any,
    acceptance_criteria: Any,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and create/refine a public execution plan."""

    clean_objective = _text(objective, limit=1200)
    clean_steps = _strings(steps, limit=_MAX_STEPS, item_limit=300)
    clean_criteria = _strings(
        acceptance_criteria,
        limit=_MAX_CRITERIA,
        item_limit=400,
    )
    if not clean_objective:
        raise ValueError("objective is required")
    if not clean_steps:
        raise ValueError("at least one concrete plan step is required")
    if not clean_criteria:
        raise ValueError("at least one acceptance criterion is required")

    previous_steps = {
        str(item.get("id")): item
        for item in (previous or {}).get("steps", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    planned: list[dict[str, Any]] = []
    for index, title in enumerate(clean_steps, start=1):
        step_id = f"step-{index}"
        old = previous_steps.get(step_id, {})
        old_status = str(old.get("status") or "pending")
        same_step = _text(old.get("title"), limit=300).casefold() == title.casefold()
        planned.append(
            {
                "id": step_id,
                "title": title,
                "status": (
                    old_status if same_step and old_status in _VALID_STATUSES else "pending"
                ),
                "summary": _text(old.get("summary"), limit=800) if same_step else "",
                "evidence": (
                    _strings(old.get("evidence"), limit=_MAX_EVIDENCE) if same_step else []
                ),
                "artifacts": (
                    _strings(old.get("artifacts"), limit=_MAX_ARTIFACTS) if same_step else []
                ),
            }
        )
    now = _now()
    return {
        "version": 1,
        "objective": clean_objective,
        "steps": planned,
        "acceptance_criteria": clean_criteria,
        "next_action": _text((previous or {}).get("next_action"), limit=800) or planned[0]["title"],
        "last_tool": _text((previous or {}).get("last_tool"), limit=80),
        "last_summary": _text((previous or {}).get("last_summary"), limit=1000),
        "workspace_revision": _revision(previous or {}),
        "created_at": str((previous or {}).get("created_at") or now),
        "updated_at": now,
    }


def update_plan(
    state: Mapping[str, Any],
    *,
    step_id: Any,
    status: Any,
    summary: Any,
    evidence: Any = None,
    artifacts: Any = None,
    next_action: Any = None,
) -> dict[str, Any]:
    """Record one semantic checkpoint without accepting arbitrary hidden prose."""

    clean_step_id = _text(step_id, limit=40)
    clean_status = _text(status, limit=30)
    clean_summary = _text(summary, limit=1000)
    if clean_status not in _VALID_STATUSES:
        raise ValueError("status must be pending, in_progress, completed or blocked")
    if not clean_summary:
        raise ValueError("checkpoint summary is required")

    next_state = cast(
        dict[str, Any],
        json.loads(json.dumps(dict(state), ensure_ascii=False)),
    )
    steps = next_state.get("steps")
    if not isinstance(steps, list):
        raise ValueError("plan state is missing steps")
    target: dict[str, Any] | None = None
    target_index = -1
    for index, item in enumerate(steps):
        if isinstance(item, dict) and item.get("id") == clean_step_id:
            target = item
            target_index = index
            break
    if target is None:
        raise ValueError(f"unknown plan step: {clean_step_id}")

    supplied_evidence = _strings(evidence, limit=_MAX_EVIDENCE)
    verified_evidence = {
        str(item.get("id")): str(item.get("tool") or "")
        for item in next_state.get("tool_evidence", [])
        if isinstance(item, Mapping)
        and item.get("ok") is True
        and item.get("id")
        and _evidence_revision(item) == _revision(next_state)
    }
    supplied_tools = {
        verified_evidence[evidence_id]
        for evidence_id in supplied_evidence
        if evidence_id in verified_evidence
    }
    required_tools = _required_tools(str(target.get("title") or ""), target_index)
    if clean_status == "completed" and (
        not supplied_tools or (required_tools and supplied_tools.isdisjoint(required_tools))
    ):
        raise ValueError(
            "completed steps require an exact compatible successful server tool-evidence id"
        )

    target["status"] = clean_status
    target["summary"] = clean_summary
    target["evidence"] = _strings(
        [*target.get("evidence", []), *supplied_evidence],
        limit=_MAX_EVIDENCE,
    )
    target["artifacts"] = _strings(
        [*target.get("artifacts", []), *_strings(artifacts, limit=_MAX_ARTIFACTS)],
        limit=_MAX_ARTIFACTS,
    )
    explicit_next = _text(next_action, limit=800)
    if explicit_next:
        next_state["next_action"] = explicit_next
    elif clean_status == "completed":
        pending = next(
            (
                item
                for item in steps
                if isinstance(item, dict) and item.get("status") != "completed"
            ),
            None,
        )
        next_state["next_action"] = str((pending or {}).get("title") or "Финальная проверка")
    next_state["last_summary"] = clean_summary
    next_state["updated_at"] = _now()
    return next_state


def record_tool_evidence(
    state: Mapping[str, Any],
    *,
    tool: str,
    ok: bool,
    summary: str,
    artifact: str = "",
    mutated: bool = False,
) -> dict[str, Any]:
    """Persist the last real observation even if the model skips update_plan."""

    next_state = cast(
        dict[str, Any],
        json.loads(json.dumps(dict(state), ensure_ascii=False)),
    )
    next_state["last_tool"] = _text(tool, limit=80)
    next_state["last_summary"] = _text(summary, limit=1000)
    next_state["last_tool_ok"] = bool(ok)
    clean_tool = _text(tool, limit=80)
    current_revision = _revision(next_state)
    if ok and mutated:
        current_revision += 1
        next_state["workspace_revision"] = current_revision
        _invalidate_steps(next_state, set(_PROOF_TOOLS))

    history = next_state.get("tool_evidence")
    if not isinstance(history, list):
        history = []
    invalidated_tools = {clean_tool}
    if not ok and clean_tool in {"runtime_check", "probe", "verify_isolation"}:
        invalidated_tools.update({"runtime_check", "probe", "verify_isolation"})
    history = [
        item
        for item in history
        if not isinstance(item, Mapping) or str(item.get("tool") or "") not in invalidated_tools
    ]
    if not ok and clean_tool in _PROOF_TOOLS:
        _invalidate_steps(next_state, invalidated_tools)
    try:
        evidence_seq = max(0, int(next_state.get("tool_evidence_seq") or 0)) + 1
    except (TypeError, ValueError):
        # Plan state is durable and may outlive schema changes. A malformed
        # legacy counter must not abort an otherwise valid server observation.
        evidence_seq = 1
    evidence_id = f"tool:{_text(clean_tool, limit=40)}:{evidence_seq}"
    history.append(
        {
            "id": evidence_id,
            "tool": clean_tool,
            "ok": bool(ok),
            "revision": current_revision,
            "summary": _text(summary, limit=500),
            "artifact": _text(artifact, limit=500),
        }
    )
    next_state["tool_evidence"] = history[-_MAX_EVIDENCE:]
    next_state["tool_evidence_seq"] = evidence_seq
    if artifact:
        artifacts = _strings(next_state.get("artifacts"), limit=_MAX_ARTIFACTS)
        next_state["artifacts"] = _strings(
            [*artifacts, artifact], limit=_MAX_ARTIFACTS, item_limit=500
        )
    next_state["updated_at"] = _now()
    return next_state


def reconcile_tool_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    """Auto-close lifecycle steps only from successful server observations."""
    next_state = cast(dict[str, Any], json.loads(json.dumps(dict(state), ensure_ascii=False)))
    history = next_state.get("tool_evidence")
    steps = next_state.get("steps")
    if not isinstance(history, list) or not isinstance(steps, list):
        return next_state
    by_tool: dict[str, list[str]] = {}
    current_revision = _revision(next_state)
    for item in history:
        if (
            not isinstance(item, Mapping)
            or item.get("ok") is not True
            or _evidence_revision(item) != current_revision
        ):
            continue
        tool = str(item.get("tool") or "")
        evidence_id = str(item.get("id") or "")
        if tool and evidence_id:
            by_tool.setdefault(tool, []).append(evidence_id)

    for index, item in enumerate(steps):
        if not isinstance(item, dict):
            continue
        required = _required_tools(str(item.get("title") or ""), index)
        proof = next((by_tool[tool][-1] for tool in required if by_tool.get(tool)), "")
        if proof:
            item["status"] = "completed"
            item["summary"] = "Подтверждено серверным инструментом."
            item["evidence"] = _strings([*item.get("evidence", []), proof], limit=_MAX_EVIDENCE)
    next_state["updated_at"] = _now()
    return next_state


def observation(state: Mapping[str, Any], summary: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "success",
        "summary": _text(summary, limit=500),
        "content": json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        "next_actions": [_text(state.get("next_action"), limit=800)],
        "artifacts": _strings(state.get("artifacts"), limit=_MAX_ARTIFACTS),
        "acceptance_criteria": _strings(
            state.get("acceptance_criteria"), limit=_MAX_CRITERIA, item_limit=400
        ),
    }


def recovery_context(state: Mapping[str, Any] | None) -> str:
    """Compact public checkpoint injected into a replacement generation."""

    if not state or not isinstance(state.get("steps"), list):
        return ""
    rows: list[str] = []
    for item in state["steps"]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            f"- [{item.get('status', 'pending')}] {item.get('id')}: "
            f"{_text(item.get('title'), limit=300)}"
        )
    return (
        "RECOVERED EXECUTION CHECKPOINT (observable state, not hidden reasoning):\n"
        f"Objective: {_text(state.get('objective'), limit=1200)}\n"
        + "\n".join(rows)
        + f"\nLast verified tool: {_text(state.get('last_tool'), limit=80)}"
        + f"\nLast observation: {_text(state.get('last_summary'), limit=1000)}"
        + f"\nNext action: {_text(state.get('next_action'), limit=800)}\n"
        "Continue from the live files and re-verify evidence; do not repeat completed work."
    )


def completion_gap(state: Mapping[str, Any] | None) -> str | None:
    """Return the first observable plan item that still blocks completion.

    This deliberately judges only the public checkpoint, never hidden reasoning.
    A native agent may refine the deterministic initial plan, but it cannot attest
    a product as complete while one of its own declared steps is still pending,
    in progress, or blocked.
    """

    if not state or not isinstance(state.get("steps"), list):
        return "Create an observable execution plan with plan_task before completion."
    incomplete: list[str] = []
    for item in state["steps"]:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "pending") != "completed":
            step_id = _text(item.get("id"), limit=40) or "step"
            title = _text(item.get("title"), limit=180) or "untitled"
            incomplete.append(f"{step_id} ({title})")
    if incomplete:
        return (
            "Execution plan is not fully attested. Complete and update these steps with "
            "factual tool evidence: " + ", ".join(incomplete[:6]) + "."
        )
    return None


__all__ = [
    "completion_gap",
    "initial_plan",
    "make_plan",
    "observation",
    "reconcile_tool_evidence",
    "record_tool_evidence",
    "recovery_context",
    "update_plan",
]
