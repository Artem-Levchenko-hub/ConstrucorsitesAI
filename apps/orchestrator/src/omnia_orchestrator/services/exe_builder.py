"""Run one ephemeral omnia-exe-builder container to produce a Windows Setup.exe.

For v1, the run uses egress only when requirements exist (deps install). Container is
non-root (image USER builder), --rm, mem/cpu-capped, timed out. Harden to a strict
two-phase (deps → no-net build) split in a follow-up if abuse appears.
"""
from __future__ import annotations
import base64, json, pathlib

import docker.errors  # type: ignore[import-untyped]
import requests.exceptions

from ..core.docker_client import _get_client
from ..core.errors import OrchestratorError
from ..schemas.build_exe import BuildExeRequest, BuildExeResult

IMAGE = "omnia-exe-builder:latest"
TIMEOUT_S = 300
MEM_LIMIT = "2g"
NANO_CPUS = 2_000_000_000  # 2 CPUs
PIDS_LIMIT = 256


def _run_container(workdir: pathlib.Path, *, egress: bool) -> int:
    client = _get_client()
    try:
        container = client.containers.run(
            IMAGE,
            detach=True,
            network_disabled=not egress,
            mem_limit=MEM_LIMIT,
            nano_cpus=NANO_CPUS,
            pids_limit=PIDS_LIMIT,       # cap forks — the build runs untrusted user code
            cap_drop=["ALL"],            # no Linux capabilities; same bar as user dev-containers
            volumes={str(workdir): {"bind": "/work", "mode": "rw"}},
        )
    except docker.errors.ImageNotFound as exc:
        raise OrchestratorError(
            code="image_not_found",
            message=f"{IMAGE} is not built on this host",
            status_code=409,
        ) from exc
    try:
        result = container.wait(timeout=TIMEOUT_S)
        return int(result.get("StatusCode", 1))
    except requests.exceptions.ReadTimeout as exc:
        # docker SDK raises ReadTimeout (not TimeoutError) when wait() exceeds the deadline.
        raise OrchestratorError(
            code="build_timeout",
            message=f"build exceeded {TIMEOUT_S}s",
            status_code=504,
        ) from exc
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def run_exe_build(req: BuildExeRequest, *, work_root: pathlib.Path) -> BuildExeResult:
    src = work_root / "src"; out = work_root / "out"
    src.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    src_resolved = src.resolve()
    for path, content in req.files.items():
        f = src / path
        # Guard against path traversal: a malicious/buggy files key (`../x`, absolute)
        # must never write outside src/. Mirrors docker_client.write_files.
        try:
            f.resolve().relative_to(src_resolved)
        except ValueError as exc:
            raise OrchestratorError(
                code="invalid_path",
                message=f"path escapes project root: {path!r}",
                status_code=400,
            ) from exc
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
