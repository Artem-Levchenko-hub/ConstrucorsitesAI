"""Runs INSIDE the Wine container. Reads /work/build_spec.json (PyInstaller args +
NSIS) plus the project source in /work/src, produces /work/out/{Name.exe, Name-Setup.exe}
and /work/out/build.log. The two network phases are enforced by the CALLER (docker), not here.
"""
import json, subprocess, sys, pathlib

WORK = pathlib.Path("/work"); SRC = WORK / "src"; OUT = WORK / "out"
OUT.mkdir(parents=True, exist_ok=True)
log = (OUT / "build.log").open("w", encoding="utf-8")

def run(cmd, **kw):
    log.write(f"$ {' '.join(cmd)}\n"); log.flush()
    p = subprocess.run(cmd, cwd=str(SRC), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, **kw)
    log.write(p.stdout or ""); log.flush()
    return p.returncode

spec = json.loads((WORK / "build_spec.json").read_text(encoding="utf-8"))
if spec.get("requirements"):
    (SRC / "requirements.txt").write_text(spec["requirements"], encoding="utf-8")
    if run(["wine", "python", "-m", "pip", "install", "-r", "requirements.txt"]):
        log.write("PIP_FAILED\n"); sys.exit(2)
# `python -m PyInstaller` (not the `pyinstaller` shim) so it resolves regardless of
# whether the Wine Scripts dir is on PATH. args[0] is the literal "pyinstaller" → drop it.
if run(["wine", "python", "-m", "PyInstaller", *spec["pyinstaller_args"][1:]]):
    log.write("PYINSTALLER_FAILED\n"); sys.exit(3)
(SRC / "installer.nsi").write_text(spec["installer_nsi"], encoding="utf-8")
if run(["makensis", "installer.nsi"]):
    log.write("NSIS_FAILED\n"); sys.exit(4)
import shutil
name = spec["name"]
shutil.copy(SRC / "dist" / f"{name}.exe", OUT / f"{name}.exe")
shutil.copy(SRC / f"{name}-Setup.exe", OUT / f"{name}-Setup.exe")
log.write("OK\n"); sys.exit(0)
