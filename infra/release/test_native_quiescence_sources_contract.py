"""New producer dependencies cannot break or silently inherit old authority."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class NativeSourceClosureTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "native_source_closure",
            Path(__file__).with_name("generation-publication-drain.py"),
        )
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hashes = {}
        self.old = {
            "services.project_machine": self.source("old-machine", b"old machine"),
            "routers.workspace": self.source("old-route", b"old route"),
        }
        self.new = {
            "services.project_machine": self.source("new-machine", b"new machine"),
            "routers.workspace": self.source("new-route", b"new route"),
            "services.machine_startup": self.source("startup", b"startup journal"),
            "schemas.workspace": self.source("schema", b"startup request schema"),
        }
        self.probes = (
            "services.project_machine",
            "routers.workspace",
            "services.machine_startup",
        )
        self.contents = {
            value: Path(self.directory.name) / (name + ".py")
            for name, value in self.hashes.items()
        }

    def source(self, name, content):
        path = Path(self.directory.name) / (name + ".py")
        path.write_bytes(content)
        self.hashes[name] = hashlib.sha256(content).hexdigest()
        return self.hashes[name]

    def collect(self, actual):
        origins = {
            "yleum_orchestrator." + name: SimpleNamespace(
                origin=str(self.contents[digest])
            )
            for name, digest in actual.items()
        }
        with (
            patch.object(
                self.checker,
                "_PORTABLE_QUIESCENCE_SOURCE_BUNDLES",
                (self.old, self.new),
            ),
            patch.object(
                self.checker, "_PORTABLE_QUIESCENCE_SOURCE_NAMES", self.probes
            ),
            patch.object(importlib.util, "find_spec", side_effect=origins.get),
        ):
            return self.checker._portable_quiescence_sources()

    def test_old_bundle_ignores_existing_schema_that_old_producer_does_not_use(self):
        actual = {**self.old, "schemas.workspace": self.new["schemas.workspace"]}
        self.assertEqual(self.collect(actual), self.old)

    def test_new_bundle_binds_its_whole_conditional_dependency_closure(self):
        self.assertEqual(self.collect(self.new), self.new)

    def test_new_producer_never_falls_back_to_old_authority(self):
        actual = {
            **self.old,
            "services.machine_startup": self.new["services.machine_startup"],
        }
        with self.assertRaises(self.checker.PhysicalDrainError):
            self.collect(actual)

    def test_missing_unknown_or_mixed_native_sources_deny(self):
        for name in self.new:
            for change in ("missing", "foreign"):
                actual = dict(self.new)
                if change == "missing":
                    del actual[name]
                else:
                    actual[name] = self.old["services.project_machine"]
                with self.subTest(name=name, change=change):
                    with self.assertRaises(self.checker.PhysicalDrainError):
                        self.collect(actual)


if __name__ == "__main__":
    unittest.main()
