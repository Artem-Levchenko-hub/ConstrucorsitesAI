"""Offline CMS recovery with synthetic keys only; never a customer restore."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import tempfile
import tracemalloc
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra/backup/cms-decrypt.py"
BLOCK = b"synthetic-backup\0" * (1024 * 1024 // 17)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class CmsDecryptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.certificate = cls.make_cert(cls.key)
        cls.other_certificate = cls.make_cert(cls.other_key)

    @classmethod
    def make_cert(cls, key, serial=17, common_name="offline-synthetic"):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.now(UTC)
        return (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(serial)
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))

    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "bounded offline CMS decryptor is missing")
        self.module = load(SCRIPT, "cms_decrypt_tests")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "synthetic.cms"
        self.output = self.directory / "restored.tgz"
        self.private = self.directory / "synthetic-private.pem"
        self.cert = self.directory / "synthetic-certificate.pem"
        self.private.write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        self.cert.write_bytes(self.certificate.public_bytes(serialization.Encoding.PEM))

    def write_cms(self, plaintext=b"synthetic\0archive" * 8):
        cms = (pkcs7.PKCS7EnvelopeBuilder().set_data(plaintext)
               .set_content_encryption_algorithm(algorithms.AES256)
               .add_recipient(self.certificate)
               .encrypt(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
        self.source.write_bytes(cms)
        return hashlib.sha256(cms).hexdigest()

    def decrypt(self, expected=None, **kwargs):
        return self.module.decrypt(
            self.source, self.output, private_key_path=self.private,
            certificate_path=self.cert, expected_sha256=expected or digest_file(self.source),
            **kwargs,
        )

    def assert_no_output_or_stage(self):
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.glob(".restored.tgz.*.tmp")), [])

    def test_short_and_final_padding_lengths_recover_every_byte(self):
        for size in (0, 1, 15, 16, 17, 31, 32, 4095, 4096, 4097):
            with self.subTest(size=size):
                plaintext = (b"a\0\r\n\xff" * (size // 5 + 1))[:size]
                cms_sha = self.write_cms(plaintext)
                receipt = self.decrypt(cms_sha)
                public_spki = self.certificate.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                self.assertEqual(receipt, {
                    "cms_sha256": cms_sha,
                    "cms_bytes": self.source.stat().st_size,
                    "ciphertext_bytes": 16 * (size // 16 + 1),
                    "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
                    "plaintext_bytes": size,
                    "recipient_public_spki_sha256": hashlib.sha256(public_spki).hexdigest(),
                })
                self.assertEqual(self.output.read_bytes(), plaintext)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
                self.assertEqual(self.output.stat().st_nlink, 1)
                self.output.unlink()

    def test_bad_sha_fails_before_private_input_is_opened(self):
        self.write_cms()
        self.private.unlink()
        self.cert.unlink()
        for expected in ("0" * 64, "A" * 64, "not-a-sha"):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, "checksum"):
                self.decrypt(expected)
            self.assert_no_output_or_stage()

    def test_wrong_key_spki_and_wrong_matching_recipient_key_fail(self):
        self.write_cms()
        self.private.write_bytes(self.other_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        with self.assertRaises(ValueError):
            self.decrypt()
        self.assert_no_output_or_stage()
        self.cert.write_bytes(self.other_certificate.public_bytes(serialization.Encoding.PEM))
        with self.assertRaises(ValueError):
            self.decrypt()
        self.assert_no_output_or_stage()

    def test_same_public_key_with_wrong_certificate_issuer_or_serial_is_not_a_recipient(self):
        self.write_cms()
        for wrong_cert in (self.make_cert(self.key, serial=18),
                           self.make_cert(self.key, common_name="other-synthetic-issuer")):
            with self.subTest(serial=wrong_cert.serial_number):
                self.cert.write_bytes(wrong_cert.public_bytes(serialization.Encoding.PEM))
                with self.assertRaisesRegex(ValueError, "recipient"):
                    self.decrypt()
                self.assert_no_output_or_stage()

    def test_invalid_final_padding_is_never_published(self):
        self.write_cms(b"p" * 32)  # Full final pad block: flip its last byte from 16 to zero.
        damaged = bytearray(self.source.read_bytes())
        damaged[-17] ^= 16
        self.source.write_bytes(damaged)
        with self.assertRaises(ValueError):
            self.decrypt()
        self.assert_no_output_or_stage()

    def test_unwrapped_content_key_must_be_exactly_32_bytes(self):
        self.write_cms()
        cms = self.source.read_bytes()
        metadata = self.module.DER.inspect_metadata(cms)
        short_key = self.key.public_key().encrypt(b"k" * 16, self.module.rsa_padding.PKCS1v15())
        self.source.write_bytes(cms.replace(metadata["encrypted_key"], short_key, 1))
        with self.assertRaisesRegex(ValueError, "content_key_length"):
            self.decrypt()
        self.assert_no_output_or_stage()

    def test_malformed_der_and_openssl_grammar_failure_are_not_bypassed(self):
        self.write_cms()
        original = self.source.read_bytes()
        for malformed in (original[:-1], original + b"trailing", b"\x31" + original[1:]):
            with self.subTest(size=len(malformed)):
                self.source.write_bytes(malformed)
                with self.assertRaises(ValueError):
                    self.decrypt()
                self.assert_no_output_or_stage()
        self.source.write_bytes(original)
        real_validate = self.module.DER.validate

        def rejected(_):
            raise ValueError("synthetic grammar rejection")

        def strict_gate(path, expected):
            return real_validate(path, expected, openssl_parser=rejected)

        with mock.patch.object(self.module.DER, "validate", side_effect=strict_gate):
            with self.assertRaises(ValueError):
                self.decrypt()
        self.assert_no_output_or_stage()

    def test_existing_output_or_hardlink_is_never_overwritten(self):
        self.write_cms()
        retained = self.directory / "retained"
        retained.write_bytes(b"previous recovery")
        os.link(retained, self.output)
        with self.assertRaises(FileExistsError):
            self.decrypt()
        self.assertEqual(retained.read_bytes(), b"previous recovery")
        self.assertEqual(self.output.read_bytes(), b"previous recovery")

    def test_concurrent_output_is_retained_and_stage_is_removed(self):
        self.write_cms()
        real_link = os.link

        def race(*args, **kwargs):
            self.output.write_bytes(b"concurrent recovery")
            return real_link(*args, **kwargs)

        with mock.patch.object(self.module.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.decrypt()
        self.assertEqual(self.output.read_bytes(), b"concurrent recovery")
        self.assertEqual(list(self.directory.glob(".restored.tgz.*.tmp")), [])

    def test_source_changed_after_initial_checksum_cannot_be_published(self):
        self.write_cms()
        real_validate = self.module.DER.validate

        def mutate(path, expected):
            receipt = real_validate(path, expected)
            with self.source.open("r+b") as source:
                source.seek(-32, os.SEEK_END)
                value = source.read(1)
                source.seek(-1, os.SEEK_CUR)
                source.write(bytes((value[0] ^ 1,)))
            before = self.source.stat()
            os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))
            return receipt

        with mock.patch.object(self.module.DER, "validate", side_effect=mutate):
            with self.assertRaises(ValueError):
                self.decrypt()
        self.assert_no_output_or_stage()

    def test_cms_bytes_used_by_decryption_are_rehashed_before_publication(self):
        self.write_cms()
        real_stream = self.module.decrypt_stream

        def changed_before_read(source, target, metadata, content_key, expected):
            with self.source.open("r+b") as changed:
                changed.seek(metadata["ciphertext_offset"])
                first = changed.read(1)
                changed.seek(-1, os.SEEK_CUR)
                changed.write(bytes((first[0] ^ 1,)))
            source.seek(0, os.SEEK_END)  # Discard the small original's read-ahead buffer.
            return real_stream(source, target, metadata, content_key, expected)

        with mock.patch.object(self.module, "decrypt_stream", side_effect=changed_before_read):
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.decrypt()
        self.assert_no_output_or_stage()

    def test_bounded_stream_supports_short_reads_and_short_write_is_rejected(self):
        plaintext = b"a" * (3 * 1024 * 1024 + 9)
        expected = self.write_cms(plaintext)
        with self.source.open("rb") as source:
            metadata = self.module.DER.parse_metadata(source, self.source.stat().st_size)
        key = self.key.decrypt(metadata["encrypted_key"], self.module.rsa_padding.PKCS1v15())
        source = io.BytesIO(self.source.read_bytes())

        class ShortReader:
            def read(inner, size):
                self.assertLessEqual(size, self.module.CHUNK)
                return source.read(min(size, 31))

            def seek(inner, offset):
                return source.seek(offset)

        output = io.BytesIO()
        receipt = self.module.decrypt_stream(ShortReader(), output, metadata, key, expected)
        self.assertEqual(output.getvalue(), plaintext)
        self.assertEqual(receipt["plaintext_bytes"], len(plaintext))
        source.seek(0)
        short_sink = mock.Mock(write=lambda data: max(0, len(data) - 1))
        with self.assertRaisesRegex(ValueError, "short_write"):
            self.module.decrypt_stream(source, short_sink, metadata, key, expected)

    @unittest.skipIf(os.name == "nt", "requires POSIX symlink privileges")
    def test_source_key_certificate_and_output_directory_symlinks_are_rejected(self):
        self.write_cms()
        for name in ("source", "private", "cert"):
            with self.subTest(input=name):
                original = getattr(self, name)
                link = self.directory / (name + "-symlink")
                link.symlink_to(original)
                setattr(self, name, link)
                with self.assertRaises(ValueError):
                    self.decrypt()
                setattr(self, name, original)
                self.assert_no_output_or_stage()
        actual = self.directory / "actual-directory"
        actual.mkdir()
        link = self.directory / "linked-directory"
        link.symlink_to(actual, target_is_directory=True)
        self.output = link / "restored.tgz"
        with self.assertRaises(ValueError):
            self.decrypt()
        self.assertFalse((actual / "restored.tgz").exists())

    def test_cli_receipt_and_errors_contain_no_private_input(self):
        expected = self.write_cms()
        args = [str(self.source), str(self.output), "--private-key", str(self.private),
                "--cert", str(self.cert), "--expected-sha256", expected]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.module.main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["cms_sha256"], expected)
        self.output.unlink()
        self.private.write_bytes(b"PRIVATE-DETAIL")
        with (mock.patch("sys.stderr", new_callable=io.StringIO) as error,
              mock.patch("sys.stdout", new_callable=io.StringIO) as output):
            self.assertEqual(self.module.main(args), 1)
        self.assertEqual(error.getvalue(), "cms-decrypt: decryption failed\n")
        self.assertEqual(output.getvalue(), "")

    @unittest.skipUnless(os.environ.get("YLEUM_SYNTHETIC_LARGE_CMS_PROOF") == "1",
                         "opt-in actual >2 GiB encrypted synthetic archive")
    def test_actual_above_two_gib_decrypts_with_bounded_memory(self):
        self.assertIsNotNone(shutil.which("openssl"))
        large_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        self.private.write_bytes(large_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        self.cert.write_bytes(self.make_cert(large_key).public_bytes(serialization.Encoding.PEM))
        encryptor = load(ROOT / "infra/backup/cms-encrypt.py", "synthetic_large_encrypt")
        plaintext = self.directory / "large-synthetic.tgz"
        size, digest = 0x80000011, hashlib.sha256()
        remaining = size
        with plaintext.open("wb") as target:
            while remaining:
                block = BLOCK[:min(len(BLOCK), remaining)]
                target.write(block)
                digest.update(block)
                remaining -= len(block)
        cms = encryptor.encrypt(plaintext, self.cert, self.source)
        self.assertGreater(cms["size_bytes"], 0x5FFFFFFC)
        tracemalloc.start()
        try:
            receipt = self.decrypt(cms["sha256"])
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(receipt["plaintext_bytes"], size)
        self.assertEqual(receipt["plaintext_sha256"], digest.hexdigest())
        self.assertEqual(digest_file(self.output), digest.hexdigest())
        self.assertLess(peak, 16 * 1024 * 1024)
        print(json.dumps({**receipt, "synthetic_python_peak_bytes": peak}, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
