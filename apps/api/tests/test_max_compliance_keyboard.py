"""Real keyboard/layout gate for the actual managed footer, not copied CSS."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import playwright

from .behavior_browser_fixture import installed_chromium


def test_managed_footer_keyboard_links_clear_fixed_navigation():
    node = shutil.which("node")
    assert node is not None, "The browser gate requires Node and frozen template dependencies"
    fixture = Path(__file__).parent / "fixtures/compliance-keyboard.browser.mjs"
    env = dict(os.environ)
    env["QA_PLAYWRIGHT_MODULE"] = str(Path(playwright.__file__).parent / "driver/package")
    executable = installed_chromium()
    # Registry is verified by the shared fixture. Explicit operator overrides retain its hash guard.
    env["QA_BEHAVIOR_CHROMIUM_EXECUTABLE"] = executable
    env["QA_BEHAVIOR_CHROMIUM_SHA256"] = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    result = subprocess.run(
        [node, str(fixture)], env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["fixture"] == "actual-managed-footer"
    assert len(receipt["results"]) == 54
    assert all(row["hit"] and row["outline"] >= 2 for row in receipt["results"])
