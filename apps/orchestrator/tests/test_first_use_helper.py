"""Exercise the optional scaffold utility with real visitor/owner HTTP sessions."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_first_use_action_and_reopen() -> None:
    suite = Path(__file__).parent / "fixtures" / "first-use.test.mjs"
    result = subprocess.run(
        ["node", "--test", str(suite)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
