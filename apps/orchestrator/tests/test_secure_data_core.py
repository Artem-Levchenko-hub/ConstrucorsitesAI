"""Execute the trusted TypeScript core with real Node crypto, without npm installs."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_secure_data_core_runtime():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node 22.13+ runtime unavailable")
    capability = subprocess.run(
        [node, "-e", "process.exit(typeof require('node:module').stripTypeScriptTypes === 'function' ? 0 : 1)"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if capability.returncode:
        pytest.skip("Node runtime lacks built-in TypeScript stripping (requires Node 22.13+)")
    harness = Path(__file__).parent / "fixtures" / "secure_data" / "run.cjs"
    result = subprocess.run(
        [node, str(harness)], capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_secure_data_http_boundary():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node 22 runtime unavailable")
    fixture = Path(__file__).parent / "fixtures" / "secure_data_http.test.mjs"
    result = subprocess.run(
        [node, "--experimental-strip-types", "--test", str(fixture)],
        capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
