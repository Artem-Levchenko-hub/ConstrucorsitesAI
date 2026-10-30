#!/usr/bin/env python3
"""Recover one authenticated DER CMS archive offline with bounded memory.

The expected CMS SHA-256 must come from an authenticated receipt. AES-CBC alone
does not authenticate an archive. This verifies decryption, not archive contents
or database restoration. Private keys stay on the offline machine.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _gate():
    spec = importlib.util.spec_from_file_location(
        "backup_cms_decrypt_envelope", Path(__file__).with_name("cms-envelope.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DER = _gate()
CHUNK = DER.CHUNK
need = DER.need


def _bounded_input(path):
    source, absolute, before, opened = DER.open_physical(path)
    with source:
        need(opened[3] <= DER.MAX_METADATA, "input_limit")
        data = source.read(DER.MAX_METADATA + 1)
        need(len(data) == opened[3], "input_length")
        DER.unchanged(source, absolute, before, opened)
    return data


def _content_key(metadata, private_key_path, certificate_path):
    private_key = serialization.load_pem_private_key(_bounded_input(private_key_path), None)
    certificate = x509.load_pem_x509_certificate(_bounded_input(certificate_path))
    public_key = certificate.public_key()
    need(isinstance(private_key, rsa.RSAPrivateKey)
         and isinstance(public_key, rsa.RSAPublicKey), "recipient_key_type")
    public_spki = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    need(private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    ) == public_spki, "recipient_key_mismatch")
    serial = certificate.serial_number.to_bytes(
        max(1, (certificate.serial_number.bit_length() + 7) // 8), "big",
    )
    if serial[0] & 0x80:
        serial = b"\0" + serial
    issuer = certificate.issuer.public_bytes()
    issuer_serial = issuer + DER.der_header(2, len(serial)) + serial
    need(metadata["issuer_serial"] == DER.der_header(0x30, len(issuer_serial)) + issuer_serial,
         "recipient_certificate_mismatch")
    content_key = private_key.decrypt(metadata["encrypted_key"], rsa_padding.PKCS1v15())
    need(len(content_key) == 32, "content_key_length")
    return content_key, hashlib.sha256(public_spki).hexdigest()


def decrypt_stream(source, target, metadata, content_key, expected_sha256):
    """Hash exactly the held bytes used, and retain only bounded cipher blocks."""
    source.seek(0)
    cms_digest, plaintext_digest = hashlib.sha256(), hashlib.sha256()
    prefix_remaining = metadata["ciphertext_offset"]
    need(0 < prefix_remaining <= DER.MAX_METADATA, "metadata_limit")
    while prefix_remaining:
        block = source.read(min(CHUNK, prefix_remaining))
        need(block and len(block) <= prefix_remaining, "truncated")
        cms_digest.update(block)
        prefix_remaining -= len(block)
    decryptor = Cipher(algorithms.AES256(content_key), modes.CBC(metadata["iv"])).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    remaining, plaintext_bytes = metadata["ciphertext_bytes"], 0

    def write(block):
        nonlocal plaintext_bytes
        if block:
            need(target.write(block) == len(block), "short_write")
            plaintext_digest.update(block)
            plaintext_bytes += len(block)

    while remaining:
        block = source.read(min(CHUNK, remaining))
        need(block and len(block) <= remaining, "truncated")
        cms_digest.update(block)
        remaining -= len(block)
        write(unpadder.update(decryptor.update(block)))
    need(source.read(1) == b"", "der_eof")
    need(cms_digest.hexdigest() == expected_sha256, "checksum_mismatch")
    write(unpadder.update(decryptor.finalize()))
    write(unpadder.finalize())
    return {
        "cms_sha256": cms_digest.hexdigest(),
        "cms_bytes": metadata["ciphertext_offset"] + metadata["ciphertext_bytes"],
        "ciphertext_bytes": metadata["ciphertext_bytes"],
        "plaintext_sha256": plaintext_digest.hexdigest(),
        "plaintext_bytes": plaintext_bytes,
    }


def _remove_owned(path, created, directory_fd):
    """Never unlink a replacement or an unrelated existing recovery."""
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


def decrypt(source_path, output_path, *, private_key_path, certificate_path, expected_sha256):
    """Validate before private operations; publish only a complete private file."""
    output_path = Path(output_path)
    need(".." not in output_path.parts, "output_path")
    output_path = Path(os.path.abspath(output_path))
    if os.path.lexists(output_path):
        raise FileExistsError("recovery output already exists")
    source, source_path, source_before, source_opened = DER.open_physical(source_path)
    with source:
        # Keep the original FD across the existing full-hash/strict-OpenSSL gate.
        # A second hash below covers exactly the bytes decrypted from this FD.
        validated = DER.validate(source_path, expected_sha256)
        DER.unchanged(source, source_path, source_before, source_opened)
        metadata = DER.parse_metadata(source, source_opened[3])
        content_key, public_spki_sha = _content_key(metadata, private_key_path, certificate_path)
        stage, created, directory_fd, linked, complete = None, None, None, False, False
        try:
            descriptor, name = tempfile.mkstemp(
                prefix="." + output_path.name + ".", suffix=".tmp", dir=output_path.parent,
            )
            stage = Path(name)
            info = os.fstat(descriptor)
            created = (info.st_dev, info.st_ino)
            with os.fdopen(descriptor, "w+b") as target:
                parents = DER.physical_snapshot(stage)[1][:-1]
                if os.link in os.supports_dir_fd and os.unlink in os.supports_dir_fd:
                    directory_fd = os.open(
                        output_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    )
                    need(DER.directory_identity(os.fstat(directory_fd)) == parents[-1][1],
                         "output_directory_changed")
                receipt = decrypt_stream(source, target, metadata, content_key, expected_sha256)
                need(receipt["cms_bytes"] == validated["size_bytes"], "file_changed")
                target.flush()
                os.fsync(target.fileno())
                DER.unchanged(source, source_path, source_before, source_opened)
                final_identity = DER.identity(os.fstat(target.fileno()))
                absolute_stage, snapshot = DER.physical_snapshot(stage)
                comparable = slice(None, 5) if os.name == "nt" else slice(None)
                need(snapshot[:-1] == parents
                     and snapshot[-1][1][comparable] == final_identity[comparable]
                     and os.fstat(target.fileno()).st_nlink == 1, "output_changed")
                linked = True
                if directory_fd is not None:
                    os.link(stage.name, output_path.name, src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd, follow_symlinks=False)
                else:
                    os.link(absolute_stage, output_path, follow_symlinks=False)
                _, published = DER.physical_snapshot(output_path)
                need(published[:-1] == parents
                     and published[-1][1][:5] == final_identity[:5]
                     and os.fstat(target.fileno()).st_nlink == 2, "output_changed")
                DER.unchanged(source, source_path, source_before, source_opened)
            _remove_owned(stage, created, directory_fd)
            if directory_fd is not None:
                os.fsync(directory_fd)
            complete = True
            return {**receipt, "recipient_public_spki_sha256": public_spki_sha}
        finally:
            try:
                if not complete and linked and created is not None:
                    _remove_owned(output_path, created, directory_fd)
            finally:
                try:
                    if stage is not None and created is not None:
                        _remove_owned(stage, created, directory_fd)
                finally:
                    if directory_fd is not None:
                        os.close(directory_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    arguments = parser.parse_args(argv)
    try:
        receipt = decrypt(
            arguments.source, arguments.output, private_key_path=arguments.private_key,
            certificate_path=arguments.cert, expected_sha256=arguments.expected_sha256,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception:
        # No key, password, path, DER values or raw cryptography errors in logs.
        print("cms-decrypt: decryption failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
