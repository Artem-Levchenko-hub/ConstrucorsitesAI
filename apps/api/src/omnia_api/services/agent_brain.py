"""Pure, bounded state for evidence-driven native-agent continuations."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_PATH_RE = re.compile(
    r"(?<![\w@])(?:[a-z]:)?(?:[\w.-]+[\\/])+[\w.-]+\.(?:tsx?|jsx?|py|json|css|mjs|cjs)"
    r"(?::\d+(?::\d+)?)?",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+\b")
_SPACE_RE = re.compile(r"\s+")


def _bounded_strings(values: Sequence[str], *, limit: int, chars: int) -> list[str]:
    return [str(value).strip()[:chars] for value in values if str(value).strip()][-limit:]


def _clone(brain: Mapping[str, object]) -> dict[str, Any]:
    return copy.deepcopy(dict(brain))


def new_brain(objective: str, acceptance: Sequence[str]) -> dict[str, object]:
    """Create JSON-serializable per-project reasoning state without source text."""

    return {
        "version": 1,
        "objective": str(objective).strip()[:4000],
        "acceptance": [
            {"criterion": criterion, "status": "open"}
            for criterion in _bounded_strings(acceptance, limit=20, chars=500)
        ],
        "active_hypothesis": None,
        "observations": [],
        "experiments": [],
        "failed_approaches": [],
        "artifacts": [],
        "diagnosis_required_signature": "",
        "next_action": "Inspect live evidence, then implement the smallest vertical slice.",
    }


def normalize_error_signature(text: str) -> str:
    """Hash error meaning while ignoring paths, locations and volatile identifiers."""

    canonical = _ANSI_RE.sub("", str(text)).casefold()
    canonical = _PATH_RE.sub("<path>", canonical)
    canonical = _UUID_RE.sub("<uuid>", canonical)
    canonical = _HEX_RE.sub("<hex>", canonical)
    canonical = _NUMBER_RE.sub("<n>", canonical)
    canonical = _SPACE_RE.sub(" ", canonical).strip()
    if not canonical:
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_hypothesis(
    brain: Mapping[str, object],
    *,
    root_cause: str,
    evidence: Sequence[str],
    experiment: str,
    expected_result: str,
) -> dict[str, object]:
    result = _clone(brain)
    item = {
        "root_cause": str(root_cause).strip()[:1000],
        "evidence": _bounded_strings(evidence, limit=8, chars=500),
        "experiment": str(experiment).strip()[:1000],
        "expected_result": str(expected_result).strip()[:500],
    }
    result["active_hypothesis"] = item
    experiments = list(result.get("experiments") or [])
    experiments.append(item)
    result["experiments"] = experiments[-12:]
    result["diagnosis_required_signature"] = ""
    result["next_action"] = item["experiment"] or "Run the stated experiment."
    return result


def record_observation(
    brain: Mapping[str, object],
    *,
    kind: str,
    status: str,
    summary: str,
    error_signature: str = "",
    evidence: Sequence[str] = (),
    artifacts: Sequence[str] = (),
) -> dict[str, object]:
    result = _clone(brain)
    signature = str(error_signature).strip()[:128]
    observation = {
        "kind": str(kind).strip()[:80],
        "status": str(status).strip()[:40],
        "summary": str(summary).strip()[:1000],
        "error_signature": signature,
        "evidence": _bounded_strings(evidence, limit=8, chars=500),
        "artifacts": _bounded_strings(artifacts, limit=12, chars=300),
    }
    observations = list(result.get("observations") or [])
    observations.append(observation)
    result["observations"] = observations[-20:]

    active = result.get("active_hypothesis")
    if observation["status"] == "error" and signature:
        if isinstance(active, Mapping):
            failed = list(result.get("failed_approaches") or [])
            failed.append({**dict(active), "error_signature": signature})
            result["failed_approaches"] = failed[-12:]
        result["active_hypothesis"] = None
        result["diagnosis_required_signature"] = signature
        result["next_action"] = (
            "Diagnose a different evidence-based experiment before another mutation."
        )
    elif observation["status"] == "ok":
        result["active_hypothesis"] = None
        result["diagnosis_required_signature"] = ""
        result["next_action"] = "Continue with the next open acceptance criterion."
    return result


def record_mutation(
    brain: Mapping[str, object],
    *,
    paths: Sequence[str],
    revision: int,
) -> dict[str, object]:
    result = _clone(brain)
    artifacts = list(result.get("artifacts") or [])
    artifacts.append(
        {
            "paths": _bounded_strings(paths, limit=20, chars=300),
            "revision": max(0, int(revision)),
        }
    )
    result["artifacts"] = artifacts[-20:]
    result["next_action"] = "Build and compare the observed result with the hypothesis."
    return result


def semantic_loop_count(brain: Mapping[str, object]) -> int:
    failed = [item for item in brain.get("failed_approaches", []) if isinstance(item, Mapping)]
    if not failed:
        return 0
    signature = str(failed[-1].get("error_signature") or "")
    if not signature:
        return 0
    count = 0
    for item in reversed(failed):
        if str(item.get("error_signature") or "") != signature:
            break
        count += 1
    return count


def brain_prompt_view(
    brain: Mapping[str, object],
    *,
    max_chars: int = 6000,
) -> str:
    """Render a bounded checkpoint view; intentionally omit artifact paths/source."""

    acceptance = [
        str(item.get("criterion") or "")
        for item in brain.get("acceptance", [])
        if isinstance(item, Mapping) and item.get("status") != "done"
    ]
    observations = [
        item for item in brain.get("observations", []) if isinstance(item, Mapping)
    ]
    failed = [
        {
            "root_cause": item.get("root_cause"),
            "experiment": item.get("experiment"),
            "error_signature": item.get("error_signature"),
        }
        for item in brain.get("failed_approaches", [])
        if isinstance(item, Mapping)
    ][-4:]
    rows = [
        "[PROJECT BRAIN v1 — private project checkpoint]",
        f"Objective: {str(brain.get('objective') or '')[:1200]}",
        f"Open acceptance: {json.dumps(acceptance[:12], ensure_ascii=False)}",
        "NEXT REQUIRED ACTION: " + str(brain.get("next_action") or "Inspect evidence."),
        "Active hypothesis: "
        + json.dumps(brain.get("active_hypothesis"), ensure_ascii=False)[:1600],
        "Latest observation: "
        + json.dumps(observations[-1] if observations else None, ensure_ascii=False)[:1200],
        f"Same-error experiment count: {semantic_loop_count(brain)}",
        "Failed approaches: " + json.dumps(failed, ensure_ascii=False)[:1800],
    ]
    return "\n".join(rows)[: max(1, int(max_chars))]


__all__ = [
    "brain_prompt_view",
    "new_brain",
    "normalize_error_signature",
    "record_hypothesis",
    "record_mutation",
    "record_observation",
    "semantic_loop_count",
]
