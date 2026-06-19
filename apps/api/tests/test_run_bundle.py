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
    assert '"snake.py"' in out["run.bat"]
    assert 'python "snake.py"' in out["run.sh"]
    assert "requirements.txt" in out["run.bat"]
    # macOS double-click file mirrors the shell script.
    assert out["run.command"] == out["run.sh"]


def test_builder_script_never_wins_the_entry() -> None:
    """The real prod bug: a bundle ships BOTH the game and a PyInstaller .exe-builder,
    each with an __main__ guard. "run" must launch the game, not compile an .exe."""
    files = {
        "build_installer.py": "import PyInstaller\nif __name__ == '__main__':\n    build()",
        "snake_game.py": "import pygame\nif __name__ == '__main__':\n    SnakeGame().run()",
        "requirements.txt": "pygame\npyinstaller",
        "README.md": "doc",
    }
    assert _pick_python_entry(
        {p: c for p, c in files.items() if p.endswith(".py")}
    ) == "snake_game.py"
    out = build_launchers(files)
    assert '"snake_game.py"' in out["run.bat"]
    assert 'python "snake_game.py"' in out["run.sh"]
    assert "build_installer.py" not in out["run.bat"]


def test_builder_excluded_by_content_even_with_neutral_name() -> None:
    # Named neither build* nor install*, but it's clearly a packager → not the entry.
    assert _pick_python_entry(
        {
            "package_app.py": "import PyInstaller\nif __name__ == '__main__':\n    go()",
            "game.py": "import pygame\nif __name__ == '__main__':\n    main()",
        }
    ) == "game.py"


def test_tooling_names_excluded() -> None:
    assert _pick_python_entry(
        {"setup.py": "if __name__ == '__main__':\n    setup()",
         "app.py": "if __name__ == '__main__':\n    main()"}
    ) == "app.py"
    assert _pick_python_entry(
        {"test_app.py": "if __name__ == '__main__':\n    t()",
         "main.py": "if __name__ == '__main__':\n    m()"}
    ) == "main.py"


def test_all_tooling_does_not_strand() -> None:
    # If literally every file looks like tooling, still return something runnable.
    only = {"build.py": "import PyInstaller\nif __name__ == '__main__':\n    b()"}
    assert _pick_python_entry(only) == "build.py"


def test_entry_prefers_main_guard_then_common_names() -> None:
    # Real __main__ guard wins even over a common-named file.
    assert _pick_python_entry(
        {"util.py": "x=1", "core.py": "if __name__ == '__main__': run()"}
    ) == "core.py"
    # No guard → common entry name.
    assert _pick_python_entry({"helpers.py": "x=1", "main.py": "print(1)"}) == "main.py"
    # Neither → shallowest/only.
    assert _pick_python_entry({"lib/a.py": "x", "b.py": "y"}) == "b.py"


def test_guard_is_a_real_guard_not_a_bare_mention() -> None:
    # A docstring/comment mention of __main__ must not beat a real guarded entry.
    files = {
        "notes.py": "# this module is not for __main__ use\nVALUE = 1",
        "run.py": "if __name__ == '__main__':\n    go()",
    }
    assert _pick_python_entry(files) == "run.py"


def test_windows_launcher_is_hardened() -> None:
    out = build_launchers({"app.py": "if __name__ == '__main__':\n    main()"})
    bat = out["run.bat"]
    assert "py -3" in bat                      # py-launcher dodges the Store alias
    assert "errorlevel" in bat                 # failures are gated, not swallowed
    assert ".venv\\Scripts\\python.exe" in bat  # runs the venv python explicitly


def test_shell_launcher_has_lf_and_crash_pause() -> None:
    out = build_launchers({"app.py": "if __name__ == '__main__':\n    main()"})
    assert "\r" not in out["run.sh"]           # CRLF would break the bash shebang
    assert "status=$?" in out["run.sh"]        # window survives a crash
    assert "|| {" in out["run.sh"]             # venv/deps failures are gated


def test_node_uses_start_dev_or_node_entry() -> None:
    # Real start script → npm start.
    out = build_launchers({"package.json": '{"scripts":{"start":"node x"}}'})
    assert "npm start" in out["run.bat"]
    # No start but a dev script → npm run dev (not a dead `npm start`).
    out = build_launchers({"package.json": '{"scripts":{"dev":"vite"}}'})
    assert "npm run dev" in out["run.sh"]
    assert "npm start" not in out["run.sh"]
    # No scripts at all but a JS entry → node <entry>.
    out = build_launchers({"package.json": "{}", "server.js": "x"})
    assert 'node "server.js"' in out["run.sh"]


def test_existing_launcher_is_not_clobbered() -> None:
    # Project already ships its own run.sh → leave the whole trio alone.
    assert build_launchers({"main.py": "if __name__ == '__main__':\n    m()", "run.sh": "#!/bin/sh"}) == {}


def test_web_only_project_gets_no_launcher() -> None:
    """A plain website has nothing to run locally → no launcher (opened via the
    preview / Открыть instead)."""
    assert build_launchers({"index.html": "<html>", "style.css": "body{}"}) == {}
    assert build_launchers({}) == {}
