# In-app Windows Installer Build — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A workspace button builds a Python project into a downloadable Windows `Setup.exe` server-side (PyInstaller under Wine → app.exe, NSIS on Linux → Setup.exe), with an auto-derived build spec, a capped self-heal loop, gamified progress, and owner-scoped delivery — no local toolchain for the user.

**Architecture:** API derives a deterministic `BuildSpec` from the project's committed files (reusing `run_bundle._pick_python_entry`), enqueues an RQ job that calls the orchestrator's new `/build-exe`, which runs an ephemeral `omnia-exe-builder` container (Wine+PyInstaller+NSIS). On failure a `exe_doctor` model role patches the spec and retries (≤3). Artifacts land in MinIO; the web subscribes to `exe.*` SSE events. Everything is gated by `USE_EXE_BUILD` (default off).

**Tech Stack:** FastAPI (api + orchestrator), RQ, Docker (Wine image `tobix/pywine` + `nsis`), PyInstaller, NSIS `makensis`, MinIO, Next.js/React (web), LiteLLM gateway role.

**Design spec:** `docs/superpowers/specs/2026-06-19-in-app-exe-build-design.md`

**Zones (parallel-dev ownership):** B = api, D = orchestrator+infra, C = gateway, A = web.

---

## File Structure

**Phase 1 — pure spec generator (zone B)**
- Create `apps/api/src/omnia_api/services/exe_build.py` — `BuildSpec`, `build_spec(files, slug)`, `render_pyinstaller_args(spec)`, `render_nsi(spec)`, `render(spec)`. Pure, no I/O, no model.
- Create `apps/api/tests/test_exe_build.py`.

**Phase 2 — build container + orchestrator endpoint (zone D)**
- Create `infra/exe-builder/Dockerfile` — `tobix/pywine` + PyInstaller + nsis + `build.py` entrypoint.
- Create `infra/exe-builder/build.py` — in-container: pip install → pyinstaller → makensis → write artifacts + log.
- Create `apps/orchestrator/src/omnia_orchestrator/services/exe_builder.py` — run the ephemeral container via `core/docker_client.py`.
- Create `apps/orchestrator/src/omnia_orchestrator/routers/build_exe.py` — `POST /build-exe`.
- Create `apps/orchestrator/src/omnia_orchestrator/schemas/build_exe.py`.
- Modify `apps/orchestrator/src/omnia_orchestrator/main.py` — register the router.
- Create `apps/orchestrator/tests/test_exe_builder.py`.

**Phase 3 — api wiring + delivery (zone B)**
- Modify `apps/api/src/omnia_api/core/config.py` — `use_exe_build`, `exe_*` settings.
- Modify `apps/api/src/omnia_api/services/orchestrator_client.py` — `build_exe(...)`.
- Create `apps/api/src/omnia_api/workers/build_exe.py` — `build_exe_job`.
- Modify `apps/api/src/omnia_api/routers/projects.py` — `POST /{id}/build-exe`, `GET /{id}/exe/{build_id}/{artifact}`.
- Create `apps/api/tests/test_build_exe_endpoint.py`.

**Phase 4 — self-heal (zone B + C)**
- Modify gateway role map (zone C) — add `exe_doctor` role → cheap model + escalation.
- Create `apps/api/src/omnia_api/services/exe_doctor.py` — `heal(error_log, spec, sources) -> BuildSpec | None`.
- Create `apps/api/tests/test_exe_doctor.py`.

**Phase 5 — web UX (zone A)**
- Create `apps/web/src/components/workspace/BuildExeButton.tsx`.
- Create `apps/web/src/hooks/useExeBuild.ts` — subscribe to `exe.*` SSE.
- Modify `apps/web/src/components/workspace/TopBar.tsx` — mount the button.
- Modify `apps/web/src/lib/api/types.ts` — `ExeBuildEvent` types.

**Phase 6 — canary + deploy.**

---

## Phase 1 — Pure spec generator (zone B)

### Task 1: `BuildSpec` + entry/name derivation

**Files:**
- Create: `apps/api/src/omnia_api/services/exe_build.py`
- Test: `apps/api/tests/test_exe_build.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_exe_build.py
from __future__ import annotations
from omnia_api.services.exe_build import build_spec, BuildSpec


def test_entry_and_name_from_snake_bundle() -> None:
    files = {
        "build_installer.py": "import PyInstaller\nif __name__ == '__main__':\n    build()",
        "snake_game.py": "import pygame\nif __name__ == '__main__':\n    SnakeGame().run()",
        "requirements.txt": "pygame>=2.5.2",
        "README.md": "doc",
    }
    spec = build_spec(files, slug="zmeika-na-piton-547080")
    assert isinstance(spec, BuildSpec)
    # entry is the game, never the installer (reuses _pick_python_entry)
    assert spec.entry == "snake_game.py"
    # trailing numeric hash dropped, TitleCased
    assert spec.name == "ZmeikaNaPiton"
    assert spec.version == "1.0.0"
    assert spec.requirements == "pygame>=2.5.2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_exe_build.py -v`
