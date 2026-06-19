"""Run one ephemeral omnia-exe-builder container to produce a Windows Setup.exe.

For v1, the run uses egress only when requirements exist (deps install). Container is
non-root (image USER builder), --rm, mem/cpu-capped, timed out. Harden to a strict
two-phase (deps → no-net build) split in a follow-up if abuse appears.
"""
from __future__ import annotations
import base64, json, pathlib
from ..core.docker_client import _get_client
from ..schemas.build_exe import BuildExeRequest, BuildExeResult

IMAGE = "omnia-exe-builder:latest"
TIMEOUT_S = 300
MEM_LIMIT = "2g"
NANO_CPUS = 2_000_000_000  # 2 CPUs


def _run_container(workdir: pathlib.Path, *, egress: bool) -> int:
    client = _get_client()
    container = client.containers.run(
        IMAGE,
        detach=True,
        network_disabled=not egress,
        mem_limit=MEM_LIMIT,
        nano_cpus=NANO_CPUS,
        volumes={str(workdir): {"bind": "/work", "mode": "rw"}},
    )
    try:
        result = container.wait(timeout=TIMEOUT_S)
        return int(result.get("StatusCode", 1))
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def run_exe_build(req: BuildExeRequest, *, work_root: pathlib.Path) -> BuildExeResult:
    src = work_root / "src"; out = work_root / "out"
    src.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    for path, content in req.files.items():
        f = src / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    (work_root / "build_spec.json").write_text(json.dumps({
        "name": req.name, "requirements": req.requirements,
        "pyinstaller_args": req.pyinstaller_args, "installer_nsi": req.installer_nsi,
    }), encoding="utf-8")
    code = _run_container(work_root, egress=bool(req.requirements))
    log = (out / "build.log").read_text(encoding="utf-8") if (out / "build.log").exists() else ""
    setup = out / f"{req.name}-Setup.exe"; exe = out / f"{req.name}.exe"
    ok = code == 0 and setup.exists()
    return BuildExeResult(
        ok=ok, log=log,
        setup_b64=base64.b64encode(setup.read_bytes()).decode() if setup.exists() else None,
        exe_b64=base64.b64encode(exe.read_bytes()).decode() if exe.exists() else None,
    )
