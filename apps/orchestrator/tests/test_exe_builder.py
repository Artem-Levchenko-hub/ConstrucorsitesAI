from __future__ import annotations

import base64

import pytest

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.routers import build_exe as build_exe_router
from omnia_orchestrator.schemas.build_exe import BuildExeRequest, BuildExeResult
from omnia_orchestrator.services.exe_builder import run_exe_build


def test_run_exe_build_collects_artifacts(monkeypatch, tmp_path) -> None:
    def fake_run_container(workdir, *, egress):
        out = workdir / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "build.log").write_text("OK\n")
        (out / "Snake.exe").write_bytes(b"MZ\x90\x00exe")
        (out / "Snake-Setup.exe").write_bytes(b"MZ\x90\x00setup")
        return 0

    monkeypatch.setattr(
        "omnia_orchestrator.services.exe_builder._run_container",
        fake_run_container,
    )
    req = BuildExeRequest(
        name="Snake",
        files={"app.py": "print(1)"},
        pyinstaller_args=["pyinstaller", "--onefile", "app.py"],
        installer_nsi='Name "Snake"',
    )
    res = run_exe_build(req, work_root=tmp_path)
    assert res.ok is True
    assert base64.b64decode(res.setup_b64) == b"MZ\x90\x00setup"
    assert base64.b64decode(res.exe_b64) == b"MZ\x90\x00exe"


def test_run_exe_build_failure_returns_log(monkeypatch, tmp_path) -> None:
    def fake_fail(workdir, *, egress):
        out = workdir / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "build.log").write_text("PYINSTALLER_FAILED\n")
        return 3

    monkeypatch.setattr(
        "omnia_orchestrator.services.exe_builder._run_container",
        fake_fail,
    )
    req = BuildExeRequest(
        name="Snake",
        files={"app.py": "x"},
        pyinstaller_args=["pyinstaller", "app.py"],
        installer_nsi="x",
    )
    res = run_exe_build(req, work_root=tmp_path)
    assert res.ok is False
    assert "PYINSTALLER_FAILED" in res.log
    assert res.setup_b64 is None


def test_run_exe_build_rejects_path_traversal(tmp_path) -> None:
    # A files key that escapes src/ must be refused before any container runs.
    req = BuildExeRequest(
        name="X",
        files={"../escape.py": "evil"},
        pyinstaller_args=["pyinstaller", "x"],
        installer_nsi="x",
    )
    with pytest.raises(OrchestratorError):
        run_exe_build(req, work_root=tmp_path)
    assert not (tmp_path / "escape.py").exists()  # nothing written outside src/


def test_build_exe_route_requires_internal_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://omnia:omnia@localhost:5432/omnia",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "route-test-internal-token")
    get_settings.cache_clear()
    monkeypatch.setattr(build_exe_router, "_WORK_BASE", tmp_path)

    called = False

    def fake_build(*_args, **_kwargs):
        nonlocal called
        called = True
        return BuildExeResult(ok=True, log="")

    monkeypatch.setattr(build_exe_router, "run_exe_build", fake_build)
    req = BuildExeRequest(
        name="Safe",
        files={"app.py": "print(1)"},
        pyinstaller_args=["pyinstaller", "app.py"],
        installer_nsi="x",
    )

    with pytest.raises(OrchestratorError) as exc_info:
        build_exe_router.build_exe(req, None)
    assert exc_info.value.code == "unauthorized"
    assert called is False

    result = build_exe_router.build_exe(req, "route-test-internal-token")
    assert result.ok is True
    assert called is True
    get_settings.cache_clear()
