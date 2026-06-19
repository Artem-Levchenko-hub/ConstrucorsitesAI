from __future__ import annotations
import base64
from omnia_orchestrator.services.exe_builder import run_exe_build
from omnia_orchestrator.schemas.build_exe import BuildExeRequest


def test_run_exe_build_collects_artifacts(monkeypatch, tmp_path) -> None:
    def fake_run_container(workdir, *, egress):
        out = workdir / "out"; out.mkdir(parents=True, exist_ok=True)
        (out / "build.log").write_text("OK\n")
        (out / "Snake.exe").write_bytes(b"MZ\x90\x00exe")
        (out / "Snake-Setup.exe").write_bytes(b"MZ\x90\x00setup")
        return 0
    monkeypatch.setattr("omnia_orchestrator.services.exe_builder._run_container", fake_run_container)
    req = BuildExeRequest(name="Snake", files={"app.py": "print(1)"},
                          pyinstaller_args=["pyinstaller", "--onefile", "app.py"],
                          installer_nsi='Name "Snake"')
    res = run_exe_build(req, work_root=tmp_path)
    assert res.ok is True
    assert base64.b64decode(res.setup_b64) == b"MZ\x90\x00setup"
    assert base64.b64decode(res.exe_b64) == b"MZ\x90\x00exe"


def test_run_exe_build_failure_returns_log(monkeypatch, tmp_path) -> None:
    def fake_fail(workdir, *, egress):
        out = workdir / "out"; out.mkdir(parents=True, exist_ok=True)
        (out / "build.log").write_text("PYINSTALLER_FAILED\n")
        return 3
    monkeypatch.setattr("omnia_orchestrator.services.exe_builder._run_container", fake_fail)
    req = BuildExeRequest(name="Snake", files={"app.py": "x"},
                          pyinstaller_args=["pyinstaller", "app.py"], installer_nsi="x")
    res = run_exe_build(req, work_root=tmp_path)
    assert res.ok is False
    assert "PYINSTALLER_FAILED" in res.log
    assert res.setup_b64 is None
