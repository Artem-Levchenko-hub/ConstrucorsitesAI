"""Disposable files and a read-only Docker fake; no daemon or network needed."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("published-artifacts.py")
spec = importlib.util.spec_from_file_location("published_artifacts", SCRIPT)
module = importlib.util.module_from_spec(spec) if SCRIPT.exists() else None
if module:
    spec.loader.exec_module(module)
REAL_FLOCK = module._flock if module else None

P = "11111111-1111-4111-8111-111111111111"
W = "22222222-2222-4222-8222-222222222222"
R = "33333333-3333-4333-8333-333333333333"
OWNER_ID = "44444444-4444-4444-8444-444444444444"
PROD = "55555555-5555-4555-8555-555555555555"
SNAP = "66666666-6666-4666-8666-666666666666"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def tar_bytes(entries):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w") as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return target.getvalue()


def image_bytes(labels=None):
    layer = tar_bytes([("hello", b"offline")])
    config = encoded({"os": "linux", "architecture": "amd64",
                      "config": {"Labels": labels or {}},
                      "rootfs": {"type": "layers", "diff_ids": ["sha256:" + sha(layer)]}})
    identity = "sha256:" + sha(config)
    raw = tar_bytes([("manifest.json", encoded([{"Config": sha(config) + ".json",
                                                "RepoTags": [], "Layers": ["layer.tar"]}])),
                     (sha(config) + ".json", config), ("layer.tar", layer)])
    return identity, raw


class Docker:
    def __init__(self, images):
        self.images, self.calls, self.hook = images, [], None

    def run(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] != ["image", "inspect"]:
            raise AssertionError("network or mutation command")
        value = self.images.get(args[2])
        return SimpleNamespace(ok=value is not None,
                               text=lambda: json.dumps([value[0]]) if value else "private error")

    def run_to_file(self, args, *, path, **kwargs):
        self.calls.append(args)
        if args[:2] != ["image", "save"]:
            raise AssertionError("network or mutation command")
        with path.open("xb") as output:
            output.write(self.images[args[2]][1])
        if self.hook:
            self.hook()
        return SimpleNamespace(ok=True)


class PublishedTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module, "published supplement is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.out = self.root / "peer"
        self.out.mkdir(mode=0o700)
        self.store = self.state / "project-machines" / "artifacts" / W
        self.store.mkdir(parents=True)
        self.journal = self.state / "cell-publications" / P / "publication.json"
        self.journal.parent.mkdir(parents=True)
        locks = self.state / "cell-publications" / "locks"
        locks.mkdir()
        (locks / f"workspace-{P}.lock").touch(mode=0o600)
        self.labels = {"omnia.managed": "true", "omnia.project_machine": "true",
                       "omnia.workspace_id": W, "omnia.project_id": P,
                       "omnia.owner_id": OWNER_ID, "omnia.resource_kind": "environment",
                       "omnia.namespace": "prod"}
        self.app_id, app_raw = image_bytes(self.labels)
        self.common_id, common_raw = image_bytes()
        self.app = self.seal("a" * 32 + ".tar", app_raw)
        self.seed = self.seal("b" * 32 + ".tar", tar_bytes([("app.js", b"build")]))
        self.payload = {"project_id": P, "owner_id": OWNER_ID, "production_workspace_id": PROD,
                        "active_release": {"release_id": R, "image_id": self.app_id,
                                           "epoch": 1, "snapshot_id": SNAP,
                                           "source_revision": "f" * 64,
                                           "manifest": {"services": []},
                                           "placement": {"backend": "kubernetes", "namespace": "app-" + P,
                                                         "app_image": "local/app:release", "core_image": "local/core:release",
                                                         "guard_image": "local/guard:release",
                                                         "seeds": [{"mount_path": "/workspace", "artifact": str(self.seed),
                                                                    "size": self.seed.stat().st_size, "durable": False},
                                                                   {"mount_path": "/var/lib/postgresql/data", "artifact": "/obsolete/pg.tar",
                                                                    "size": 100, "durable": True}]}}}
        self.request = {"project_id": P, "owner_id": OWNER_ID, "workspace_id": W,
                        "snapshot_id": SNAP, "source_revision": "f" * 64}
        self.request_path = self.journal.parent / "requests" / (R + ".json")
        self.request_path.parent.mkdir()
        self.request_path.write_bytes(encoded(self.request))
        self.archive = self.root / "state.tar.gz"
        self.refresh_archive()
        records, images = {}, {}
        for role in ("app", "core", "guard", "redis"):
            identity, raw = (self.app_id, app_raw) if role == "app" else (self.common_id, common_raw)
            reference = "local/" + role + ":release"
            digest = "local/" + role + "@sha256:" + sha(role.encode())
            records[role] = {"reference": reference, "config_id": identity, "repo_digest": digest,
                             "runtime_image_id": "docker-pullable://" + digest,
                             "platform": {"os": "linux", "architecture": "amd64", "variant": ""}}
            images[identity] = ({"Id": identity, "RepoDigests": [], "Os": "linux", "Architecture": "amd64"}, raw)
        for record in records.values():
            images[record["config_id"]][0]["RepoDigests"].append(record["repo_digest"])
        self.witness = {"schema": 1, "publications": [{"project_id": P, "release_id": R, "epoch": 1,
                        "journal_sha256": sha(self.journal.read_bytes()), "source_image": {"artifact_ref": self.app.name},
                        "images": records}]}
        self.runner = Docker(images)
        self.cells = SimpleNamespace(_publication_records=self.records)
        self.addCleanup(patch.stopall)
        patch.object(module, "_flock", lambda fd: None).start()

    def records(self, state):
        return [(P, json.loads(self.journal.read_bytes()))]

    def seal(self, name, data):
        path = self.store / name
        path.write_bytes(data)
        info = path.stat()
        path.with_name(name + ".ok").write_bytes(encoded({"sha256": sha(data), "size": len(data),
                                                        "inode": info.st_ino, "mtime_ns": info.st_mtime_ns}))
        return path

    def refresh_archive(self):
        self.journal.write_bytes(encoded(self.payload))
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(self.state / "cell-publications", arcname="state/cell-publications")

    def collect(self, **kwargs):
        return module.collect(self.cells, self.runner, self.state, self.archive, self.out,
                              runtime_witness=kwargs.get("runtime_witness", self.witness))

    def runtime_facts(self):
        facts = []
        for role, image in self.witness["publications"][0]["images"].items():
            facts.append({"project_id": P, "component": role, "reference": image["reference"],
                          "runtime_image_id": image["runtime_image_id"], "config_id": image["config_id"],
                          "repo_digests": [image["repo_digest"]], "platform": image["platform"]})
        return facts

    def resolve(self, facts=None):
        return module.resolve_witness(self.cells, self.runner, self.state, self.archive,
                                      self.runtime_facts() if facts is None else facts)

    def test_resolve_exact_historical_image_ignores_latest_environment(self):
        stale_id, raw = image_bytes({**self.labels, "stale": "true"})
        stale = self.seal("c" * 32 + ".tar", raw)
        metadata = self.state / "project-machines" / W / "docker.json"
        metadata.parent.mkdir()
        metadata.write_bytes(encoded({"environment_ref": {"image_id": stale_id, "artifact_ref": stale.name}}))
        self.assertEqual(self.resolve(), self.witness)
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertTrue(all(call[:2] == ["image", "inspect"] for call in self.runner.calls))

    def test_resolve_missing_archive_and_unknown_runtime_config_fail(self):
        facts = self.runtime_facts()
        facts[0]["config_id"] = self.common_id
        with self.assertRaises(module.PublishedError):
            self.resolve(facts)
        self.app.unlink()
        with self.assertRaises(module.PublishedError):
            self.resolve()

    def test_resolve_identical_archive_dedup_but_ambiguous_bytes_fail(self):
        self.seal("c" * 32 + ".tar", self.app.read_bytes())
        self.assertEqual(self.resolve(), self.witness)
        with tarfile.open(self.app) as archive:
            entries = [(m.name, archive.extractfile(m).read()) for m in archive]
        entries.append(("extra-metadata", b"different save, same config"))
        self.seal("c" * 32 + ".tar", tar_bytes(entries))
        with self.assertRaisesRegex(module.PublishedError, "source_image_ambiguous"):
            self.resolve()

    def test_resolve_filters_peer_projects_and_requires_complete_active_images(self):
        facts = self.runtime_facts()
        facts.append({**facts[0], "project_id": PROD})
        self.assertEqual(self.resolve(facts), self.witness)
        with self.assertRaises(module.PublishedError):
            self.resolve(facts[1:])

    def test_round_trip_dedup_current_pg_reference_and_no_network(self):
        result = self.collect()
        validated = module.validate(self.out, self.archive, runtime_witness=self.witness)
        self.assertEqual(validated, result)
        self.assertEqual(len(list(self.out.glob("published-*.tar"))), 3)
        self.assertEqual(sum(c[:2] == ["image", "save"] for c in self.runner.calls), 1)
        self.assertFalse(result["full_dr_verified"])
        self.assertEqual(result["publications"][0]["databases"][0]["workspace_id"], PROD)
        self.assertEqual(result["publications"][0]["databases"][0]["kind"], "project")
        self.assertNotIn("/obsolete", json.dumps(result))

    def test_witness_required_before_docker(self):
        with self.assertRaisesRegex(module.PublishedError, "runtime_witness_required"):
            self.collect(runtime_witness=None)
        self.assertEqual(self.runner.calls, [])

    def test_missing_corrupt_and_stale_seal_fail_closed(self):
        original = self.seed.read_bytes()
        seal = self.seed.with_name(self.seed.name + ".ok")
        for fault in ("missing", "corrupt", "seal"):
            with self.subTest(fault=fault):
                self.seal(self.seed.name, original)
                if fault == "missing":
                    self.seed.unlink()
                elif fault == "corrupt":
                    info = self.seed.stat()
                    self.seed.write_bytes(b"X" + original[1:])
                    os.utime(self.seed, ns=(info.st_atime_ns, info.st_mtime_ns))
                else:
                    seal.write_text("{}")
                with self.assertRaises(module.PublishedError):
                    module.verify_sealed(self.seed, expected_size=len(original))

    def test_symlink_artifact_rejected(self):
        target = self.root / "external.tar"
        target.write_bytes(self.seed.read_bytes())
        self.seed.unlink()
        try:
            self.seed.symlink_to(target)
        except OSError:
            self.skipTest("Windows symlink privilege unavailable")
        with self.assertRaises(module.PublishedError):
            self.collect()

    def test_archived_journal_mismatch_prevents_docker(self):
        self.payload["active_release"]["epoch"] = 2
        self.journal.write_bytes(encoded(self.payload))
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertEqual(self.runner.calls, [])

    def test_release_changes_during_export_no_success_sidecar(self):
        self.runner.hook = lambda: self.journal.write_bytes(b"{}")
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertFalse((self.out / "PUBLISHED.json").exists())

    def test_request_changes_during_export_no_success_sidecar(self):
        self.runner.hook = lambda: self.request_path.write_bytes(b"{}")
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertFalse((self.out / "PUBLISHED.json").exists())

    def test_active_set_changes_during_export_no_success_sidecar(self):
        self.runner.hook = lambda: setattr(self.cells, "_publication_records", lambda root: [])
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertFalse((self.out / "PUBLISHED.json").exists())

    def test_new_active_project_during_export_rejects_incomplete_closure(self):
        def add_publication():
            other = copy.deepcopy(self.payload)
            other["project_id"] = PROD
            other["active_release"]["placement"]["namespace"] = "app-" + PROD
            base = self.state / "cell-publications" / PROD
            (base / "requests").mkdir(parents=True)
            (base / "publication.json").write_bytes(encoded(other))
            (base / "requests" / (R + ".json")).write_bytes(encoded({**self.request, "project_id": PROD}))
            (self.state / "cell-publications" / "locks" / f"workspace-{PROD}.lock").touch(mode=0o600)
            self.cells._publication_records = lambda root: self.records(root) + [(PROD, other)]

        self.runner.hook = add_publication
        with self.assertRaisesRegex(module.PublishedError, "publication_or_lock_changed"):
            self.collect()
        self.assertFalse((self.out / "PUBLISHED.json").exists())

    def test_offline_shared_image_bytes_verified_once_per_identity(self):
        self.collect()
        with patch.object(module, "verify_image", wraps=module.verify_image) as verify:
            module.validate(self.out, self.archive, runtime_witness=self.witness)
        common_calls = [call for call in verify.call_args_list if call.args[1] == self.common_id]
        self.assertEqual(len(common_calls), 1)

    def test_source_changes_after_copy_no_success(self):
        self.runner.hook = lambda: self.seed.write_bytes(b"changed")
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertFalse((self.out / "PUBLISHED.json").exists())

    def test_config_id_is_not_runtime_manifest_digest(self):
        self.witness["publications"][0]["images"]["app"]["runtime_image_id"] = self.app_id
        with self.assertRaises(module.PublishedError):
            self.collect()

    def test_official_dockerhub_manifest_aliases_match_but_foreign_repo_does_not(self):
        image = copy.deepcopy(self.witness["publications"][0]["images"]["redis"])
        suffix = "@sha256:" + "e" * 64
        image["repo_digest"] = "docker.io/library/redis" + suffix
        record = self.runner.images[self.common_id][0]
        record["Variant"] = None
        for alias in ("redis", "library/redis", "docker.io/library/redis"):
            record["RepoDigests"] = [alias + suffix]
            module.inspect_local(self.runner, image)
        record["RepoDigests"] = ["private.example/library/redis" + suffix]
        with self.assertRaises(module.PublishedError):
            module.inspect_local(self.runner, image)

    def test_seed_from_other_state_root_is_not_silently_rebased(self):
        seed = self.payload["active_release"]["placement"]["seeds"][0]
        seed["artifact"] = str(self.root / "foreign" / "project-machines" / "artifacts" / W / self.seed.name)
        self.refresh_archive()
        self.witness["publications"][0]["journal_sha256"] = sha(self.journal.read_bytes())
        with self.assertRaisesRegex(module.PublishedError, "seed_source_root_mismatch"):
            self.collect()

    @unittest.skipUnless(os.name == "posix", "fcntl requires Linux")
    def test_existing_flock_contention_fails_and_success_releases_same_inode(self):
        import fcntl
        lock = self.state / "cell-publications" / "locks" / f"workspace-{P}.lock"
        before = lock.stat().st_ino
        with lock.open("r+b") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (patch.object(module, "_flock", REAL_FLOCK),
                  patch.object(module.time, "monotonic", side_effect=[0, 61]),
                  self.assertRaisesRegex(module.PublishedError, "publication_lock_timeout")):
                self.collect()
            fcntl.flock(owner, fcntl.LOCK_UN)
        with patch.object(module, "_flock", REAL_FLOCK):
            self.collect()
        with lock.open("r+b") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(lock.stat().st_ino, before)

    @unittest.skipUnless(os.name == "posix", "fcntl requires Linux")
    def test_publication_lock_free_during_artifact_copy_and_image_export(self):
        import fcntl
        lock = self.state / "cell-publications" / "locks" / f"workspace-{P}.lock"
        probes = []
        original_copy = module.copy_blob

        def probe():
            with lock.open("r+b") as publisher:
                try:
                    fcntl.flock(publisher, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.fail("backup holds publication lock during heavy I/O")
                probes.append(True)

        def copy(*args, **kwargs):
            probe()
            return original_copy(*args, **kwargs)

        self.runner.hook = probe
        with patch.object(module, "_flock", REAL_FLOCK), patch.object(module, "copy_blob", copy):
            self.collect()
        self.assertEqual(len(probes), 3)

    @unittest.skipUnless(os.name == "posix", "Windows prevents unlink of open lock")
    def test_lock_inode_replacement_rejected_before_sidecar(self):
        lock = self.state / "cell-publications" / "locks" / f"workspace-{P}.lock"

        def replace_lock():
            lock.unlink()
            lock.touch(mode=0o600)

        self.runner.hook = replace_lock
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertFalse((self.out / "PUBLISHED.json").exists())

    def test_missing_local_image_does_not_pull(self):
        del self.runner.images[self.common_id]
        with self.assertRaises(module.PublishedError):
            self.collect()
        self.assertTrue(all(c[:2] in (["image", "inspect"], ["image", "save"]) for c in self.runner.calls))

    def test_foreign_app_provenance_and_incomplete_image_rejected(self):
        labels = {**self.labels, "omnia.workspace_id": PROD}
        identity, raw = image_bytes(labels)
        path = self.root / "foreign.tar"
        path.write_bytes(raw)
        with self.assertRaises(module.PublishedError):
            module.verify_image(path, identity, provenance=self.labels)
        with tarfile.open(path) as archive:
            entries = [(m.name, archive.extractfile(m).read()) for m in archive if m.name != "layer.tar"]
        path.write_bytes(tar_bytes(entries))
        with self.assertRaises(module.PublishedError):
            module.verify_image(path, identity)

    def test_durable_gap_explicit_and_current_pg_not_copied(self):
        self.payload["active_release"]["placement"]["seeds"].append(
            {"mount_path": "/business", "artifact": None, "size": 0, "durable": True})
        self.refresh_archive()
        self.witness["publications"][0]["journal_sha256"] = sha(self.journal.read_bytes())
        result = self.collect()
        self.assertEqual(result["publications"][0]["durable_pvc_gaps"], ["/business"])
        self.assertFalse(result["full_dr_verified"])

    def test_offline_validation_detects_missing_and_corrupt_blob_and_omitted_closure(self):
        result = self.collect()
        target = next(self.out.glob("published-*.tar"))
        original = target.read_bytes()
        target.write_bytes(b"corrupt")
        with self.assertRaises(module.PublishedError):
            module.validate(self.out, self.archive, runtime_witness=self.witness)
        target.unlink()
        with self.assertRaises(module.PublishedError):
            module.validate(self.out, self.archive, runtime_witness=self.witness)
        target.write_bytes(original)
        modified = copy.deepcopy(result)
        modified["publications"] = []
        (self.out / "PUBLISHED.json").write_bytes(encoded(modified))
        with self.assertRaises(module.PublishedError):
            module.validate(self.out, self.archive, runtime_witness=self.witness)


if __name__ == "__main__":
    unittest.main()
