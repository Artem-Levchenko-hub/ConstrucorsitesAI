"""Complete fresh observations survive bounded churn; uncertainty never authorizes."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

import test_reader_docker_inventory_contract as fixtures


class DockerObservationFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DockerInventoryTests()
        self.fixture.setUp()
        self.observer = self.fixture.observer

    def test_short_unknown_container_churn_requires_a_complete_fresh_observation(self):
        f = self.fixture
        readers = [f.reader(f.a, "api"), f.reader(f.b),
                   f.reader(f.c, "generation-worker")]
        helper = f.opaque_dev(f.d)
        clock, sleeps, calls = [0.0], [], []

        def sleep(delay):
            self.assertGreater(delay, 0)
            clock[0] += delay
            sleeps.append(delay)

        def run(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 20)
            calls.append(argv)
            if argv[1] == "ps":
                rows = readers + ([helper] if clock[0] < 0.1 else [])
                return SimpleNamespace(returncode=0, stdout=f.census(*rows), stderr="")
            if f.d in argv[4:]:
                return SimpleNamespace(returncode=1, stdout=f.inspection(*readers),
                                       stderr="Error: No such object: " + f.d + "\n")
            return SimpleNamespace(returncode=0, stdout=f.inspection(*readers), stderr="")

        with patch.object(self.observer.subprocess, "run", side_effect=run), \
                patch.object(self.observer.time, "sleep", side_effect=sleep), \
                contextlib.redirect_stderr(io.StringIO()) as captured:
            result = self.observer.docker_inventory()
        self.assertEqual(self.observer.classify_containers(result, "core").keys(),
                         {"api", "worker", "generation-worker"})
        self.assertEqual(sleeps, [0.1])
        self.assertEqual([argv[1] for argv in calls],
                         ["ps", "inspect", "ps", "inspect", "inspect", "ps"])
        diagnostic = json.loads(captured.getvalue())["docker_observation_failure"]
        self.assertEqual(diagnostic["error_class"], "inspect_missing_object")
        self.assertEqual(diagnostic["stderr_sha256"], hashlib.sha256(
            ("Error: No such object: " + f.d + "\n").encode()).hexdigest())
        self.assertEqual(diagnostic["requested_ids_sha256"], self.observer.digest(
            sorted([f.a, f.b, f.c, f.d])))
        self.assertEqual(diagnostic["missing_ids_sha256"], self.observer.digest([f.d]))
        self.assertNotIn(f.d, captured.getvalue())

    def test_parser_failure_has_safe_diagnostic_without_raw_output(self):
        f = self.fixture
        raw = '{"private-synthetic-cookie": INVALID}'
        with self.assertRaises(ValueError):
            f.collect_real_command([raw] * 3)
        diagnostics = [json.loads(line)["docker_observation_failure"]
                       for line in f.command_stderr.getvalue().splitlines()]
        self.assertTrue(diagnostics)
        self.assertTrue(all(row["error_class"] == "invalid_json" for row in diagnostics))
        self.assertTrue(all(row["stdout_bytes"] == len(raw.encode()) for row in diagnostics))
        self.assertNotIn("private-synthetic-cookie", f.command_stderr.getvalue())

    def test_persistent_unknown_candidate_remains_denied_after_three_attempts(self):
        f = self.fixture
        reader, candidate = f.reader(f.a), f.opaque_dev(f.b)
        outputs = [f.census(reader, candidate),
                   (1, f.inspection(reader), "Error: No such object: " + f.b)] * 3
        with patch.object(self.observer.time, "sleep") as sleep, \
                self.assertRaises(self.observer.CommandObservationFailure):
            f.collect_real_command(outputs)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2])
        self.assertEqual([call[1] for call in f.calls], ["ps", "inspect"] * 3)
        self.assertEqual(len(f.command_stderr.getvalue().splitlines()), 3)

    def test_unclassified_permission_daemon_and_template_errors_never_gain_authority(self):
        f = self.fixture
        candidate = f.opaque_dev(f.a)
        cases = [
            ("private-synthetic-token", "unclassified_exit"),
            ("permission denied: private-synthetic-token", "permission_denied"),
            ("Cannot connect to the Docker daemon at private-path", "daemon_unavailable"),
            ("template parsing error: private-template", "inspect_template_error"),
        ]
        for stderr, category in cases:
            with self.subTest(category=category), \
                    patch.object(self.observer.time, "sleep") as sleep, \
                    self.assertRaises(self.observer.CommandObservationFailure) as raised:
                f.collect_real_command([f.census(candidate), (1, "", stderr)] * 3)
            sleep.assert_not_called()
            self.assertNotIn("private-", str(raised.exception))
            diagnostics = [json.loads(line)["docker_observation_failure"]
                           for line in f.command_stderr.getvalue().splitlines()]
            self.assertEqual([row["error_class"] for row in diagnostics], [category] * 3)
            self.assertTrue(all(row["missing_ids_sha256"] is None for row in diagnostics))
            self.assertNotIn("private-", f.command_stderr.getvalue())

    def test_missing_error_must_bind_all_lines_to_requested_full_ids(self):
        f = self.fixture
        argv = ["docker", "inspect", "--format", "private-template", f.a, f.b]
        good = [
            "Error: No such object: " + f.a,
            "Error: No such container: " + f.a,
            "Error response from daemon: No such container: " + f.a,
            "Error: No such object: " + f.a + "\nError: No such object: " + f.b,
        ]
        for stderr in good:
            with self.subTest(stderr=stderr):
                self.assertEqual(self.observer._docker_exit_details(argv, stderr)["error_class"],
                                 "inspect_missing_object")
        bad = ["", "Error: No such object: " + f.c,
               "Error: No such object: " + f.a[:12],
               "Error: No such object: " + f.a + "\nprivate-extra-error",
               "prefix Error: No such object: " + f.a]
        for stderr in bad:
            with self.subTest(stderr=stderr):
                result = self.observer._docker_exit_details(argv, stderr)
                self.assertEqual(result["error_class"], "unclassified_exit")
                self.assertIsNone(result["missing_ids_sha256"])
        self.assertEqual(self.observer._docker_exit_details(
            ["docker", "ps"], good[0])["error_class"], "unclassified_exit")

    def test_restart_checks_foreign_reader_and_never_reuses_partial_rows(self):
        f = self.fixture
        reader, candidate = f.reader(f.a), f.opaque_dev(f.b)
        foreign = f.reader(f.c, name="foreign-worker")
        with patch.object(self.observer.time, "sleep"):
            result = f.collect_real_command([
                f.census(reader, candidate),
                (1, f.inspection(reader), "Error: No such object: " + f.b),
                f.census(foreign), f.inspection(foreign),
                f.inspection(foreign), f.census(foreign),
            ])
        self.assertEqual(result, [foreign])
        with self.assertRaises(ValueError):
            self.observer.classify_containers(result, "core")

    def test_missing_required_reader_after_restart_still_denies(self):
        f = self.fixture
        reader = f.reader(f.a)
        with patch.object(self.observer.time, "sleep"):
            result = f.collect_real_command([
                f.census(reader), (1, "", "Error: No such object: " + f.a),
                f.census(), f.census(),
            ])
        with self.assertRaises(ValueError):
            self.observer.classify_containers(result, "core")

    def test_replacement_within_observation_remains_denied(self):
        f = self.fixture
        reader, replacement = f.reader(f.a), f.reader(f.b)
        with self.assertRaises(ValueError):
            f.collect([f.census(reader), f.inspection(reader), f.inspection(reader),
                       f.census(replacement), f.inspection(replacement)])

    def test_malformed_inspect_schema_is_diagnosed_and_never_partially_accepted(self):
        f = self.fixture
        reader = f.reader(f.a)
        for raw in ("[]", "null", '{"name":1}', f.inspection(reader, reader)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                f.collect_real_command([f.census(reader), raw] * 3)
            rows = [json.loads(line)["docker_observation_failure"]
                    for line in f.command_stderr.getvalue().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["error_class"] == "invalid_inventory" for row in rows))

    def test_remote_diagnostics_validate_all_new_hashes_categories_and_keys(self):
        f = self.fixture
        with self.assertRaises(ValueError):
            f.collect_real_command([f.census(f.opaque_dev(f.a)),
                                   (1, "", "Error: No such object: " + f.a)] * 3)
        raw = f.command_stderr.getvalue()
        self.assertEqual(len(self.observer.remote_docker_diagnostics(raw)), 3)
        row = json.loads(raw.splitlines()[0])["docker_observation_failure"]
        mutations = [
            {**row, "error_class": "private-error"},
            {**row, "stderr_sha256": "private-error"},
            {**row, "requested_ids_sha256": []},
            {**row, "missing_ids_sha256": None},
            {**row, "reason": "invalid_output", "returncode": 0},
            {**row, "raw_stderr": "private-error"},
            {**row, "error_class": "invalid_json"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertEqual(self.observer.remote_docker_diagnostics(json.dumps({
                    "docker_observation_failure": mutation,
                })), [])
        self.assertEqual(self.observer.remote_docker_diagnostics(raw + raw), [])

    def test_failed_scan_still_denies_before_waiting_for_other_collectors(self):
        sentinel = ValueError("unknown inventory")
        events = []
        failed, other = Future(), Future()
        failed.set_exception(sentinel)
        futures = iter([failed, other])
        pool = SimpleNamespace(submit=lambda *a, **kw: next(futures),
                               shutdown=lambda **kw: events.append("join"))
        # Preserve the production scan's exception/finally ordering; no live
        # Redis, SSH, Docker or publication is reachable from this fixture.
        with patch.object(self.observer, "redis_connection") as redis, \
                patch.object(self.observer, "registrations_now", return_value={}), \
                patch.object(self.observer, "ThreadPoolExecutor", return_value=pool), \
                patch.object(self.observer, "as_completed", return_value=[failed]), \
                patch.object(self.observer, "deny_snapshot", side_effect=lambda *a: events.append("deny")):
            redis.return_value.get.return_value = "12345678-1234-4234-8234-123456789abc"
            redis.return_value.info.return_value = {"run_id": "a" * 40}
            with self.assertRaises(ValueError) as raised:
                self.observer.scan({"host": "core", "release_sha": "b" * 40,
                                    "api_image": "sha256:" + "c" * 64, "ssh_user": "fixture"})
            self.assertIs(raised.exception, sentinel)
        self.assertEqual(events, ["deny", "join"])


if __name__ == "__main__":
    unittest.main()
