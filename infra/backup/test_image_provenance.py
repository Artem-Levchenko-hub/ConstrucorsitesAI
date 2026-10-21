"""Cryptographic OCI archive proofs; no Docker daemon, network or extraction."""

import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name("image-provenance.py")
        self.assertTrue(path.is_file(), "explicit archive provenance helper missing")
        spec = importlib.util.spec_from_file_location("image_provenance", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def image(self, *, fault=None, extra_platform=False):
        layer = b"immutable filesystem tar fixture"
        compressed = b"invalid gzip" if fault == "gzip" else gzip.compress(layer, mtime=0)
        diff_id = "sha256:" + hashlib.sha256(layer).hexdigest()
        config = encoded(
            {
                "os": "linux",
                "architecture": "amd64",
                "config": {"Env": ["DO_NOT_EXPOSE=config-value"]},
                "rootfs": {
                    "type": "layers",
                    "diff_ids": ["sha256:" + "f" * 64 if fault == "diff" else diff_id],
                },
            }
        )
        if fault == "platform":
            config = config.replace(b'"amd64"', b'"arm64"')
        blobs = {}

        def descriptor(raw, media):
            digest = hashlib.sha256(raw).hexdigest()
            blobs["blobs/sha256/" + digest] = raw
            return {"digest": "sha256:" + digest, "size": len(raw), "mediaType": media}

        configuration = descriptor(config, "application/vnd.oci.image.config.v1+json")
        filesystem = descriptor(compressed, "application/vnd.oci.image.layer.v1.tar+gzip")
        if fault == "layer_size":
            filesystem["size"] += 1
        if fault == "size_null":
            filesystem["size"] = None
        if fault == "size_missing":
            del filesystem["size"]
        if fault == "config_size":
            configuration["size"] += 1
        if fault == "media":
            filesystem["mediaType"] = "application/unknown"
        manifest = descriptor(
            encoded(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": configuration,
                    "layers": [filesystem],
                }
            ),
            "application/vnd.oci.image.manifest.v1+json",
        )
        manifest["platform"] = {"os": "linux", "architecture": "amd64"}
        if fault == "manifest_size":
            manifest["size"] += 1
        descriptors = [manifest]
        if fault == "ambiguous":
            descriptors.append(dict(manifest))
        if extra_platform:
            descriptors.extend(
                [
                    {
                        "digest": "sha256:" + "e" * 64,
                        "size": 123,
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "platform": {"os": "linux", "architecture": "arm64"},
                    },
                    {
                        "digest": "sha256:" + "d" * 64,
                        "size": 123,
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "platform": {"os": "unknown", "architecture": "unknown"},
                        "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
                    },
                ]
            )
        index = descriptor(
            encoded(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": descriptors,
                }
            ),
            "application/vnd.oci.image.index.v1+json",
        )
        requested = "example/immutable@" + index["digest"]
        names = {"index": index, "manifest": manifest, "config": configuration, "layer": filesystem}
        if fault in {"index", "manifest", "config", "layer"}:
            name = "blobs/sha256/" + names[fault]["digest"].split(":")[1]
            blobs[name] += b"tampered"
        if fault == "missing":
            del blobs["blobs/sha256/" + manifest["digest"].split(":")[1]]
        path = self.root / "image.tar"
        with tarfile.open(path, "w") as archive:
            for name, data in blobs.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            if fault in {"duplicate", "traversal", "link"}:
                name = next(iter(blobs)) if fault == "duplicate" else "../outside"
                item = tarfile.TarInfo(name)
                if fault == "link":
                    item.type, item.linkname = tarfile.SYMTYPE, "/outside"
                archive.addfile(item)
        local = {
            "Id": configuration["digest"],
            "Os": "linux",
            "Architecture": "amd64",
            "RootFS": {"Type": "layers", "Layers": [diff_id]},
        }
        return path, requested, local

    def runner(self, row, *, ok=True):
        calls = []

        class Runner:
            def run(self, args, **kwargs):
                calls.append(args)
                return SimpleNamespace(ok=ok, stdout=json.dumps(row).encode())

        return Runner(), calls

    def test_index_binds_selected_config_and_both_compressed_and_decoded_layer_hashes(self):
        path, requested, local = self.image(extra_platform=True)
        runner, calls = self.runner(local)
        proof = self.module.verify_local_image(runner, requested, path)
        self.assertEqual(proof["requested_image"], requested)
        self.assertEqual(proof["execution_image"], local["Id"])
        self.assertEqual(proof["platform"], "linux/amd64")
        self.assertEqual(proof["rootfs_diff_ids"], local["RootFS"]["Layers"])
        self.assertEqual(len(proof["layers"]), 1)
        self.assertEqual(proof["unselected_descriptors"], 2)
        self.assertEqual(
            calls, [["image", "inspect", local["Id"], "--format", self.module.INSPECT_FORMAT]]
        )
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(proof))
        self.assertEqual([entry.name for entry in self.root.iterdir()], ["image.tar"])

    def test_missing_ambiguous_tampered_wrong_platform_sizes_and_media_fail_before_docker(self):
        for fault in (
            "index",
            "manifest",
            "config",
            "layer",
            "missing",
            "ambiguous",
            "diff",
            "platform",
            "manifest_size",
            "config_size",
            "layer_size",
            "media",
            "duplicate",
            "traversal",
            "link",
            "gzip",
            "size_null",
            "size_missing",
        ):
            with self.subTest(fault=fault):
                path, requested, local = self.image(fault=fault)
                runner, calls = self.runner(local)
                with self.assertRaises(self.module.ImageProvenanceError):
                    self.module.verify_local_image(runner, requested, path)
                self.assertEqual(calls, [])

    def test_local_config_platform_and_layer_identity_must_be_affirmative(self):
        path, requested, local = self.image()
        for field, value in (
            ("Id", "sha256:" + "a" * 64),
            ("Os", "windows"),
            ("Architecture", "arm64"),
            ("RootFS", {"Layers": []}),
        ):
            with self.subTest(field=field):
                runner, _ = self.runner({**local, field: value})
                with self.assertRaises(self.module.ImageProvenanceError):
                    self.module.verify_local_image(runner, requested, path)
        runner, _ = self.runner(local, ok=False)
        with self.assertRaises(self.module.ImageProvenanceError):
            self.module.verify_local_image(runner, requested, path)

    def test_original_pin_is_required_and_archive_is_not_a_fallback_for_arbitrary_image_id(self):
        path, _requested, local = self.image()
        for requested in ("example/immutable:latest", local["Id"], "example@sha256:" + "c" * 64):
            with self.subTest(requested=requested):
                runner, calls = self.runner(local)
                with self.assertRaises(self.module.ImageProvenanceError):
                    self.module.verify_local_image(runner, requested, path)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
