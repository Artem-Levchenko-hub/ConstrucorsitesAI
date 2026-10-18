"""Atomic metadata classifies short helpers without hiding reader authority."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class DockerInventoryTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("generation-reader-inventory.py")
        spec = importlib.util.spec_from_file_location("docker_inventory_contract", source)
        self.observer = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: self.observer}):
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

    def opaque_dev(self, identity, **changes):
        return self.helper(identity, **{
            "configured_image": "sha256:" + "5" * 64,
            "command": ["node", "server.js"],
            **changes,
        })

    def reader(self, identity, role="worker", name=None, **changes):
        commands = {
            "api": ["python", "-m", "uvicorn", "yleum_api.main:app"],
            "worker": ["python", "-m", "yleum_api.workers.run"],
            "generation-worker": ["python", "-m", "yleum_api.workers.generation"],
            "orchestrator": ["uvicorn", "yleum_orchestrator.main:app"],
        }
        return self.helper(identity, name or "yleum-prod-" + role, **{
            "image": "sha256:" + "3" * 64,
            "configured_image": "omnia-api:" + "4" * 40,
            "service": role,
            "command": commands[role],
            **changes,
        })

    def summary(self, row, **changes):
        # Moby joins Path+Args, quoting arguments containing spaces. CLI --no-trunc
        # Go-quotes the full text. ASCII fixtures have the same outer quoting
        # with json.dumps. Never reconstruct inspect argv from this summary.
        command = " ".join(
            "'" + value + "'" if " " in value else value for value in row["command"]
        )
        return {
            "id": row["id"],
            "name": row["name"],
            "image_reference": row["configured_image"],
            "rendered_command": json.dumps(command),
            **changes,
        }

    def raw_census(self, *summaries):
        return "\n".join(json.dumps(row) for row in summaries)

    def census(self, *rows):
        return self.raw_census(*(self.summary(row) for row in rows))

    def inspection(self, *rows):
        output = []
        for original in rows:
            row = copy.deepcopy(original)
            row["name"] = "/" + row["name"]
            row["entrypoint"] = row.pop("command")
            row["cmd"] = []
            output.append(json.dumps(row))
        return "\n".join(output)

    def collect(self, outputs, *, restart=False):
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
            if argv[1] == "ps":
                self.assertEqual(argv[5], (
                    '{"id":{{json .ID}},"name":{{json .Names}},'
                    '"image_reference":{{json .Image}},"rendered_command":{{json .Command}}}'
                ))
            value = next(outputs)
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(self.observer, "command", side_effect=read_only):
            # Existing fixtures falsify one complete observation. The public
            # retry boundary has separate fixtures with multiple full censuses.
            observe = (self.observer.docker_inventory if restart
                       else self.observer._observe_docker_readers)
            result = observe()
        self.assertEqual(list(outputs), [], "An accepted census must consume its full fixture")
        return result

    def inspected_ids(self):
        return [call[4:] for call in self.calls if call[1] == "inspect"]

    def possible(self, summary):
        with patch.object(self.observer, "command", return_value=self.raw_census(summary)):
            parsed = self.observer._docker_container_census()[summary["id"]]
        return self.observer._possible_reader(parsed)

    def test_stable_complete_readers_are_freshly_reinspected(self):
        rows = [self.reader(self.a, "api"), self.reader(self.b, "worker"),
                self.reader(self.c, "generation-worker")]
        result = self.collect([
            self.census(*rows), self.inspection(*rows), self.inspection(*rows),
            self.census(*reversed(rows)),
        ])
        self.assertEqual(set(self.observer.classify_containers(result, "core")),
                         {"api", "worker", "generation-worker"})
        self.assertEqual(self.inspected_ids(),
                         [[self.a, self.b, self.c], [self.a, self.b, self.c]])

    def test_torn_opaque_inspect_restarts_from_complete_fresh_census(self):
        reader, helper = self.reader(self.a), self.opaque_dev(self.b)
        fresh = {**reader, "pid": 200}
        result = self.collect([
            self.census(reader, helper), ValueError("unknown candidate disappeared"),
            self.census(fresh), self.inspection(fresh), self.inspection(fresh),
            self.census(fresh),
        ], restart=True)
        self.assertEqual(result, [fresh])
        self.assertEqual(self.inspected_ids(), [[self.a, self.b], [self.a], [self.a]])

    def test_restart_discards_partial_inspection_and_checks_new_reader(self):
        reader, helper = self.reader(self.a), self.opaque_dev(self.b)
        foreign = self.reader(self.c, name="foreign-worker")
        result = self.collect([
            self.census(reader, helper), self.inspection(reader),
            self.census(reader, foreign), self.inspection(reader, foreign),
            self.inspection(reader, foreign), self.census(reader, foreign),
        ], restart=True)
        self.assertEqual({row["id"] for row in result}, {self.a, self.c})
        with self.assertRaises(ValueError):
            self.observer.classify_containers(result, "core")

    def test_continuously_unclassified_candidate_denies_after_bounded_full_attempts(self):
        helper = self.opaque_dev(self.a)
        with self.assertRaisesRegex(ValueError, "candidate disappeared"):
            self.collect([
                self.census(helper), ValueError("candidate disappeared"),
                self.census(helper), ValueError("candidate disappeared"),
                self.census(helper), ValueError("candidate disappeared"),
            ], restart=True)
        self.assertEqual([call[1] for call in self.calls], ["ps", "inspect"] * 3)

    def test_restart_cannot_certify_a_missing_required_reader(self):
        reader = self.reader(self.a)
        result = self.collect([
            self.census(reader), ValueError("reader disappeared"),
            self.census(), self.census(),
        ], restart=True)
        with self.assertRaises(ValueError):
            self.observer.classify_containers(result, "core")

    def test_restart_never_retries_permission_or_transport_exceptions(self):
        for error in (PermissionError("denied"), TimeoutError("unobserved")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.collect([error], restart=True)
            self.assertEqual(len(self.calls), 1)

    def test_named_helpers_are_classified_from_complete_atomic_metadata(self):
        a, b = self.helper(self.a), self.helper(self.b)
        self.assertEqual(self.collect([self.census(a), self.census(a, b)]), [])
        self.assertEqual(self.inspected_ids(), [])

    def test_short_helper_seen_in_closing_census_never_requires_later_inspect(self):
        reader, helper = self.reader(self.a), self.helper(self.b)
        # Helper disappears immediately after closing list: no helper inspect
        # output is supplied, so attempting the old detail read fails this test.
        result = self.collect([
            self.census(reader), self.inspection(reader), self.inspection(reader),
            self.census(reader, helper),
        ])
        self.assertEqual(result, [reader])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.a]])

    def test_observed_named_nonreader_can_disappear_before_detail_read(self):
        a, b = self.helper(self.a), self.helper(self.b)
        self.assertEqual(self.collect([self.census(a, b), self.census(b)]), [])
        self.assertEqual(self.inspected_ids(), [])

    def test_obvious_named_helper_churn_does_not_require_stable_whole_roster(self):
        a, b = self.helper(self.a), self.helper(self.b)
        self.assertEqual(self.collect([self.census(a), self.census(b)]), [])
        self.assertEqual(len(self.calls), 2)

    def test_inspected_opaque_dev_nonreader_can_disappear_during_pause(self):
        reader, dev = self.reader(self.a), self.opaque_dev(self.b)
        result = self.collect([
            self.census(reader, dev), self.inspection(reader, dev),
            self.inspection(reader), self.census(reader),
        ])
        self.assertEqual(result, [reader])
        self.assertEqual(self.inspected_ids(), [[self.a, self.b], [self.a]])

    def test_known_opaque_nonreader_can_rename_between_two_benign_names(self):
        old = self.opaque_dev(self.a, name="project-dev-before")
        renamed = {**old, "name": "project-dev-after"}
        self.assertEqual(self.collect([
            self.census(old), self.inspection(old), self.census(renamed),
        ]), [])
        self.assertEqual(self.inspected_ids(), [[self.a]])

    def test_new_opaque_nonreader_is_inspected_before_its_later_removal_is_accepted(self):
        reader, dev = self.reader(self.a), self.opaque_dev(self.b)
        result = self.collect([
            self.census(reader), self.inspection(reader), self.inspection(reader),
            self.census(reader, dev), self.inspection(dev), self.inspection(reader),
            self.census(reader),
        ])
        self.assertEqual(result, [reader])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.a], [self.b], [self.a]])

    def test_opaque_churn_keeps_actual_reader_metadata_fresh_each_cycle(self):
        reader, dev = self.reader(self.a), self.opaque_dev(self.b)
        fresh, newer = {**reader, "pid": 200}, {**reader, "pid": 300}
        result = self.collect([
            self.census(reader), self.inspection(reader), self.inspection(fresh),
            self.census(reader, dev), self.inspection(dev), self.inspection(newer),
            self.census(reader, dev, self.helper(self.c)),
        ])
        self.assertEqual(result, [newer])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.a], [self.b], [self.a]])

    def test_nonreader_rename_does_not_publish_fake_argv_or_stale_state(self):
        reader, old = self.reader(self.a), self.helper(self.b, name="source-read-before")
        renamed = {**old, "name": "source-read-after"}
        result = self.collect([
            self.census(reader, old), self.inspection(reader), self.inspection(reader),
            self.census(reader, renamed),
        ])
        self.assertEqual(result, [reader], "Only actual fresh reader details are returned")
        self.assertNotIn("rendered_command", result[0])
        self.assertNotIn("image_reference", result[0])

    def test_known_nonreader_rename_into_protected_prefix_denies(self):
        old = self.opaque_dev(self.a)
        renamed = {**old, "name": "yleum-prod-api-shadow"}
        with self.assertRaises(ValueError):
            self.collect([self.census(old), self.inspection(old), self.census(renamed)])

    def test_new_named_helper_rename_into_protected_prefix_requires_inspect_then_denies(self):
        old = self.helper(self.a)
        renamed = {**old, "name": "yleum-prod-api-shadow"}
        with self.assertRaises(ValueError):
            self.collect([self.census(old), self.census(renamed), self.inspection(renamed)])
        self.assertEqual(self.inspected_ids(), [[self.a]])

    def test_reader_rename_during_fresh_inspection_denies(self):
        old = self.reader(self.a)
        with self.assertRaises(ValueError):
            self.collect([self.census(old), self.inspection(old),
                          self.inspection({**old, "name": "renamed-reader"})])

    def test_reader_rename_after_fresh_inspection_denies(self):
        old = self.reader(self.a)
        with self.assertRaises(ValueError):
            self.collect([
                self.census(old), self.inspection(old), self.inspection(old),
                self.census({**old, "name": "renamed-reader"}),
            ])

    def test_initial_candidate_inspection_must_match_initial_names(self):
        old = self.reader(self.a)
        with self.assertRaises(ValueError):
            self.collect([self.census(old), self.inspection({**old, "name": "different"})])
        self.assertEqual(len(self.calls), 2)

    def test_dormant_restart_policy_is_fresh_and_still_rejected_by_classifier(self):
        old = self.reader(self.a, name="retired-worker", running=False, status="exited", pid=0)
        fresh = {**old, "restart": "always"}
        result = self.collect([
            self.census(old), self.inspection(old), self.inspection(fresh), self.census(old),
        ])
        self.assertEqual(result, [fresh])
        self.assertFalse(self.observer.retired_dormant(result[0]))
        self.assertEqual(self.observer.classify_containers([old], "commerce"), {})
        with self.assertRaises(ValueError):
            self.observer.classify_containers(result, "commerce")

    def test_unchanged_dormant_reader_remains_fully_observed(self):
        old = self.reader(self.a, name="retired-worker", running=False, status="exited", pid=0)
        result = self.collect([
            self.census(old), self.inspection(old), self.inspection(old), self.census(old),
        ])
        self.assertEqual(result, [old])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.a]])

    def test_removing_an_initial_reader_denies_even_if_it_was_dormant(self):
        old = self.reader(self.a, name="retired-worker", running=False, status="exited", pid=0)
        with self.assertRaises(ValueError):
            self.collect([
                self.census(old), self.inspection(old), self.inspection(old), self.census(),
            ])

    def test_new_reader_of_each_role_denies_without_filtering(self):
        for role in ("api", "worker", "generation-worker", "orchestrator"):
            with self.subTest(role=role):
                added = self.reader(self.b, role, name="unexpected-" + role,
                                    configured_image="controller-fixture:local")
                with self.assertRaises(ValueError):
                    self.collect([self.census(), self.census(added), self.inspection(added)])
                self.assertEqual(self.inspected_ids(), [[self.b]])

    def test_new_dormant_candidate_is_not_silently_accepted(self):
        added = self.reader(self.b, name="new-retired-worker", running=False,
                            status="exited", pid=0)
        with self.assertRaises(ValueError):
            self.collect([self.census(), self.census(added), self.inspection(added)])

    def test_new_api_image_alias_is_candidate_without_standard_entrypoint(self):
        added = self.helper(self.b, configured_image="omnia-api:" + "f" * 40)
        with self.assertRaises(ValueError):
            self.collect([self.census(), self.census(added), self.inspection(added)])

    def test_fresh_protected_name_cannot_be_hidden_by_preceding_census_name(self):
        old = self.opaque_dev(self.a)
        protected = {**old, "name": "yleum-prod-api-shadow"}
        with self.assertRaises(ValueError):
            self.collect([self.census(), self.census(old), self.inspection(protected)])
        self.assertEqual(len(self.calls), 3, "Fresh actual readers deny before another census")

    def test_unknown_opaque_id_disappearing_before_inspect_remains_unknown(self):
        dev = self.opaque_dev(self.a)
        with self.assertRaisesRegex(ValueError, "inspect vanished"):
            self.collect([self.census(), self.census(dev), ValueError("inspect vanished")])
        self.assertEqual(self.inspected_ids(), [[self.a]])
        self.assertEqual(len(self.calls), 3)

    def test_continuous_unclassified_growth_denies_after_three_cycles(self):
        a, b, c, d = (self.opaque_dev(identity) for identity in
                      (self.a, self.b, self.c, self.d))
        with self.assertRaisesRegex(ValueError, "unclassified"):
            self.collect([
                self.census(a), self.inspection(a), self.census(a, b),
                self.inspection(b), self.census(a, b, c),
                self.inspection(c), self.census(a, b, c, d),
            ])
        self.assertEqual(self.inspected_ids(), [[self.a], [self.b], [self.c]])
        self.assertEqual(len(self.calls), 7)

    def test_empty_census_skips_inspection(self):
        self.assertEqual(self.collect([self.census(), self.census()]), [])
        self.assertEqual(self.inspected_ids(), [])
        self.assertEqual(len(self.calls), 2)

    def test_malformed_duplicate_or_incomplete_initial_census_denies(self):
        a, b = self.summary(self.helper(self.a)), self.summary(self.helper(self.b))
        invalid = [
            "{invalid", self.raw_census(a, a), self.raw_census(a, {**b, "name": a["name"]}),
            self.raw_census({**a, "id": "invalid"}), self.raw_census({**a, "name": ""}),
            self.raw_census({**a, "extra": True}),
            self.raw_census({key: value for key, value in a.items() if key != "rendered_command"}),
            self.raw_census({**a, "rendered_command": ["sh"]}),
            self.raw_census({**a, "image_reference": None}), "[]", "null",
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect([raw])
            self.assertEqual(len(self.calls), 1)

    def test_closing_census_is_strictly_validated(self):
        a = self.helper(self.a)
        for raw in ("{invalid", self.census(a, a)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect([self.census(a), raw])
            self.assertEqual(len(self.calls), 2)

    def test_complete_census_above_container_limit_denies_before_inspection(self):
        base = self.summary(self.helper(self.a))
        raw = self.raw_census(*(
            {**base, "id": f"{index:064x}", "name": f"fixture-{index}"}
            for index in range(10001)
        ))
        with self.assertRaises(ValueError):
            self.collect([raw])
        self.assertEqual(len(self.calls), 1)

    def test_incomplete_duplicate_or_foreign_inspection_denies_immediately(self):
        a, b = self.reader(self.a), self.reader(self.b)
        for raw in ("", self.inspection(a, a), self.inspection(b)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.collect([self.census(a), raw])
            self.assertEqual(len(self.calls), 2)

    def test_command_failure_or_malformed_inspection_is_not_retried(self):
        a = self.reader(self.a)
        for failure in (ValueError("docker failed"), "{invalid"):
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.collect([self.census(a), failure])
            self.assertEqual(len(self.calls), 2)

    def test_census_command_failure_is_not_retried(self):
        with self.assertRaisesRegex(ValueError, "list failed"):
            self.collect([ValueError("list failed")])
        self.assertEqual(len(self.calls), 1)

    def test_image_only_api_candidate_survives_list_image_fallback_to_bare_digest(self):
        row = self.helper(self.a, configured_image="omnia-api:" + "f" * 40)
        fallback = self.raw_census(self.summary(row, image_reference=row["image"]))
        result = self.collect([
            fallback, self.inspection(row), self.inspection(row), fallback,
        ])
        self.assertEqual(result, [row])
        self.assertTrue(self.observer.is_reader_container(result[0]))

    def test_possible_reader_covers_all_roles_without_name_or_image_heuristics(self):
        for role in ("api", "worker", "generation-worker", "orchestrator"):
            row = self.reader(self.a, role, name="unrelated-name",
                              configured_image="controller-fixture:local")
            with self.subTest(role=role):
                self.assertTrue(self.observer.is_reader_container(row))
                self.assertTrue(self.possible(self.summary(row)))

    def test_possible_reader_uses_untruncated_namespace_even_far_after_twenty_characters(self):
        row = self.helper(self.a, command=["python", "-c", "x" * 100 + " yleum_api.main:app"])
        summary = self.summary(row)
        self.assertGreater(summary["rendered_command"].index("yleum_"), 100)
        self.assertTrue(self.observer.is_reader_container(row))
        self.assertTrue(self.possible(summary))

    def test_possible_predicate_never_shell_parses_formatted_python_c(self):
        rendered = "\"python -c 'unterminated yleum_unrelated\""
        self.assertTrue(self.possible(self.summary(self.helper(self.a),
                                                   rendered_command=rendered)))

    def test_namespace_false_positive_is_inspected_but_not_a_stable_actual_reader(self):
        row = self.helper(self.a, command=["echo", "yleum_unrelated"])
        self.assertFalse(self.observer.is_reader_container(row))
        self.assertTrue(self.possible(self.summary(row)))
        self.assertEqual(self.collect([
            self.census(row), self.inspection(row), self.census(),
        ]), [])

    def test_named_pinned_references_are_not_opaque_ids(self):
        images = [
            "alpine@sha256:" + "a" * 64,
            "registry/project-machine@sha256:" + "b" * 64,
            "registry.example:5000/team/project-machine:stable@sha256:" + "c" * 64,
        ]
        for image in images:
            with self.subTest(image=image):
                self.assertFalse(self.possible(self.summary(self.helper(self.a),
                                                            image_reference=image)))

    def test_bare_ids_unknown_image_and_empty_command_require_inspection(self):
        for image in ("sha256:" + "a" * 64, "a" * 64, "a" * 12,
                      "", "<no image>", "unknown image format", "sha512:" + "b" * 128):
            with self.subTest(image=image):
                self.assertTrue(self.possible(self.summary(self.helper(self.a),
                                                           image_reference=image)))
        for rendered in ("", '""', " "):
            with self.subTest(rendered=rendered):
                self.assertTrue(self.possible(self.summary(self.helper(self.a),
                                                           rendered_command=rendered)))

    def test_protected_names_and_api_image_are_possible_without_namespace_command(self):
        for name in ("yleum-prod-api-shadow", "omnia-prod-api-shadow", "yleum-prod-worker-old",
                     "omnia-prod-worker-old", "yleum-prod-generation-worker-old",
                     "omnia-prod-generation-worker-old"):
            with self.subTest(name=name):
                self.assertTrue(self.possible(self.summary(self.helper(self.a, name=name))))
        self.assertTrue(self.possible(self.summary(self.helper(self.a),
                                                   image_reference="omnia-api:fixture")))


if __name__ == "__main__":
    unittest.main()
