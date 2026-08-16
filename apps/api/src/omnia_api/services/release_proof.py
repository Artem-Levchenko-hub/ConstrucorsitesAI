"""Universal release checks for the exact live tree about to be attested."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from omnia_api.core.config import get_settings
from omnia_api.services import orchestrator_client
from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize
from omnia_api.services.max_functional_gate import MaxPlannedFlow

_INFRA_DETAIL_PREFIX = "infrastructure unavailable: "
_OWNER_DEPENDENCY_DETAIL_PREFIX = "owner dependency: "
_RELEASE_PROOF_CACHE_VERSION = 3
_RELEASE_PROOF_DIGEST_LENGTH = 64
_RELEASE_PROOF_MAX_SUMMARY_LENGTH = 500
_RELEASE_PROOF_MAX_CHECKS = 40
_RELEASE_PROOF_MAX_CHECK_NAME_LENGTH = 80
_RELEASE_PROOF_MAX_CHECK_DETAIL_LENGTH = 240


def _valid_release_proof_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _RELEASE_PROOF_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _infra_check(name: str, detail: str) -> Check:
    return Check(name, False, (_INFRA_DETAIL_PREFIX + detail)[:240])


def release_proof_infrastructure_unavailable(verdict: FunctionalVerdict) -> bool:
    """Distinguish retryable proof infrastructure from objective product red."""

    failures = [check for check in verdict.checks if not check.ok]
    return bool(failures) and all(
        check.detail.startswith(_INFRA_DETAIL_PREFIX) for check in failures
    )


def release_proof_owner_dependency(verdict: FunctionalVerdict) -> bool:
    """Return true only when every failure needs owner-controlled configuration."""

    failures = [check for check in verdict.checks if not check.ok]
    return bool(failures) and all(
        check.detail.startswith(_OWNER_DEPENDENCY_DETAIL_PREFIX) for check in failures
    )


def serialize_release_proof(
    verdict: FunctionalVerdict,
    *,
    source_digest: str,
    contract_digest: str,
) -> dict[str, object]:
    """Persist only a bounded all-green proof for one source+contract identity."""

    if not _valid_release_proof_digest(source_digest):
        raise ValueError("source_digest must be a SHA-256 hex digest")
    if not _valid_release_proof_digest(contract_digest):
        raise ValueError("contract_digest must be a SHA-256 hex digest")
    if (
        not verdict.passed
        or not isinstance(verdict.summary, str)
        or not 1 <= len(verdict.summary) <= _RELEASE_PROOF_MAX_SUMMARY_LENGTH
        or not 1 <= len(verdict.checks) <= _RELEASE_PROOF_MAX_CHECKS
    ):
        raise ValueError("release proof must be bounded and all-green")

    checks: list[dict[str, object]] = []
    for check in verdict.checks:
        if (
            check.ok is not True
            or not isinstance(check.name, str)
            or not 1 <= len(check.name) <= _RELEASE_PROOF_MAX_CHECK_NAME_LENGTH
            or not isinstance(check.detail, str)
            or len(check.detail) > _RELEASE_PROOF_MAX_CHECK_DETAIL_LENGTH
        ):
            raise ValueError("release proof must contain bounded all-green checks")
        checks.append({"name": check.name, "ok": True, "detail": check.detail})

    return {
        "version": _RELEASE_PROOF_CACHE_VERSION,
        "source_digest": source_digest,
        "contract_digest": contract_digest,
        "passed": verdict.passed,
        "summary": verdict.summary,
        "checks": checks,
    }


def restore_release_proof(
    payload: object,
    *,
    source_digest: str,
    contract_digest: str,
) -> FunctionalVerdict | None:
    """Restore only exact source+contract current-schema all-green proof."""

    expected_keys = {"version", "source_digest", "contract_digest", "passed", "summary", "checks"}
    if (
        not _valid_release_proof_digest(source_digest)
        or not _valid_release_proof_digest(contract_digest)
        or not isinstance(payload, Mapping)
        or set(payload) != expected_keys
        or type(payload.get("version")) is not int
        or payload.get("version") != _RELEASE_PROOF_CACHE_VERSION
        or payload.get("source_digest") != source_digest
        or payload.get("contract_digest") != contract_digest
    ):
        return None
    raw_checks = payload.get("checks")
    summary = payload.get("summary")
    if (
        payload.get("passed") is not True
        or not isinstance(summary, str)
        or not 1 <= len(summary) <= _RELEASE_PROOF_MAX_SUMMARY_LENGTH
        or not isinstance(raw_checks, list)
        or not 1 <= len(raw_checks) <= _RELEASE_PROOF_MAX_CHECKS
    ):
        return None
    checks: list[Check] = []
    for raw_check in raw_checks:
        if not isinstance(raw_check, Mapping) or set(raw_check) != {"name", "ok", "detail"}:
            return None
        name = raw_check.get("name")
        ok = raw_check.get("ok")
        detail = raw_check.get("detail", "")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= _RELEASE_PROOF_MAX_CHECK_NAME_LENGTH
            or ok is not True
            or not isinstance(detail, str)
            or len(detail) > _RELEASE_PROOF_MAX_CHECK_DETAIL_LENGTH
        ):
            return None
        checks.append(Check(name, True, detail))
    restored = summarize(checks)
    return restored if restored.passed else None


async def run_max_hydration_check(project_id: UUID) -> Check:
    """Prove the generated MAX product mounted, without re-running build checks."""

    try:
        status_payload = await orchestrator_client.get_status(project_id)
        raw_base_url = status_payload.get("dev_url") if isinstance(status_payload, dict) else None
        if not raw_base_url:
            raise RuntimeError("dev_url missing")
        from omnia_api.services import max_hydration_gate

        hydration = await max_hydration_gate.audit_url(str(raw_base_url))
        return (
            Check("max_hydration", hydration.passed, hydration.detail[:240])
            if hydration.rendered
            else _infra_check("max_hydration", hydration.detail)
        )
    except Exception as exc:
        return _infra_check("max_hydration", f"probe failed: {type(exc).__name__}")


async def run_release_proof(
    project_id: UUID,
    project_slug: str,
    *,
    require_hydrated_product: bool = False,
    hydrated_product_check: Check | None = None,
    require_max_functional: bool = False,
    max_require_persistence: bool = False,
    max_planned_flow: MaxPlannedFlow | None = None,
    require_dependency_security: bool = False,
    dependency_doctor: bool = True,
) -> FunctionalVerdict:
    """Prove that the live project typechecks, serves HTTP and has safe transport.

    Every failure becomes a failed check instead of escaping. Callers can therefore
    persist an honest negative attestation and keep production deploy fail-closed.
    """
    checks: list[Check] = []
    try:
        build_options: dict[str, bool] = {} if dependency_doctor else {"dependency_doctor": False}
        typecheck = (
            await orchestrator_client.agent_build(
                project_id,
                project_slug,
                code_intelligence=True,
                security_scan=True,
                **build_options,
            )
            if require_dependency_security
            else await orchestrator_client.agent_build(
                project_id,
                project_slug,
                **build_options,
            )
        )
        typecheck_detail = str(
            typecheck.get("detail") or typecheck.get("error") or "typecheck failed"
        )[:240]
        checks.append(
            _infra_check("typecheck", typecheck_detail)
            if typecheck.get("infra_dead") is True
            else Check("typecheck", bool(typecheck.get("ok", False)), typecheck_detail)
        )
        if require_dependency_security:
            security_scan_completed = typecheck.get("security_scan_completed") is True
            raw_unavailable = typecheck.get("analysis_unavailable")
            unavailable = raw_unavailable if isinstance(raw_unavailable, list) else []
            raw_security_findings = typecheck.get("security_findings")
            if isinstance(raw_security_findings, list) and all(
                isinstance(item, dict) for item in raw_security_findings
            ):
                security_findings_valid = True
                security_findings = raw_security_findings
            else:
                security_findings_valid = False
                security_findings = []
            security_unavailable = [
                str(item)
                for item in unavailable
                if "osv" in str(item).casefold() or "analyze-code" in str(item).casefold()
            ]
            security_infra = (
                not security_scan_completed
                or not security_findings_valid
                or bool(security_unavailable)
            )
            detail = (
                str(security_findings[0].get("message") or "vulnerability found")
                if security_findings
                else security_unavailable[0]
                if security_unavailable
                else "OSV lockfile scan did not complete"
                if not security_scan_completed
                else "OSV lockfile scan returned malformed findings"
                if not security_findings_valid
                else "OSV lockfile scan clean"
            )
            checks.append(
                _infra_check("dependency_security", detail)
                if security_infra
                else Check("dependency_security", not security_findings, detail[:240])
            )
    except Exception as exc:
        checks.append(_infra_check("typecheck", f"probe failed: {type(exc).__name__}"))

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
        raw_base_url = status_payload.get("dev_url") if isinstance(status_payload, dict) else None
        base_url = str(raw_base_url) if raw_base_url else None
    except Exception as exc:
        checks.append(_infra_check("runtime", f"probe failed: {type(exc).__name__}"))

    if get_settings().use_security_gate:
        try:
            if not base_url:
                raise RuntimeError("dev_url missing")
            from omnia_api.services import security_gate

            security = await security_gate.run_security_gate(base_url)
            if getattr(security, "executed", True):
                checks.extend(
                    Check(check.name, check.ok, check.detail) for check in security.checks
                )
            else:
                checks.append(_infra_check("transport_security", security.summary))
        except Exception as exc:
            checks.append(_infra_check("transport_security", f"probe failed: {type(exc).__name__}"))

    if require_hydrated_product:
        if hydrated_product_check is not None:
            checks.append(hydrated_product_check)
        elif base_url:
            from omnia_api.services import max_hydration_gate

            hydration = await max_hydration_gate.audit_url(base_url)
            checks.append(
                Check("max_hydration", hydration.passed, hydration.detail[:240])
                if hydration.rendered
                else _infra_check("max_hydration", hydration.detail)
            )
        else:
            checks.append(_infra_check("max_hydration", "dev_url missing"))

    if require_max_functional:
        try:
            preview_session = await orchestrator_client.create_max_preview_session(project_id)
            bootstrap_url = str(preview_session.get("bootstrap_url") or "")
            if not bootstrap_url:
                raise RuntimeError("signed bootstrap_url missing")
            from omnia_api.services.max_functional_gate import run_max_functional_gate

            max_functional = await run_max_functional_gate(
                bootstrap_url,
                project_id=project_id,
                require_persistence=max_require_persistence,
                planned_flow=max_planned_flow,
            )
            checks.extend(max_functional.checks)
        except Exception as exc:
            checks.append(
                _infra_check("max_signed_functional", f"probe failed: {type(exc).__name__}")
            )

    return summarize(checks)
