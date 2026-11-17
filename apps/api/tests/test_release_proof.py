from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import release_proof
from yleum_api.services.functional_gate import Check, FunctionalVerdict
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.max_runtime_probe import MaxRuntimeProbe
from yleum_api.services.orchestrator_client import ProjectCellPreviewSession


async def test_release_proof_uses_only_selected_project_cell_runtime(monkeypatch) -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    calls: list[str] = []
    preview = ProjectCellPreviewSession(
        workspace_id=workspace_id,
        preview_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
        ),
        bootstrap_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru/"
            "api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
        ),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )

    async def execute(action):
        calls.append(action.name)
        if action.name == "build":
            return {"ok": True, "detail": "cell typecheck clean"}
        assert action.name == "runtime_check"
        return {"ok": True, "detail": "cell runtime HTTP ok"}

    async def create_preview_session():
        calls.append("preview_session")
        return preview

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy orchestrator path must not be used")

    async def cell_probe(session, *, path, fallback_paths=(), **proof_options):
        assert session is preview
        assert path == "/"
        assert fallback_paths == ()
        assert set(proof_options.values()) == {None}
        calls.append("max_cell_probe")
        return MaxRuntimeProbe(True, "cell protected data verified")

    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "yleum_api.services.max_runtime_probe.probe_max_cell_runtime", cell_probe
    )

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_max_data=True,
        project_cell_handle=SimpleNamespace(
            execute=execute,
            create_preview_session=create_preview_session,
        ),
    )

    assert verdict.passed
    assert calls == ["build", "runtime_check", "preview_session", "max_cell_probe"]


async def test_release_proof_uses_fallback_probe_route_when_home_page_is_missing(
    monkeypatch,
) -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    calls: list[str] = []
    preview = ProjectCellPreviewSession(
        workspace_id=workspace_id,
        preview_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
        ),
        bootstrap_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru/"
            "api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
        ),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )

    async def execute(action):
        calls.append(action.name)
        if action.name == "build":
            return {"ok": True, "detail": "cell typecheck clean"}
        assert action.name == "runtime_check"
        assert action.args == {"path": "/"}
        return {"ok": True, "detail": "cell runtime HTTP ok via /support"}

    async def create_preview_session():
        calls.append("preview_session")
        return preview

    async def snapshot_files():
        return {
            ".omnia/cell.json": '{"version":1}',
            "src/app/support/page.tsx": "export default function Support(){return null}\n",
        }

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy orchestrator path must not be used")

    async def cell_probe(session, *, path, fallback_paths=(), **proof_options):
        assert session is preview
        assert path == "/"
        assert fallback_paths == ("/support",)
        assert set(proof_options.values()) == {None}
        calls.append("max_cell_probe")
        return MaxRuntimeProbe(True, "cell protected data verified via /support")

    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "yleum_api.services.max_runtime_probe.probe_max_cell_runtime", cell_probe
    )

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_max_data=True,
        project_cell_handle=SimpleNamespace(
            execute=execute,
            create_preview_session=create_preview_session,
            snapshot_files=snapshot_files,
        ),
    )

    assert verdict.passed
    assert calls == ["build", "runtime_check", "preview_session", "max_cell_probe"]


@pytest.mark.parametrize("declares_readiness", [True, False])
@pytest.mark.parametrize("portable", [True, False])
async def test_proofless_release_reads_only_declared_product_readiness(
    monkeypatch: pytest.MonkeyPatch, declares_readiness: bool, portable: bool,
) -> None:
    origin = "https://audit-dev.preview.example"
    project_id = uuid4()
    preview = SimpleNamespace(
        preview_url=origin,
        bootstrap_url=f"{origin}/api/omnia/preview-session",
    )
    requested = []
    client_type = httpx.AsyncClient

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        requested.append(request.url.path)
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(200, headers={"Set-Cookie": "preview=fixture; Path=/; Secure"})
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") != "preview=fixture":
                return httpx.Response(401)
            return httpx.Response(200, json={
                "project_id": str(project_id), "user_id": "preview", "epoch": 7,
            })
        assert request.headers.get("cookie") == "preview=fixture"
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        if request.url.path == "/":
            return httpx.Response(200)
        assert request.url.path == "/api/yleum-readiness"
        return httpx.Response(500, json={"error": "synthetic DB query failure"})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client_type(
            transport=httpx.MockTransport(serve), **kwargs,
        ),
    )
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    async def execute(action):
        assert action.name in {"build", "runtime_check"}
        return {"ok": True}

    async def create_preview_session():
        return preview

    async def snapshot_files():
        product_handler = "export async function GET() { return await readProductData(); }"
        files = {
            "src/app/page.tsx": "export default function Page() { return null; }",
            "src/app/api/custom/route.ts": product_handler,
        }
        if declares_readiness:
            files["src/app/api/yleum-readiness/route.ts"] = product_handler
        return files

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        require_max_data=True,
        project_cell_handle=SimpleNamespace(
            execute=execute,
            create_preview_session=create_preview_session,
            snapshot_files=snapshot_files,
            is_portable=lambda: portable,
            control_fencing_epoch=7,
            restoration_adaptation_workspace=None,
        ),
    )

    assert verdict.passed is (not declares_readiness)
    expected_paths = ["/api/omnia/preview-session", "/", "/api/omnia/actions"]
    if portable and declares_readiness:
        identity_path = "/__omnia/identity"
        expected_paths = [identity_path, identity_path, *expected_paths, identity_path]
    if declares_readiness:
        expected_paths.append("/api/yleum-readiness")
        assert Check(
            "max_data_plane", False,
            "signed_business_route_failed: GET /api/yleum-readiness (HTTP 500)",
        ) in verdict.checks
        assert "synthetic DB query failure" not in verdict.summary
    assert requested == expected_paths


