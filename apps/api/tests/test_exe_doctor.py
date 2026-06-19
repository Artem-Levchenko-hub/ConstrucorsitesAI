from omnia_api.services.exe_build import build_spec
from omnia_api.services import exe_doctor


async def test_heal_applies_hidden_import(monkeypatch):
    spec = build_spec({"app.py": "import pygame\nif __name__=='__main__':\n run()"}, slug="x")

    async def fake_model(prompt: str) -> str:
        return '{"hidden_imports": ["pkg_resources.py2_warn"], "collect_all": [], "requirements": null}'

    monkeypatch.setattr(exe_doctor, "_ask_model", fake_model)
    patched = await exe_doctor.heal("ModuleNotFoundError: pkg_resources.py2_warn", spec, {})
    assert patched is not None
    assert "pkg_resources.py2_warn" in patched.hidden_imports


async def test_heal_returns_none_on_unparseable(monkeypatch):
    spec = build_spec({"app.py": "x=1"}, slug="x")

    async def fake_model(prompt: str) -> str:
        return "sorry I cannot help"

    monkeypatch.setattr(exe_doctor, "_ask_model", fake_model)
    assert await exe_doctor.heal("some error", spec, {}) is None


async def test_heal_returns_none_when_no_new_info(monkeypatch):
    spec = build_spec({"app.py": "import pygame\nif __name__=='__main__': run()"}, slug="x")

    # model echoes back only what's already in the spec (collect_all already ["pygame"])
    async def fake_model(prompt: str) -> str:
        return '{"hidden_imports": [], "collect_all": ["pygame"], "requirements": null}'

    monkeypatch.setattr(exe_doctor, "_ask_model", fake_model)
    assert await exe_doctor.heal("err", spec, {}) is None
