"""One-click run bundle (owner 2026-06-19 — «скачал → уже играешь локально»).

A browser download can't auto-run code (sandbox), but we can drop a double-click
LAUNCHER into the project's .zip so the user goes from download → playing in one
extra click: the launcher creates a venv, installs deps, and runs the entry point.

``build_launchers(files)`` inspects the project's actual files and returns the extra
files to add to the zip — Python (``requirements.txt`` / ``*.py``) and Node
(``package.json``) are supported; anything else (a plain website, an unknown
language) returns ``{}`` (nothing to launch). Pure + deterministic; never raises.

The picked Python entry is the runnable APP, never a build/installer/test helper:
those carry an ``if __name__ == "__main__"`` guard too (a real bundle shipped both
``snake_game.py`` and ``build_installer.py``), but "run" must launch the game, not
compile an .exe. Tooling is filtered by name *and* by content (a file that imports
PyInstaller / cx_Freeze / setuptools is a builder, whatever it's called).
"""

from __future__ import annotations

import json
import re

# Common entry-point filenames, most-likely first.
_PY_ENTRY_NAMES = (
    "main.py", "app.py", "run.py", "game.py", "snake.py", "__main__.py",
    "bot.py", "cli.py", "start.py", "server.py", "manage.py",
)

# A real run guard — not just the bare word in a comment/docstring.
_MAIN_GUARD = re.compile(r"if\s+__name__\s*==\s*([\"'])__main__\1")

# Content that marks a .py as a BUILD/packaging helper, not the app to run.
_BUILDER_SIGNALS = re.compile(
    r"\b(PyInstaller|pyinstaller|cx_Freeze|cx_freeze|py2exe|nuitka|setuptools|distutils)\b"
    r"|--onefile|--noconsole|setup\s*\("
)

# Filenames that are tooling even with an __main__ guard — never the run target.
_PY_TOOLING_NAMES = frozenset({
    "setup.py", "conftest.py", "noxfile.py", "tasks.py", "fabfile.py",
})
_PY_TOOLING_DIRS = frozenset({"tests", "test", "build", "dist", "scripts", "__pycache__"})


def _is_tooling(path: str, content: str) -> bool:
    """True when a .py is build/packaging/test tooling, not the app to run."""
    base = path.rsplit("/", 1)[-1].lower()
    parent = path.lower().split("/")[:-1]
    if base in _PY_TOOLING_NAMES:
        return True
    if base.endswith(".py") and base.startswith(("build", "install", "package_", "make_")):
        return True
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    if any(p in _PY_TOOLING_DIRS for p in parent):
        return True
    return bool(_BUILDER_SIGNALS.search(content))


def _pick_python_entry(py_files: dict[str, str]) -> str:
    """Best-guess the script to RUN: a real ``__main__`` guard wins, then a common
    entry name, then the shallowest / largest file — but build/installer/test helpers
    are excluded first so "run" never compiles an .exe instead of launching the app."""
    runnable = {p: c for p, c in py_files.items() if not _is_tooling(p, c)}
    pool = runnable or py_files  # if everything looks like tooling, don't strand
    by_base = {p.rsplit("/", 1)[-1].lower(): p for p in pool}
    # 1. a known entry name that also carries a real __main__ guard
    for name in _PY_ENTRY_NAMES:
        p = by_base.get(name)
        if p and _MAIN_GUARD.search(pool[p]):
            return p
    # 2. any file with a real __main__ guard
    for path, content in pool.items():
        if _MAIN_GUARD.search(content):
            return path
    # 3. a known entry name (even without a guard)
    for name in _PY_ENTRY_NAMES:
        if name in by_base:
            return by_base[name]
    # 4. shallowest / largest
    return sorted(pool, key=lambda p: (p.count("/"), -len(pool[p])))[0]


# ── Launcher templates (placeholder-substituted, NOT f-strings — bat `%var%` and
# shell `{ ... }` blocks would otherwise need escaping). Always emitted with LF. ──

_PY_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Установка и запуск ===
rem Prefer the `py` launcher: it dodges the WindowsApps "python" alias stub and
rem works even when the python.org install skipped "Add to PATH". Verify it runs.
set "PYEXE="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo.
  echo Python 3 не найден. Установи с https://www.python.org/downloads/
  echo При установке поставь галочку "Add python.exe to PATH", потом запусти этот файл снова.
  echo.
  pause
  exit /b 1
)
%PYEXE% -m venv .venv
if errorlevel 1 (
  echo.
  echo Не удалось создать окружение.
  pause
  exit /b 1
)
set "VPY=.venv\\Scripts\\python.exe"
if not exist "%VPY%" (
  echo.
  echo Окружение не создано.
  pause
  exit /b 1
)
"%VPY%" -m pip install --upgrade pip >nul 2>nul
if exist requirements.txt (
  echo Ставлю зависимости...
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Не удалось установить зависимости. Проверь интернет и запусти снова.
    pause
    exit /b 1
  )
)
echo Запускаю...
"%VPY%" "__OMNIA_ENTRY__"
if errorlevel 1 (
  echo.
  echo Программа завершилась с ошибкой ^(см. сообщение выше^).
) else (
  echo.
  echo Готово.
)
pause
"""

_PY_SH = """#!/usr/bin/env bash
cd "$(dirname "$0")" || { echo "Не удалось перейти в папку проекта"; read -r -p "Нажми Enter..."; exit 1; }
echo "=== Установка и запуск ==="
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 не найден. Установи с https://www.python.org/downloads/"
  read -r -p "Нажми Enter..."
  exit 1
