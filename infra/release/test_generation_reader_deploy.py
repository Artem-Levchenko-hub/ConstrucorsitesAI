"""Execute canonical retry shell functions with inert transport/source doubles."""

import ast
import base64
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASH = shutil.which("bash") or shutil.which("sh")
PYTHON_BRIDGE = 'python3() { ' + shlex.quote(sys.executable.replace("\\", "/")) + ' "$@"; }\n'

@unittest.skipUnless(BASH, "bash required")
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
        result = subprocess.run([BASH, "-c", mock + command], capture_output=True, text=True, timeout=10)
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
            result = subprocess.run([BASH, "-c", PYTHON_BRIDGE + script], cwd=root, capture_output=True, text=True, timeout=10)
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

    def test_pair_transport_streams_exact_target_source_once_or_denies_before_ssh(self):
        for available in (True, False):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = b"# exact owned target bytes\nFIXTURE = True\n\n"
                (root / "target.py").write_bytes(source)
                script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
READER_NONCE=11111111-1111-4111-8111-111111111111
REPO=fixture
git() {
 [ "$*" = "-C fixture show $SHA:infra/release/generation-reader-inventory.py" ];
 [ "$SOURCE_AVAILABLE" = 1 ] || return 7;
 cat target.py;
}
ssh() { printf '%s\\n' "$*" >> transport; cat > payload; }
''' + self.function("reader_target") + "\nreader_target\n"
                result = subprocess.run(
                    [BASH, "-c", PYTHON_BRIDGE
                     + f"SOURCE_AVAILABLE={int(available)}\n" + script],
                    cwd=root, capture_output=True, text=True, timeout=10,
                )
                if not available:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((root / "transport").exists())
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = (root / "transport").read_text().splitlines()
                self.assertEqual(len(calls), 1)
                self.assertIn("StrictHostKeyChecking=yes", calls[0])
                self.assertIn("max-core", calls[0])
                self.assertLess(len(calls[0]), 512, "source must arrive on stdin, not argv")
                assignments = ast.parse((root / "payload").read_text()).body[:2]
                values = {item.targets[0].id: ast.literal_eval(item.value) for item in assignments}
                self.assertEqual(base64.b64decode(values["TARGET_SOURCE_B64"]), source)
                self.assertEqual(values["TARGET_SOURCE_SHA256"], hashlib.sha256(source).hexdigest())


class PinnedReaderPairTests(unittest.TestCase):
    target = "a" * 40
    nonce = "11111111-1111-4111-8111-111111111111"
    verified = {"verified": True, "digest": "b" * 64,
                "epoch": "22222222-2222-4222-8222-222222222222"}

    def wrapper(self):
        source = Path(__file__).with_name("deploy-prod.sh").read_text()
        marker = "<<'READER_TARGET_PAIR'\n"
        self.assertTrue(marker in source, "target validation must share one canonical observer pin")
        return source.split(marker, 1)[1].split("\nREADER_TARGET_PAIR", 1)[0]

    def invoke(self, *, right=None, failure="", actor="1000", checksum=None):
        source = b'''import json
from contextlib import contextmanager
from pathlib import Path
import owned_pair_fixture as fixture
fixture.imported = True
DIRECTORY = Path("/run/yleum-generation-readers")
@contextmanager
def observer_lock(*, readonly):
    assert readonly is True
    with fixture.pin:
        fixture.events.append("pin")
        try:
            yield
        finally:
            fixture.events.append("unpin")
def main():
    raise AssertionError("nested main would reacquire the exclusive observer lock")
def run_action(args):
    assert (args.action, args.host, args.release, args.owner_nonce) == (
        "verify-target", "core", fixture.target, fixture.nonce)
    assert fixture.pin.locked() and fixture.sent == fixture.source
    assert fixture.observer_waiting.wait(1) and not fixture.observer_done.is_set()
    fixture.events.append("core")
    if fixture.failure == "deadline":
        fixture.alarm_handler(14, None)
    if fixture.failure == "core":
        raise ValueError("private core diagnostics")
    print(json.dumps(fixture.left))
def read_root_json(path):
    assert path == DIRECTORY / "current.json" and fixture.pin.locked()
    return {"digest": fixture.left["digest"], "epoch": fixture.left["epoch"],
            "state": "ready", "release_sha": fixture.target}
def redis_connection():
    return object()
def check_live(snapshot, redis):
    assert fixture.pin.locked() and "peer" in fixture.events
    fixture.events.append("live")
    if fixture.failure in {"expired", "revoked"}:
        raise ValueError("snapshot lost authority")
