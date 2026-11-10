"""Release-gate contract: real flag parsing/compose checks with no live calls."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).with_name("deploy-prod.sh").read_text(encoding="utf-8")
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")
SHA = "a" * 40


@unittest.skipUnless(BASH, "bash required")
class GenerationWindowTests(unittest.TestCase):
    def parse(self, *args):
        preamble = SOURCE.split("\nREPO=", 1)[0]
        # Windows argv translation drops trailing LF in a positional argument.
        # Environment values preserve the exact hostile input for Bash parsing.
        arguments = (SHA, *args)
        environment = {**os.environ, **{f"WINDOW_TEST_ARG_{i}": value
                                        for i, value in enumerate(arguments)}}
        setup = "set -- " + " ".join(f'"$WINDOW_TEST_ARG_{i}"' for i in range(len(arguments))) + "\n"
        return subprocess.run(
            [BASH, "-c", setup + preamble + '\nprintf "window=%s;api=%s;web=%s" "$GENERATION_WINDOW" "$API" "$WEB"'],
            capture_output=True, text=True, timeout=5, env=environment,
        )

    def test_without_override_preserves_existing_configuration(self):
        result = self.parse()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "window=;api=1;web=1")

    def test_explicit_valid_seconds_accepts_full_and_backend_release(self):
        for seconds in ("60", "3300", "7200"):
            for mode in ((), ("--no-web",)):
                with self.subTest(seconds=seconds, mode=mode):
                    result = self.parse("--generation-window", seconds, *mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("window=" + seconds, result.stdout)

    def test_invalid_window_denies_before_any_release_command(self):
        for value in ("", "0", "59", "7201", "99999", "03300", "3.3", "-1", "+3300",
                      "3300\n", " 3300", "3300;true", "$(true)", "`true`"):
            with self.subTest(value=value):
                result = self.parse("--generation-window", value)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        self.assertNotEqual(self.parse("--generation-window").returncode, 0)

    def test_web_only_and_owned_continuations_reject_new_window(self):
        nonce = "11111111-1111-4111-8111-111111111111"
        for mode in (("--web-only",),
                     ("--finalize-owned-release", nonce, "--control-revision", SHA, "--no-web"),
                     ("--resume-owned-release", nonce, "--control-revision", SHA)):
            with self.subTest(mode=mode):
                result = self.parse(*mode, "--generation-window", "3300")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def compose_check(self, window, values):
        prefix = 'docker compose -f docker-compose.yml -f docker-compose.hostdb.yml config --format json 2>/dev/null | python3 -c \'\n'
        code = SOURCE.split(prefix, 1)[1].split("\n'\"", 1)[0].replace('\\"', '"')
        for name, value in {"API": "1", "WEB": "1", "SHA": SHA, "REPAIR": "3600",
                            "LEGAL": "", "GENERATION_WINDOW": window}.items():
            code = code.replace("$" + name, value)
        roles = {role: {"environment": {"RESTORATION_ADAPTATION_REPAIR_SECONDS": "3600",
                                        "MAX_GENERATION_DEADLINE_SECONDS": value},
                        "image": "fixture:" + SHA} for role, value in values.items()}
        roles["api"]["environment"]["OAUTH_LOGIN_REDIRECT_BASE_URL"] = "https://yleum.ru"
        roles["web"] = {"image": "fixture:" + SHA,
                        "environment": {"OMNIA_RELEASE_SHA": SHA}}
        with patch("sys.stdin", io.StringIO(json.dumps({"services": roles}))), patch("sys.stdout", io.StringIO()):
            exec(compile(code, "canonical-compose-proof", "exec"), {})

    def test_rendered_compose_requires_requested_window_for_every_reader(self):
        roles = {name: "3300" for name in ("api", "worker", "generation-worker")}
        self.compose_check("3300", roles)
        for role in roles:
            with self.subTest(role=role), self.assertRaises(AssertionError):
                self.compose_check("3300", {**roles, role: "1500"})

    def test_no_override_retains_existing_compose_value(self):
        self.compose_check("", {name: "1600" for name in ("api", "worker", "generation-worker")})

    def test_existing_environment_is_changed_only_in_owned_drained_release(self):
        write = SOURCE.index("update-env-value.sh .env MAX_GENERATION_DEADLINE_SECONDS -")
        for earlier in ('say "core: замок выкатки"', "ingress_drain begin", "reader_before"):
            self.assertLess(SOURCE.rindex(earlier, 0, write), write)
        self.assertLess(write, SOURCE.index('say "core: проверка отрендеренного compose'))
        self.assertLess(write, SOURCE.index('say "core: api worker generation-worker'))


if __name__ == "__main__":
    unittest.main()
