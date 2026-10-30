#!/usr/bin/env python3
"""Stream OpenSSL encryption into the backup's existing canonical DER profile.

OpenSSL performs all RSA/AES operations. This helper accepts only its exact
five-wrapper BER streaming shape, preserves the metadata and ciphertext bytes,
and replaces framing with definite DER lengths. No private keys are handled.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from pathlib import Path


def _gate():
    spec = importlib.util.spec_from_file_location(
        "backup_cms_envelope", Path(__file__).with_name("cms-envelope.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DER = _gate()
CHUNK = DER.CHUNK
need = DER.need


class BerReader:
    """Fixed grammar reader; indefinite lengths are legal only at known wrappers."""

    def __init__(self, source):
        self.source = source
        self.metadata_bytes = 0

    def read(self, size):
        need(0 <= size <= CHUNK, "read_bound")
        result, remaining = bytearray(), size
        while remaining:
            block = self.source.read(remaining)
            need(block and len(block) <= remaining, "truncated")
            result.extend(block)
            remaining -= len(block)
        return bytes(result)

    def exact(self, value):
        need(self.read(len(value)) == value, "ber_wrapper")

    def length(self, start):
        encoded, size = start, start[1]
        if size & 0x80:
            count = size & 0x7F
            need(1 <= count <= 8, "ber_length")
            octets = self.read(count)
            size = int.from_bytes(octets, "big")
            need(octets[0] != 0 and size >= 128, "ber_length")
            encoded += octets
        return encoded, size

    def metadata(self, tag):
        start = self.read(2)
        need(start[0] == tag, "metadata_tag")
        encoded, size = self.length(start)
        self.metadata_bytes += len(encoded) + size
        need(self.metadata_bytes <= DER.MAX_METADATA, "metadata_limit")
        return encoded + self.read(size)


def _ciphertext(reader, expected):
    remaining = expected
    while True:
        start = reader.read(2)
        if start == b"\0\0":
            break
        need(start[0] == 4, "cipher_fragment")
        _, size = reader.length(start)
        need(0 < size <= min(CHUNK, remaining), "cipher_length")
        # OpenSSL's primitive chunks are bounded; ciphertext is never searched
        # for EOC markers, which can also occur inside legitimate encrypted data.
        yield reader.read(size)
        remaining -= size
    need(remaining == 0, "cipher_length")
    reader.exact(b"\0\0" * 4)
    need(reader.source.read(1) == b"", "ber_eof")


def canonicalize(source, sink, plaintext_size):
    """Flatten only the opaque encrypted OCTET STRING, with bounded memory."""
    need(type(plaintext_size) is int and plaintext_size >= 0, "plaintext_size")
    cipher_size = 16 * (plaintext_size // 16 + 1)
    DER.der_header(0x80, cipher_size)  # Enforce the shared length bound up front.
    reader = BerReader(source)
    reader.exact(b"\x30\x80")
    need(reader.metadata(6) == DER.ENVELOPED, "content_type")
    reader.exact(b"\xa0\x80\x30\x80")
    version, recipients = reader.metadata(2), reader.metadata(0x31)
    reader.exact(b"\x30\x80")
    need(reader.metadata(6) == DER.DATA, "encrypted_content_type")
    algorithm = reader.metadata(0x30)
    reader.exact(b"\xa0\x80")
    metadata = {"version": version, "recipients": recipients, "algorithm": algorithm}
    pieces = _ciphertext(reader, cipher_size)
    primed, first = [], bytearray()
    for piece in pieces:
        primed.append(piece)
        first.extend(piece[:16 - len(first)])
        if len(first) == 16:
            break
    need(len(first) == 16, "cipher_length")
    # Reuse the exact recipient/cipher/DER policy, including metadata depth,
    # node, size and SET-order checks, with the first actual encrypted block.
    DER.inspect_metadata(DER.envelope_prefix(metadata, 16) + bytes(first))
    digest, measured = hashlib.sha256(), 0
    for block in itertools.chain((DER.envelope_prefix(metadata, cipher_size),), primed, pieces):
        need(sink.write(block) == len(block), "short_write")
        digest.update(block)
        measured += len(block)
    return {"profile": "der-rsa-aes256cbc", "size_bytes": measured,
            "ciphertext_bytes": cipher_size, "sha256": digest.hexdigest()}


def _remove_stage(path, created, directory_fd):
    """Remove this process's stage only; a replacement is retained untouched."""
    try:
        info = (os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
                if directory_fd is not None else path.lstat())
        if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == created:
            if directory_fd is not None:
                os.unlink(path.name, dir_fd=directory_fd)
            else:
                path.unlink()
    except FileNotFoundError:
        pass


