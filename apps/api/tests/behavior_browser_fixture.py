"""Use Chromium installed by the locked Playwright package; no host fallback."""

import os
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from playwright.sync_api import sync_playwright


@lru_cache(maxsize=1)
def installed_chromium() -> str:
    if version("playwright") != "1.60.0":
        raise RuntimeError("Browser tests require the locked Playwright 1.60.0 package")
    # Explicit operator input is only for test environments that already retain
    # a different, approved browser revision. CI uses Playwright's registry.
    # Never silently fall back from a missing installed browser to a host path.
    override = os.environ.get("QA_BEHAVIOR_CHROMIUM_EXECUTABLE", "")
    pin = os.environ.get("QA_BEHAVIOR_CHROMIUM_SHA256", "")
    if override or pin:
        from yleum_api.services.behavior_driver_configuration import _pin

        try:
            _pin(override, pin)  # absolute/no-symlink/regular executable/hash/stat-race checks
        except Exception:
            raise RuntimeError("Explicit test browser pin is invalid") from None
        return override
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or executable.is_symlink()
        or not os.access(executable, os.X_OK)
    ):
        raise RuntimeError(
            "Install the locked browser with: playwright install --with-deps chromium"
        )
    return str(executable)