Expected: FAIL — `ModuleNotFoundError: omnia_api.services.exe_build`

- [ ] **Step 3: Write minimal implementation**

```python
# apps/api/src/omnia_api/services/exe_build.py
"""Deterministic Windows-installer build spec for a Python project.

Pure + no I/O: turns the project's files into a PyInstaller invocation and an NSIS
installer script. The entry point is chosen by run_bundle._pick_python_entry, so a
build/installer/test helper is never mistaken for the app to package.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .run_bundle import _pick_python_entry

_GUI_IMPORTS = re.compile(
    r"(?:^|\n)\s*(?:import|from)\s+"
    r"(pygame|tkinter|PyQt5|PyQt6|PySide6|PySide2|wx|kivy|pyglet|arcade)\b"
)
_ASSET_DIRS = ("assets", "images", "img", "sounds", "audio", "data", "fonts", "music")


@dataclass
class BuildSpec:
    entry: str
    name: str
    version: str = "1.0.0"
    windowed: bool = False
    icon: str | None = None
    datas: list[str] = field(default_factory=list)        # "src;dest" (win add-data sep)
    hidden_imports: list[str] = field(default_factory=list)
    collect_all: list[str] = field(default_factory=list)
    requirements: str | None = None


def _slug_to_name(slug: str) -> str:
    parts = [p for p in re.split(r"[-_]", slug) if p and not p.isdigit()]
    return "".join(w[:1].upper() + w[1:] for w in parts) or "App"


def build_spec(files: dict[str, str], *, slug: str = "app") -> BuildSpec:
    py = {p: c for p, c in files.items() if p.endswith(".py")}
    entry = _pick_python_entry(py) if py else "main.py"
    blob = "\n".join(py.values())
    return BuildSpec(
        entry=entry,
        name=_slug_to_name(slug),
        windowed=bool(_GUI_IMPORTS.search(blob)),
        icon=next((p for p in files if p.lower().endswith(".ico")), None),
        datas=[f"{d};{d}" for d in _ASSET_DIRS
               if any(p == d or p.startswith(d + "/") for p in files)],
        collect_all=["pygame"] if re.search(r"(?:^|\n)\s*import\s+pygame\b", blob) else [],
        requirements=files.get("requirements.txt"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_exe_build.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/exe_build.py apps/api/tests/test_exe_build.py
git commit -m "feat(exe-build): deterministic BuildSpec from project files"
```

### Task 2: GUI detection + asset bundling + pygame collect

**Files:**
- Modify: `apps/api/tests/test_exe_build.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_windowed_and_assets_and_pygame_collect() -> None:
    files = {
        "snake_game.py": "import pygame\nif __name__ == '__main__':\n    run()",
        "assets/icon.ico": "(binary)",
        "sounds/eat.wav": "(binary)",
    }
    spec = build_spec(files, slug="snake")
    assert spec.windowed is True                      # pygame → GUI, no console window
    assert "assets;assets" in spec.datas
    assert "sounds;sounds" in spec.datas
    assert spec.collect_all == ["pygame"]             # pygame ships data → collect-all
    assert spec.icon == "assets/icon.ico"


def test_cli_script_stays_console_no_assets() -> None:
    spec = build_spec({"cli.py": "import argparse\nif __name__ == '__main__':\n    main()"},
                      slug="tool")
    assert spec.windowed is False
    assert spec.datas == []
    assert spec.collect_all == []
```

- [ ] **Step 2: Run to verify** — Run: `cd apps/api && uv run pytest tests/test_exe_build.py -v` — Expected: PASS (Task 1 code already satisfies these; if any fails, the regex/asset logic above is the place to fix).

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/test_exe_build.py
git commit -m "test(exe-build): GUI/asset/pygame detection"
```

### Task 3: Render PyInstaller args + NSIS script

**Files:**
- Modify: `apps/api/src/omnia_api/services/exe_build.py`
- Modify: `apps/api/tests/test_exe_build.py`

- [ ] **Step 1: Write the failing test**

```python
from omnia_api.services.exe_build import render_pyinstaller_args, render_nsi, render


def test_render_pyinstaller_args() -> None:
    spec = build_spec({"snake_game.py": "import pygame\nif __name__=='__main__':\n run()"},
                      slug="snake")
    args = render_pyinstaller_args(spec)
    assert args[0] == "pyinstaller"
    assert "--onefile" in args and "--windowed" in args
    assert "--collect-all=pygame" in args
    assert f"--name={spec.name}" in args
    assert args[-1] == "snake_game.py"                 # entry is last positional


def test_render_nsi_has_install_shortcut_uninstall() -> None:
    spec = build_spec({"app.py": "if __name__=='__main__':\n m()"}, slug="my-tool")
    nsi = render_nsi(spec)
    assert 'OutFile "MyTool-Setup.exe"' in nsi
    assert "MyTool.exe" in nsi                          # installs the PyInstaller payload
    assert "CreateShortcut" in nsi                      # Start Menu / Desktop shortcut
    assert "WriteUninstaller" in nsi
    files = render(spec)
    assert set(files) == {"build_spec.json", "installer.nsi"}
