"""Universal release checks for the exact live tree about to be attested."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from yleum_api.core.config import get_settings
from yleum_api.services import orchestrator_client
from yleum_api.services.agent_builder import Action
from yleum_api.services.functional_gate import Check, FunctionalVerdict, summarize

if TYPE_CHECKING:
    from yleum_api.services.max_finalization import ProofBundle
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession
    from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle


async def run_release_proof(
    project_id: UUID,
    project_slug: str,
    *,
    proof: ProofBundle | None = None,
    require_max_data: bool = False,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
) -> FunctionalVerdict:
    """Prove that the live project typechecks, serves HTTP and has safe transport.

    Every failure becomes a failed check instead of escaping. Callers can therefore
    persist an honest negative attestation and keep production deploy fail-closed.
    """
    settings = get_settings()
    checks: list[Check] = []
    if proof is not None:
        from yleum_api.services.max_finalization import proof_bundle_verdict

        checks.extend(proof_bundle_verdict(proof, require_max_data=False).checks)
    else:
        try:
            if project_cell_handle is None:
                typecheck = await orchestrator_client.agent_build(project_id, project_slug)
            else:
                typecheck = await project_cell_handle.execute(Action(name="build", args={}))
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
    cell_preview: ProjectCellPreviewSession | None = None
    if proof is not None and project_cell_handle is not None and (
        require_max_data or settings.use_security_gate
    ):
        try:
            cell_preview = await project_cell_handle.create_preview_session()
            base_url = cell_preview.preview_url
        except Exception as exc:
            checks.append(Check("signed_preview_session", False, f"probe failed: {exc!r}"))
    if proof is None:
        try:
            if project_cell_handle is None:
                runtime = await orchestrator_client.runtime_status(
                    project_id,
                    slug=project_slug,
                    path="/",
                )
            else:
                runtime = await project_cell_handle.execute(
                    Action(name="runtime_check", args={"path": "/"})
                )
            checks.append(
                Check(
                    "runtime",
                    bool(runtime.get("ok", False)),
                    str(
                        runtime.get("detail")
                        or runtime.get("error")
                        or runtime.get("status_code")
                        or "HTTP ok"
                    )[:240],
                )
            )
            if project_cell_handle is None:
                status_payload = await orchestrator_client.get_status(project_id)
                raw_base_url = (
                    status_payload.get("dev_url") if isinstance(status_payload, dict) else None
                )
                base_url = str(raw_base_url) if raw_base_url else None
            elif require_max_data or settings.use_security_gate:
                cell_preview = await project_cell_handle.create_preview_session()
                base_url = cell_preview.preview_url
        except Exception as exc:
            checks.append(Check("runtime", False, f"probe failed: {exc!r}"))

    if require_max_data:
        try:
            if proof is not None:
                if project_cell_handle is None or cell_preview is None:
                    raise RuntimeError("exact MAX behavior probe unavailable")
                from yleum_api.services.max_runtime_probe import probe_max_cell_runtime
                from yleum_api.services.max_runtime_routes import (
                    resolve_max_runtime_probe_paths,
                )

                probe_path = "/"
                proof_fallback_paths: tuple[str, ...] = ()
                snapshot_files = getattr(project_cell_handle, "snapshot_files", None)
                if callable(snapshot_files):
                    current_files = await snapshot_files()
                    probe_path, resolved_fallback_paths = resolve_max_runtime_probe_paths(
                        current_files,
                        requested_path="/",
                    )
                    proof_fallback_paths = tuple(resolved_fallback_paths)
                max_probe = await probe_max_cell_runtime(
                    cell_preview,
                    path=probe_path,
                    fallback_paths=proof_fallback_paths,
                    portable_project_id=project_id,
                    expected_epoch=proof.identity.fencing_epoch,
                    proof_key=proof.identity.proof_key,
                )
            elif project_cell_handle is None:
                from yleum_api.services.max_runtime_probe import probe_max_runtime

                max_probe = await probe_max_runtime(
                    project_id,
                    project_slug,
                    base_url=base_url,
                )
            else:
                from yleum_api.services.max_runtime_probe import probe_max_cell_runtime
                from yleum_api.services.max_runtime_routes import (
                    resolve_max_runtime_probe_paths,
                )

                if cell_preview is None:
                    cell_preview = await project_cell_handle.create_preview_session()
                probe_path = "/"
                fallback_paths: tuple[str, ...] = ()
                snapshot_files = getattr(project_cell_handle, "snapshot_files", None)
                if callable(snapshot_files):
                    current_files = await snapshot_files()
                    probe_path, resolved_fallback_paths = resolve_max_runtime_probe_paths(
                        current_files,
                        requested_path="/",
                    )
                    fallback_paths = tuple(resolved_fallback_paths)
                max_probe = await probe_max_cell_runtime(
                    cell_preview,
                    path=probe_path,
                    fallback_paths=fallback_paths,
                )
            checks.append(Check("max_data_plane", max_probe.ok, max_probe.detail[:240]))
        except Exception as exc:
            checks.append(Check("max_data_plane", False, f"probe failed: {exc!r}"))

    mandatory_max_security = proof is not None and require_max_data
    if settings.use_security_gate or mandatory_max_security:
        try:
            if project_cell_handle is not None and base_url is None:
                cell_preview = await project_cell_handle.create_preview_session()
                base_url = cell_preview.preview_url
            if not base_url:
                raise RuntimeError("dev_url missing")
            from yleum_api.services import security_gate

            if proof is not None and require_max_data:
                if cell_preview is None:
                    raise RuntimeError("signed preview session missing")
                security = await security_gate.run_security_gate(
                    base_url,
                    bootstrap_url=cell_preview.bootstrap_url,
                    require_embedded_framing=True,
                    framing_policy=security_gate.owner_preview_framing_policy(),
                )
            else:
                security = await security_gate.run_security_gate(base_url)
            checks.extend(Check(check.name, check.ok, check.detail) for check in security.checks)
        except Exception as exc:
            checks.append(Check("transport_security", False, f"probe failed: {exc!r}"))

    return summarize(checks)