async def test_release_proof_preserves_cell_runtime_failure_detail(monkeypatch) -> None:
    project_id = uuid4()

    async def execute(action):
        if action.name == "build":
            return {"ok": True, "detail": "cell typecheck clean"}
        return {"ok": False, "detail": "cell runtime returned HTTP 503"}

    async def create_preview_session():
        raise AssertionError("optional preview must not be created")

    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        project_cell_handle=SimpleNamespace(
            execute=execute,
            create_preview_session=create_preview_session,
        ),
    )

    assert verdict.passed is False
    assert verdict.checks[1] == Check(
        "runtime", False, "cell runtime returned HTTP 503"
    )


@pytest.mark.parametrize("runtime_ok", [True, False])
async def test_release_reuses_candidate_runtime_and_still_checks_security(monkeypatch, runtime_ok):
    proof_key = "a" * 64
    green = SimpleNamespace(outcome="green", redacted_detail="green")
    workspace_id = uuid4()
    project_id = uuid4()
    preview = ProjectCellPreviewSession(
        workspace_id=workspace_id,
        preview_url=f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru",
        bootstrap_url=(
            f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru/"
            "api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
        ),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    bundle = ProofBundle(
        identity=SimpleNamespace(proof_key=proof_key, fencing_epoch=7),
        full_build=green,
        runtime=green if runtime_ok else SimpleNamespace(
            outcome="red", redacted_detail="candidate runtime failed",
        ),
        release=green,
    )
    calls: list[str] = []

    async def create_preview_session():
        calls.append("preview_session")
        return preview

    async def snapshot_files():
        return {"src/app/page.tsx": "export default function Page() { return null }"}

    async def runtime_probe(*_args, **_kwargs) -> MaxRuntimeProbe:
        calls.append("duplicated_runtime")
        return MaxRuntimeProbe(False, "runtime must only run in the coordinator")

    async def security(
        base_url,
        *,
        bootstrap_url,
        require_embedded_framing,
        framing_policy,
    ):
        from yleum_api.services.security_gate import owner_preview_framing_policy

        assert base_url == preview.preview_url
        assert bootstrap_url == preview.bootstrap_url
        assert require_embedded_framing is True
        assert framing_policy == owner_preview_framing_policy()
        calls.append("same_session_security")
        return FunctionalVerdict(True, [Check("embedded security", True, "green")], "green")

    # MAX promotion security is mandatory; the optional legacy flag cannot skip it.
    settings = get_settings().model_copy(update={"use_security_gate": False})
    monkeypatch.setattr(release_proof, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "yleum_api.services.max_runtime_probe.probe_max_cell_runtime",
        runtime_probe,
    )
    monkeypatch.setattr("yleum_api.services.security_gate.run_security_gate", security)

    verdict = await release_proof.run_release_proof(
        project_id,
        "max-app",
        proof=bundle,
        require_max_data=True,
        project_cell_handle=SimpleNamespace(
            create_preview_session=create_preview_session,
            snapshot_files=snapshot_files,
        ),
    )

    assert calls == ["preview_session", "same_session_security"]
    assert verdict.passed is runtime_ok
    if not runtime_ok:
        assert Check("runtime", False, "candidate runtime failed") in verdict.checks


def test_legacy_release_receipt_cannot_mint_current_permit() -> None:
    from yleum_api.services.promotion_permit import PromotionPermitError, issue_promotion_permit

    identity = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        generation_run_id=uuid4(),
        fencing_epoch=7,
        proof_key="1" * 64,
        workspace_revision="2" * 64,
    )
    build = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="build/sha256/" + "3" * 64,
    )
    runtime = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="verification/sha256/" + "4" * 64,
    )
    legacy_release = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="verification/sha256/" + "5" * 64,
    )

    with pytest.raises(PromotionPermitError) as raised:
        issue_promotion_permit(
            ProofBundle(
                identity=identity,
                full_build=build,
                runtime=runtime,
                release=legacy_release,
            )
        )

    assert raised.value.code == "PROMOTION_EVIDENCE_STALE"


async def test_current_release_receipt_is_accepted_by_actual_prepare_candidate(
    monkeypatch,
) -> None:
    from yleum_api.services import project_cell_candidates
    from yleum_api.services.promotion_permit import release_receipt_ref

    workspace_id = uuid4()
    run_id = uuid4()
    added = []

    async def noop(*_args, **_kwargs):
        return None

    async def no_candidate(*_args, **_kwargs):
        return None

    class Session:
        def add(self, row):
            added.append(row)

        async def flush(self):
            return None

    monkeypatch.setattr(project_cell_candidates, "_lock_workspace", noop)
    monkeypatch.setattr(project_cell_candidates, "_locked_workspace", noop)
    monkeypatch.setattr(project_cell_candidates, "_require_workspace_lease", lambda *_: None)
    monkeypatch.setattr(project_cell_candidates, "_require_generation_status", noop)
    monkeypatch.setattr(project_cell_candidates, "_accepted_candidate_id", no_candidate)
    monkeypatch.setattr(project_cell_candidates, "_matching_candidate", no_candidate)

    verification_ref = release_receipt_ref(
        artifact_digest="3" * 64,
        receipt_digest="4" * 64,
    )
    candidate = await project_cell_candidates.prepare_candidate(
        Session(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
        generation_run_id=run_id,
        fencing_epoch=7,
        source_revision="1" * 64,
        migration_digest="2" * 64,
        database_backup_ref="database-backup/sha256/" + "5" * 64,
        build_ref="build/sha256/" + "3" * 64,
        verification_ref=verification_ref,
    )

    assert candidate.verification_ref == verification_ref
    assert added == [candidate]