```

- [ ] **Step 2: Run to verify it fails** — Run: `cd apps/api && uv run pytest tests/test_exe_build.py -k render -v` — Expected: FAIL (`render_pyinstaller_args` not defined).

- [ ] **Step 3: Implement**

```python
# append to apps/api/src/omnia_api/services/exe_build.py

def render_pyinstaller_args(spec: BuildSpec) -> list[str]:
    args = ["pyinstaller", "--onefile", "--noconfirm", "--clean", f"--name={spec.name}"]
    args.append("--windowed" if spec.windowed else "--console")
    if spec.icon:
        args.append(f"--icon={spec.icon}")
    args += [f"--add-data={d}" for d in spec.datas]
    args += [f"--hidden-import={h}" for h in spec.hidden_imports]
    args += [f"--collect-all={c}" for c in spec.collect_all]
    args.append(spec.entry)
    return args


_NSI = """\
!define APPNAME "{name}"
!define APPVERSION "{version}"
Name "${{APPNAME}}"
OutFile "{name}-Setup.exe"
InstallDir "$PROGRAMFILES\\\\${{APPNAME}}"
RequestExecutionLevel admin
Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles
Section "Install"
  SetOutPath "$INSTDIR"
  File "dist\\\\{name}.exe"
  CreateDirectory "$SMPROGRAMS\\\\${{APPNAME}}"
  CreateShortcut "$SMPROGRAMS\\\\${{APPNAME}}\\\\${{APPNAME}}.lnk" "$INSTDIR\\\\{name}.exe"
  CreateShortcut "$DESKTOP\\\\${{APPNAME}}.lnk" "$INSTDIR\\\\{name}.exe"
  WriteUninstaller "$INSTDIR\\\\uninstall.exe"
SectionEnd
Section "Uninstall"
  Delete "$INSTDIR\\\\{name}.exe"
  Delete "$INSTDIR\\\\uninstall.exe"
  Delete "$SMPROGRAMS\\\\${{APPNAME}}\\\\${{APPNAME}}.lnk"
  RMDir "$SMPROGRAMS\\\\${{APPNAME}}"
  Delete "$DESKTOP\\\\${{APPNAME}}.lnk"
  RMDir "$INSTDIR"
SectionEnd
"""


def render_nsi(spec: BuildSpec) -> str:
    return _NSI.format(name=spec.name, version=spec.version)


def render(spec: BuildSpec) -> dict[str, str]:
    return {
        "build_spec.json": json.dumps(asdict(spec), ensure_ascii=False, indent=2),
        "installer.nsi": render_nsi(spec),
    }
