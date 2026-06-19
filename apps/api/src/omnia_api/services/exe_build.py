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
    datas: list[str] = field(default_factory=list)
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


# NSIS template. Uses {{...}} to escape literal braces that .format() would
# otherwise treat as placeholders — ${{APPNAME}} → ${APPNAME} in the emitted
# file (NSIS variable reference). Single backslashes are the correct NSIS path
# separator; the Python string already stores them as single backslashes because
# we use raw concatenation, not \\-escape sequences here.
_NSI = (
    '!define APPNAME "{name}"\n'
    '!define APPVERSION "{version}"\n'
    'Name "${{APPNAME}}"\n'
    'OutFile "{name}-Setup.exe"\n'
    'InstallDir "$PROGRAMFILES\\${{APPNAME}}"\n'
    "RequestExecutionLevel admin\n"
    "Page directory\n"
    "Page instfiles\n"
    "UninstPage uninstConfirm\n"
    "UninstPage instfiles\n"
    'Section "Install"\n'
    '  SetOutPath "$INSTDIR"\n'
    '  File "dist\\{name}.exe"\n'
    '  CreateDirectory "$SMPROGRAMS\\${{APPNAME}}"\n'
    '  CreateShortcut "$SMPROGRAMS\\${{APPNAME}}\\${{APPNAME}}.lnk" "$INSTDIR\\{name}.exe"\n'
    '  CreateShortcut "$DESKTOP\\${{APPNAME}}.lnk" "$INSTDIR\\{name}.exe"\n'
    '  WriteUninstaller "$INSTDIR\\uninstall.exe"\n'
    "SectionEnd\n"
    'Section "Uninstall"\n'
    '  Delete "$INSTDIR\\{name}.exe"\n'
    '  Delete "$INSTDIR\\uninstall.exe"\n'
    '  Delete "$SMPROGRAMS\\${{APPNAME}}\\${{APPNAME}}.lnk"\n'
    '  RMDir "$SMPROGRAMS\\${{APPNAME}}"\n'
    '  Delete "$DESKTOP\\${{APPNAME}}.lnk"\n'
    '  RMDir "$INSTDIR"\n'
    "SectionEnd\n"
)


def render_nsi(spec: BuildSpec) -> str:
    return _NSI.format(name=spec.name, version=spec.version)


def render(spec: BuildSpec) -> dict[str, str]:
    return {
        "build_spec.json": json.dumps(asdict(spec), ensure_ascii=False, indent=2),
        "installer.nsi": render_nsi(spec),
    }
