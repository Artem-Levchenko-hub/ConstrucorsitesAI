"""Trusted MAX harness exemptions never become candidate-source authority."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services import max_project_kit, sast_gate
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_finalization, agent_verification
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.max_project_kit import max_sast_source_view

PATHS = ("tests/catalog-config.test.mjs", "tests/database-compatibility.test.mjs")


@pytest.fixture
def canonical_harnesses():
    # Read actual trusted fixture bytes, preserving line endings exactly.
    return {
        path: next(p for p in max_project_kit._template_candidates(path) if p.is_file())
        .read_bytes().decode("utf-8")
        for path in PATHS
    }


def test_only_exact_max_harnesses_are_exempt_and_input_is_unchanged(canonical_harnesses):
    source = {**canonical_harnesses, "src/app/page.tsx": "export default () => null;"}
    before = dict(source)
    assert {f.cwe for f in sast_gate.scan_sast(source)} == {"CWE-89", "CWE-94"}
    view = max_sast_source_view(source, is_max=True)
    assert view == {"src/app/page.tsx": source["src/app/page.tsx"]}
    assert not sast_gate.scan_sast(view)
    assert source == before
    assert not set(PATHS) & max_project_kit.MAX_SECURITY_LOCKED_FILES
    assert not set(PATHS) & max_project_kit.MAX_MODEL_LOCKED_FILES


def test_non_max_and_generic_sast_keep_the_same_canonical_findings(canonical_harnesses):
    assert max_sast_source_view(canonical_harnesses, is_max=False) == canonical_harnesses
    assert len(sast_gate.scan_sast(canonical_harnesses)) == 2


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("suffix", [
    "\nconst value = eval(request.body);\n",
    '\nconst password = "genuine-sensitive-value";\n',
    "\n// different candidate, not the trusted fixture\n",
])
def test_changed_same_path_is_scanned_in_full(canonical_harnesses, path, suffix):
    source = {path: canonical_harnesses[path] + suffix}
    assert max_sast_source_view(source, is_max=True) == source
    findings = sast_gate.scan_sast(max_sast_source_view(source, is_max=True))
    assert findings
    if "password" in suffix:
        assert any(f.cwe == "CWE-798" for f in findings)


@pytest.mark.parametrize("original", PATHS)
@pytest.mark.parametrize("destination", ["tests/renamed.test.mjs", "src/app/copied.ts"])
def test_copied_or_moved_canonical_harness_has_no_exemption(
    canonical_harnesses, original, destination,
):
    source = {destination: canonical_harnesses[original]}
    assert max_sast_source_view(source, is_max=True) == source
    assert sast_gate.scan_sast(max_sast_source_view(source, is_max=True))


@pytest.mark.parametrize("path", PATHS)
def test_no_line_ending_normalization_grants_trust(canonical_harnesses, path):
    original = canonical_harnesses[path]
    changed = (
        original.replace("\r\n", "\n") if "\r\n" in original
        else original.replace("\n", "\r\n")
    )
    assert changed != original
    assert max_sast_source_view({path: changed}, is_max=True) == {path: changed}


@pytest.mark.parametrize("failure", ["missing", "directory", "invalid_utf8", "unreadable"])
def test_unavailable_trusted_fixture_retains_candidate_for_scan(
    monkeypatch, tmp_path, canonical_harnesses, failure,
):
    trusted = tmp_path / "trusted"
    if failure == "directory":
        trusted.mkdir()
    elif failure == "invalid_utf8":
        trusted.write_bytes(b"\xff")
    elif failure == "unreadable":
        trusted.write_bytes(b"not readable")
        real_read = Path.read_bytes

        def read_bytes(path):
            if path == trusted:
                raise PermissionError("trusted fixture unreadable")
            return real_read(path)

        monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(max_project_kit, "_template_candidates", lambda _: (trusted,))
    view = max_sast_source_view(canonical_harnesses, is_max=True)
    assert view == canonical_harnesses
    assert len(sast_gate.scan_sast(view)) == 2


def test_unreadable_primary_template_cannot_fall_back_to_another_copy(
    monkeypatch, tmp_path, canonical_harnesses,
):
    first, second = tmp_path / "unreadable", tmp_path / "fallback"
    first.mkdir()
    second.write_bytes(canonical_harnesses[PATHS[0]].encode("utf-8"))
    monkeypatch.setattr(max_project_kit, "_template_candidates", lambda _: (first, second))
    source = {PATHS[0]: canonical_harnesses[PATHS[0]]}
    assert max_sast_source_view(source, is_max=True) == source


@pytest.mark.parametrize("template,is_max", [("max_miniapp", True), ("nextjs", False)])
async def test_both_verification_sites_scope_only_sast_and_preserve_backend_view(
    monkeypatch, canonical_harnesses, template, is_max,
):
    from yleum_api.services import max_generation_contract

    source = {**canonical_harnesses, "src/old.ts": "const unsafe = eval(request.body);"}
    backend_views, sast_views = [], []
    real_scan = sast_gate.check_sast

    def backend_view(files):
        backend_views.append(dict(files))
        return []

    def scan(files):
        sast_views.append(dict(files))
        return real_scan(files)

    monkeypatch.setattr(max_generation_contract, "unsafe_max_backend_paths", backend_view)
    monkeypatch.setattr(sast_gate, "check_sast", scan)
    monkeypatch.setattr(agent_verification, "get_settings", lambda: SimpleNamespace(
        use_agent_gate_feedback=True, use_native_agent=False, agent_gate_max_attempts=0,
        use_sast_gate=True, sast_gate_blocking=False,
    ))
    await agent_verification.check_backend_and_normalize_css(
        _active_max_locked_files=frozenset(),
        _agent_res=AgentResult(done=True, summary="candidate", files={}, steps=1),
        _is_edit=False, _max_seed_files={}, baseline=SourceBaseline(None, None, source),
        files={}, ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=SimpleNamespace(template=template, slug="owned-test"),
        runtime=GenerationRuntime(), plan=SimpleNamespace(), operations=SimpleNamespace(),
    )
    assert len(backend_views) == len(sast_views) == 2
    assert all(view == source for view in backend_views)
    expected = {k: v for k, v in source.items() if not is_max or k not in PATHS}
    assert all(view == expected for view in sast_views)
    # This finding is inherited from the full baseline, not a new write.
    assert all(any(f.path == "src/old.ts" for f in real_scan(v).findings) for v in sast_views)


@pytest.mark.parametrize("malicious", [False, True])
async def test_native_repair_scans_full_current_snapshot_with_same_narrow_exemption(
    monkeypatch, canonical_harnesses, malicious,
):
    from yleum_api.services import agent_native
    from yleum_api.services.generation import agent_runtime

    full = {**canonical_harnesses, "src/app/page.tsx": "export default () => null;"}
    if malicious:
        full["src/old.ts"] = "const unsafe = eval(request.body);"
    snapshot = AsyncMock(return_value=full)
    provider = AsyncMock(return_value=AgentResult(
        done=True, summary="repaired", files={"src/app/page.tsx": full["src/app/page.tsx"]},
        steps=1,
    ))
    monkeypatch.setattr(agent_native, "run_native_build", provider)
    monkeypatch.setattr(agent_finalization, "with_managed_source_context", lambda task, _: task)
    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", AsyncMock(
        side_effect=lambda _runtime, _ids, execute, _completion: (execute, None),
    ))
    monkeypatch.setattr(agent_finalization, "get_settings", lambda: SimpleNamespace(
        use_sast_gate=True, sast_gate_blocking=True,
    ))
    runtime = GenerationRuntime(handle=SimpleNamespace(snapshot_files=snapshot),
                                coordinator=SimpleNamespace())
    kwargs = dict(
        task="repair", prompt_text="request", baseline={"src/app/page.tsx": "before"},
        runtime=runtime, ids=GenerationIds(*(uuid4() for _ in range(5))),
        plan=SimpleNamespace(model="fake", stack_guide="", skills=None, steps=1),
        operations=SimpleNamespace(execute=AsyncMock(), emit=AsyncMock()),
        is_free=True, shell_enabled=False, edit_deadline=None,
    )
    if malicious:
        with pytest.raises(RuntimeError, match="repaired source failed security checks"):
            await agent_finalization.run_native_source_repair(**kwargs)
    else:
        result = await agent_finalization.run_native_source_repair(**kwargs)
        assert result.done
    assert snapshot.await_count == provider.await_count == 1