def _close_child(process, watchdog):
    if watchdog is not None:
        watchdog.cancel()
    if process is None:
        return
    try:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)
    finally:
        if process.stdout is not None:
            process.stdout.close()


def encrypt(source_path, certificate_path, output_path, *, timeout=3600):
    """Hold input identities; publish one private file only after child success."""
    need(type(timeout) in (int, float) and timeout > 0, "timeout")
    output_path = Path(output_path)
    need(".." not in output_path.parts, "output_path")
    output_path = Path(os.path.abspath(output_path))
    if os.path.lexists(output_path):
        raise FileExistsError("ciphertext output already exists")
    source, source_path, source_before, source_opened = DER.open_physical(source_path)
    with source:
        certificate, certificate_path, cert_before, cert_opened = (
            DER.open_physical(certificate_path)
        )
        with certificate:
            stage, created, directory_fd, process, watchdog = None, None, None, None, None
            expired = threading.Event()
            try:
                descriptor, name = tempfile.mkstemp(
                    prefix="." + output_path.name + ".", suffix=".tmp", dir=output_path.parent
                )
                stage = Path(name)
                info = os.fstat(descriptor)
                created = (info.st_dev, info.st_ino)
                with os.fdopen(descriptor, "w+b") as target:
                    parents = DER.physical_snapshot(stage)[1][:-1]
                    if os.link in os.supports_dir_fd and os.unlink in os.supports_dir_fd:
                        directory_fd = os.open(
                            output_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                        )
                        held = DER.directory_identity(os.fstat(directory_fd))
                        need(held == parents[-1][1], "output_directory_changed")
                    process = subprocess.Popen(
                        ["openssl", "cms", "-encrypt", "-stream", "-binary", "-aes-256-cbc",
                         "-outform", "DER", str(certificate_path)],
                        stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    )

                    def terminate():
                        expired.set()
                        with suppress(ProcessLookupError):
                            process.kill()

                    watchdog = threading.Timer(timeout, terminate)
                    watchdog.daemon = True
                    watchdog.start()
                    need(process.stdout is not None, "openssl_output")
                    result = canonicalize(process.stdout, target, source_opened[3])
                    need(process.wait(timeout=30) == 0 and not expired.is_set(), "openssl_failed")
                    watchdog.cancel()
                    target.flush()
                    os.fsync(target.fileno())
                    DER.unchanged(source, source_path, source_before, source_opened)
                    DER.unchanged(certificate, certificate_path, cert_before, cert_opened)
                    target.seek(0)
                    metadata = DER.parse_metadata(target, result["size_bytes"])
                    need(metadata["ciphertext_bytes"] == result["ciphertext_bytes"],
                         "cipher_length")
                    final_identity = DER.identity(os.fstat(target.fileno()))
                final_path, final_snapshot = DER.physical_snapshot(stage)
                # Windows fstat/lstat expose different ctime meanings, as in
                # the shared input gate. POSIX keeps the complete comparison.
                path_identity = final_snapshot[-1][1]
                comparable = slice(None, 5) if os.name == "nt" else slice(None)
                need(final_snapshot[:-1] == parents
                     and path_identity[comparable] == final_identity[comparable], "output_changed")
                if directory_fd is not None:
                    os.link(stage.name, output_path.name, src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd, follow_symlinks=False)
                    os.fsync(directory_fd)
                else:
                    os.link(final_path, output_path, follow_symlinks=False)
                return result
            finally:
                try:
                    _close_child(process, watchdog)
                finally:
                    try:
                        if stage is not None and created is not None:
                            _remove_stage(stage, created, directory_fd)
                    finally:
                        if directory_fd is not None:
                            os.close(directory_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cert", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = encrypt(arguments.source, arguments.cert, arguments.output)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("cms-encrypt: encryption failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
