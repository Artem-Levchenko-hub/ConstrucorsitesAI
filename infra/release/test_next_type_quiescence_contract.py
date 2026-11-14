"""The new command helper needs complete, explicit source authority."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class NextTypeSourceClosureTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "next_type_drain", self.directory / "generation-publication-drain.py",
        )
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        self.old = self.checker._PORTABLE_QUIESCENCE_PLATFORM_BOOTSTRAP_V2_SOURCES
        self.next_type = self.checker._PORTABLE_QUIESCENCE_NEXT_TYPE_PREPARATION_SOURCES
        self.bootstrap_recheck = self.checker._PORTABLE_QUIESCENCE_BOOTSTRAP_RECHECK_SOURCES
        self.guard_retry = self.checker._PORTABLE_QUIESCENCE_SOURCE_GUARD_RETRY_SOURCES
        self.guard_recovery = self.checker._PORTABLE_QUIESCENCE_SOURCE_GUARD_RECOVERY_SOURCES
        self.guard_lease = self.checker._PORTABLE_QUIESCENCE_SOURCE_GUARD_LEASE_SOURCES
        self.reader_wait = self.checker._PORTABLE_QUIESCENCE_READER_WAIT_SOURCES
        self.current = self.checker._PORTABLE_QUIESCENCE_MEAL_ACCEPTANCE_SOURCES

    def test_historical_complete_bundle_is_preserved_and_still_supported(self):
        old_fixture = json.loads((self.directory / "fixtures/platform-bootstrap-v2-quiescence-sources.json").read_bytes())
        self.assertEqual(self.old, old_fixture)
        self.assertEqual(len(self.old), 20)
        for fixture in (self.directory / "fixtures").glob("*quiescence-sources.json"):
            with self.subTest(fixture=fixture.name):
                self.checker._require_reviewed_portable_sources(json.loads(fixture.read_bytes()))

    def test_new_bundle_adds_only_the_reviewed_helper_and_adapter(self):
        fixture = json.loads((self.directory / "fixtures/next-type-preparation-quiescence-sources.json").read_bytes())
        self.assertEqual(self.next_type, fixture)
        self.assertEqual(len(self.next_type), 21)
        self.assertEqual(set(self.next_type), set(self.old) | {"services.next_type_preparation"})
        self.assertEqual(
            {key: value for key, value in self.next_type.items() if key not in {"services.machine_adapter", "services.next_type_preparation"}},
            {key: value for key, value in self.old.items() if key != "services.machine_adapter"},
        )
        self.assertIn("services.next_type_preparation", self.checker._PORTABLE_QUIESCENCE_SOURCE_NAMES)
        self.assertNotIn("services.next_type_preparation", self.checker._PORTABLE_QUIESCENCE_CONDITIONAL_SOURCE_NAMES)

    def test_bootstrap_recheck_changes_only_the_adapter_in_the_complete_bundle(self):
        fixture = json.loads((self.directory / "fixtures/bootstrap-recheck-quiescence-sources.json").read_bytes())
        self.assertEqual(self.bootstrap_recheck, fixture)
        self.assertEqual(set(self.bootstrap_recheck), set(self.next_type))
        self.assertEqual(
            {key for key in self.bootstrap_recheck if self.bootstrap_recheck[key] != self.next_type[key]},
            {"services.machine_adapter"},
        )

    def test_guard_retry_updates_only_the_adapter_and_resource_manager(self):
        fixture = self.directory / "fixtures/source-guard-retry-quiescence-sources.json"
        self.assertEqual(self.guard_retry, json.loads(fixture.read_bytes()))
        self.assertEqual(set(self.guard_retry), set(self.bootstrap_recheck))
        self.assertEqual(
            {key for key in self.guard_retry if self.guard_retry[key] != self.bootstrap_recheck[key]},
            {"services.machine_adapter", "services.docker_cell_resources"},
        )

    def test_guard_recovery_updates_only_the_startup_checker(self):
        fixture = self.directory / "fixtures/source-guard-recovery-quiescence-sources.json"
        self.assertEqual(self.guard_recovery, json.loads(fixture.read_bytes()))
        self.assertEqual(set(self.guard_recovery), set(self.guard_retry))
        self.assertEqual(
            {key for key in self.guard_recovery if self.guard_recovery[key] != self.guard_retry[key]},
            {"services.machine_startup"},
        )

    def test_guard_lease_observation_updates_only_the_adapter(self):
        fixture = self.directory / "fixtures/source-guard-lease-quiescence-sources.json"
        self.assertEqual(self.guard_lease, json.loads(fixture.read_bytes()))
        self.assertEqual(set(self.guard_lease), set(self.guard_recovery))
        self.assertEqual(
            {key for key in self.guard_lease if self.guard_lease[key] != self.guard_recovery[key]},
            {"services.machine_adapter"},
        )

    def test_reader_wait_changes_only_the_fenced_command_and_dispatch_sources(self):
        self.assertEqual(set(self.reader_wait), set(self.guard_lease))
        changed = {key for key in self.reader_wait if self.reader_wait[key] != self.guard_lease[key]}
        self.assertEqual(changed, {"services.generation_reader_inventory",
                                   "services.machine_adapter", "routers.workspace"})
        for key in changed:
            with self.subTest(module=key), self.assertRaises(self.checker.PhysicalDrainError):
                self.checker._require_reviewed_portable_sources(
                    {**self.reader_wait, key: self.guard_lease[key]},
                )

    def test_meal_acceptance_pins_exact_successor_of_reader_wait(self):
        self.checker._require_reviewed_portable_sources(self.reader_wait)
        self.assertEqual(set(self.current), set(self.reader_wait))
        changed = {key for key in self.current if self.current[key] != self.reader_wait[key]}
        self.assertEqual(changed, {"routers.workspace", "schemas.workspace",
                                   "services.generation_reader_inventory"})
        for key in changed:
            with self.subTest(module=key), self.assertRaises(self.checker.PhysicalDrainError):
                self.checker._require_reviewed_portable_sources(
                    {**self.current, key: self.reader_wait[key]},
                )

    def test_current_committed_source_closure_matches_linux_git_bytes(self):
        root = self.directory.parents[1] / "apps/orchestrator/src/yleum_orchestrator"
        for name, expected in self.current.items():
            path = root / (name.replace(".", "/") + ".py")
            with self.subTest(module=name):
                # Windows checkout line endings are not live Linux source bytes.
                raw = path.read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)

    def test_partial_unknown_and_mixed_successors_do_not_inherit_authority(self):
        for key in self.current:
            for change in ("missing", "foreign"):
                candidate = dict(self.current)
                if change == "missing":
                    del candidate[key]
                else:
                    candidate[key] = "0" * 64
                with self.subTest(module=key, change=change):
                    with self.assertRaises(self.checker.PhysicalDrainError):
                        self.checker._require_reviewed_portable_sources(candidate)
        mixed = {**self.current, "services.machine_adapter": self.old["services.machine_adapter"]}
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.checker._require_reviewed_portable_sources(mixed)

    def test_new_helper_presence_prevents_old_producer_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for label in ("old", "new", "helper"):
                path = Path(directory) / (label + ".py")
                path.write_bytes(label.encode())
                paths[label] = path
            def digest(label):
                return hashlib.sha256(paths[label].read_bytes()).hexdigest()
            old = {"services.machine_adapter": digest("old")}
            new = {"services.machine_adapter": digest("new"), "services.next_type_preparation": digest("helper")}

            def collect(adapter, helper):
                origins = {"yleum_orchestrator.services.machine_adapter": SimpleNamespace(origin=str(paths[adapter]))}
                if helper:
                    origins["yleum_orchestrator.services.next_type_preparation"] = SimpleNamespace(origin=str(paths["helper"]))
                with (
                    patch.object(self.checker, "_PORTABLE_QUIESCENCE_SOURCE_BUNDLES", (old, new)),
                    patch.object(self.checker, "_PORTABLE_QUIESCENCE_SOURCE_NAMES", tuple(new)),
                    patch.object(importlib.util, "find_spec", side_effect=origins.get),
                ):
                    return self.checker._portable_quiescence_sources()

            self.assertEqual(collect("old", False), old)
            self.assertEqual(collect("new", True), new)
            for adapter, helper in (("old", True), ("new", False)):
                with self.subTest(adapter=adapter, helper=helper):
                    with self.assertRaises(self.checker.PhysicalDrainError):
                        collect(adapter, helper)


if __name__ == "__main__":
    unittest.main()