'''
        fixture = SimpleNamespace(
            target=self.target, nonce=self.nonce, left=dict(self.verified),
            right=self.verified if right is None else right, failure=failure,
            events=[], pin=threading.Lock(), observer_waiting=threading.Event(),
            observer_done=threading.Event(), source=source, sent=None, imported=False,
        )

        class Input(io.BytesIO):
            def close(stream):
                if not stream.closed:
                    fixture.sent = stream.getvalue()
                super().close()

        class Peer:
            pid = 424242
            returncode = None

            def __init__(peer, command, **kwargs):
                self.assertTrue(fixture.pin.locked())
                fixture.events.append("spawn")
                fixture.command, fixture.options = command, kwargs
                fixture.peer = peer
                peer.stdin, peer.stdout, peer.stderr = Input(), io.BytesIO(), io.BytesIO()

                def observer():
                    fixture.observer_waiting.set()
                    with fixture.pin:
                        fixture.observer_done.set()

                fixture.observer = threading.Thread(target=observer, daemon=True)
                fixture.observer.start()

            def communicate(peer, **kwargs):
                self.assertTrue(fixture.pin.locked())
                self.assertGreater(kwargs["timeout"], 0)
                fixture.events.append("peer")
                if fixture.failure == "timeout":
                    raise subprocess.TimeoutExpired("synthetic ssh", 1)
                peer.returncode = 7 if fixture.failure == "commerce" else 0
                output = b"not JSON" if fixture.failure == "json" else json.dumps(fixture.right).encode()
                return output, b"private peer diagnostics"

            def poll(peer):
                return peer.returncode

            def wait(peer, *, timeout):
                fixture.events.append("reaped")
                return peer.returncode

        def kill_group(pid, sig):
            self.assertEqual((pid, sig), (Peer.pid, signal.SIGKILL))
            fixture.events.append("killed")
            fixture.peer.returncode = -9

        stdout, stderr = io.StringIO(), io.StringIO()

        def alarm_handler(sig, handler):
            if callable(handler):
                fixture.alarm_handler = handler

        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                "owned_pair_fixture": fixture,
                "pwd": SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(
                    pw_uid=1000, pw_gid=1000, pw_name="operator", pw_dir="/home/operator")),
            }))
            stack.enter_context(patch.dict(os.environ, {"SUDO_UID": actor, "SUDO_USER": "operator"}))
            stack.enter_context(patch.object(os, "geteuid", return_value=0, create=True))
            stack.enter_context(patch.object(os, "killpg", side_effect=kill_group, create=True))
            stack.enter_context(patch.object(signal, "SIGALRM", 14, create=True))
            stack.enter_context(patch.object(signal, "SIGKILL", 9, create=True))
            stack.enter_context(patch.object(signal, "signal", side_effect=alarm_handler))
            stack.enter_context(patch.object(signal, "alarm", return_value=0, create=True))
            stack.enter_context(patch.object(subprocess, "Popen", Peer))
            stack.enter_context(patch.object(sys, "argv", ["reader-pair", self.target, self.nonce]))
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            namespace = {
                "TARGET_SOURCE_B64": base64.b64encode(source).decode(),
                "TARGET_SOURCE_SHA256": checksum or hashlib.sha256(source).hexdigest(),
            }
            try:
                exec(compile(self.wrapper(), "reader-target-pair", "exec"), namespace)
            except SystemExit as result:
                code = result.code
            else:
                self.fail("the bounded helper must return an explicit status")
        if hasattr(fixture, "observer"):
            fixture.observer.join(1)
            self.assertFalse(fixture.observer.is_alive(), "observer pin leaked")
        return code, fixture, stdout.getvalue(), stderr.getvalue()

    def test_pair_uses_original_operator_and_one_pin_for_both_normal_validations(self):
        code, fixture, stdout, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(fixture.events, ["pin", "spawn", "core", "peer", "live", "unpin"])
        self.assertTrue(fixture.observer_done.is_set())
        self.assertEqual(fixture.command[:4], ["/usr/sbin/runuser", "-u", "operator", "--"])
        self.assertIn("BatchMode=yes", fixture.command)
        self.assertIn("StrictHostKeyChecking=yes", fixture.command)
        self.assertEqual(fixture.command[-7:], ["verify-target", "--host", "commerce", "--release",
                                               self.target, "--owner-nonce", self.nonce])
        self.assertEqual(fixture.options["env"]["HOME"], "/home/operator")
        self.assertTrue(fixture.options["start_new_session"])
        self.assertIn("both reader inventories and retirement verified", stdout)

    def test_every_peer_result_must_match_the_complete_current_tuple(self):
        for right in ({**self.verified, "digest": "c" * 64},
                      {**self.verified, "epoch": "33333333-3333-4333-8333-333333333333"},
                      {**self.verified, "verified": False},
                      {**self.verified, "unexpected": True}, {"verified": True}):
            with self.subTest(right=right):
                code, fixture, stdout, _ = self.invoke(right=right)
                self.assertEqual(code, 75)
                self.assertEqual(fixture.events[-1], "unpin")
                self.assertNotIn("live", fixture.events)
                self.assertNotIn("verified\n", stdout)

    def test_failures_timeout_and_late_revocation_release_pin_and_reap_peer(self):
        for failure in ("core", "commerce", "json", "timeout", "deadline", "expired", "revoked"):
            with self.subTest(failure=failure):
                code, fixture, stdout, stderr = self.invoke(failure=failure)
                self.assertEqual(code, 75)
                self.assertEqual(fixture.events[-1], "unpin")
                if failure in {"core", "timeout", "deadline"}:
                    self.assertIn("killed", fixture.events)
                    self.assertIn("reaped", fixture.events)
                self.assertNotIn("private", stdout + stderr)

    def test_wrong_source_or_sudo_actor_denies_before_any_reader_action(self):
        for options in ({"checksum": "0" * 64}, {"actor": "0"}, {"actor": ""},
                        {"actor": "1001"}, {"actor": "-1"}):
            with self.subTest(options=options):
                code, fixture, _, _ = self.invoke(**options)
                self.assertEqual(code, 75)
                self.assertEqual(fixture.events, [])
                if "checksum" in options:
                    self.assertFalse(fixture.imported)


if __name__ == "__main__":
    unittest.main()
