"""Complete census tolerates helper churn without hiding reader authority changes."""

from __future__ import annotations

import copy
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
        self.a, self.b, self.c, self.d = (value * 64 for value in "abcd")
        self.calls = []

    def helper(self, identity, name=None, **changes):
        row = {
            "id": identity,
            "name": name or "helper-" + identity[:8],
            "image": "sha256:" + "1" * 64,
            "configured_image": "alpine@sha256:" + "2" * 64,
            "service": None,
            "running": True,
            "pid": 100,
            "status": "running",
            "paused": False,
            "dead": False,
            "restart": "no",
            "command": ["sh", "-c", "sleep 10"],
        }
        return {**row, **changes}

    def reader(self, identity, role="worker", name=None, **changes):
        commands = {
            "api": ["python", "-m", "uvicorn", "yleum_api.main:app"],
            "worker": ["python", "-m", "yleum_api.workers.run"],
            "generation-worker": ["python", "-m", "yleum_api.workers.generation"],
            "orchestrator": ["uvicorn", "yleum_orchestrator.main:app"],
        }
        return self.helper(
            identity,
            name or "yleum-prod-" + role,
            image="sha256:" + "3" * 64,
            configured_image="omnia-api:" + "4" * 40,
            service=role,
            command=commands[role],
            **changes,
        )

    def roster(self, *rows):
        return "\n".join(
            json.dumps({"id": row["id"], "name": row["name"]}) for row in rows
        )

    def inspection(self, *rows):
        output = []
        for original in rows:
            row = copy.deepcopy(original)
            row["name"] = "/" + row["name"]
            row["entrypoint"] = row.pop("command")
            row["cmd"] = []
            output.append(json.dumps(row))
        return "\n".join(output)

    def collect(self, outputs):
        self.calls = []
        outputs = iter(outputs)

        def read_only(argv):
            self.calls.append(argv)
            self.assertTrue(
                (
                    argv[:5] == ["docker", "ps", "-a", "--no-trunc", "--format"]
                    and len(argv) == 6
                )
                or (argv[:3] == ["docker", "inspect", "--format"] and len(argv) > 4)
            )
            value = next(outputs)
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(self.observer, "command", side_effect=read_only):
            return self.observer.docker_inventory()

    def inspected_ids(self):
        return [call[4:] for call in self.calls if call[1] == "inspect"]

    def test_stable_complete_readers_are_freshly_reinspected(self):
        rows = [
            self.reader(self.a, "api"),
            self.reader(self.b, "worker"),
            self.reader(self.c, "generation-worker"),
        ]
        result = self.collect([
            self.roster(*rows),
            self.inspection(*rows),
            self.inspection(*rows),
            self.roster(*reversed(rows)),
        ])
        self.assertEqual(set(self.observer.classify_containers(result, "core")),
                         {"api", "worker", "generation-worker"})
        self.assertEqual(self.inspected_ids(),
                         [[self.a, self.b, self.c], [self.a, self.b, self.c]])

    def test_helper_creation_is_fully_classified_before_acceptance(self):
        a, b = self.helper(self.a), self.helper(self.b)
        result = self.collect([
            self.roster(a), self.inspection(a), self.roster(a, b),
            self.inspection(b), self.roster(a, b),
        ])
        self.assertEqual({row["id"] for row in result}, {self.a, self.b})
        self.assertEqual(self.inspected_ids(), [[self.a], [self.b]])

    def test_observed_nonreader_removal_returns_only_current_ids(self):
        a, b = self.helper(self.a), self.helper(self.b)
        result = self.collect([
            self.roster(a, b), self.inspection(a, b), self.roster(b),
        ])
        self.assertEqual([row["id"] for row in result], [self.b])

    def test_helper_churn_keeps_reader_metadata_fresh_each_cycle(self):
        reader, a, b = self.reader(self.a), self.helper(self.b), self.helper(self.c)
        fresh = {**reader, "pid": 200}
        newer = {**reader, "pid": 300}
        result = self.collect([
            self.roster(reader, a), self.inspection(reader, a),
            self.inspection(fresh), self.roster(reader, b),
            self.inspection(b), self.inspection(newer), self.roster(reader, b),
        ])
        self.assertEqual({row["id"] for row in result}, {self.a, self.c})
        actual_reader = next(row for row in result if row["id"] == self.a)
        self.assertEqual(actual_reader["pid"], 300)
        self.assertEqual(self.inspected_ids(),
                         [[self.a, self.b], [self.a], [self.c], [self.a]])

    def test_nonreader_rename_uses_current_name_without_stale_mutable_fields(self):
        old = self.helper(self.a, name="source-read-before")
        renamed = {**old, "name": "source-read-after"}
        result = self.collect([
            self.roster(old), self.inspection(old), self.roster(renamed),
        ])
        self.assertEqual(result, [{
            key: renamed[key] for key in
            ("id", "image", "configured_image", "command", "name")
        }])
        for field in ("status", "running", "pid", "restart", "paused", "dead", "service"):
            self.assertNotIn(field, result[0])

    def test_same_id_nonreader_rename_into_protected_prefix_denies(self):
        old = self.helper(self.a)
        renamed = {**old, "name": "yleum-prod-api-shadow"}
        with self.assertRaises(ValueError):
            self.collect([self.roster(old), self.inspection(old), self.roster(renamed)])

    def test_reader_rename_during_fresh_inspection_denies(self):
        old = self.reader(self.a)
        renamed = {**old, "name": "renamed-reader"}
        with self.assertRaises(ValueError):
            self.collect([
                self.roster(old), self.inspection(old), self.inspection(renamed),
            ])

    def test_reader_rename_after_fresh_inspection_denies(self):
        old = self.reader(self.a)
        renamed = {**old, "name": "renamed-reader"}
        with self.assertRaises(ValueError):
            self.collect([
                self.roster(old), self.inspection(old), self.inspection(old),
                self.roster(renamed),
            ])

    def test_initial_inspection_must_match_initial_names(self):
        old = self.helper(self.a)
        renamed = {**old, "name": "different-at-inspection"}
        with self.assertRaises(ValueError):
            self.collect([self.roster(old), self.inspection(renamed)])
        self.assertEqual(len(self.calls), 2)

    def test_dormant_restart_policy_is_fresh_and_still_rejected_by_classifier(self):
        old = self.reader(
            self.a, name="retired-worker", running=False, status="exited", pid=0
        )
        fresh = {**old, "restart": "always"}
        result = self.collect([
            self.roster(old), self.inspection(old), self.inspection(fresh),
            self.roster(old),
        ])
        self.assertEqual(result[0]["restart"], "always")
        self.assertFalse(self.observer.retired_dormant(result[0]))
        self.assertEqual(self.observer.classify_containers([old], "commerce"), {})
        with self.assertRaises(ValueError):
            self.observer.classify_containers(result, "commerce")

    def test_unchanged_dormant_reader_remains_fully_observed(self):
        old = self.reader(
            self.a, name="retired-worker", running=False, status="exited", pid=0
        )
        result = self.collect([
            self.roster(old), self.inspection(old), self.inspection(old),
            self.roster(old),
        ])
        self.assertEqual(result, [old])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.a]])

    def test_removing_an_initial_reader_denies_even_if_it_was_dormant(self):
        old = self.reader(
            self.a, name="retired-worker", running=False, status="exited", pid=0
        )
        with self.assertRaises(ValueError):
            self.collect([
                self.roster(old), self.inspection(old), self.inspection(old),
                self.roster(),
            ])

    def test_new_reader_of_each_role_denies_without_filtering(self):
        a = self.helper(self.a)
        for role in ("api", "worker", "generation-worker", "orchestrator"):
            with self.subTest(role=role):
                added = self.reader(self.b, role, name="unexpected-" + role)
                if role == "orchestrator":
                    # Exercise role recognition independently of API image/name heuristics.
                    added["configured_image"] = "controller-fixture:local"
                with self.assertRaises(ValueError):
                    self.collect([
                        self.roster(a), self.inspection(a), self.roster(a, added),
                        self.inspection(added),
                    ])
                self.assertEqual(self.inspected_ids(), [[self.a], [self.b]])

    def test_new_dormant_candidate_is_not_silently_accepted(self):
        a = self.helper(self.a)
        added = self.reader(
            self.b, name="new-retired-worker", running=False, status="exited", pid=0
        )
        with self.assertRaises(ValueError):
            self.collect([
                self.roster(a), self.inspection(a), self.roster(a, added),
                self.inspection(added),
            ])

    def test_new_api_image_alias_is_a_candidate_without_standard_entrypoint(self):
        a = self.helper(self.a)
        added = self.helper(self.b, configured_image="omnia-api:" + "f" * 40)
        with self.assertRaises(ValueError):
            self.collect([
                self.roster(a), self.inspection(a), self.roster(a, added),
                self.inspection(added),
            ])

    def test_fresh_new_protected_name_cannot_be_hidden_by_earlier_roster_name(self):
        a, b = self.helper(self.a), self.helper(self.b)
        protected = {**b, "name": "yleum-prod-api-shadow"}
        with self.assertRaises(ValueError):
            self.collect([
                self.roster(a), self.inspection(a), self.roster(a, b),
                self.inspection(protected), self.roster(a, b),
            ])
        self.assertEqual(len(self.calls), 4, "A newly inspected candidate must deny immediately")

    def test_unclassified_id_disappearing_before_inspect_is_unknown(self):
        a, b = self.helper(self.a), self.helper(self.b)
        with self.assertRaisesRegex(ValueError, "inspect vanished"):
            self.collect([
                self.roster(a), self.inspection(a), self.roster(a, b),
                ValueError("inspect vanished"),
            ])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.b]])
        self.assertEqual(len(self.calls), 4)

    def test_continuous_unclassified_growth_denies_after_three_cycles(self):
        a, b, c, d = (self.helper(identity) for identity in
                      (self.a, self.b, self.c, self.d))
        with self.assertRaisesRegex(ValueError, "unclassified"):
            self.collect([
                self.roster(a), self.inspection(a), self.roster(a, b),
                self.inspection(b), self.roster(a, b, c),
                self.inspection(c), self.roster(a, b, c, d),
            ])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.b], [self.c]])
        self.assertEqual(len(self.calls), 7)

    def test_empty_roster_skips_inspection(self):
        self.assertEqual(self.collect([self.roster(), self.roster()]), [])
        self.assertEqual(self.inspected_ids(), [])
        self.assertEqual(len(self.calls), 2)

    def test_malformed_duplicate_or_incomplete_initial_roster_denies(self):
        a, b = self.helper(self.a), self.helper(self.b)
        same_name = {**b, "name": a["name"]}
        invalid = [
            "{invalid",
            self.roster(a, a),
            self.roster(a, same_name),
            json.dumps({"id": "invalid", "name": "helper"}),
            json.dumps({"id": self.a, "name": ""}),
            json.dumps({"id": self.a, "name": "helper", "extra": True}),
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect([raw])
            self.assertEqual(len(self.calls), 1)

    def test_final_roster_is_strictly_validated(self):
        a = self.helper(self.a)
        for raw in ("{invalid", self.roster(a, a)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect([self.roster(a), self.inspection(a), raw])
            self.assertEqual(len(self.calls), 3)

    def test_incomplete_duplicate_or_foreign_inspection_denies_immediately(self):
        a, b = self.helper(self.a), self.helper(self.b)
        for raw in ("", self.inspection(a, a), self.inspection(b)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect([self.roster(a), raw])
            self.assertEqual(len(self.calls), 2)

    def test_command_failure_or_malformed_inspection_is_not_retried(self):
        a = self.helper(self.a)
        for failure in (ValueError("docker failed"), "{invalid"):
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.collect([self.roster(a), failure])
            self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()

