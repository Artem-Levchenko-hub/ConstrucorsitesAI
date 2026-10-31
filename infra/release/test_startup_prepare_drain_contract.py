"""A durable pending startup can outlive SQL activity without a container."""

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid5


WORKSPACE = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
OPERATION = str(uuid5(UUID(RUN), "machine-startup-prepare/v1:7"))


@unittest.skipUnless(os.name == "posix", "Linux controller filesystem contract")
class StartupPrepareDrainTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "startup_prepare_drain",
            Path(__file__).with_name("generation-publication-drain.py"),
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        authority = patch.object(
            self.module, "_portable_quiescence_sources", return_value={}
        )
        authority.start()
        self.addCleanup(authority.stop)
        self.client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: []),
        )

    @staticmethod
    def record(root, state="completed"):
        value = {
            "binding": {
                "version": 1,
                "workspace_id": WORKSPACE,
                "project_id": WORKSPACE,
                "owner_id": WORKSPACE,
                "generation_run_id": RUN,
                "fencing_epoch": 7,
                "admission_operation_id": WORKSPACE,
                "admission_request_sha256": "a" * 64,
                "physical_volume": "fixture-volume",
                "physical_volume_identity": "b" * 64,
                "workspace_revision": "c" * 64,
                "manifest_digest": "d" * 64,
            },
            "state": state,
            "incarnation": "e" * 64 if state == "completed" else None,
            "recovery_reference": "f" * 64 if state == "recovered" else None,
        }
        path = (
            root
            / "project-machines"
            / WORKSPACE
            / "startup-prepares"
            / (OPERATION + ".json")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path, value

    def test_pending_create_denies_even_without_docker_container_or_sql_activity(self):
        for state in ("intent", "dispatched"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.record(root, state)
                with self.assertRaises(self.module.PhysicalDrainError):
                    self.module.require_physical_writers_drained(root, self.client)

    def test_terminal_histories_allow_drain_without_claiming_runtime_proof(self):
        for state in ("completed", "recovered"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.record(root, state)
                self.module.require_physical_writers_drained(root, self.client)

    def test_read_access_timestamp_is_not_a_journal_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _ = self.record(root)
            os.utime(path, ns=(1, path.stat().st_mtime_ns))
            self.module.require_physical_writers_drained(root, self.client)

    def test_unreviewed_model_cannot_admit_terminal_journal_with_empty_docker_inventory(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.record(root)
            with patch.object(
                self.module,
                "_portable_quiescence_sources",
                side_effect=self.module.PhysicalDrainError(
                    "quiescence_unsupported_source"
                ),
            ):
                with self.assertRaises(self.module.PhysicalDrainError):
                    self.module.require_physical_writers_drained(root, self.client)

    def test_malformed_or_foreign_terminal_receipt_denies(self):
        mutations = (
            lambda v: v.update(state="unknown"),
            lambda v: v.update(incarnation=None),
            lambda v: v.update(recovery_reference="f" * 64),
            lambda v: v.update(extra=True),
            lambda v: v.pop("recovery_reference"),
            lambda v: v["binding"].update(version=True),
            lambda v: v["binding"].pop("version"),
            lambda v: v["binding"].update(fencing_epoch=True),
            lambda v: v["binding"].update(fencing_epoch="7"),
            lambda v: v["binding"].update(workspace_id=RUN),
            lambda v: v["binding"].update(generation_run_id=WORKSPACE),
            lambda v: v["binding"].update(admission_request_sha256="not-a-digest"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path, original = self.record(root)
                value = copy.deepcopy(original)
                mutation(value)
                path.write_text(json.dumps(value))
                with self.assertRaises(self.module.PhysicalDrainError):
                    self.module.require_physical_writers_drained(root, self.client)

    def test_symlink_at_each_startup_path_level_denies(self):
        for level in ("machines", "workspace", "journal", "file"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path, _ = self.record(root)
                link = {
                    "machines": root / "project-machines",
                    "workspace": path.parent.parent,
                    "journal": path.parent,
                    "file": path,
                }[level]
                target = root / "retained-fixture"
                link.rename(target)
                try:
                    link.symlink_to(target, target_is_directory=target.is_dir())
                except OSError as exc:
                    self.skipTest(f"symlink privilege unavailable: {exc.errno}")
                with self.assertRaises(self.module.PhysicalDrainError):
                    self.module.require_physical_writers_drained(root, self.client)

    def test_unknown_entries_and_unbounded_receipts_deny(self):
        for shape in ("invalid_json", "large", "directory", "unknown_entry"):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path, _ = self.record(root)
                if shape == "directory":
                    path.unlink()
                    path.mkdir()
                elif shape == "unknown_entry":
                    path.rename(path.with_suffix(".pending"))
                else:
                    path.write_text("{" if shape == "invalid_json" else " " * 32769)
                with self.assertRaises(self.module.PhysicalDrainError):
                    self.module.require_physical_writers_drained(root, self.client)

    def test_receipt_change_during_inspection_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, value = self.record(root)
            calls = 0

            def inventory(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    value["incarnation"] = "0" * 64
                    path.write_text(json.dumps(value))
                return []

            with patch.object(self.client.containers, "list", inventory):
                with self.assertRaises(self.module.PhysicalDrainError):
                    self.module.require_physical_writers_drained(root, self.client)


if __name__ == "__main__":
    unittest.main()
