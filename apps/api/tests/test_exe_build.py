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
    assert spec.entry == "snake_game.py"
    assert spec.name == "ZmeikaNaPiton"
    assert spec.version == "1.0.0"
    assert spec.requirements == "pygame>=2.5.2"


def test_windowed_and_assets_and_pygame_collect() -> None:
    files = {
        "snake_game.py": "import pygame\nif __name__ == '__main__':\n    run()",
        "assets/icon.ico": "(binary)",
        "sounds/eat.wav": "(binary)",
    }
    spec = build_spec(files, slug="snake")
    assert spec.windowed is True
    assert "assets;assets" in spec.datas
    assert "sounds;sounds" in spec.datas
    assert spec.collect_all == ["pygame"]
    assert spec.icon == "assets/icon.ico"


def test_cli_script_stays_console_no_assets() -> None:
    spec = build_spec({"cli.py": "import argparse\nif __name__ == '__main__':\n    main()"},
                      slug="tool")
    assert spec.windowed is False
    assert spec.datas == []
    assert spec.collect_all == []


def test_from_pygame_import_also_collects() -> None:
    # `from pygame import …` is windowed AND needs --collect-all=pygame, same as
    # bare `import pygame` — the two must not diverge.
    spec = build_spec(
        {"game.py": "from pygame import sprite\nif __name__ == '__main__':\n    run()"},
        slug="game")
    assert spec.windowed is True
    assert spec.collect_all == ["pygame"]


from omnia_api.services.exe_build import render_pyinstaller_args, render_nsi, render


def test_render_pyinstaller_args() -> None:
    spec = build_spec({"snake_game.py": "import pygame\nif __name__=='__main__':\n run()"},
                      slug="snake")
    args = render_pyinstaller_args(spec)
    assert args[0] == "pyinstaller"
    assert "--onefile" in args and "--windowed" in args
    assert "--collect-all=pygame" in args
    assert f"--name={spec.name}" in args
    assert args[-1] == "snake_game.py"


def test_render_nsi_has_install_shortcut_uninstall() -> None:
    spec = build_spec({"app.py": "if __name__=='__main__':\n m()"}, slug="my-tool")
    nsi = render_nsi(spec)
    assert 'OutFile "MyTool-Setup.exe"' in nsi
    assert "MyTool.exe" in nsi
    assert "CreateShortcut" in nsi
    assert "WriteUninstaller" in nsi
    files = render(spec)
    assert set(files) == {"build_spec.json", "installer.nsi"}
