import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def checker():
    path = Path(__file__).resolve().parents[3] / "infra/release/generation-publication-drain.py"
    spec = importlib.util.spec_from_file_location("publication_drain_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("phase", ["queued", "building", "swapping", "unknown"])
def test_database_zero_does_not_allow_active_or_unknown_publication(checker, tmp_path, phase):
    root = tmp_path / "cell-publications"
    project = root / "project"
    project.mkdir(parents=True)
    (project / "publication.json").write_text(
        json.dumps(
            {
                "project_id": "project",
                "history": [{"response": {"phase": phase}}],
            }
        )
    )
    with pytest.raises(RuntimeError, match="not quiescent"):
        checker.require_publications_drained(root)


def test_terminal_journals_and_empty_host_are_readonly(checker, tmp_path):
    root = tmp_path / "cell-publications"
    assert checker.require_publications_drained(root) == 0
    project = root / "project"
    project.mkdir(parents=True)
    path = project / "publication.json"
    original = json.dumps(
        {
            "project_id": "project",
            "history": [
                {"response": {"phase": "queued"}},
                {"response": {"phase": "done"}},
            ],
        }
    )
    path.write_text(original)
    assert checker.require_publications_drained(root) == 1
    assert path.read_text() == original
    path.write_text("malformed")
    with pytest.raises(RuntimeError, match="not quiescent"):
        checker.require_publications_drained(root)


def test_canonical_restart_gate_checks_both_controllers_after_database_gate():
    source = (Path(__file__).resolve().parents[3] / "infra/release/deploy-prod.sh").read_text(
        encoding="utf-8"
    )
    assert "publication_drain core" in source and "publication_drain commerce" in source
    assert source.count("quiescence_gate") >= 3


@pytest.mark.parametrize("option", ["--gateway", "--bootstrap-admission"])
def test_web_only_rejects_controller_mutation_before_any_ssh(option):
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    executable = str(bash) if bash.exists() else shutil.which("bash")
    if executable is None:
        pytest.skip("bash is required")
    script = Path(__file__).resolve().parents[3] / "infra/release/deploy-prod.sh"
    result = subprocess.run(
        [executable, str(script), "a" * 40, "--web-only", option],
        capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert b"--web-only" in result.stderr
    assert not result.stdout
