"""Real managed TypeScript modules, no live runtime or production data."""
import hashlib
import os
import subprocess
from pathlib import Path

import playwright

from .behavior_browser_fixture import installed_chromium


def _node_fixture(filename, browser_environment=None):
    harness = Path(__file__).with_name("fixtures") / filename
    result = subprocess.run(["node", "--test", str(harness)], text=True,
                            capture_output=True, timeout=60, check=False, encoding="utf-8",
                            env=None if browser_environment is None else
                            {**os.environ, **browser_environment})
    assert result.returncode == 0, result.stdout + result.stderr


def test_managed_runtime_sdk():
    _node_fixture("max_management_sdk.test.mjs")


def test_real_max_provider_identity_lifecycle():
    _node_fixture("max_provider_lifecycle.test.mjs")


def test_real_max_provider_gate_covers_portals():
    executable = installed_chromium()
    env = {
        "QA_PLAYWRIGHT_MODULE": str(Path(playwright.__file__).parent / "driver/package"),
        "QA_BEHAVIOR_CHROMIUM_EXECUTABLE": executable,
        "QA_BEHAVIOR_CHROMIUM_SHA256": hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
    }
    _node_fixture("max_provider_gate.browser.mjs", env)
