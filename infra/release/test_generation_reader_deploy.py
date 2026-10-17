"""Execute canonical retry shell functions with inert transport/source doubles."""

import json
import re
import shlex
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("bash"), "bash required")
class ReaderDeployTests(unittest.TestCase):
    def test_same_image_cutover_forces_only_three_captured_api_readers(self):
        source = Path(__file__).with_name("deploy-prod.sh").read_text()
        command = re.search(r"docker compose [^\n]+? up -d --no-build [^&;]+", source).group()
        command = command.split("2>", 1)[0].strip()
        arguments = shlex.split(command)
        self.assertEqual(arguments[-3:], ["api", "worker", "generation-worker"])
        mock = '''docker() {
case " $* " in *" --force-recreate "*) printf new-incarnations;; *) printf cached-old-incarnations;; esac
}
'''
        result = subprocess.run(["bash", "-c", mock + command], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "new-incarnations")

    def function(self, name):
        source = Path(__file__).with_name("deploy-prod.sh").read_text()
        return name + "() {" + source.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"

    def invoke(self, left, right, *, retry_before=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, value in (("core", left), ("commerce", right)):
                (root / name).write_text(json.dumps({"owner": value}))
            script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
reader_control() {
  if [ "$2" = prepare-owner ]; then cat "$1"; else printf '%s\\n' "$*" >> calls; fi
}
ssh() { echo forbidden >&2; return 99; }
''' + self.function("reader_prepare_retry") + self.function("reader_before")
            script += '\nreader_prepare_retry\nprintf "%s:%s\\n" "$READER_RETRY" "$READER_NONCE"\n'
            if retry_before:
                script += "reader_before\n"
            result = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True, timeout=10)
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            return result, calls

    def owner(self):
        return {"version": 1, "release_sha": "a" * 40,
                "owner_nonce": "11111111-1111-4111-8111-111111111111"}

    def test_fresh_release_has_no_retry_authority(self):
        result, calls = self.invoke(None, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0:")
        self.assertEqual(calls, "")

    def test_same_owner_partial_capture_reuses_nonce_and_skips_current_gate(self):
        for right in (None, self.owner()):
            with self.subTest(right=right):
                result, calls = self.invoke(self.owner(), right, retry_before=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "1:" + self.owner()["owner_nonce"])
                self.assertEqual(len(calls.splitlines()), 2)
                self.assertIn("core capture --release " + "a" * 40, calls)
                self.assertIn("commerce capture --release " + "a" * 40, calls)

    def test_foreign_target_or_nonce_denies_before_capture(self):
        for other in ({**self.owner(), "release_sha": "b" * 40},
                      {**self.owner(), "owner_nonce": "22222222-2222-4222-8222-222222222222"}):
            with self.subTest(other=other):
                result, calls = self.invoke(self.owner(), other)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
