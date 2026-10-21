#!/usr/bin/env python3
"""Validate the backup's DER CMS envelope without loading its opaque ciphertext.

OpenSSL's ASN1 BIO loader has a fixed ~1.5 GiB buffer ceiling. Preserve its CMS
grammar check on bounded, byte-identical metadata and the first real AES block;
validate the original lengths/EOF and SHA-256 separately by streaming the file.
This is envelope validation, not a private-key decrypt or restore test.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

CHUNK = 1024 * 1024
MAX_METADATA = 256 * 1024
MAX_NODES = 2048
MAX_DEPTH = 24
ENVELOPED = bytes.fromhex("06092a864886f70d010703")
DATA = bytes.fromhex("06092a864886f70d010701")
RSA = bytes.fromhex("300d06092a864886f70d0101010500")
AES = bytes.fromhex("060960864801650304012a")
VERSION = bytes.fromhex("020100")


def need(condition, code):
    if not condition:
        raise ValueError(code)


def der_header(tag, size):
    need(0 <= size < 2**63, "der_length")
    if size < 128:
        return bytes((tag, size))
    encoded = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes((tag, 0x80 | len(encoded))) + encoded


class Reader:
    def __init__(self, source, size):
        self.source = source
        self.size = size
        self.position = 0
        self.metadata_bytes = 0
        self.nodes = 0

    def read(self, size):
        need(0 <= size <= MAX_METADATA - self.metadata_bytes, "metadata_limit")
        result = self.source.read(size)
        need(len(result) == size, "truncated")
        self.position += size
        self.metadata_bytes += size
        return result

    def header(self, expected, parent_end):
        self.nodes += 1
        need(self.nodes <= MAX_NODES and self.position + 2 <= parent_end, "der_bounds")
        tag, length = self.read(2)
        need(tag == expected and tag & 31 != 31, "der_tag")
        encoded = bytes((tag, length))
        if length & 0x80:
            count = length & 0x7F
            need(1 <= count <= 8 and self.position + count <= parent_end, "der_length")
            octets = self.read(count)
            length = int.from_bytes(octets, "big")
            need(octets[0] != 0 and length >= 128, "der_noncanonical_length")
            encoded += octets
        end = self.position + length
        need(end <= parent_end <= self.size, "der_bounds")
        return encoded, end

    def value(self, expected, parent_end):
        header, end = self.header(expected, parent_end)
        return header + self.read(end - self.position)


def metadata_nodes(data):
    """Check bounded DER encodings recursively; OpenSSL checks the CMS grammar."""
    nodes = 0

    def walk(start, end, depth):
        nonlocal nodes
        need(depth <= MAX_DEPTH, "metadata_depth")
        while start < end:
            nodes += 1
            need(nodes <= MAX_NODES and start + 2 <= end, "metadata_nodes")
            tag, size = data[start : start + 2]
            need(tag != 0 and tag & 31 != 31, "der_tag")
            body = start + 2
            if size & 0x80:
                count = size & 0x7F
                need(1 <= count <= 8 and body + count <= end, "der_length")
                encoded = data[body : body + count]
                size = int.from_bytes(encoded, "big")
                need(encoded[0] != 0 and size >= 128, "der_noncanonical_length")
                body += count
            finish = body + size
            need(finish <= end, "der_bounds")
            if tag & 0x20:
                need(tag in (0x30, 0x31), "der_constructed")
                walk(body, finish, depth + 1)
                if tag == 0x31:
                    items = children(data[start:finish], 0x31)
                    need(items == sorted(items), "der_set_order")
            elif tag == 2:
                value = data[body:finish]
                need(bool(value), "der_integer")
                if len(value) > 1:
                    need(not (value[0] == 0 and value[1] < 128), "der_integer")
                    need(not (value[0] == 255 and value[1] >= 128), "der_integer")
            elif tag == 5:
                need(size == 0, "der_null")
            elif tag == 6:
                value = data[body:finish]
                need(bool(value) and value[-1] < 128, "der_oid")
                at_start = True
                for octet in value:
                    need(not (at_start and octet == 128), "der_oid")
                    at_start = octet < 128
            start = finish
        need(start == end, "der_bounds")

    need(len(data) <= MAX_METADATA, "metadata_limit")
    walk(0, len(data), 0)
    return nodes


def children(data, expected):
    reader = Reader(io.BytesIO(data), len(data))
    _, end = reader.header(expected, len(data))
    need(end == len(data), "der_bounds")
    result = []
    while reader.position < end:
        tag = data[reader.position]
        result.append(reader.value(tag, end))
    return result


def octets(data, tag):
    reader = Reader(io.BytesIO(data), len(data))
    _, end = reader.header(tag, len(data))
    need(end == len(data), "der_bounds")
    return reader.read(end - reader.position)


def parse_metadata(source, size):
    """Return bounded metadata plus the original ciphertext's offset and length.

    Only the current one-recipient RSA / AES-256-CBC DER profile is accepted.
    No originatorInfo, unprotectedAttrs, BER fragments or detached content.
    The four enclosing container lengths must terminate at the same exact EOF.
    """
    reader = Reader(source, size)
    _, outer_end = reader.header(0x30, size)
    need(outer_end == size, "der_eof")
    need(reader.value(6, outer_end) == ENVELOPED, "content_type")
    _, explicit_end = reader.header(0xA0, outer_end)
    need(explicit_end == outer_end, "der_bounds")
    _, envelope_end = reader.header(0x30, explicit_end)
    need(envelope_end == explicit_end, "der_bounds")
    version = reader.value(2, envelope_end)
    need(version == VERSION, "envelope_version")
    recipients = reader.value(0x31, envelope_end)
    metadata_count = metadata_nodes(recipients)
    entries = children(recipients, 0x31)
    need(len(entries) == 1, "recipient_count")
    recipient = children(entries[0], 0x30)
    need(
        len(recipient) == 4 and recipient[0] == VERSION and recipient[2] == RSA,
        "rsa_recipient",
    )
    issuer_serial = children(recipient[1], 0x30)
    need(len(issuer_serial) == 2 and issuer_serial[0][0] == 0x30, "issuer_serial")
    serial = octets(issuer_serial[1], 2)
    need(bool(serial) and serial[0] < 128, "issuer_serial")
    encrypted_key = octets(recipient[3], 4)
    need(bool(encrypted_key), "encrypted_key")
    _, content_end = reader.header(0x30, envelope_end)
    need(content_end == envelope_end, "der_bounds")
    need(reader.value(6, content_end) == DATA, "encrypted_content_type")
    algorithm = reader.value(0x30, content_end)
    metadata_count += metadata_nodes(algorithm)
    need(metadata_count + reader.nodes <= MAX_NODES, "metadata_nodes")
    parameters = children(algorithm, 0x30)
    need(len(parameters) == 2 and parameters[0] == AES, "cipher_algorithm")
    iv = octets(parameters[1], 4)
    need(len(iv) == 16, "cipher_iv")
    _, cipher_end = reader.header(0x80, content_end)
    cipher_size = cipher_end - reader.position
    need(
        cipher_end == content_end and cipher_size >= 16 and cipher_size % 16 == 0,
        "cipher_length",
    )
    return {
        "version": version,
        "recipients": recipients,
        "algorithm": algorithm,
        "ciphertext_offset": reader.position,
        "ciphertext_bytes": cipher_size,
        "iv": iv,
        "encrypted_key": encrypted_key,
        "issuer_serial": recipient[1],
    }


def inspect_metadata(data):
    """Inspect a small in-memory synthetic CMS; real files use parse_metadata."""
    need(len(data) <= MAX_METADATA, "metadata_limit")
    return parse_metadata(io.BytesIO(data), len(data))


def envelope_prefix(metadata, cipher_size):
    content = DATA + metadata["algorithm"] + der_header(0x80, cipher_size)
    content = der_header(0x30, len(content) + cipher_size) + content
    envelope = metadata["version"] + metadata["recipients"] + content
    envelope = der_header(0x30, len(envelope) + cipher_size) + envelope
    explicit = der_header(0xA0, len(envelope) + cipher_size) + envelope
    return (
        der_header(0x30, len(ENVELOPED) + len(explicit) + cipher_size)
        + ENVELOPED
        + explicit
    )


def identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def directory_identity(value):
    # Sibling creation changes directory timestamps and size without changing
    # this path's binding. Retain inode, kind, ownership and permissions; files
    # continue to use their complete size/mtime/ctime identity above.
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_uid,
        value.st_gid,
        stat.S_IMODE(value.st_mode),
    )


def physical_snapshot(path):
    need(".." not in path.parts, "physical_path")
    absolute = Path(os.path.abspath(path))
    result = []
    for entry in (*reversed(absolute.parents), absolute):
        info = entry.lstat()
        need(
            not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400,
            "physical_path",
        )
        need(
            stat.S_ISREG(info.st_mode)
            if entry == absolute
            else stat.S_ISDIR(info.st_mode),
            "physical_path",
        )
        binding = identity(info) if entry == absolute else directory_identity(info)
        result.append((str(entry), binding))
    return absolute, result


def open_physical(path):
    absolute, before = physical_snapshot(Path(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(absolute, flags)
    try:
        opened = os.fstat(descriptor)
        # Windows lstat/fstat expose different ctime meanings; bind each API's
        # full snapshot independently, and compare common identity fields here.
        need(
            stat.S_ISREG(opened.st_mode) and identity(opened)[:5] == before[-1][1][:5],
            "file_changed",
        )
        need(physical_snapshot(absolute)[1] == before, "path_changed")
        return os.fdopen(descriptor, "rb"), absolute, before, identity(opened)
    except BaseException:
        os.close(descriptor)
        raise


def unchanged(source, path, path_before, descriptor_before):
    need(identity(os.fstat(source.fileno())) == descriptor_before, "file_changed")
    need(physical_snapshot(path)[1] == path_before, "path_changed")


def sidecar_digest(path, filename):
    source, absolute, before, opened = open_physical(path)
    with source:
        data = source.read(4097)
        need(len(data) <= 4096, "checksum_sidecar")
        unchanged(source, absolute, before, opened)
    expected_suffix = b"  " + filename.encode("utf-8") + b"\n"
    need(
        len(data) == 64 + len(expected_suffix) and data[64:] == expected_suffix,
        "checksum_sidecar",
    )
    digest = data[:64].decode("ascii")
    need(re.fullmatch("[0-9a-f]{64}", digest) is not None, "checksum_sidecar")
    return digest


def openssl_grammar(compact):
    result = subprocess.run(
        ["openssl", "cms", "-cmsout", "-inform", "DER", "-noout"],
        input=compact,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    need(result.returncode == 0, "openssl_grammar")


def validate(path, expected_sha256, *, openssl_parser=openssl_grammar):
    need(re.fullmatch("[0-9a-f]{64}", expected_sha256) is not None, "checksum_format")
    source, absolute, before, opened = open_physical(path)
    with source:
        size = opened[3]
        metadata = parse_metadata(source, size)
        first_block = source.read(16)
        need(len(first_block) == 16, "truncated")
        compact = envelope_prefix(metadata, 16) + first_block
        need(len(compact) <= MAX_METADATA, "metadata_limit")
        source.seek(0)
        digest = hashlib.sha256()
        measured = 0
        while block := source.read(CHUNK):
            measured += len(block)
            need(measured <= size, "file_changed")
            digest.update(block)
        need(measured == size, "truncated")
        need(digest.hexdigest() == expected_sha256, "checksum_mismatch")
        unchanged(source, absolute, before, opened)
        openssl_parser(compact)
        unchanged(source, absolute, before, opened)
    return {
        "profile": "der-rsa-aes256cbc",
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "ciphertext_bytes": metadata["ciphertext_bytes"],
        "envelope_validated": True,
        "ciphertext_restore_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cms", type=Path)
    checksum = parser.add_mutually_exclusive_group(required=True)
    checksum.add_argument("--sha256")
    checksum.add_argument("--sha256-file", type=Path)
    arguments = parser.parse_args()
    try:
        expected = arguments.sha256 or sidecar_digest(
            arguments.sha256_file, arguments.cms.name
        )
        print(json.dumps(validate(arguments.cms, expected), sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        # No ASN.1 values, paths, ciphertext or raw OpenSSL diagnostics in logs.
        print("cms-envelope: validation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