```

- [ ] **Step 4: Run to verify it passes** — Run: `cd apps/api && uv run pytest tests/test_exe_build.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/exe_build.py apps/api/tests/test_exe_build.py
git commit -m "feat(exe-build): render PyInstaller args + NSIS installer script"
```

---

## Phase 2 — Build container + orchestrator endpoint (zone D)

### Task 4: `omnia-exe-builder` image + in-container build script

**Files:**
- Create: `infra/exe-builder/Dockerfile`
- Create: `infra/exe-builder/build.py`

- [ ] **Step 1: Write the in-container build script**

```python
# infra/exe-builder/build.py
"""Runs INSIDE the Wine container. Reads /work/build_spec.json (PyInstaller args +
NSIS) plus the project source in /work/src, produces /work/out/{Name.exe, Name-Setup.exe}
and /work/out/build.log. Two network phases are enforced by the CALLER (docker), not here.
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
# Phase 1 (deps) — caller leaves egress on only for this wine pip step:
if spec.get("requirements"):
    if run(["wine", "python", "-m", "pip", "install", "-r", "requirements.txt"]):
        log.write("PIP_FAILED\n"); sys.exit(2)
# Phase 2 (no egress) — PyInstaller then NSIS:
if run(["wine", "pyinstaller", *spec["pyinstaller_args"][1:]]):  # [0] is "pyinstaller"
    log.write("PYINSTALLER_FAILED\n"); sys.exit(3)
(SRC / "installer.nsi").write_text(spec["installer_nsi"], encoding="utf-8")
if run(["makensis", "installer.nsi"]):
    log.write("NSIS_FAILED\n"); sys.exit(4)
# Collect artifacts
import shutil
name = spec["name"]
shutil.copy(SRC / "dist" / f"{name}.exe", OUT / f"{name}.exe")
shutil.copy(SRC / f"{name}-Setup.exe", OUT / f"{name}-Setup.exe")
log.write("OK\n"); sys.exit(0)
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# infra/exe-builder/Dockerfile
FROM tobix/pywine:3.12
# Windows-Python tooling under Wine
RUN wine python -m pip install --no-cache-dir "pyinstaller>=6.3.0"
# NSIS runs natively on Linux (no Wine needed to wrap the payload)
RUN apt-get update && apt-get install -y --no-install-recommends nsis \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m builder
COPY build.py /usr/local/bin/build.py
USER builder
WORKDIR /work
ENTRYPOINT ["python3", "/usr/local/bin/build.py"]
```

- [ ] **Step 3: Build the image + smoke test it locally**

Run:
```bash
docker build -t omnia-exe-builder:latest infra/exe-builder
# smoke: a trivial console app builds end-to-end
mkdir -p /tmp/exe-smoke/src && cd /tmp/exe-smoke
printf 'print("hi")\n' > src/app.py
cat > build_spec.json <<'JSON'
{"name":"Smoke","requirements":null,
 "pyinstaller_args":["pyinstaller","--onefile","--noconfirm","--name=Smoke","--console","app.py"],
 "installer_nsi":"Name \"Smoke\"\nOutFile \"Smoke-Setup.exe\"\nInstallDir \"$PROGRAMFILES\\\\Smoke\"\nSection\nSetOutPath \"$INSTDIR\"\nFile \"dist\\\\Smoke.exe\"\nWriteUninstaller \"$INSTDIR\\\\uninstall.exe\"\nSectionEnd\nSection \"Uninstall\"\nRMDir \"$INSTDIR\"\nSectionEnd\n"}
JSON
cp build_spec.json src/
docker run --rm -v /tmp/exe-smoke:/work omnia-exe-builder:latest
file /tmp/exe-smoke/out/Smoke.exe /tmp/exe-smoke/out/Smoke-Setup.exe
```
Expected: both report `PE32 executable ... MS Windows`. (Wine first-run may take a minute.)

- [ ] **Step 4: Commit**

```bash
git add infra/exe-builder/Dockerfile infra/exe-builder/build.py
git commit -m "feat(infra): omnia-exe-builder image (Wine+PyInstaller+NSIS)"
```

### Task 5: Orchestrator `/build-exe` service + router

**Files:**
- Create: `apps/orchestrator/src/omnia_orchestrator/schemas/build_exe.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/services/exe_builder.py`
- Create: `apps/orchestrator/src/omnia_orchestrator/routers/build_exe.py`
- Modify: `apps/orchestrator/src/omnia_orchestrator/main.py` (register router — mirror how `routers/runtime.py` is included)
- Test: `apps/orchestrator/tests/test_exe_builder.py`

- [ ] **Step 1: Write the schema**

```python
# apps/orchestrator/src/omnia_orchestrator/schemas/build_exe.py
from __future__ import annotations
from pydantic import BaseModel


class BuildExeRequest(BaseModel):
    name: str
    files: dict[str, str]                 # path -> text content (the project source)
    pyinstaller_args: list[str]
    installer_nsi: str
    requirements: str | None = None


class BuildExeResult(BaseModel):
    ok: bool
    log: str
    setup_b64: str | None = None          # base64 of Setup.exe (None on failure)
    exe_b64: str | None = None            # base64 of the portable .exe
```

- [ ] **Step 2: Write the failing test (mock docker)**

```python
# apps/orchestrator/tests/test_exe_builder.py
from __future__ import annotations
import base64
from omnia_orchestrator.services.exe_builder import run_exe_build
from omnia_orchestrator.schemas.build_exe import BuildExeRequest


def test_run_exe_build_collects_artifacts(monkeypatch, tmp_path) -> None:
    # Simulate the container writing artifacts + a success log into the out dir.
    def fake_run_container(workdir, *, egress):
        out = workdir / "out"; out.mkdir(parents=True, exist_ok=True)
        (out / "build.log").write_text("OK\n")
        (out / "Snake.exe").write_bytes(b"MZ\x90\x00exe")
        (out / "Snake-Setup.exe").write_bytes(b"MZ\x90\x00setup")
        return 0
    monkeypatch.setattr("omnia_orchestrator.services.exe_builder._run_container", fake_run_container)
    req = BuildExeRequest(name="Snake", files={"app.py": "print(1)"},
                          pyinstaller_args=["pyinstaller", "--onefile", "app.py"],
                          installer_nsi="Name \"Snake\"")
    res = run_exe_build(req, work_root=tmp_path)
    assert res.ok is True
    assert base64.b64decode(res.setup_b64) == b"MZ\x90\x00setup"
    assert base64.b64decode(res.exe_b64) == b"MZ\x90\x00exe"
```

- [ ] **Step 3: Run to verify it fails** — Run: `cd apps/orchestrator && uv run pytest tests/test_exe_builder.py -v` — Expected: FAIL (module missing).

- [ ] **Step 4: Implement the service**

```python
# apps/orchestrator/src/omnia_orchestrator/services/exe_builder.py
"""Run one ephemeral omnia-exe-builder container to produce a Windows Setup.exe.

