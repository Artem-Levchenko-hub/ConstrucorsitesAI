"""Universal release checks for the exact live tree about to be attested."""

from __future__ import annotations

from uuid import UUID

from omnia_api.core.config import get_settings
from omnia_api.services import orchestrator_client
from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize


async def run_release_proof(project_id: UUID, project_slug: str) -> FunctionalVerdict:
    """Prove that the live project typechecks, serves HTTP and has safe transport.

    Every failure becomes a failed check instead of escaping. Callers can therefore
    persist an honest negative attestation and keep production deploy fail-closed.
    """
    checks: list[Check] = []
    try:
        typecheck = await orchestrator_client.agent_build(project_id, project_slug)
        checks.append(
            Check(
                "typecheck",
                bool(typecheck.get("ok", False)),
                str(typecheck.get("detail") or "")[:240],
            )
        )
    except Exception as exc:
        checks.append(Check("typecheck", False, f"probe failed: {exc!r}"))

    base_url: str | None = None
    try:
        runtime = await orchestrator_client.runtime_status(
            project_id,
            slug=project_slug,
            path="/",
        )
        checks.append(
            Check(
                "runtime",
                bool(runtime.get("ok", False)),
                str(runtime.get("error") or runtime.get("status_code") or "HTTP ok")[:240],
            )
        )
        status_payload = await orchestrator_client.get_status(project_id)
        raw_base_url = (
            status_payload.get("dev_url") if isinstance(status_payload, dict) else None
        )
        base_url = str(raw_base_url) if raw_base_url else None
    except Exception as exc:
        checks.append(Check("runtime", False, f"probe failed: {exc!r}"))

    if get_settings().use_security_gate:
        try:
            if not base_url:
                raise RuntimeError("dev_url missing")
            from omnia_api.services import security_gate

            security = await security_gate.run_security_gate(base_url)
            checks.extend(Check(check.name, check.ok, check.detail) for check in security.checks)
        except Exception as exc:
            checks.append(Check("transport_security", False, f"probe failed: {exc!r}"))

    return summarize(checks)
