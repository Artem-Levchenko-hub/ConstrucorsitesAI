import asyncio
import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from . import behavior_browser_fixture as fixture


@pytest.fixture
def registry(tmp_path, monkeypatch):
    executable = tmp_path / "installed-registry" / "chrome"
    executable.parent.mkdir()
    executable.write_bytes(b"synthetic registry executable, never launched")
    executable.chmod(0o700)

    @contextmanager
    def selected_registry():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            pytest.fail("Resolve the sync Playwright registry before entering an event loop")
        yield NS(chromium=NS(executable_path=str(executable)))

    fixture.installed_chromium.cache_clear()
    monkeypatch.delenv("QA_BEHAVIOR_CHROMIUM_EXECUTABLE", raising=False)
    monkeypatch.delenv("QA_BEHAVIOR_CHROMIUM_SHA256", raising=False)
    monkeypatch.setattr(fixture, "sync_playwright", selected_registry)
    monkeypatch.setattr(fixture, "version", lambda _: "1.60.0")
    yield executable
    fixture.installed_chromium.cache_clear()


def test_fixture_uses_locked_registry_location_without_host_path(registry):
    assert fixture.installed_chromium() == str(registry)


def test_missing_registry_browser_fails_without_fallback_or_skip(registry):
    registry.unlink()
    with pytest.raises(RuntimeError, match="playwright install --with-deps chromium"):
        fixture.installed_chromium()


def test_other_playwright_version_does_not_select_an_unpinned_browser(registry, monkeypatch):
    monkeypatch.setattr(fixture, "version", lambda _: "1.59.0")
    with pytest.raises(RuntimeError, match=r"locked Playwright 1\.60\.0"):
        fixture.installed_chromium()


def test_explicit_operator_local_browser_requires_actual_pin(registry, monkeypatch):
    monkeypatch.setenv("QA_BEHAVIOR_CHROMIUM_EXECUTABLE", str(registry))
    monkeypatch.setenv(
        "QA_BEHAVIOR_CHROMIUM_SHA256", hashlib.sha256(registry.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(
        fixture, "sync_playwright", lambda: pytest.fail("explicit pin queried registry")
    )
    assert fixture.installed_chromium() == str(registry)


@pytest.mark.parametrize("bad", ["hash", "missing", "relative", "symlink"])
def test_bad_explicit_pin_never_falls_back_to_registry(registry, monkeypatch, bad):
    path = registry
    pin = hashlib.sha256(registry.read_bytes()).hexdigest()
    if bad == "hash":
        pin = "0" * 64
    elif bad == "missing":
        pin = ""
    elif bad == "relative":
        path = registry.relative_to(registry.parent)
    else:
        path = registry.parent / "alias"
        path.symlink_to(registry)
    monkeypatch.setenv("QA_BEHAVIOR_CHROMIUM_EXECUTABLE", str(path))
    monkeypatch.setenv("QA_BEHAVIOR_CHROMIUM_SHA256", pin)
    with pytest.raises(RuntimeError, match="Explicit test browser pin is invalid"):
        fixture.installed_chromium()
