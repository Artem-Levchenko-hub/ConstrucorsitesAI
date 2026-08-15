"""Pure, bounded state for evidence-driven native-agent continuations."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from omnia_api.services.agent_progress import redact_sensitive_text

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
_DIAGNOSTIC_RE = re.compile(
    r"(?im)\b(?P<code>TS\d{4,5}|[a-z][\w-]+\([a-z][\w-]+\)|[a-z][\w-]+/[a-z][\w-]+)"
    r"\s*:\s*(?P<message>[^\r\n]+)"
)


def _bounded_strings(values: object, *, limit: int, chars: int) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return []
    return [str(value).strip()[:chars] for value in values if str(value).strip()][-limit:]


def _clone(brain: Mapping[str, object]) -> dict[str, Any]:
    return copy.deepcopy(dict(brain))


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def new_brain(objective: str, acceptance: Sequence[str]) -> dict[str, object]:
    """Create JSON-serializable per-project reasoning state without source text."""

    return {
        "version": 2,
        "objective": str(objective).strip()[:4000],
        "acceptance": [
            {"criterion": criterion, "status": "open", "evidence": []}
            for criterion in _bounded_strings(acceptance, limit=20, chars=500)
        ],
        "acceptance_evidence": [],
        "active_hypothesis": None,
        "observations": [],
        "experiments": [],
        "failed_approaches": [],
        "artifacts": [],
        "diagnosis_required_signature": "",
        "next_action": "Inspect live evidence, then implement the smallest vertical slice.",
    }


def sync_acceptance(brain: Mapping[str, object], acceptance: Sequence[str]) -> dict[str, object]:
    """Replace criteria from the observable plan while preserving matching proof state."""

    criteria = _bounded_strings(acceptance, limit=20, chars=500)
    if not criteria:
        return _clone(brain)
    result = _clone(brain)
    previous = {
        str(item.get("criterion") or ""): item
        for item in result.get("acceptance", [])
        if isinstance(item, Mapping) and item.get("criterion")
    }
    result["version"] = 2
    synchronized: list[dict[str, object]] = []
    for criterion in criteria:
        prior = previous.get(criterion, {})
        evidence = _bounded_strings(
            prior.get("evidence") or [],
            limit=30,
            chars=160,
        )
        synchronized.append(
            {
                "criterion": criterion,
                # Legacy checkpoints could claim "done" without proof.  Reopen them:
                # completion is an evidence-backed state, not an inherited assertion.
                "status": "done" if prior.get("status") == "done" and evidence else "open",
                "evidence": evidence,
            }
        )
    result["acceptance"] = synchronized
    result.setdefault("acceptance_evidence", [])
    return result


def upgrade_brain(
    brain: Mapping[str, object], *, acceptance: Sequence[str] = ()
) -> dict[str, object]:
    """Migrate persisted v1 checkpoints without dropping experiments or observations."""

    result = _clone(brain)
    current = [
        str(item.get("criterion") or "")
        for item in result.get("acceptance", [])
        if isinstance(item, Mapping) and item.get("criterion")
    ]
    result = sync_acceptance(result, current or acceptance)
    result["version"] = 2
    result.setdefault("acceptance", [])
    result.setdefault("acceptance_evidence", [])
    return result


def durable_brain_memory(brain: Mapping[str, object]) -> dict[str, object]:
    """Return a public-checkpoint-safe anti-loop memory without source or transcript."""

    def safe_text(value: object, limit: int) -> str:
        redacted = redact_sensitive_text(str(value or ""))
        redacted = _PATH_RE.sub("[PROJECT PATH]", redacted)
        return redacted.strip()[:limit]

    def safe_hypothesis(value: object) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None
        raw_evidence = value.get("evidence")
        evidence = raw_evidence if isinstance(raw_evidence, list) else []
        return {
            "root_cause": safe_text(value.get("root_cause"), 1000),
            "evidence": [safe_text(item, 300) for item in evidence[:8]],
            "experiment": safe_text(value.get("experiment"), 1000),
            "expected_result": safe_text(value.get("expected_result"), 500),
            **(
                {"error_signature": safe_text(value.get("error_signature"), 128)}
                if value.get("error_signature")
                else {}
            ),
            "error_signatures": [
                safe_text(item, 128)
                for item in _bounded_strings(
                    value.get("error_signatures") or [], limit=41, chars=128
                )
            ],
        }

    failed = [
        item
        for value in _mapping_items(brain.get("failed_approaches"))[-12:]
        if (item := safe_hypothesis(value)) is not None
    ]
    experiments = [
        item
        for value in _mapping_items(brain.get("experiments"))[-12:]
        if (item := safe_hypothesis(value)) is not None
    ]
    observations: list[dict[str, object]] = []
    for observation in _mapping_items(brain.get("observations"))[-20:]:
        raw_evidence = observation.get("evidence")
        evidence = raw_evidence if isinstance(raw_evidence, list) else []
        observations.append(
            {
                "kind": safe_text(observation.get("kind"), 80),
                "status": safe_text(observation.get("status"), 40),
                "summary": safe_text(observation.get("summary"), 500),
                "error_signature": safe_text(observation.get("error_signature"), 128),
                "error_signatures": [
                    safe_text(item, 128)
                    for item in _bounded_strings(
                        observation.get("error_signatures") or [],
                        limit=41,
                        chars=128,
                    )
                ],
                "evidence": [safe_text(value, 160) for value in evidence[:8]],
            }
        )
    return {
        "version": 2,
        "active_hypothesis": safe_hypothesis(brain.get("active_hypothesis")),
        "observations": observations,
        "experiments": experiments,
        "failed_approaches": failed,
        "diagnosis_required_signature": safe_text(brain.get("diagnosis_required_signature"), 128),
        "next_action": safe_text(brain.get("next_action"), 800),
    }


def restore_durable_brain(
    memory: Mapping[str, object], *, objective: str, acceptance: Sequence[str]
) -> dict[str, object]:
    """Attach sanitized anti-loop history to the current request's fresh contract."""
    brain = new_brain(objective, acceptance)
    safe = durable_brain_memory(memory)
    for key in (
        "active_hypothesis",
        "observations",
        "experiments",
        "failed_approaches",
        "diagnosis_required_signature",
        "next_action",
    ):
        brain[key] = safe[key]
    return brain


