"""Only synthetic local tests run here; no application or database is imported."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class FileTimingTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).with_name("pytest_file_timings.py")
        self.assertTrue(path.is_file(), "file timing plugin missing")
        spec = importlib.util.spec_from_file_location("file_timing_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_real_pytest_writes_atomic_phase_totals_even_with_call_and_setup_failures(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "tests").mkdir()
            (root / "tests/test_fixture.py").write_text(
                "import pytest\n"
                "@pytest.fixture\ndef broken(): raise RuntimeError('synthetic setup failure')\n"
                "def test_ok(): pass\n"
                "def test_failed(): assert False, 'synthetic call failure'\n"
                "def test_setup_failed(broken): pass\n",
                encoding="utf-8",
            )
            output = root / "scratch/timings/report.json"
            (root / "scratch").mkdir()
            environment = {
                **os.environ,
                "RUNNER_TEMP": str(root / "scratch"),
                "PYTHONPATH": str(Path(module.__file__).parent),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "pytest_file_timings",
                    "--file-timings-output",
                    str(output),
                    "tests/test_fixture.py",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = json.loads(output.read_bytes())
            self.assertEqual(report["schema"], 1)
            self.assertEqual(report["pytest_exit_status"], 1)
            self.assertEqual(report["collected_tests"], 3)
            entry = report["files"]["tests/test_fixture.py"]
            self.assertEqual(entry["reports"], {"setup": 3, "call": 2, "teardown": 3})
            self.assertEqual(entry["outcomes"]["failed"], 2)
            self.assertAlmostEqual(entry["seconds"], sum(entry["phase_seconds"].values()))
            self.assertGreater(entry["bytes"], 0)
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertFalse(output.with_suffix(".tmp").exists())

    def test_existing_or_outside_scratch_output_is_refused_before_write(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "tests").mkdir()
            (root / "tests/test_fixture.py").write_text("def test_ok(): pass\n")
            scratch = root / "scratch"
            scratch.mkdir()
            for output in (scratch / "taken/report.json", root / "outside/report.json"):
                if output.is_relative_to(scratch):
                    output.parent.mkdir()
                    output.write_bytes(b"retained original")
                config = SimpleNamespace(rootpath=root, args=["tests/test_fixture.py"])
                with (
                    patch.dict(os.environ, {"RUNNER_TEMP": str(scratch)}),
                    self.assertRaises((ValueError, FileExistsError)),
                ):
                    module.Recorder(config, output)
                if output.exists():
                    self.assertEqual(output.read_bytes(), b"retained original")
            self.assertFalse((root / "outside").exists())

    def test_disabled_telemetry_does_not_require_scratch_or_register_hooks(self):
        module = self.module()
        config = SimpleNamespace(getoption=lambda _name: None)
        with patch.dict(os.environ, {}, clear=True):
            module.pytest_configure(config)

    def test_late_output_collision_preserves_original_and_measured_temporary_report(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "tests").mkdir()
            (root / "tests/test_fixture.py").write_text("def test_ok(): pass\n")
            scratch = root / "scratch"
            scratch.mkdir()
            output = scratch / "timings/report.json"
            config = SimpleNamespace(rootpath=root, args=["tests/test_fixture.py"])
            with patch.dict(os.environ, {"RUNNER_TEMP": str(scratch)}):
                recorder = module.Recorder(config, output)
            output.write_bytes(b"retained concurrent output")
            with self.assertRaises(FileExistsError):
                recorder.pytest_sessionfinish(None, 1)
            self.assertEqual(output.read_bytes(), b"retained concurrent output")
            measured = json.loads(output.with_suffix(".tmp").read_bytes())
            self.assertEqual(measured["pytest_exit_status"], 1)

    def test_collection_error_still_produces_failed_report_with_selected_source(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "tests").mkdir()
            (root / "tests/test_syntax.py").write_text("this is deliberately invalid syntax\n")
            scratch = root / "scratch"
            scratch.mkdir()
            output = scratch / "timings/report.json"
            environment = {
                **os.environ,
                "RUNNER_TEMP": str(scratch),
                "PYTHONPATH": str(Path(module.__file__).parent),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "pytest_file_timings",
                    "--file-timings-output",
                    str(output),
                    "tests/test_syntax.py",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(output.read_bytes())
            self.assertEqual(report["pytest_exit_status"], result.returncode)
            self.assertEqual(report["collected_tests"], 0)
            self.assertEqual(set(report["files"]), {"tests/test_syntax.py"})


if __name__ == "__main__":
    unittest.main()
