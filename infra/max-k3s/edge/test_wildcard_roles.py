"""Run the certificate distributor CLI against a disposable, offline host fixture."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("20-wildcard-distribute.sh")
WINDOWS_GIT = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git"
)
BASH = shutil.which("bash") or next((
    str(path) for path in (WINDOWS_GIT / "bin/bash.exe", WINDOWS_GIT / "usr/bin/sh.exe")
    if path.is_file()
), None)
ACTIONS = ("receive", "apply", "status", "rollback", "authorize")


@unittest.skipUnless(BASH, "Bash is required for the distributor CLI contract")
class WildcardRoleContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edge-role-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = self.root / "host"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.effects = self.root / "effects.log"
        self.fixture_script = self.root / "max-edge-distribute.sh"
        self.receive = self.root / "receive"
        # Every hard-coded host path is confined to this temporary tree. The
        # implementation, role dispatch and certificate checks run unchanged.
        source = SCRIPT.read_text(encoding="utf-8")
        for original in ("/etc", "/opt", "/usr/local", "/var/lib"):
            source = source.replace(original, self.host.as_posix() + original)
        self.fixture_script.write_text(source, encoding="utf-8", newline="\n")
        self.fixture_script.chmod(0o700)
        edge = self.host / "etc/max-studio/edge"
        edge.mkdir(parents=True)
        (edge / "edge.env").write_text(
            "EDGE_DOMAIN=fixture.invalid\n", encoding="utf-8"
        )
        for base in (edge, self.receive):
            for group in ("root", "apps", "dev", "dev2"):
                directory = base / "certs" / group
                directory.mkdir(parents=True)
                for name in ("fullchain.pem", "privkey.pem"):
                    (directory / name).write_text("offline fixture\n", encoding="utf-8")
        for relative in (
            "etc/ssh/authorized_keys.d", "etc/ssh/sshd_config.d", "etc/sudoers.d",
            "etc/nginx/sites-available", "usr/local/sbin", "usr/local/bin",
        ):
            (self.host / relative).mkdir(parents=True, exist_ok=True)
        (self.host / "usr/local/sbin/max-edge-distribute").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n"
        )
        # These doubles replace host mutation/network boundaries, not role
        # validation. Any attempted effect is recorded even if the CLI exits 0.
        fake = """#!/bin/sh
name=${0##*/}
case "$name" in
  hostname) [ "${1:-}" = -I ] && echo 127.0.0.1 || echo core ;;
  id) [ "${1:-}" != -u ] || echo 0 ;;
  timeout) shift; exec "$@" ;;
  seq) echo 1 ;;
  openssl)
    case " $* " in
      *" -pubkey "*|*" -pubout "*) echo fixture-public-key ;;
      *" subjectAltName "*)
        echo 'DNS:fixture.invalid, DNS:*.fixture.invalid, DNS:*.apps.fixture.invalid'
        echo 'DNS:*.dev.fixture.invalid, DNS:*.dev2.fixture.invalid' ;;
      *" -enddate "*) echo 'notAfter=Jan 1 00:00:00 2099 GMT' ;;
    esac ;;
  *) printf '%s\\n' "$name $*" >> "$EDGE_TEST_EFFECTS"
     [ "$name" != mktemp ] || printf '%s\\n' "$EDGE_TEST_RECEIVE" ;;
esac
exit 0
"""
        for name in (
            "hostname", "id", "openssl", "install", "mktemp", "tar", "rm", "cp",
            "chmod", "chown", "mv", "ln", "nginx", "systemctl", "ssh", "useradd",
            "touch", "visudo", "sshd", "ssh-keygen", "timeout", "seq",
        ):
            path = self.bin / name
            path.write_text(fake, encoding="utf-8", newline="\n")
            path.chmod(0o700)
        (self.host / "usr/local/bin/k3s").write_text(
            fake, encoding="utf-8", newline="\n"
        )
        (self.host / "usr/local/bin/k3s").chmod(0o700)

    def host_snapshot(self):
        return {
            path.relative_to(self.host).as_posix(): path.read_bytes()
            for path in self.host.rglob("*") if path.is_file()
        }

    def invoke(self, action, role=None, *, extra=()):
        if self.effects.exists():
            self.effects.unlink()
        args = [action]
        if action == "authorize":
            args.append("ssh-ed25519 fixture-public-key max-edge@fixture")
        if role is not None:
            args.append(role)
        env = {
            **os.environ,
            "PATH": os.pathsep.join((
                self.bin.as_posix(), Path(BASH).parent.as_posix(),
                os.environ.get("PATH", ""),
            )),
            "EDGE_TEST_EFFECTS": self.effects.as_posix(),
            "EDGE_TEST_RECEIVE": self.receive.as_posix(),
            "MSYS2_ARG_CONV_EXCL": "*",
        }
        env.pop("BASH_ENV", None)
        env.pop("ENV", None)
        result = subprocess.run(
            [BASH, str(self.fixture_script), *args, *extra],
            input=b"", capture_output=True,
            env=env, timeout=15,
        )
        effects = self.effects.read_text(encoding="utf-8") if self.effects.exists() else ""
        return result, effects

    def assert_rejected_without_effects(self, action, role=None, *, extra=()):
        before = self.host_snapshot()
        result, effects = self.invoke(action, role, extra=extra)
        evidence = json.dumps({
            "exit": result.returncode, "effects": effects,
            "stdout": result.stdout.decode("utf-8", errors="replace")[-1000:],
            "stderr": result.stderr.decode("utf-8", errors="replace")[-1000:],
        })
        self.assertNotEqual(result.returncode, 0, evidence)
        self.assertEqual(effects, "", evidence)
        self.assertEqual(self.host_snapshot(), before, evidence)
        self.assertTrue(result.stderr, evidence)

    def test_unknown_role_is_rejected_before_any_host_effect(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assert_rejected_without_effects(action, "typo-role")

    def test_missing_role_is_rejected_even_when_hostname_is_core(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assert_rejected_without_effects(action)

    def test_malformed_roles_are_not_defaulted_or_normalized(self):
        for action in ACTIONS:
            for role in ("", "Core", " core", "runtime\ncommerce", "../core", "core runtime"):
                with self.subTest(action=action, role=repr(role)):
                    self.assert_rejected_without_effects(action, role)

    def test_extra_arguments_are_rejected_before_any_host_effect(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assert_rejected_without_effects(action, "core", extra=("extra",))

    def test_supported_roles_keep_their_apply_targets(self):
        expected = {
            "core": ("ln -sfn ../../certs/dev/fullchain.pem", "systemctl reload nginx"),
            "runtime": ("k3s kubectl -n kube-system create secret tls wildcard-yleum",),
            "commerce": ("ln -sfn ../../certs/dev2/fullchain.pem", "systemctl reload nginx"),
        }
        for role, targets in expected.items():
            with self.subTest(role=role):
                if self.effects.exists():
                    self.effects.unlink()
                result, effects = self.invoke("apply", role)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                for target in targets:
                    self.assertIn(target, effects)
                if role != "runtime":
                    self.assertNotIn("k3s ", effects)

    def test_supported_role_still_requires_its_own_certificate_group(self):
        for role, group in (("core", "dev"), ("runtime", "apps"), ("commerce", "dev2")):
            with self.subTest(role=role):
                certificate = self.host / "etc/max-studio/edge/certs" / group / "fullchain.pem"
                content = certificate.read_bytes()
                certificate.unlink()
                try:
                    self.assert_rejected_without_effects("apply", role)
                finally:
                    certificate.write_bytes(content)


if __name__ == "__main__":
    unittest.main()
