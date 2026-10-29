"""Real managed TypeScript modules, no live runtime or production data."""
import subprocess
from pathlib import Path


def test_managed_runtime_sdk():
    harness = Path(__file__).with_name("fixtures") / "max_management_sdk.test.mjs"
    result = subprocess.run(["node", "--test", str(harness)], text=True,
                            capture_output=True, timeout=60, check=False, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
