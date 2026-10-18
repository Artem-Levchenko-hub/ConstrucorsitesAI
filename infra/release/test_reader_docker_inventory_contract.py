"""Complete Docker observation retries use no mutations or weakened admission."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


class DockerInventoryTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("generation-reader-inventory.py")
        spec = importlib.util.spec_from_file_location("docker_inventory_contract", source)
        self.observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.observer)
        self.a, self.b = "a" * 64, "b" * 64

    def listing(self, *ids):
        return "\n".join(ids)

    def inspection(self, *ids):
        return "\n".join(json.dumps({
            "id": value, "name": "/helper-" + value[:8],
            "entrypoint": ["sh"], "cmd": ["-c", "sleep 10"],
        }) for value in ids)

    def collect(self, outputs):
        calls = []
        def read_only(argv):
            calls.append(argv)
            self.assertTrue(
                argv == ["docker", "ps", "-aq", "--no-trunc"]
                or argv[:3] == ["docker", "inspect", "--format"]
            )
            value = next(outputs)
            if isinstance(value, Exception):
                raise value
            return value
        with patch.object(self.observer, "command", side_effect=read_only):
            rows = self.observer.docker_inventory()
        return rows, calls

    def test_stable_complete_inventory_is_accepted(self):
        rows, calls = self.collect(iter([
            self.listing(self.a, self.b), self.inspection(self.a, self.b),
            self.listing(self.b, self.a),
        ]))
        self.assertEqual({r["id"] for r in rows}, {self.a, self.b})
        self.assertEqual(len(calls), 3)

    def test_creation_restarts_all_reads_and_returns_new_row(self):
        rows, calls = self.collect(iter([
            self.listing(self.a), self.inspection(self.a), self.listing(self.a, self.b),
            self.listing(self.a, self.b), self.inspection(self.a, self.b),
            self.listing(self.a, self.b),
        ]))
        self.assertEqual({r["id"] for r in rows}, {self.a, self.b})
        inspections = [c[4:] for c in calls if c[1] == "inspect"]
        self.assertEqual(inspections, [[self.a], [self.a, self.b]])

    def test_removal_restarts_all_reads(self):
        rows, _ = self.collect(iter([
            self.listing(self.a, self.b), self.inspection(self.a, self.b),
            self.listing(self.a), self.listing(self.a),
            self.inspection(self.a), self.listing(self.a),
        ]))
        self.assertEqual([r["id"] for r in rows], [self.a])

    def test_continuous_churn_denies_after_bounded_reads(self):
        outputs = [self.listing(self.a), self.inspection(self.a),
                   self.listing(self.a, self.b)] * 3
        with self.assertRaisesRegex(ValueError, "roster changed"):
            self.collect(iter(outputs))

    def test_empty_roster_skips_inspect(self):
        rows, calls = self.collect(iter(["", ""]))
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 2)

    def test_duplicate_or_malformed_ids_deny(self):
        for value in [self.listing(self.a, self.a), self.a + "\ninvalid"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.collect(iter([value]))

    def test_final_roster_is_validated_too(self):
        with self.assertRaises(ValueError):
            self.collect(iter([self.a, self.inspection(self.a), "invalid"]))

    def test_incomplete_duplicate_or_foreign_inspection_denies(self):
        for raw in ["", self.inspection(self.a, self.a), self.inspection(self.b)]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect(iter([self.a, raw]))

    def test_read_failure_and_malformed_json_are_not_retried(self):
        for failure in [ValueError("docker failed"), "{invalid"]:
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.collect(iter([self.a, failure]))

    def test_new_reader_is_not_filtered_from_stable_retry(self):
        reader = json.dumps({
            "id": self.b, "name": "/unexpected-reader",
            "entrypoint": ["python", "-m"], "cmd": ["yleum_api.workers.run"],
            "configured_image": "omnia-api:" + "c" * 40,
        })
        rows, _ = self.collect(iter([
            self.a, self.inspection(self.a), self.listing(self.a, self.b),
            self.listing(self.a, self.b), self.inspection(self.a) + "\n" + reader,
            self.listing(self.a, self.b),
        ]))
        added = next(r for r in rows if r["id"] == self.b)
        self.assertTrue(self.observer.is_reader_container(added))


if __name__ == "__main__":
    unittest.main()

