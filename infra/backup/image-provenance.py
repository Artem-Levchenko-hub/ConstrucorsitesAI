"""Bind an explicit saved OCI archive to a requested digest and local config ID.

Only reads the archive and the local Docker image. Never loads, pulls, tags,
extracts files, creates resources or changes RepoDigests.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
import zlib
from pathlib import Path, PurePosixPath

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
PLATFORM = {"os": "linux", "architecture": "amd64"}
MAX_JSON_BYTES = 4 * 1024**2
MAX_DECODED_BYTES = 4 * 1024**3
INSPECT_FORMAT = (
    '{"Id":{{json .Id}},"Os":{{json .Os}},"Architecture":{{json .Architecture}},'
    '"RootFS":{{json .RootFS}}}'
)


class ImageProvenanceError(RuntimeError):
    """Fixed nonsecret codes; no archive config or Docker stderr is exposed."""


def need(condition, code):
    if not condition:
        raise ImageProvenanceError(code)


def hash_stream(stream, limit):
    digest, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(chunk)
        need(size <= limit, "image_provenance_size_bound")
        digest.update(chunk)
    return digest.hexdigest(), size


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        need(key not in value, "image_provenance_duplicate_json_key")
        value[key] = item
    return value


def parsed_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def file_signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class ArchiveChain:
    def __init__(self, archive):
        self.archive, self.members = archive, {}
        for count, member in enumerate(archive):
            need(count < 20000, "image_provenance_entry_bound")
            path = PurePosixPath(member.name)
            need(member.isfile() or member.isdir(), "image_provenance_unsupported_entry")
            need(
                not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in member.name
                and ":" not in member.name,
                "image_provenance_unsafe_path",
            )
            name = str(path)
            need(name not in self.members, "image_provenance_duplicate_entry")
            self.members[name] = member

    def member(self, digest, size=None):
        need(
            isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
            "image_provenance_digest_invalid",
        )
        member = self.members.get("blobs/sha256/" + digest.split(":")[1])
        need(member is not None and member.isfile(), "image_provenance_blob_missing")
        if size is not None:
            need(
                type(size) is int and size > 0 and member.size == size,
                "image_provenance_descriptor_size",
            )
        return member

    def json_blob(self, digest, size=None):
        member = self.member(digest, size)
        need(member.size <= MAX_JSON_BYTES, "image_provenance_json_bound")
        raw = self.archive.extractfile(member).read()
        need(hashlib.sha256(raw).hexdigest() == digest.split(":")[1], "image_provenance_blob_hash")
        value = parsed_json(raw)
        need(isinstance(value, dict), "image_provenance_json_object_required")
        return value

    def descriptor_json(self, descriptor, media_type):
        need(
            isinstance(descriptor, dict) and descriptor.get("mediaType") == media_type,
            "image_provenance_descriptor_media",
        )
        need(
            type(descriptor.get("size")) is int and descriptor["size"] > 0,
            "image_provenance_descriptor_size",
        )
        return self.json_blob(descriptor.get("digest"), descriptor["size"])

    def layer(self, descriptor, diff_id):
        need(isinstance(descriptor, dict), "image_provenance_layer_descriptor")
        need(
            type(descriptor.get("size")) is int and descriptor["size"] > 0,
            "image_provenance_descriptor_size",
        )
        media = descriptor.get("mediaType")
        need(
            media
            in {
                "application/vnd.oci.image.layer.v1.tar+gzip",
                "application/vnd.oci.image.layer.v1.tar",
            },
            "image_provenance_layer_media",
        )
        member = self.member(descriptor.get("digest"), descriptor.get("size"))
        with self.archive.extractfile(member) as source:
            digest, size = hash_stream(source, MAX_DECODED_BYTES)
        need(digest == descriptor["digest"].split(":")[1], "image_provenance_layer_hash")
        if media.endswith("+gzip"):
            with (
                self.archive.extractfile(member) as source,
                gzip.GzipFile(fileobj=source) as decoded,
            ):
                decoded_digest, decoded_size = hash_stream(decoded, MAX_DECODED_BYTES)
        else:
            decoded_digest, decoded_size = digest, size
        need("sha256:" + decoded_digest == diff_id, "image_provenance_diff_id")
        return {
            "digest": descriptor["digest"],
            "bytes": size,
            "media_type": media,
            "diff_id": diff_id,
            "decoded_bytes": decoded_size,
        }

    def proof(self, index_digest):
        index = self.json_blob(index_digest)
        need(
            index.get("schemaVersion") == 2
            and index.get("mediaType") == INDEX
            and isinstance(index.get("manifests"), list)
            and 0 < len(index["manifests"]) <= 256,
            "image_provenance_index_required",
        )
        candidates = [
            row
            for row in index["manifests"]
            if isinstance(row, dict) and row.get("platform") == PLATFORM
        ]
        need(len(candidates) == 1, "image_provenance_platform_not_unique")
        selected = candidates[0]
        need(
            not selected.get("annotations", {}).get("vnd.docker.reference.type"),
            "image_provenance_attestation_not_image",
        )
        manifest = self.descriptor_json(selected, MANIFEST)
        need(
            manifest.get("schemaVersion") == 2 and manifest.get("mediaType") == MANIFEST,
            "image_provenance_manifest_required",
        )
        configuration = manifest.get("config")
        config = self.descriptor_json(configuration, CONFIG)
        need(
            config.get("os") == PLATFORM["os"]
            and config.get("architecture") == PLATFORM["architecture"],
            "image_provenance_config_platform",
        )
        rootfs, layers = config.get("rootfs", {}), manifest.get("layers")
        diffs = rootfs.get("diff_ids")
        need(
            rootfs.get("type") == "layers"
            and isinstance(diffs, list)
            and isinstance(layers, list)
            and 0 < len(layers) == len(diffs) <= 256
            and all(
                isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                for value in diffs
            ),
            "image_provenance_layer_scope",
        )
        checked = [
            self.layer(descriptor, diff) for descriptor, diff in zip(layers, diffs, strict=True)
        ]
        return {
            "index_digest": index_digest,
            "image_manifest_digest": selected["digest"],
            "config_id": configuration["digest"],
            "platform": "linux/amd64",
            "rootfs_diff_ids": diffs,
            "layers": checked,
            "unselected_descriptors": len(index["manifests"]) - 1,
        }


def verify_local_image(runner, requested_image, archive_path):
    """An explicit archive is proof input; lookup failure never selects this path."""
    try:
        need(
            isinstance(requested_image, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", requested_image),
            "image_provenance_original_pin_required",
        )
        path = Path(archive_path)
        need(
            not any(part.is_symlink() for part in (path, *path.parents)),
            "image_provenance_archive_symlink",
        )
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            need(stat.S_ISREG(before.st_mode), "image_provenance_archive_not_regular")
            archive_sha, archive_size = hash_stream(source, 8 * 1024**3)
            source.seek(0)
            with tarfile.open(fileobj=source, mode="r:") as archive:
                proof = ArchiveChain(archive).proof(requested_image.rsplit("@", 1)[1])
            need(
                file_signature(before) == file_signature(os.fstat(source.fileno())),
                "image_provenance_archive_changed",
            )
        local = runner.run(
            ["image", "inspect", proof["config_id"], "--format", INSPECT_FORMAT], timeout=30
        )
        need(local.ok, "image_provenance_local_config_unavailable")
        observed = parsed_json(local.stdout)
        need(
            isinstance(observed, dict)
            and observed.get("Id") == proof["config_id"]
            and observed.get("Os") == "linux"
            and observed.get("Architecture") == "amd64"
            and observed.get("RootFS", {}).get("Type") == "layers"
            and observed["RootFS"].get("Layers") == proof["rootfs_diff_ids"],
            "image_provenance_local_content_mismatch",
        )
        return {
            "status": "verified_explicit_archive_content_mapping",
            **proof,
            "requested_image": requested_image,
            "execution_image": proof["config_id"],
            "archive_sha256": archive_sha,
            "archive_bytes": archive_size,
            "local_config_verified": True,
            "repo_digest_lookup_claimed": False,
        }
    except ImageProvenanceError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        EOFError,
        tarfile.TarError,
        zlib.error,
    ) as error:
        raise ImageProvenanceError("image_provenance_unreadable_or_invalid") from error