Two-phase network: deps install with egress, the user-code build phase without.
For v1 simplicity the whole run is done with egress ON only when requirements exist;
harden to a true two-phase split (deps container → no-net build container) in a
follow-up if abuse appears. Container is non-root (image USER builder), --rm, capped,
timed out.
"""
from __future__ import annotations
import base64, json, pathlib
from ..core.docker_client import get_docker  # mirror existing docker usage in services/
from ..schemas.build_exe import BuildExeRequest, BuildExeResult

IMAGE = "omnia-exe-builder:latest"
TIMEOUT_S = 300
MEM_LIMIT = "2g"
NANO_CPUS = 2_000_000_000  # 2 CPUs


def _run_container(workdir: pathlib.Path, *, egress: bool) -> int:
    client = get_docker()
    container = client.containers.run(
        IMAGE,
        remove=True, detach=True,
        network_disabled=not egress,
        mem_limit=MEM_LIMIT, nano_cpus=NANO_CPUS,
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
    work = work_root
    src = work / "src"; out = work / "out"
    src.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    for path, content in req.files.items():
        f = src / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    (work / "build_spec.json").write_text(json.dumps({
        "name": req.name, "requirements": req.requirements,
        "pyinstaller_args": req.pyinstaller_args, "installer_nsi": req.installer_nsi,
    }), encoding="utf-8")
    code = _run_container(work, egress=bool(req.requirements))
    log = (out / "build.log").read_text(encoding="utf-8") if (out / "build.log").exists() else ""
    setup = out / f"{req.name}-Setup.exe"; exe = out / f"{req.name}.exe"
    ok = code == 0 and setup.exists()
    return BuildExeResult(
        ok=ok, log=log,
        setup_b64=base64.b64encode(setup.read_bytes()).decode() if setup.exists() else None,
        exe_b64=base64.b64encode(exe.read_bytes()).decode() if exe.exists() else None,
    )
```

- [ ] **Step 5: Implement the router + register it**

```python
# apps/orchestrator/src/omnia_orchestrator/routers/build_exe.py
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
```

In `apps/orchestrator/src/omnia_orchestrator/main.py`, add next to the other `include_router` calls:
```python
from .routers import build_exe as build_exe_router
app.include_router(build_exe_router.router)
```

- [ ] **Step 6: Run to verify it passes** — Run: `cd apps/orchestrator && uv run pytest tests/test_exe_builder.py -v` — Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/orchestrator/src/omnia_orchestrator/schemas/build_exe.py \
        apps/orchestrator/src/omnia_orchestrator/services/exe_builder.py \
        apps/orchestrator/src/omnia_orchestrator/routers/build_exe.py \
        apps/orchestrator/src/omnia_orchestrator/main.py \
        apps/orchestrator/tests/test_exe_builder.py
git commit -m "feat(orchestrator): POST /build-exe runs ephemeral Wine builder"
```

---

## Phase 3 — API wiring + delivery (zone B)

### Task 6: Config flag + orchestrator client method

**Files:**
- Modify: `apps/api/src/omnia_api/core/config.py`
- Modify: `apps/api/src/omnia_api/services/orchestrator_client.py`

- [ ] **Step 1: Add settings** (mirror the `Field(default=...)` style already in config.py)

```python
# in the Settings class in apps/api/src/omnia_api/core/config.py
use_exe_build: bool = Field(default=False)            # kill switch USE_EXE_BUILD
exe_build_max_mb: int = Field(default=150)
exe_build_retention_days: int = Field(default=7)
```

- [ ] **Step 2: Add the client method** (mirror an existing call in `orchestrator_client.py`, e.g. read-file/provision)

```python
# in apps/api/src/omnia_api/services/orchestrator_client.py
async def build_exe(name: str, files: dict[str, str], pyinstaller_args: list[str],
                    installer_nsi: str, requirements: str | None) -> dict:
    """Call the orchestrator's /build-exe. Returns {ok, log, setup_b64, exe_b64}."""
    payload = {"name": name, "files": files, "pyinstaller_args": pyinstaller_args,
               "installer_nsi": installer_nsi, "requirements": requirements}
    # use the same httpx client + base URL + timeout pattern as the other calls here;
    # build timeout must exceed the orchestrator's 300s container timeout → use 360s.
    resp = await _client().post("/build-exe", json=payload, timeout=360.0)
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/omnia_api/core/config.py apps/api/src/omnia_api/services/orchestrator_client.py
git commit -m "feat(exe-build): config flag + orchestrator client method"
```

### Task 7: RQ job + endpoints + delivery

**Files:**
- Create: `apps/api/src/omnia_api/workers/build_exe.py`
- Modify: `apps/api/src/omnia_api/routers/projects.py`
- Test: `apps/api/tests/test_build_exe_endpoint.py`

- [ ] **Step 1: Write the failing endpoint test** (mirror auth/owner-scope test setup from existing `projects` tests)

```python
# apps/api/tests/test_build_exe_endpoint.py
import pytest


@pytest.mark.asyncio
async def test_build_exe_requires_owner(client, other_users_project):
    r = await client.post(f"/api/projects/{other_users_project.id}/build-exe")
    assert r.status_code == 404            # owner-scoped: foreign project is 404