fi
"$PY" -m venv .venv || { echo "Не удалось создать окружение (Debian/Ubuntu: sudo apt install python3-venv)"; read -r -p "Нажми Enter..."; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null 2>&1
if [ -f requirements.txt ]; then
  echo "Ставлю зависимости..."
  pip install -r requirements.txt || { echo "Не удалось установить зависимости. Проверь интернет."; read -r -p "Нажми Enter..."; exit 1; }
fi
echo "Запускаю..."
python "__OMNIA_ENTRY__"
status=$?
if [ "$status" -ne 0 ]; then
  echo
  echo "Программа завершилась с ошибкой ($status)."
  read -r -p "Нажми Enter..."
fi
"""

_PY_README = """КАК ЗАПУСТИТЬ — в один клик

Windows:  двойной клик по файлу  run.bat
macOS:    правый клик по         run.command  → Открыть → Открыть
          (первый раз macOS спросит про «неизвестного разработчика» — это нормально)
Linux:    в терминале:           bash run.sh

Скрипт сам создаст окружение, поставит зависимости и запустит:  __OMNIA_ENTRY__

Нужен установленный Python 3 — https://www.python.org/downloads/
(на Windows при установке поставь галочку «Add python.exe to PATH»).
"""


def _python_launchers(entry: str) -> dict[str, str]:
    def sub(t: str) -> str:
        return t.replace("__OMNIA_ENTRY__", entry).replace("\r\n", "\n")

    return {
        "run.bat": sub(_PY_BAT),
        "run.sh": sub(_PY_SH),
        # macOS runs *.command on double-click; identical to run.sh.
        "run.command": sub(_PY_SH),
        "КАК-ЗАПУСТИТЬ.txt": sub(_PY_README),
    }


def _node_run_cmd(files: dict[str, str]) -> str:
    """Pick how to start a Node project: a real `start` script, else `dev`, else the
    first script, else a bare `node <entry>` — never a blind `npm start` that dies
    with 'Missing script: start'."""
    try:
        data = json.loads(files.get("package.json") or "{}")
    except Exception:
        data = {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if isinstance(scripts, dict) and scripts:
        if "start" in scripts:
            return "npm start"
        if "dev" in scripts:
            return "npm run dev"
        return "npm run " + next(iter(scripts))
    main = data.get("main") if isinstance(data, dict) else None
    if isinstance(main, str) and main.strip():
        return f'node "{main.strip()}"'
    for cand in ("server.js", "index.js", "app.js", "main.js"):
        if cand in files:
            return f'node "{cand}"'
    return "npm start"


_NODE_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js не найден. Установи с https://nodejs.org/ и запусти снова.
  pause
  exit /b 1
)
echo Ставлю зависимости...
call npm install
if errorlevel 1 (
  echo.
  echo Не удалось установить зависимости. Проверь интернет и запусти снова.
  pause
  exit /b 1
)
echo Запускаю...
call __OMNIA_RUN__
pause
"""

_NODE_SH = """#!/usr/bin/env bash
cd "$(dirname "$0")" || { echo "Не удалось перейти в папку проекта"; read -r -p "Нажми Enter..."; exit 1; }
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js не найден. Установи с https://nodejs.org/"
  read -r -p "Нажми Enter..."
  exit 1
fi
echo "Ставлю зависимости..."
npm install || { echo "Не удалось установить зависимости. Проверь интернет."; read -r -p "Нажми Enter..."; exit 1; }
echo "Запускаю..."
__OMNIA_RUN__
"""

_NODE_README = """КАК ЗАПУСТИТЬ — в один клик

Windows:  двойной клик по  run.bat
macOS:    правый клик по   run.command  → Открыть (первый раз)
Linux:    в терминале:     bash run.sh

Нужен установленный Node.js — https://nodejs.org/
Скрипт выполнит npm install и запустит проект.
"""


def _node_launchers(run_cmd: str) -> dict[str, str]:
    def sub(t: str) -> str:
        return t.replace("__OMNIA_RUN__", run_cmd).replace("\r\n", "\n")

    return {
        "run.bat": sub(_NODE_BAT),
        "run.sh": sub(_NODE_SH),
        "run.command": sub(_NODE_SH),
        "КАК-ЗАПУСТИТЬ.txt": _NODE_README,
    }


def build_launchers(files: dict[str, str]) -> dict[str, str]:
    """Return the double-click launcher files to add to a project's download zip,
    or ``{}`` when the project isn't a runnable program (e.g. a plain website).

    Content-based (not template-based) so it also works for a `code` project that
    was pivoted to web but still carries its source. If the project already ships
    any launcher of the trio (run.bat/run.sh/run.command), we leave the whole set
    alone — mixing our run.bat with the project's run.sh would diverge."""
    if any(name in files for name in ("run.bat", "run.sh", "run.command")):
        return {}
    py_files = {p: c for p, c in files.items() if p.endswith(".py")}
    if py_files:
        return _python_launchers(_pick_python_entry(py_files))
    if "package.json" in files:
        return _node_launchers(_node_run_cmd(files))
    return {}


__all__ = ["build_launchers"]
