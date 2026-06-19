from __future__ import annotations
import tempfile, pathlib
from fastapi import APIRouter
from ..schemas.build_exe import BuildExeRequest, BuildExeResult
from ..services.exe_builder import run_exe_build

router = APIRouter(tags=["build-exe"])


@router.post("/build-exe", response_model=BuildExeResult)
def build_exe(req: BuildExeRequest) -> BuildExeResult:
    with tempfile.TemporaryDirectory(prefix="omnia-exe-") as d:
        return run_exe_build(req, work_root=pathlib.Path(d))