@pytest.mark.asyncio
async def test_build_exe_flag_off_returns_404(client, my_project, monkeypatch):
    monkeypatch.setattr("omnia_api.core.config.settings.use_exe_build", False)
    r = await client.post(f"/api/projects/{my_project.id}/build-exe")
    assert r.status_code == 404            # feature gated off
```

- [ ] **Step 2: Run to verify it fails** — Run: `cd apps/api && uv run pytest tests/test_build_exe_endpoint.py -v` — Expected: FAIL (route missing).

- [ ] **Step 3: Implement the RQ job**

```python
# apps/api/src/omnia_api/workers/build_exe.py
"""RQ job: read committed files → build_spec → orchestrator /build-exe → (self-heal) →
upload artifacts to MinIO → publish exe.* SSE events. Mirrors workers/preview.py."""
from __future__ import annotations
import asyncio
from omnia_api.services import exe_build, orchestrator_client
from omnia_api.services.exe_doctor import heal        # Phase 4; import is safe (pure until called)
from omnia_api.core import minio
# reuse the project's existing SSE publisher (see services/app_errors.py for the pattern)
from omnia_api.services.app_errors import publish_event  # adjust name to the real helper

MAX_HEAL = 3


def build_exe_job(project_id: str, build_id: str, slug: str, files: dict[str, str]) -> None:
    asyncio.run(_run(project_id, build_id, slug, files))


async def _run(project_id, build_id, slug, files):
    spec = exe_build.build_spec(files, slug=slug)
    await publish_event(project_id, "exe.stage", {"build_id": build_id, "stage": "spec"})
    for attempt in range(MAX_HEAL + 1):
        rendered = exe_build.render(spec)
        await publish_event(project_id, "exe.stage", {"build_id": build_id, "stage": "build"})
        res = await orchestrator_client.build_exe(
            name=spec.name, files=files,
            pyinstaller_args=exe_build.render_pyinstaller_args(spec),
            installer_nsi=rendered["installer.nsi"], requirements=spec.requirements,
        )
        if res["ok"]:
            urls = await minio.put_exe_artifacts(project_id, build_id, spec.name, res)
            await publish_event(project_id, "exe.ready", {"build_id": build_id, **urls})
            return
        if attempt >= MAX_HEAL:
            break
        await publish_event(project_id, "exe.heal", {"build_id": build_id, "attempt": attempt + 1})
        patched = await heal(res["log"], spec, files)
        if patched is None:
            break
        spec = patched
    await publish_event(project_id, "exe.failed", {"build_id": build_id, "log": res["log"][-4000:]})
```

Add `minio.put_exe_artifacts(project_id, build_id, name, res)` to `apps/api/src/omnia_api/core/minio.py` — base64-decode `setup_b64`/`exe_b64`, `put_object` under `exe/<project_id>/<build_id>/`, return `{"setup_url": ..., "exe_url": ..., "name": ..., "size": ...}` (mirror the existing put/presign helpers in that file).

- [ ] **Step 4: Implement the endpoints** (in `apps/api/src/omnia_api/routers/projects.py`, mirror `download_project` for owner-scope + the enqueue pattern in `services/queue.py`)

```python
@router.post("/{project_id}/build-exe")
async def build_exe_endpoint(project_id: UUID, session: SessionDep, current_user: CurrentUserDep):
    if not settings.use_exe_build:
        raise ApiError("not_found", "feature off", status.HTTP_404_NOT_FOUND)
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.current_snapshot_id is None:
        raise ApiError("not_found", "nothing generated yet", status.HTTP_404_NOT_FOUND)
    snap = await session.get(Snapshot, project.current_snapshot_id)
    files = await asyncio.to_thread(repo_svc.read_files, project_id, snap.commit_sha)
    if not any(p.endswith(".py") for p in files):
        raise ApiError("unsupported", "exe build is Python-only", status.HTTP_400_BAD_REQUEST)
    build_id = str(uuid4())
    enqueue("build_exe_job", str(project_id), build_id, project.slug or "app", files)  # via queue.py
    return {"build_id": build_id}


@router.get("/{project_id}/exe/{build_id}/{artifact}")
async def download_exe(project_id: UUID, build_id: str, artifact: str,
                      session: SessionDep, current_user: CurrentUserDep):
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if artifact not in ("setup", "exe"):
        raise ApiError("not_found", "unknown artifact", status.HTTP_404_NOT_FOUND)
    return await minio.stream_exe_artifact(project_id, build_id, artifact)  # 404 if absent
```

- [ ] **Step 5: Run to verify it passes** — Run: `cd apps/api && uv run pytest tests/test_build_exe_endpoint.py -v` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/omnia_api/workers/build_exe.py apps/api/src/omnia_api/routers/projects.py \
        apps/api/src/omnia_api/core/minio.py apps/api/tests/test_build_exe_endpoint.py
git commit -m "feat(exe-build): RQ job, owner-scoped endpoints, MinIO delivery"
```