def _canonical_error_text(text: str) -> str:
    canonical = _ANSI_RE.sub("", str(text)).casefold()
    canonical = _PATH_RE.sub("<path>", canonical)
    canonical = _UUID_RE.sub("<uuid>", canonical)
    canonical = _HEX_RE.sub("<hex>", canonical)
    canonical = _NUMBER_RE.sub("<n>", canonical)
    return _SPACE_RE.sub(" ", canonical).strip()


def error_signatures(text: str) -> list[str]:
    """Return stable per-diagnostic hashes, independent of order and transcript noise."""

    raw = _ANSI_RE.sub("", str(text))
    diagnostics: set[str] = set()
    for match in _DIAGNOSTIC_RE.finditer(raw):
        code = str(match.group("code") or "").casefold()
        message = _canonical_error_text(str(match.group("message") or ""))
        if code and message:
            diagnostics.add(f"{code}:{message}")
    if not diagnostics:
        fallback = _canonical_error_text(raw)
        if fallback:
            diagnostics.add(fallback)
    return [hashlib.sha256(item.encode("utf-8")).hexdigest() for item in sorted(diagnostics)]


def normalize_error_signature(text: str) -> str:
    """Hash the stable set of diagnostics, not the whole volatile build transcript."""

    signatures = error_signatures(text)
    if not signatures:
        return ""
    canonical = json.dumps(signatures, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_acceptance_evidence(
    brain: Mapping[str, object],
    *,
    proof_ids: Sequence[str],
    passed: bool,
) -> dict[str, object]:
    """Attest acceptance only from bounded observable proof identifiers."""

    result = _clone(brain)
    clean_proofs = _bounded_strings(proof_ids, limit=30, chars=160)
    result["acceptance_evidence"] = clean_proofs
    acceptance = result.get("acceptance")
    if isinstance(acceptance, list):
        for item in acceptance:
            if not isinstance(item, dict):
                continue
            item["status"] = "done" if passed and clean_proofs else "open"
            item["evidence"] = clean_proofs if item["status"] == "done" else []
    if passed and clean_proofs:
        result["next_action"] = "All acceptance criteria have executable evidence."
    return result


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
    diagnostic_signatures: Sequence[str] = (),
    evidence: Sequence[str] = (),
    artifacts: Sequence[str] = (),
) -> dict[str, object]:
    result = _clone(brain)
    signature = str(error_signature).strip()[:128]
    signatures = _bounded_strings(diagnostic_signatures, limit=40, chars=128)
    if signature and signature not in signatures:
        signatures.append(signature)
    observation = {
        "kind": str(kind).strip()[:80],
        "status": str(status).strip()[:40],
        "summary": str(summary).strip()[:1000],
        "error_signature": signature,
        "error_signatures": signatures,
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
            failed.append(
                {
                    **dict(active),
                    "error_signature": signature,
                    "error_signatures": signatures,
                }
            )
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
    result["acceptance_evidence"] = []
    acceptance = result.get("acceptance")
    if isinstance(acceptance, list):
        for item in acceptance:
            if isinstance(item, dict):
                item["status"] = "open"
                item["evidence"] = []
    result["next_action"] = "Build and compare the observed result with the hypothesis."
    return result


def semantic_loop_count(brain: Mapping[str, object]) -> int:
    failed = _mapping_items(brain.get("failed_approaches"))
    error_observations = [
        item
        for item in _mapping_items(brain.get("observations"))
        if item.get("status") == "error" and item.get("error_signature")
    ]
    if not failed and not error_observations:
        return 0

    def item_signatures(item: Mapping[str, object]) -> set[str]:
        signatures = set(_bounded_strings(item.get("error_signatures") or [], limit=41, chars=128))
        legacy = str(item.get("error_signature") or "").strip()[:128]
        if legacy:
            signatures.add(legacy)
        return signatures

    latest = error_observations[-1] if error_observations else failed[-1]
    latest_signatures = item_signatures(latest)
    if not latest_signatures:
        return 0
    source = failed[-12:] if failed else error_observations[-12:]
    counts = {
        signature: sum(1 for item in source if signature in item_signatures(item))
        for signature in latest_signatures
    }
    # Once experiments exist, the breaker counts failed experiments rather than
    # the initial observation that triggered diagnosis. Otherwise the third
    # distinct repair would be rejected after only two actual attempts.
    return max(counts.values(), default=0)


def brain_prompt_view(
    brain: Mapping[str, object],
    *,
    max_chars: int = 6000,
) -> str:
    """Render a bounded checkpoint view; intentionally omit artifact paths/source."""

    acceptance = [
        str(item.get("criterion") or "")
        for item in _mapping_items(brain.get("acceptance"))
        if item.get("status") != "done"
    ]
    observations = _mapping_items(brain.get("observations"))
    failed = [
        {
            "root_cause": item.get("root_cause"),
            "experiment": item.get("experiment"),
            "error_signature": item.get("error_signature"),
            "error_signatures": item.get("error_signatures"),
        }
        for item in _mapping_items(brain.get("failed_approaches"))
    ][-4:]
    rows = [
        "[PROJECT BRAIN v2 — private project checkpoint]",
        f"Objective: {str(brain.get('objective') or '')[:1200]}",
        f"Open acceptance: {json.dumps(acceptance[:12], ensure_ascii=False)}",
        "NEXT REQUIRED ACTION: " + str(brain.get("next_action") or "Inspect evidence."),
        "Active hypothesis: "
        + json.dumps(brain.get("active_hypothesis"), ensure_ascii=False)[:1600],
        "Latest observation: "
        + json.dumps(observations[-1] if observations else None, ensure_ascii=False)[:1200],
        "Acceptance evidence: "
        + json.dumps(brain.get("acceptance_evidence") or [], ensure_ascii=False)[:800],
        f"Same-error experiment count: {semantic_loop_count(brain)}",
        "Failed approaches: " + json.dumps(failed, ensure_ascii=False)[:1800],
    ]
    return "\n".join(rows)[: max(1, int(max_chars))]


__all__ = [
    "brain_prompt_view",
    "durable_brain_memory",
    "error_signatures",
    "new_brain",
    "normalize_error_signature",
    "record_acceptance_evidence",
    "record_hypothesis",
    "record_mutation",
    "record_observation",
    "restore_durable_brain",
    "semantic_loop_count",
    "sync_acceptance",
    "upgrade_brain",
]
