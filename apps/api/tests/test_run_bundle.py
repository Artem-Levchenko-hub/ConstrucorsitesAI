"""Tests for the one-click run bundle (owner 2026-06-19 — «скачал → играешь»).

`build_launchers` drops a double-click launcher into the project's download zip so
a Python/Node project runs locally right after download. Pure + deterministic.
"""

from __future__ import annotations

from omnia_api.services.run_bundle import _pick_python_entry, build_launchers


def test_python_project_gets_launchers_running_the_entry() -> None:
    files = {
        "snake.py": "import pygame\nif __name__ == '__main__':\n    main()",
        "requirements.txt": "pygame",
        "README.md": "doc",
    }
    out = build_launchers(files)
    assert set(out) == {"run.bat", "run.sh", "run.command", "КАК-ЗАПУСТИТЬ.txt"}
    # The launcher must run the real entry point + install deps.
    assert 'python "snake.py"' in out["run.bat"]
    assert 'python "snake.py"' in out["run.sh"]
    assert "requirements.txt" in out["run.bat"]
    # macOS double-click file mirrors the shell script.
    assert out["run.command"] == out["run.sh"]


def test_entry_prefers_main_guard_then_common_names() -> None:
    # __main__ guard wins even over a common-named file.
    assert _pick_python_entry(
        {"util.py": "x=1", "core.py": "if __name__ == '__main__': run()"}
    ) == "core.py"
    # No guard → common entry name.
    assert _pick_python_entry({"helpers.py": "x=1", "main.py": "print(1)"}) == "main.py"
    # Neither → shallowest/only.
    assert _pick_python_entry({"lib/a.py": "x", "b.py": "y"}) == "b.py"


def test_node_project_gets_npm_launchers() -> None:
    out = build_launchers({"package.json": "{}", "server.js": "x"})
    assert set(out) == {"run.bat", "run.sh", "run.command", "КАК-ЗАПУСТИТЬ.txt"}
    assert "npm install" in out["run.bat"]
    assert "npm start" in out["run.sh"]


def test_web_only_project_gets_no_launcher() -> None:
    """A plain website has nothing to run locally → no launcher (opened via the
    preview / Открыть instead)."""
    assert build_launchers({"index.html": "<html>", "style.css": "body{}"}) == {}
    assert build_launchers({}) == {}