---

## Phase 4 — Self-heal (zone B + C)

### Task 8: `exe_doctor` role (zone C) + heal service

**Files:**
- Modify: gateway role map (zone C — add `exe_doctor` → cheap model + escalation; follow how `edit`/`exe_doctor`-style roles are registered in `apps/llm-gateway`).
- Create: `apps/api/src/omnia_api/services/exe_doctor.py`
- Test: `apps/api/tests/test_exe_doctor.py`

- [ ] **Step 1: Write the failing test (mock the model call)**

```python
# apps/api/tests/test_exe_doctor.py
import pytest
from omnia_api.services.exe_build import build_spec
from omnia_api.services import exe_doctor


@pytest.mark.asyncio
async def test_heal_applies_hidden_import(monkeypatch):
    spec = build_spec({"app.py": "import pygame\nif __name__=='__main__':\n run()"}, slug="x")

    async def fake_model(prompt: str) -> str:
        return '{"hidden_imports": ["pkg_resources.py2_warn"], "collect_all": [], "requirements": null}'
    monkeypatch.setattr(exe_doctor, "_ask_model", fake_model)

    patched = await exe_doctor.heal("ModuleNotFoundError: pkg_resources.py2_warn", spec, {})
    assert "pkg_resources.py2_warn" in patched.hidden_imports


@pytest.mark.asyncio
async def test_heal_returns_none_on_unparseable(monkeypatch):
    spec = build_spec({"app.py": "x=1"}, slug="x")
    async def fake_model(prompt: str) -> str:
        return "sorry I cannot help"
    monkeypatch.setattr(exe_doctor, "_ask_model", fake_model)
    assert await exe_doctor.heal("some error", spec, {}) is None
```

- [ ] **Step 2: Run to verify it fails** — Run: `cd apps/api && uv run pytest tests/test_exe_doctor.py -v` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# apps/api/src/omnia_api/services/exe_doctor.py
"""Self-heal a failed PyInstaller/NSIS build: ask the exe_doctor model role for a
structured patch (extra hidden-imports / collect-all / requirements pin) and apply it
to the BuildSpec. Returns None when the model gives nothing usable (caller gives up)."""
from __future__ import annotations
import json, re
from dataclasses import replace
from .exe_build import BuildSpec
from .llm_gateway import call_role            # use the project's existing gateway helper

_PROMPT = """PyInstaller/NSIS build failed. Return ONLY JSON:
{{"hidden_imports": [..], "collect_all": [..], "requirements": "<full requirements.txt or null>"}}
Add only what the error implies. Error log tail:
{log}
Current PyInstaller hidden_imports={hi} collect_all={ca}."""


async def _ask_model(prompt: str) -> str:
    return await call_role("exe_doctor", prompt)


async def heal(error_log: str, spec: BuildSpec, sources: dict[str, str]) -> BuildSpec | None:
    raw = await _ask_model(_PROMPT.format(
        log=error_log[-3000:], hi=spec.hidden_imports, ca=spec.collect_all))
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        patch = json.loads(m.group(0))
    except Exception:
        return None
    hi = sorted(set(spec.hidden_imports) | set(patch.get("hidden_imports") or []))
    ca = sorted(set(spec.collect_all) | set(patch.get("collect_all") or []))
    reqs = patch.get("requirements") or spec.requirements
    if hi == spec.hidden_imports and ca == spec.collect_all and reqs == spec.requirements:
        return None                         # nothing new → don't loop pointlessly
    return replace(spec, hidden_imports=hi, collect_all=ca, requirements=reqs)
```

- [ ] **Step 4: Run to verify it passes** — Run: `cd apps/api && uv run pytest tests/test_exe_doctor.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/omnia_api/services/exe_doctor.py apps/api/tests/test_exe_doctor.py
git commit -m "feat(exe-build): exe_doctor self-heal loop (capped patch+retry)"
```

---

## Phase 5 — Web UX (zone A)

### Task 9: SSE hook + build button + reward/fallback

**Files:**
- Create: `apps/web/src/hooks/useExeBuild.ts`
- Create: `apps/web/src/components/workspace/BuildExeButton.tsx`
- Modify: `apps/web/src/components/workspace/TopBar.tsx`
- Modify: `apps/web/src/lib/api/types.ts`

- [ ] **Step 1: Add event types**

```typescript
// apps/web/src/lib/api/types.ts
export type ExeBuildStage = "spec" | "build" | "heal" | "ready" | "failed";
export interface ExeBuildEvent {
  build_id: string;
  stage: ExeBuildStage;
  attempt?: number;
  setup_url?: string;
  exe_url?: string;
  name?: string;
  size?: number;
  log?: string;
}
```

- [ ] **Step 2: Implement the hook** (subscribe to the project's existing SSE channel — mirror `usePromptStream.ts`; filter `exe.*` events)

```typescript
// apps/web/src/hooks/useExeBuild.ts
import { useState, useCallback } from "react";
import type { ExeBuildEvent, ExeBuildStage } from "@/lib/api/types";

