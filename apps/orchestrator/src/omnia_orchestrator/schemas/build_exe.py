from __future__ import annotations
from pydantic import BaseModel


class BuildExeRequest(BaseModel):
    name: str
    files: dict[str, str]
    pyinstaller_args: list[str]
    installer_nsi: str
    requirements: str | None = None


class BuildExeResult(BaseModel):
    ok: bool
    log: str
    setup_b64: str | None = None
    exe_b64: str | None = None
