"""Pure CMS encoding tests: real cryptography, no private backup keys or services."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import subprocess
import tempfile
import threading
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
SCRIPT = ROOT / "infra/backup/cms-encrypt.py"
GATE = ROOT / "infra/backup/cms-envelope.py"
BLOCK = b"\xa5" * (1024 * 1024)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def header(tag, length):
    if length < 128:
        return bytes((tag, length))
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes((tag, 0x80 | len(encoded))) + encoded


def ber_prefix(metadata):
    return (b"\x30\x80" + bytes.fromhex("06092a864886f70d010703") + b"\xa0\x80\x30\x80"
            + metadata["version"] + metadata["recipients"] + b"\x30\x80"
            + bytes.fromhex("06092a864886f70d010701") + metadata["algorithm"] + b"\xa0\x80")


def fragmented(metadata, ciphertext, lengths=(1, 7, 31, 4096)):
    parts = [ber_prefix(metadata)]
    at, index = 0, 0
    while at < len(ciphertext):
        chunk = ciphertext[at:at + lengths[index % len(lengths)]]
        parts.extend((header(4, len(chunk)), chunk))
        at += len(chunk)
        index += 1
    return b"".join(parts) + b"\0\0" * 5


class Segments:
    """Bound every read, return short reads and generate large payloads lazily."""

    def __init__(self, parts, *, short=1024 * 1024):
        self.parts = iter(parts)
        self.pending = b""
        self.position = 0
        self.short = short
        self.largest_read = 0

    def read(self, size=-1):
        if not 0 <= size <= 1024 * 1024:
            raise AssertionError("unbounded read")
        self.largest_read = max(self.largest_read, size)
        if size == 0:
            return b""
        while not self.pending:
            self.pending = next(self.parts, b"")
            self.position = 0
            if not self.pending:
                return b""
        amount = min(size, self.short, len(self.pending) - self.position)
        result = self.pending[self.position:self.position + amount]
        self.position += amount
        if self.position == len(self.pending):
            self.pending = b""
        return result

    def close(self):
        pass


class Counter:
    def __init__(self):
        self.size = 0
        self.largest_write = 0
        self.prefix = bytearray()

    def write(self, value):
        self.size += len(value)
        self.largest_write = max(self.largest_write, len(value))
        if len(self.prefix) < 2048:
            self.prefix.extend(value[:2048 - len(self.prefix)])
        return len(value)


class Child:
    def __init__(self, payload, *, code=0, on_wait=None):
        self.stdout = io.BytesIO(payload)
        self.code = code
        self.on_wait = on_wait
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        if self.on_wait and self.returncode is None:
            self.on_wait()
        self.returncode = self.code
        return self.code

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class CmsStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load(GATE, "cms_gate_streaming_tests")
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "streaming-dummy")])
        now = datetime.now(UTC)
        cls.certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(cls.key.public_key()).serial_number(17)
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .sign(cls.key, hashes.SHA256())
        )
        cls.small_der = cls.make_der(b"fixture\0" * 8)
        cls.metadata = cls.gate.inspect_metadata(cls.small_der)

    @classmethod
    def make_der(cls, plaintext):
        return (pkcs7.PKCS7EnvelopeBuilder().set_data(plaintext)
                .set_content_encryption_algorithm(algorithms.AES256)
                .add_recipient(cls.certificate)
                .encrypt(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))

    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "bounded streaming CMS encoder is missing")
        self.module = load(SCRIPT, "cms_streaming_tests")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "payload.tgz"
        self.output = self.directory / "backup.cms"
        self.cert = self.directory / "recipient.pem"
        self.source.write_bytes(b"fixture\0" * 8)
        self.cert.write_bytes(self.certificate.public_bytes(serialization.Encoding.PEM))

    def stream_for(self, plaintext):
        der = self.make_der(plaintext)
        metadata = self.gate.inspect_metadata(der)
        ciphertext = der[metadata["ciphertext_offset"]:]
        return der, fragmented(metadata, ciphertext)

    def encrypt(self, child, **kwargs):
        def launch(command, **options):
            self.assertEqual(command[:6], ["openssl", "cms", "-encrypt", "-stream", "-binary",
                                           "-aes-256-cbc"])
            self.assertNotIn("-in", command)
            self.assertEqual(options["stdin"].read(), self.source.read_bytes())
            self.assertEqual(options["stdout"], subprocess.PIPE)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            return child

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=launch):
            return self.module.encrypt(self.source, self.cert, self.output, **kwargs)

    def test_fragmented_real_der_preserves_every_byte_and_decrypts(self):
        for size, expected in ((0, 16), (1, 16), (15, 16), (16, 32), (17, 32),
                               (127, 128), (128, 144), (4095, 4096), (4096, 4112), (4097, 4112)):
            with self.subTest(size=size):
                plaintext = (b"a\0\r\n\xff" * (size // 5 + 1))[:size]
                der, ber = self.stream_for(plaintext)
                output = io.BytesIO()
                result = self.module.canonicalize(Segments([ber], short=1), output, size)
                self.assertEqual(output.getvalue(), der)
                self.assertEqual(result["ciphertext_bytes"], expected)
                self.assertEqual(result["sha256"], hashlib.sha256(der).hexdigest())
                self.assertEqual(pkcs7.pkcs7_decrypt_der(
                    output.getvalue(), self.certificate, self.key, []), plaintext)

    def test_large_logical_stream_above_two_gib_uses_bounded_reads_and_writes(self):
        ciphertext_size = 0x80000010

        def parts():
            yield ber_prefix(self.metadata)
            remaining = ciphertext_size
            while remaining:
                amount = min(len(BLOCK), remaining)
                yield header(4, amount)
                yield BLOCK[:amount]
                remaining -= amount
            yield b"\0\0" * 5

        source, sink = Segments(parts()), Counter()
        tracemalloc.start()
        try:
            result = self.module.canonicalize(source, sink, 0x80000001)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        observed = self.gate.parse_metadata(io.BytesIO(sink.prefix), sink.size)
        self.assertEqual(observed["ciphertext_bytes"], ciphertext_size)
        self.assertEqual(result["ciphertext_bytes"], ciphertext_size)
        self.assertEqual(result["size_bytes"], sink.size)
        self.assertLessEqual(source.largest_read, 1024 * 1024)
        self.assertLessEqual(sink.largest_write, 1024 * 1024)
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_many_short_reads_do_not_accumulate_unbounded_python_objects(self):
        source = Segments([ber_prefix(self.metadata), header(4, len(BLOCK)), BLOCK,
                           b"\0\0" * 5], short=3)
        tracemalloc.start()
        try:
            self.module.canonicalize(source, Counter(), len(BLOCK) - 1)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_ciphertext_eoc_bytes_are_preserved_as_opaque_data(self):
        ciphertext = (b"\0\0\x30\x80\x04\x10" * 20)[:80]
        output = io.BytesIO()
        self.module.canonicalize(io.BytesIO(fragmented(self.metadata, ciphertext)), output, 64)
        metadata = self.gate.inspect_metadata(output.getvalue())
        self.assertEqual(output.getvalue()[metadata["ciphertext_offset"]:], ciphertext)

    def test_each_truncated_byte_refuses_completion(self):
        _, ber = self.stream_for(self.source.read_bytes())
        for cut in range(len(ber)):
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                self.module.canonicalize(io.BytesIO(ber[:cut]), io.BytesIO(), 64)

    def test_only_exact_ber_wrappers_primitive_chunks_and_eof_are_accepted(self):
        ciphertext = self.small_der[self.metadata["ciphertext_offset"]:]
        prefix = ber_prefix(self.metadata)
        good = fragmented(self.metadata, ciphertext)
        malformed = {
            "trailing": good + b"extra",
            "extra_eoc": good + b"\0\0",
            "wrong_wrapper": b"\x31\x80" + good[2:],
            "definite_wrapper": b"\x30\x00" + good[2:],
            "missing_eoc": good[:-2],
            "constructed_chunk": prefix + b"\x24\x80" + b"\0\0" * 6,
            "indefinite_chunk": prefix + b"\x04\x80" + b"\0\0" * 6,
            "wrong_chunk_tag": prefix + b"\x05\x10" + b"x" * 16 + b"\0\0" * 5,
            "empty_chunk": prefix + b"\x04\x00" + good[len(prefix):],
            "nonminimal_length": prefix + b"\x04\x81\x10" + b"x" * 16 + b"\0\0" * 5,
            "huge_length": prefix + b"\x04\x88" + b"\xff" * 8,
            "short_cipher": fragmented(self.metadata, ciphertext[:-16]),
            "long_cipher": fragmented(self.metadata, ciphertext + b"x" * 16),
            "fragment_limit": prefix + header(4, len(BLOCK) + 1),
        }
        content_at = 17 + len(self.metadata["version"]) + len(self.metadata["recipients"])
        encrypted_at = content_at + 13 + len(self.metadata["algorithm"])
        for at in (0, 13, 15, content_at, encrypted_at):
            malformed[f"wrapper_tag_{at}"] = good[:at] + bytes((good[at] ^ 1,)) + good[at + 1:]
            malformed[f"wrapper_length_{at}"] = good[:at + 1] + b"\0" + good[at + 2:]
        for fault, payload in malformed.items():
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                self.module.canonicalize(Segments([payload], short=3), io.BytesIO(), 64)

    def test_profile_policy_is_the_existing_strict_der_parser(self):
        ciphertext = self.small_der[self.metadata["ciphertext_offset"]:]
        changes = {
            "version": {"version": b"\x02\x01\x02"},
            "recipient": {"recipients": b"\x31\x00"},
            "wrong_cipher": {"algorithm": self.metadata["algorithm"].replace(
                bytes.fromhex("060960864801650304012a"), bytes.fromhex("0609608648016503040102"))},
            "metadata_limit": {"recipients": header(0x31, 300_000) + b"x" * 300_000},
        }
        for fault, fields in changes.items():
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                self.module.canonicalize(io.BytesIO(fragmented(
                    {**self.metadata, **fields}, ciphertext)), io.BytesIO(), 64)

    def test_invalid_plaintext_size_refuses_before_output(self):
        for size in (-1, True, 1.5, 2**63):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.module.canonicalize(io.BytesIO(b""), io.BytesIO(), size)

    def test_short_output_write_is_a_failure(self):
        _, ber = self.stream_for(self.source.read_bytes())
        sink = mock.Mock(write=lambda value: max(0, len(value) - 1))
        with self.assertRaises(ValueError):
            self.module.canonicalize(io.BytesIO(ber), sink, 64)

    def test_success_is_private_atomic_and_consumable_by_unchanged_gate(self):
        der, ber = self.stream_for(self.source.read_bytes())

        def unpublished():
            self.assertFalse(self.output.exists(), "final ciphertext appeared before child success")

        result = self.encrypt(Child(ber, on_wait=unpublished))
        self.assertEqual(self.output.read_bytes(), der)
        self.gate.validate(self.output, result["sha256"], openssl_parser=lambda _: None)
        if os.name == "posix":
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual({path.name for path in self.directory.iterdir()},
                         {"payload.tgz", "recipient.pem", "backup.cms"})

    def test_failed_child_or_malformed_stream_removes_only_own_stage(self):
        _, good = self.stream_for(self.source.read_bytes())
        retained = self.directory / "unrelated.tmp"
        retained.write_bytes(b"retained evidence")
        for payload, code in ((good, 9), (good[:-1], 0), (good + b"suffix", 0)):
            with self.subTest(code=code, size=len(payload)), self.assertRaises(ValueError):
                self.encrypt(Child(payload, code=code))
            self.assertFalse(self.output.exists())
            self.assertEqual(retained.read_bytes(), b"retained evidence")
            self.assertEqual({path.name for path in self.directory.iterdir()},
                             {"payload.tgz", "recipient.pem", "unrelated.tmp"})

    def test_existing_final_or_concurrent_final_is_never_replaced(self):
        _, ber = self.stream_for(self.source.read_bytes())
        self.output.write_bytes(b"successful previous archive")
        with self.assertRaises(FileExistsError):
            self.encrypt(Child(ber))
        self.assertEqual(self.output.read_bytes(), b"successful previous archive")
        self.output.unlink()
        child = Child(ber, on_wait=lambda: self.output.write_bytes(b"concurrent archive"))
        with self.assertRaises(FileExistsError):
            self.encrypt(child)
        self.assertEqual(self.output.read_bytes(), b"concurrent archive")

    def test_child_close_error_still_cleans_only_own_stage(self):
        _, ber = self.stream_for(self.source.read_bytes())
        child = Child(ber, code=9)
        child.stdout = mock.Mock(wraps=io.BytesIO(ber))
        child.stdout.close.side_effect = OSError("fixture close failure")
        with self.assertRaises(OSError):
            self.encrypt(child)
        self.assertFalse(self.output.exists())
        self.assertEqual({path.name for path in self.directory.iterdir()},
                         {"payload.tgz", "recipient.pem"})

    def test_source_mutation_or_growth_prevents_publication(self):
        for grow in (False, True):
            with self.subTest(grow=grow):
                self.source.write_bytes(b"fixture\0" * 8)
                _, ber = self.stream_for(self.source.read_bytes())

                def mutate(grow=grow):
                    with self.source.open("r+b") as target:
                        target.seek(0, 2) if grow else target.seek(0)
                        target.write(b"changed")
                    before = self.source.stat()
                    os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))

                with self.assertRaises(ValueError):
                    self.encrypt(Child(ber, on_wait=mutate))
                self.assertFalse(self.output.exists())

    def test_nonregular_source_is_rejected(self):
        with mock.patch.object(self.module.subprocess, "Popen") as child:
            with self.assertRaises(ValueError):
                self.module.encrypt(self.directory, self.cert, self.output)
            child.assert_not_called()

    @unittest.skipIf(os.name == "nt", "requires POSIX file replacement and symlink privileges")
    def test_source_replacement_and_symlink_are_rejected(self):
        _, ber = self.stream_for(self.source.read_bytes())
        replacement = self.directory / "replacement"
        replacement.write_bytes(self.source.read_bytes())
        with self.assertRaises(ValueError):
            self.encrypt(Child(ber, on_wait=lambda: os.replace(replacement, self.source)))
        link = self.directory / "source-link"
        link.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.module.encrypt(link, self.cert, self.output)

    def test_watchdog_terminates_a_child_that_never_produces_a_stream(self):
        child = Child(b"")
        stopped = threading.Event()
        child.stdout = mock.Mock(read=lambda _: b"" if stopped.wait(2) else b"unexpected")
        child.kill = lambda: (stopped.set(), setattr(child, "returncode", -9))
        with self.assertRaises(ValueError):
            self.encrypt(child, timeout=0.01)
        self.assertTrue(stopped.is_set())
        self.assertFalse(self.output.exists())

    def test_cli_failure_does_not_log_payload_paths_or_crypto_diagnostics(self):
        with (mock.patch.object(self.module, "encrypt", side_effect=ValueError("PRIVATE-INPUT")),
              mock.patch("sys.stderr", new_callable=io.StringIO) as error):
            code = self.module.main([str(self.source), str(self.output), "--cert", str(self.cert)])
        self.assertEqual(code, 1)
        self.assertEqual(error.getvalue(), "cms-encrypt: encryption failed\n")


if __name__ == "__main__":
    unittest.main()
