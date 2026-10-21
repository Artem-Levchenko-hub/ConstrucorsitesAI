"""Exercise the real CMS grammar and a large opaque ciphertext, never real backups."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("cms-envelope.py")
OPENSSL = shutil.which("openssl")


def tlv(tag: int, body: bytes) -> bytes:
    size = len(body)
    encoded = size.to_bytes(max(1, (size.bit_length() + 7) // 8), "big")
    length = bytes([size]) if size < 128 else bytes([0x80 | len(encoded)]) + encoded
    return bytes([tag]) + length + body


ENVELOPED = bytes.fromhex("06092a864886f70d010703")
DATA = bytes.fromhex("06092a864886f70d010701")
RSA = bytes.fromhex("300d06092a864886f70d0101010500")
AES = bytes.fromhex("060960864801650304012a")
VERSION = bytes.fromhex("020100")
ISSUER = tlv(
    0x30, tlv(0x31, tlv(0x30, bytes.fromhex("0603550403") + tlv(0x0C, b"synthetic")))
)
RECIPIENT = tlv(
    0x30,
    VERSION + tlv(0x30, ISSUER + bytes.fromhex("020101")) + RSA + tlv(4, b"k" * 256),
)


def envelope(
    cipher: bytes = bytes(range(32)),
    *,
    version=VERSION,
    recipient=RECIPIENT,
    algorithm=None,
    tail=b"",
) -> bytes:
    if algorithm is None:
        algorithm = tlv(0x30, AES + tlv(4, b"i" * 16))
    content = tlv(0x30, DATA + algorithm + tlv(0x80, cipher))
    return tlv(
        0x30,
        ENVELOPED
        + tlv(0xA0, tlv(0x30, version + tlv(0x31, recipient) + content + tail)),
    )


def load_module():
    spec = importlib.util.spec_from_file_location("cms_envelope", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CmsEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "shared bounded CMS validator is missing")
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "synthetic.cms"

    def write(self, body=None):
        self.path.write_bytes(envelope() if body is None else body)
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def validate(self, sha, parser=None):
        return self.module.validate(
            self.path, sha, openssl_parser=parser or (lambda _: None)
        )

    def test_preserves_every_metadata_byte_and_actual_cipher_prefix(self):
        sha = self.write()
        seen = []
        result = self.validate(sha, seen.append)
        self.assertEqual(seen, [envelope(bytes(range(16)))])
        self.assertEqual(result["sha256"], sha)
        self.assertEqual(result["size_bytes"], self.path.stat().st_size)
        self.assertEqual(result["ciphertext_bytes"], 32)
        self.assertFalse(result["ciphertext_restore_verified"])

    def test_truncation_suffix_and_length_corruption_fail_before_openssl(self):
        original = envelope()
        cases = [
            original[:-1],
            original + b"suffix",
            b"\x30\x80" + original[4:],
            b"\x30\x85\0" + original[2:],
            original.replace(VERSION, b"\x02\x81\x01\0", 1),
        ]
        for index, body in enumerate(cases):
            with self.subTest(index=index):
                sha = self.write(body)
                parser = mock.Mock()
                with self.assertRaises(ValueError):
                    self.validate(sha, parser)
                parser.assert_not_called()

    def test_closed_profile_rejects_wrong_version_recipients_cipher_and_algorithms(
        self,
    ):
        cases = [
            envelope(version=b"\x02\x01\x02"),
            envelope(recipient=b""),
            envelope(recipient=RECIPIENT * 2),
            envelope(cipher=b""),
            envelope(cipher=b"c" * 17),
            envelope(tail=b"\xa1\0"),
            envelope(algorithm=tlv(0x30, AES + tlv(4, b"i" * 15))),
            envelope().replace(AES, AES[:-1] + b"\x29"),
            envelope().replace(RSA, RSA[:-1] + b"\x01"),
        ]
        for index, body in enumerate(cases):
            with self.subTest(index=index):
                sha = self.write(body)
                with self.assertRaises(ValueError):
                    self.validate(sha)

    def test_hash_mismatch_or_openssl_failure_never_passes(self):
        self.write()
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.validate("0" * 64)
        sha = self.write()
        with self.assertRaisesRegex(ValueError, "grammar"):
            self.validate(sha, mock.Mock(side_effect=ValueError("grammar")))

    def test_file_mutation_and_path_replacement_during_validation_refuse(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                if replacement and os.name == "nt":
                    continue  # Windows refuses replacing an open file itself.
                sha = self.write()

                def change(_, replacement=replacement):
                    if replacement:
                        replacement_path = self.root / "replacement.cms"
                        replacement_path.write_bytes(self.path.read_bytes())
                        os.replace(replacement_path, self.path)
                    else:
                        with self.path.open("r+b") as target:
                            target.seek(-1, 2)
                            target.write(b"x")
                        # NTFS may defer publishing LastWriteTime while another
                        # handle remains open. Publish a deterministic changed
                        # timestamp; the independent ctime-only test stays strict.
                        info = self.path.stat()
                        os.utime(
                            self.path,
                            ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000),
                        )

                with self.assertRaisesRegex(ValueError, "changed"):
                    self.validate(sha, change)

    def test_unrelated_sibling_creation_during_validation_is_allowed(self):
        sha = self.write()

        def sibling(_):
            (self.root / "unrelated-sibling").write_bytes(
                b"independent synthetic fixture"
            )

        self.assertEqual(self.validate(sha, sibling)["sha256"], sha)

    @unittest.skipIf(os.name == "nt", "POSIX directory replacement and mode fixture")
    def test_directory_replacement_or_permission_change_refuses_even_with_same_file_inode(
        self,
    ):
        original = self.root / "original"
        replacement = self.root / "replacement"
        retained = self.root / "retained"
        original.mkdir()
        replacement.mkdir()
        target = original / self.path.name
        target.write_bytes(envelope())
        os.link(target, replacement / target.name)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        original_file = self.module.identity(target.stat())

        def replace_directory(_):
            original.rename(retained)
            replacement.rename(original)
            self.assertEqual(self.module.identity(target.stat()), original_file)

        with self.assertRaisesRegex(ValueError, "path_changed"):
            self.module.validate(target, sha, openssl_parser=replace_directory)

        def change_mode(_):
            original.chmod(0o755 if original.stat().st_mode & 0o077 == 0 else 0o700)

        with self.assertRaisesRegex(ValueError, "path_changed"):
            self.module.validate(target, sha, openssl_parser=change_mode)

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_file_and_ancestor_symlinks_refused(self):
        sha = self.write()
        alias = self.root / "alias.cms"
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            self.module.validate(alias, sha, openssl_parser=lambda _: None)
        alias.unlink()
        directory = self.root / "alias"
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.module.validate(
                directory / self.path.name, sha, openssl_parser=lambda _: None
            )

    def test_sidecar_requires_one_exact_filename_and_digest(self):
        sha = self.write()
        sidecar = self.root / "OFFHOST_SHA256"
        sidecar.write_text(f"{sha}  {self.path.name}\n", encoding="ascii", newline="\n")
        self.assertEqual(self.module.sidecar_digest(sidecar, self.path.name), sha)
        for content in (
            f"{sha}  foreign.cms\n",
            f"{sha}  {self.path.name}\nextra\n",
            f"{sha.upper()}  {self.path.name}\n",
        ):
            sidecar.write_text(content, encoding="ascii", newline="\n")
            with self.assertRaises(ValueError):
                self.module.sidecar_digest(sidecar, self.path.name)

    def test_metadata_depth_node_and_byte_bounds(self):
        excessive = tlv(
            0x30,
            VERSION + tlv(0x30, tlv(0x30, b"x" * (300 * 1024))) + RSA + tlv(4, b"k"),
        )
        nested = tlv(0x0C, b"x")
        for _ in range(40):
            nested = tlv(0x30, nested)
        many = tlv(
            0x30, VERSION + tlv(0x30, tlv(0x30, b"\x05\0" * 3000)) + RSA + tlv(4, b"k")
        )
        for recipient in (excessive, many, RECIPIENT.replace(ISSUER, nested)):
            with self.subTest(size=len(recipient)):
                sha = self.write(envelope(recipient=recipient))
                with self.assertRaises(ValueError):
                    self.validate(sha)

    def test_ciphertext_corruption_and_unbounded_reads_are_not_hidden_by_compacting(
        self,
    ):
        sha = self.write()
        changed = self.path.read_bytes()[:-1] + b"x"
        self.path.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.validate(sha)
        sha = self.write()
        original_open = self.module.open_physical

        class BoundedReader:
            def __init__(self, source):
                self.source = source

            def read(self, size=-1):
                self_limit = self_module.CHUNK
                if not 0 <= size <= self_limit:
                    raise AssertionError("unbounded input read")
                return self.source.read(size)

            def __getattr__(self, name):
                return getattr(self.source, name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.source.__exit__(*args)

        self_module = self.module

        def bounded_open(path):
            source, *rest = original_open(path)
            return BoundedReader(source), *rest

        with mock.patch.object(self.module, "open_physical", bounded_open):
            self.validate(sha)

    def test_reparse_flag_and_ctime_only_change_refuse(self):
        sha = self.write()
        real_fstat = self.module.os.fstat
        altered = False

        def changed_stat(fd):
            info = real_fstat(fd)
            if not altered:
                return info
            proxy = mock.Mock(wraps=info)
            for name in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            ):
                setattr(proxy, name, getattr(info, name))
            proxy.st_ctime_ns += 1
            return proxy

        def change_ctime(_):
            nonlocal altered
            altered = True

        with mock.patch.object(self.module.os, "fstat", changed_stat):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.validate(sha, change_ctime)
        real_lstat = Path.lstat

        def reparse(path):
            info = real_lstat(path)
            if path != self.path:
                return info
            proxy = mock.Mock(wraps=info)
            proxy.st_mode = info.st_mode
            proxy.st_file_attributes = 0x400
            return proxy

        with mock.patch.object(Path, "lstat", reparse):
            with self.assertRaisesRegex(ValueError, "physical_path"):
                self.validate(sha)

    def test_all_three_consumers_use_shared_gate_with_expected_whole_file_hash(self):
        root = SCRIPT.parents[2]
        backup = (SCRIPT.parent / "backup-omnia.sh").read_text(encoding="utf-8")
        restore = (SCRIPT.parent / "restore-test-omnia.sh").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/offhost-backup.yml").read_text(
            encoding="utf-8"
        )
        for source in (backup, restore, workflow):
            self.assertIn("cms-envelope.py", source)
            self.assertNotIn("openssl cms -cmsout -inform DER -in", source)
        self.assertIn('--sha256-file "$dir/OFFHOST_SHA256"', backup)
        self.assertIn('--sha256-file "${latest}/OFFHOST_SHA256"', restore)
        self.assertIn('--sha256 "${expected_sha}"', workflow)
        self.assertLess(
            workflow.index("actions/checkout@v4"), workflow.index("cms-envelope.py")
        )


@unittest.skipUnless(OPENSSL, "real OpenSSL CLI is required")
class RealOpenSSLTests(unittest.TestCase):
    setUp = CmsEnvelopeTests.setUp

    def test_small_real_encrypt_validate_decrypt_roundtrip(self):
        key, cert, plain, restored = [
            self.root / name for name in ("key.pem", "cert.pem", "plain", "restored")
        ]
        commands = [
            [
                OPENSSL,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-subj",
                "/CN=synthetic-cms-fixture",
                "-days",
                "1",
            ]
        ]
        plain.write_bytes(b"synthetic recovery fixture\0" * 100)
        commands.append(
            [
                OPENSSL,
                "cms",
                "-encrypt",
                "-binary",
                "-aes-256-cbc",
                "-in",
                str(plain),
                "-out",
                str(self.path),
                "-outform",
                "DER",
                str(cert),
            ]
        )
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.module.validate(self.path, sha)
        subprocess.run(
            [
                OPENSSL,
                "cms",
                "-decrypt",
                "-binary",
                "-inform",
                "DER",
                "-in",
                str(self.path),
                "-recip",
                str(cert),
                "-inkey",
                str(key),
                "-out",
                str(restored),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(restored.read_bytes(), plain.read_bytes())

    @unittest.skipIf(
        os.name == "nt", "requires Linux RLIMIT_AS and sparse file support"
    )
    def test_sparse_above_openssl_limit_is_validated_under_96_mib(self):
        # A grammar-valid synthetic envelope, not a decryptability claim. Only
        # the primitive opaque ciphertext is enlarged; no recovery data is read.
        cipher_size = 0x60000010
        little = envelope(b"c" * 16)
        spec = self.module.inspect_metadata(little)
        prefix = self.module.envelope_prefix(spec, cipher_size)
        with self.path.open("wb") as target:
            target.write(prefix)
            target.seek(cipher_size - 1, 1)
            target.write(b"\0")
        digest = hashlib.sha256()
        with self.path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        # The original OpenSSL whole-object parser genuinely fails. The new
        # helper must finish in the SAME bounded child, not skip the large gate.
        import resource

        def limited():
            resource.setrlimit(resource.RLIMIT_AS, (96 * 1024 * 1024,) * 2)

        old = subprocess.run(
            [
                OPENSSL,
                "cms",
                "-cmsout",
                "-inform",
                "DER",
                "-in",
                str(self.path),
                "-noout",
            ],
            capture_output=True,
            preexec_fn=limited,
            timeout=45,
        )
        self.assertNotEqual(old.returncode, 0)
        self.assertIn(b"malloc", old.stderr.lower())
        new = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                str(self.path),
                "--sha256",
                digest.hexdigest(),
            ],
            capture_output=True,
            preexec_fn=limited,
            timeout=90,
        )
        self.assertEqual(new.returncode, 0, new.stderr.decode())
        self.assertEqual(json.loads(new.stdout)["ciphertext_bytes"], cipher_size)


if __name__ == "__main__":
    unittest.main()
