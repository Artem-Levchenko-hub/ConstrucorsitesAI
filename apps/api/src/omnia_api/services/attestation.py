"""Per-build verification attestation — a tamper-evident record of the runtime
gate verdicts for ONE generated-app build.

Additive + best-effort: assembling/emitting an attestation changes no gate
behaviour. It makes "what was proven" durable and inspectable — the foundation
for fresh-plan Step 3 ("сохранённая аттестация" → deploy ↔ proven).

Honesty about scope:
  * `digest` is a sha256 CONTENT hash over the canonical record — tamper-EVIDENT
    (any changed check flips it), but NOT a PKI/HMAC signature (no secret key, so
    it is not unforgeable). A keyed signature is a later hardening.
  * An attestation reflects ONLY the gates that actually ran. Today the drizzle
    path runs the anon-leak/public-access gate + transport security; the positive
    two-tenant A-vs-B proof is still agent-driven (`verify_isolation`), so it is
    NOT claimed here unless it ran. `overall_passed` on an EMPTY gate set is
    False — "nothing proven" is never "proven safe".
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

ATTESTATION_VERSION = 1


def now_iso() -> str:
    """Current UTC timestamp (ISO 8601). Isolated so tests pass a fixed stamp."""
    return datetime.now(UTC).isoformat()


def _check_to_dict(c: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(c, "name", "")),
        "ok": bool(getattr(c, "ok", False)),
        "detail": str(getattr(c, "detail", "")),
    }


def _canonical(body: dict[str, Any]) -> str:
    # Sorted keys + no whitespace → a stable byte string to hash, order-independent.
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_attestation(
    *,
    gates: list[tuple[str, Any]],
    stack: str,
    project_id: str,
    created_at: str,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Assemble a canonical attestation from gate verdicts.

    ``gates`` is ``[(gate_name, verdict), ...]``; each verdict exposes ``.passed``
    and ``.checks`` (objects with ``.name`` / ``.ok`` / ``.detail``) —
    ``FunctionalVerdict`` and ``SecurityVerdict`` both satisfy this. ``None``
    verdicts are skipped (a gate that did not run).

    ``overall_passed`` is True only when EVERY recorded gate passed AND at least
    one gate ran. ``digest`` is a sha256 over the canonical JSON of the body
    (without the digest field) — change any check and the digest changes.
    """
    gate_records: list[dict[str, Any]] = []
    for name, verdict in gates:
        if verdict is None:
            continue
        gate_records.append(
            {
                "name": str(name),
                "passed": bool(getattr(verdict, "passed", False)),
                "checks": [_check_to_dict(c) for c in (getattr(verdict, "checks", []) or [])],
            }
        )
    overall = bool(gate_records) and all(g["passed"] for g in gate_records)
    body: dict[str, Any] = {
        "version": ATTESTATION_VERSION,
        "project_id": str(project_id),
        "stack": str(stack),
        "commit_sha": commit_sha,
        "created_at": created_at,
        "overall_passed": overall,
        "gates": gate_records,
    }
    digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    return {**body, "digest": digest}


def verify_digest(attestation: dict[str, Any]) -> bool:
    """True iff the attestation's ``digest`` matches its body (tamper check)."""
    body = {k: v for k, v in attestation.items() if k != "digest"}
    want = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    return attestation.get("digest") == want


def to_log_line(attestation: dict[str, Any]) -> str:
    """Compact one-line JSON for the durable log stream (grep ``[ATTEST]``)."""
    return _canonical(attestation)
