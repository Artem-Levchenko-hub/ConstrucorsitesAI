"""One-click run bundle (owner 2026-06-19 — «скачал → уже играешь локально»).

A browser download can't auto-run code (sandbox), but we can drop a double-click
LAUNCHER into the project's .zip so the user goes from download → playing in one
extra click: the launcher creates a venv, installs deps, and runs the entry point.

``build_launchers(files)`` inspects the project's actual files and returns the extra
files to add to the zip — Python (``requirements.txt`` / ``*.py``) and Node
(``package.json``) are supported; anything else (a plain website, an unknown
language) returns ``{}`` (nothing to launch). Pure + deterministic; never raises.
"""

from __future__ import annotations

# Common entry-point filenames, most-likely first. A file carrying an
# ``if __name__ == "__main__"`` guard always wins over these.
_PY_ENTRY_NAMES = (
    "main.py", "app.py", "run.py", "game.py", "snake.py", "__main__.py",
    "bot.py", "cli.py", "start.py", "server.py", "manage.py",
)


def _pick_python_entry(py_files: dict[str, str]) -> str:
    """Best-guess the script to run: an ``__main__`` guard, then a common entry
    name, then the shallowest / largest file."""
    for path, content in py_files.items():
        if "__main__" in content:
            return path
    by_base = {p.rsplit("/", 1)[-1].lower(): p for p in py_files}
    for name in _PY_ENTRY_NAMES:
        if name in by_base:
            return by_base[name]
    return sorted(py_files, key=lambda p: (p.count("/"), -len(py_files[p])))[0]


def _python_launchers(entry: str) -> dict[str, str]:
    bat = f"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Установка и запуск ===
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python не найден. Установи Python 3 с https://www.python.org/downloads/
  echo При установке поставь галочку "Add python.exe to PATH", потом запусти этот файл снова.
  echo.
  pause
  exit /b 1
)
python -m venv .venv
call ".venv\\Scripts\\activate.bat"
python -m pip install --upgrade pip >nul 2>nul
if exist requirements.txt (
  echo Ставлю зависимости...
  python -m pip install -r requirements.txt
)
echo Запускаю...
python "{entry}"
echo.
pause
"""
    sh = f"""#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "=== Установка и запуск ==="
PY=python3
command -v $PY >/dev/null 2>&1 || PY=python
if ! command -v $PY >/dev/null 2>&1; then
  echo "Python 3 не найден. Установи с https://www.python.org/downloads/"
  read -r -p "Нажми Enter..."
  exit 1
fi
$PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null 2>&1
if [ -f requirements.txt ]; then
  echo "Ставлю зависимости..."
  pip install -r requirements.txt
fi
echo "Запускаю..."
python "{entry}"
"""
    readme = f"""КАК ЗАПУСТИТЬ — в один клик

Windows:  двойной клик по файлу  run.bat
macOS:    двойной клик по        run.command   (если не открылся — правый клик → Открыть)
Linux:    в терминале:           bash run.sh

Скрипт сам создаст окружение, поставит зависимости и запустит:  {entry}

Нужен установленный Python 3 — https://www.python.org/downloads/
(на Windows при установке поставь галочку «Add python.exe to PATH»).
"""
    return {
        "run.bat": bat,
        "run.sh": sh,
        # macOS runs *.command on double-click; identical to run.sh.
        "run.command": sh,
        "КАК-ЗАПУСТИТЬ.txt": readme,
    }


def _node_launchers() -> dict[str, str]:
    bat = """@echo off
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
echo Запускаю...
call npm start
pause
"""
    sh = """#!/usr/bin/env bash
cd "$(dirname "$0")"
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js не найден. Установи с https://nodejs.org/"
  read -r -p "Нажми Enter..."
  exit 1
fi
echo "Ставлю зависимости..."
npm install
echo "Запускаю..."
npm start
"""
    readme = """КАК ЗАПУСТИТЬ — в один клик

Windows:  двойной клик по  run.bat
macOS:    двойной клик по  run.command
Linux:    в терминале:     bash run.sh

Нужен установленный Node.js — https://nodejs.org/
Скрипт выполнит `npm install` и `npm start`.
"""
    return {
        "run.bat": bat,
        "run.sh": sh,
        "run.command": sh,
        "КАК-ЗАПУСТИТЬ.txt": readme,
    }


def build_launchers(files: dict[str, str]) -> dict[str, str]:
    """Return the double-click launcher files to add to a project's download zip,
    or ``{}`` when the project isn't a runnable program (e.g. a plain website).

    Content-based (not template-based) so it also works for a `code` project that
    was pivoted to web but still carries its source. Never overwrites a file the
    project already ships (the caller uses ``setdefault``)."""
    py_files = {p: c for p, c in files.items() if p.endswith(".py")}
    if py_files:
        return _python_launchers(_pick_python_entry(py_files))
    if "package.json" in files:
        return _node_launchers()
    return {}


__all__ = ["build_launchers"]
