"""Durable migration exclusion must survive loss of the controller process."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


class MigrationDrainTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "migration_drain", Path(__file__).with_name("generation-publication-drain.py")
        )
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "publications"
        self.root.mkdir()
        self.project, self.operation = str(uuid4()), str(uuid4())
        self.directory = self.root / self.project / "migrations"
        self.directory.mkdir(parents=True)

    def journal(self, state="prepared", effect=False, **changes):
        record = {"version": 1, "project_id": self.project, "operation_id": self.operation,
                  "state": state, "effect_possible": effect, **changes}
        (self.directory / f"{self.operation}.json").write_text(json.dumps(record))

    def test_orphan_effect_blocks_even_without_a_publication_or_running_task(self):
        self.journal("blocked", True)
        with self.assertRaisesRegex(RuntimeError, "migration journals are not quiescent"):
            self.checker.require_publications_drained(self.root)

    def test_preparing_applying_and_reconciling_are_not_drained(self):
        for state in ("preparing", "applying", "reconciling"):
            with self.subTest(state=state):
                self.journal(state)
                with self.assertRaises(RuntimeError):
                    self.checker.require_publications_drained(self.root)

    def test_completed_or_prepared_operation_without_effect_does_not_block(self):
        for state in ("prepared", "applied", "failed", "stale", "blocked"):
            with self.subTest(state=state):
                self.journal(state)
                self.assertEqual(self.checker.require_publications_drained(self.root), 0)

    def test_malformed_or_forged_journal_fails_closed(self):
        for changes in ({"project_id": str(uuid4())}, {"operation_id": str(uuid4())},
                        {"state": "unknown"}, {"effect_possible": "false"}, {"version": 2}):
            with self.subTest(changes=changes):
                self.journal(**changes)
                with self.assertRaises(RuntimeError):
                    self.checker.require_publications_drained(self.root)

    def test_orphan_verifier_must_be_removed_even_if_its_controller_journal_is_missing(self):
        for name, labels in (
            ("orphan", {"omnia.resource_kind": "publication-migration-verifier"}),
            ("omnia-publication-migration-" + self.operation.replace("-", ""), {}),
        ):
            with self.subTest(name=name):
                container = SimpleNamespace(name=name, reload=lambda: None,
                                            attrs={"Config": {"Labels": labels}})
                client = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: [container]))
                with self.assertRaisesRegex(self.checker.PhysicalDrainError, "verifier cleanup"):
                    self.checker.require_physical_writers_drained(self.root.parent, client)
