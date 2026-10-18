"""Executable command operands, rather than comments or argv data, identify readers."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class CommandRoleTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("generation-reader-inventory.py")
        spec = importlib.util.spec_from_file_location("command_role_observer", source)
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)

    def test_valid_python_comments_cannot_break_or_invent_readers(self):
        for code in (
            "pass # yleum_probe's harmless comment",
            "# yleum_api.workers.generation\npass",
            "# yleum_api.workers.generation yleum_api.workers.run\npass",
        ):
            with self.subTest(code=code):
                compile(code, "<not-executed>", "exec")
                self.assertIsNone(self.observer.process_role(["python3", "-c", code]))

    def test_python_wrapper_data_is_not_executable_code(self):
        for data in (
            "yleum_unrelated's data",
            '{"note":"yleum_api.workers.generation yleum_api.workers.run"}',
        ):
            with self.subTest(data=data):
                self.assertIsNone(self.observer.process_role(
                    ["python3", "-c", "import sys; print(sys.argv[1])", data]
                ))
        self.assertEqual(self.observer.process_role([
            "python3", "-c", "import runpy,sys; runpy.run_module(sys.argv[1])",
            "yleum_api.workers.generation",
        ]), "generation-worker")

    def test_python_script_and_module_arguments_are_not_python_options(self):
        for prefix in (
            ["python3", "-m", "unrelated_module"],
            ["python3", "script.py"],
            ["python3", "--", "script.py"],
        ):
            with self.subTest(prefix=prefix):
                self.assertIsNone(self.observer.process_role(
                    prefix + ["-c", "yleum_unrelated's data"]
                ))
        self.assertEqual(self.observer.process_role([
            "python3", "-X", "utf8", "-W", "ignore", "-c",
            "import yleum_api.workers.generation",
        ]), "generation-worker")

    def test_only_shell_launchers_parse_shell_code(self):
        for argv in (
            ["node", "-c", "/tmp/yleum_project's/file.js"],
            ["node", "script.js", "python3", "-c", "yleum_unrelated's data"],
            ["sh", "script.sh", "-c", "yleum_unrelated's data"],
        ):
            with self.subTest(argv=argv):
                self.assertIsNone(self.observer.process_role(argv))
        self.assertEqual(self.observer.process_role([
            "unknown-launcher", "-c", "python -m yleum_api.workers.generation",
        ]), "generation-worker")

    def test_missing_code_and_excessive_shell_nesting_fail_closed(self):
        for argv in (["python3", "-c"], ["sh", "-lc"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                self.observer.process_role(argv)
        import shlex
        argv = ["python3", "-m", "yleum_api.workers.generation"]
        for _ in range(6):
            argv = ["sh", "-c", shlex.join(argv)]
        with self.assertRaises(ValueError):
            self.observer.process_role(argv)

    def test_real_python_and_shell_readers_remain_visible(self):
        cases = (
            (["python3", "-m", "yleum_api.workers.generation"], "generation-worker"),
            (["uvicorn", "yleum_orchestrator.main:app"], "orchestrator"),
            (["sh", "-lc", "exec python -m yleum_api.workers.run"], "worker"),
            (["python3", "-c", "import yleum_api.workers.generation"], "generation-worker"),
            (["python3", "-c", "from yleum_api.workers import run"], "worker"),
            (["python3", "-c", 'import uvicorn; uvicorn.run("yleum_api.main:app")'], "api"),
            (["sh", "-lc", 'python3 -c "import yleum_api.workers.generation"'], "generation-worker"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(self.observer.process_role(argv), expected)

    def test_ambiguous_or_malformed_possible_readers_fail_closed(self):
        for code in (
            "import yleum_api.workers.generation, yleum_api.workers.run",
            "'unterminated yleum_unknown",
        ):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.observer.process_role(["python3", "-c", code])
        self.assertEqual(self.observer.process_role(
            ["python3", "-c", "x" * 100 + " yleum_api.main:app"]
        ), "api")

    def test_failure_locations_do_not_expose_exception_bodies_or_locals(self):
        def fail():
            private_argv = "sensitive-argument-never-log"
            raise ValueError(private_argv)

        try:
            fail()
        except ValueError as exc:
            context = self.observer.failure_context(exc)
        self.assertEqual(context[-1]["function"], "fail")
        self.assertTrue(all(set(frame) == {"file", "function", "line"} for frame in context))
        self.assertTrue(all(Path(frame["file"]).name == frame["file"] for frame in context))
        self.assertNotIn("sensitive-argument-never-log", json.dumps(context))

    def test_remote_failure_forwards_locations_without_private_error_fields(self):
        context = [{"file": "generation-reader-inventory.py", "function": "collect_host", "line": 700}]
        result = SimpleNamespace(returncode=1, stdout="private-stdout", stderr=json.dumps({
            "ready": False, "failure_context": context, "message": "private-stderr",
        }))
        with patch.object(self.observer.subprocess, "run", return_value=result), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(ValueError):
                self.observer.command(["ssh", "commerce", "collect"])
        self.assertEqual(json.loads(output.getvalue()), {"host": "commerce", "failure_context": context})
        self.assertNotIn("private-", output.getvalue())
        for value in ("not-json", json.dumps({"failure_context": [
            {**context[0], "file": "/private/host/path.py"}
        ]})):
            self.assertEqual(self.observer.remote_failure_context(value), [])
        for function in ("<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>"):
            generated = [{**context[0], "function": function}]
            self.assertEqual(self.observer.remote_failure_context(json.dumps({
                "failure_context": generated,
            })), generated)


if __name__ == "__main__":
    unittest.main()
