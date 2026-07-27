from __future__ import annotations

import pathlib
import shutil
import tempfile
from typing import Annotated

from fastapi import APIRouter, Header

from ..core.internal_auth import verify_internal_token
from ..schemas.build_exe import BuildExeRequest, BuildExeResult
from ..services.exe_builder import run_exe_build

router = APIRouter(tags=["build-exe"])

# The build workdir is bind-mounted into the builder container, so it MUST live on a
# path the Docker daemon can see. The orchestrator runs as a systemd unit with
# PrivateTmp=yes — its /tmp is a private namespace invisible to the daemon, so a
# tempfile under /tmp would mount as an empty /work. Build under the shared runtime
# root (systemd ReadWritePaths=/opt/omnia-runtime) instead.
_WORK_BASE = pathlib.Path("/opt/omnia-runtime/exe-builds")


@router.post("/build-exe", response_model=BuildExeResult)
def build_exe(
    req: BuildExeRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> BuildExeResult:
    verify_internal_token(x_internal_token)
    _WORK_BASE.mkdir(parents=True, exist_ok=True)
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="b-", dir=str(_WORK_BASE)))
    try:
        return run_exe_build(req, work_root=workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