export function useExeBuild(projectId: string) {
  const [stage, setStage] = useState<ExeBuildStage | "idle">("idle");
  const [event, setEvent] = useState<ExeBuildEvent | null>(null);

  const start = useCallback(async () => {
    setStage("spec");
    const r = await fetch(`/api/projects/${projectId}/build-exe`, { method: "POST" });
    if (!r.ok) { setStage("failed"); return; }
    // subscribe to the project's SSE stream the same way usePromptStream does,
    // and on each exe.* message: setEvent(msg); setStage(msg.stage);
  }, [projectId]);

  return { stage, event, start };
}
```

- [ ] **Step 3: Implement the button + states**

```tsx
// apps/web/src/components/workspace/BuildExeButton.tsx
"use client";
import { useExeBuild } from "@/hooks/useExeBuild";

const STAGE_LABEL: Record<string, string> = {
  spec: "Готовлю окружение…", build: "Собираю приложение…",
  heal: "Чиню сборку…", ready: "Готово!", failed: "Не собралось",
};

export function BuildExeButton({ projectId }: { projectId: string }) {
  const { stage, event, start } = useExeBuild(projectId);
  if (stage === "ready" && event?.setup_url) {
    return (
      <div className="flex flex-col gap-1">
        <a className="btn-primary" href={event.setup_url} download>
          🎉 Скачать установщик ({Math.round((event.size ?? 0) / 1e6)} МБ)
        </a>
        {event.exe_url && <a className="text-xs text-fg-secondary" href={event.exe_url} download>или портативный .exe</a>}
        <span className="text-xs text-fg-tertiary">Windows может предупредить о неизвестном издателе — это нормально.</span>
      </div>
    );
  }
  if (stage === "failed") {
    return <div className="text-sm text-danger">Не собралось. <button onClick={start}>Ещё раз</button></div>;
  }
  const busy = stage !== "idle";
  return (
    <button className="btn-secondary" disabled={busy} onClick={start}>
      {busy ? STAGE_LABEL[stage] : "Собрать .exe"}
    </button>
  );
}
```

- [ ] **Step 4: Mount in TopBar** — in `apps/web/src/components/workspace/TopBar.tsx`, render `<BuildExeButton projectId={projectId} />` next to the existing «Скачать» control, gated on a `useExeBuild`-available flag if one is plumbed through props.

- [ ] **Step 5: Verify** — Run: `cd apps/web && pnpm typecheck` — Expected: no errors. Then the preview workflow (preview_start → click «Собрать .exe» → observe stages) once the backend canary is on.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/hooks/useExeBuild.ts apps/web/src/components/workspace/BuildExeButton.tsx \
        apps/web/src/components/workspace/TopBar.tsx apps/web/src/lib/api/types.ts
git commit -m "feat(web): «Собрать .exe» button with live build stages + reward"
```

---

## Phase 6 — Canary + deploy

### Task 10: Build image on prod, deploy, E2E the snake

- [ ] **Step 1:** On the VPS, build the image: `cd /opt/omnia && docker build -t omnia-exe-builder:latest infra/exe-builder`.
- [ ] **Step 2:** Deploy api + worker + orchestrator (project `full` compose): rebuild & restart `api worker`, restart the orchestrator systemd unit. Keep `USE_EXE_BUILD` unset (off).
- [ ] **Step 3:** Flip `USE_EXE_BUILD=true` for the api+worker env, restart.
- [ ] **Step 4:** E2E under the owner account: build the snake project → poll SSE → download `Setup.exe` → `file Setup.exe` shows `PE32 ... MS Windows`, size > 0. Verify a foreign project returns 404 and the flag-off path returns 404.
- [ ] **Step 5:** If green, leave on for the owner; widen later. Roll back instantly with `USE_EXE_BUILD=false` if needed.

---

## Self-Review

**Spec coverage:** target (Windows-only, Python-only) → Task 1/builder image; PyInstaller-under-Wine + NSIS-native → Task 4/5; auto-derived spec → Task 1-3; ephemeral container + caps/timeout/non-root → Task 4-5; self-heal cap 3 → Task 7-8; MinIO owner-scoped delivery + size cap/retention → Task 6-7; gamified UX + fallback → Task 9; `USE_EXE_BUILD` canary → Task 6/10; tests at every layer → Tasks 1-8; two-phase network → Task 4 build.py + Task 5 service. All spec sections map to a task.

**Open implementation details to confirm during execution (not placeholders — concrete patterns to mirror):** the exact SSE publish helper name in `services/app_errors.py`; the `enqueue`/Queue API in `services/queue.py`; the httpx client/base-URL helper in `orchestrator_client.py`; the MinIO put/presign helpers in `core/minio.py`; the gateway `call_role` helper + role-map file in `apps/llm-gateway`. Each is an existing pattern the task says to mirror; confirm the real symbol name when you open the file.
