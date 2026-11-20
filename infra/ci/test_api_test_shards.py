"""The API partition changes scheduling, never test membership or file order."""

import hashlib
import importlib.util
import json
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ApiShardTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).with_name("api-test-shards.py")
        self.assertTrue(path.is_file(), "API shard selector missing")
        spec = importlib.util.spec_from_file_location("api_shard_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_partition_is_exhaustive_disjoint_deterministic_and_keeps_old_pairs(self):
        module = self.module()
        for count in (*range(100), 121, 242, 363, 1001):
            names = [f"tests/test_{index:04d}.py" for index in range(count)]
            shuffled = list(names)
            random.Random(count).shuffle(shuffled)
            with self.subTest(count=count):
                groups = module.partition(shuffled, 6)
                self.assertEqual(groups, module.partition(names, 6))
                self.assertEqual(sorted(name for group in groups for name in group), names)
                self.assertLessEqual(max(map(len, groups)) - min(map(len, groups)), 1)
                old = module.partition(names, 3)
                for index in range(3):
                    self.assertEqual(sorted(groups[index] + groups[index + 3]), old[index])

    def test_invalid_partition_input_fails_instead_of_omitting_files(self):
        module = self.module()
        for names, count in ((["tests/test_a.py"] * 2, 6), ([], 0), ([], 65)):
            with self.subTest(names=names, count=count), self.assertRaises(ValueError):
                module.partition(names, count)

    def test_measured_schedule_balances_long_files_and_is_order_independent(self):
        module = self.module()
        weights = dict(zip("abcdef", (10, 9, 8, 7, 6, 5), strict=True))
        groups = module.partition(list(reversed(weights)), 2, weights)
        self.assertEqual(groups, module.partition(list(weights), 2, weights))
        self.assertEqual(sorted(name for group in groups for name in group), sorted(weights))
        self.assertEqual(sorted(sum(weights[name] for name in group) for group in groups), [22, 23])
        self.assertTrue(all(group == sorted(group) for group in groups))

    def test_unmeasured_and_zero_duration_files_are_never_dropped_or_empty_a_shard(self):
        module = self.module()
        names = list("abcdef")
        for weights in ({}, {name: 0 for name in names}, {"a": 9}):
            with self.subTest(weights=weights):
                groups = module.partition(names, 3, weights)
                module.verify_coverage(names, groups, names)
                self.assertTrue(all(groups))
        for weights in ({"missing": 1}, {"a": True}, {"a": -1}, {"a": float("inf")}, {"a": float("nan")}):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                module.partition(names, 3, weights)

    def test_only_matching_source_hashes_receive_measured_weights(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "tests").mkdir()
            names = [f"tests/test_{name}.py" for name in "abc"]
            for name in names:
                (root / name).write_bytes(b"current\r\n")
            data = {"schema": 1, "files": {
                names[0]: {"sha256": hashlib.sha256(b"current\n").hexdigest(), "seconds": 5},
                names[1]: {"sha256": hashlib.sha256(b"old\n").hexdigest(), "seconds": 500},
                "tests/test_removed.py": {"sha256": "a" * 64, "seconds": 700},
            }}
            path = root / "timings.json"
            self.assertIsNone(module.load_weights(path, root, names))
            path.write_text(json.dumps(data), encoding="utf-8")
            weights = module.load_weights(path, root, names)
            self.assertEqual(weights, {names[0]: 5})
            # Changed/new files still get scheduled; a removed source is not revived.
            module.verify_coverage(names, module.partition(names, 2, weights), names)
            data["files"][names[0]]["seconds"] = -1
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.load_weights(path, root, names)

    def test_coverage_guard_refuses_missing_duplicate_or_empty_shards_and_critical_paths(self):
        module = self.module()
        for groups, required in (([["a"], ["a"]], []), ([["a"], []], []),
                                 ([["a"], ["b"]], ["missing"]), ([["a"], ["b"]], ["a", "a"])):
            with self.subTest(groups=groups, required=required), self.assertRaises(ValueError):
                module.verify_coverage(["a", "b"], groups, required)

    def test_workflow_keeps_thirteen_jobs_and_requires_all_original_critical_files(self):
        module = self.module()
        repo = Path(__file__).resolve().parents[2]
        workflow = (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        job_names = re.findall(r"^  ([a-z][a-z-]+):$", workflow.split("jobs:\n", 1)[1], re.MULTILINE)
        self.assertEqual(set(job_names), {"gateway-tests", "api-release-gate", "api-suite",
                                         "orchestrator-release-gate", "web", "py-syntax",
                                         "image-build", "workflow-lint"})
        self.assertEqual(len(job_names), 8)
        self.assertIn("shard: [1, 2, 3, 4, 5, 6]", workflow)
        start = workflow.index("      - name: Verify release-critical files remain in the complete API suite")
        step = workflow[start:workflow.index("      - name:", start + 1)]
        self.assertIn("--shards 6 --verify-coverage --require-files", step)
        self.assertNotIn("pytest", step)
        required = re.findall(r"tests/test_[a-zA-Z0-9_]+\.py", step)
        metadata = json.loads(Path(__file__).with_name("api-test-timings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(required), 68)
        self.assertEqual(required, metadata["release_critical_files"])
        self.assertEqual(metadata["source_revision"], "597d9ffa3dae023a5b8b0860da1ec54b47af4dfd")
        self.assertEqual(metadata["workflow_run_id"], 44693844149)
        api = repo / "apps/api"
        names = sorted("tests/" + path.name for path in (api / "tests").glob("test_*.py"))
        weights = module.load_weights(Path(__file__).with_name("api-test-timings.json"), api, names)
        groups = module.partition(names, 6, weights)
        module.verify_coverage(names, groups, required)
        result = subprocess.run([sys.executable, str(Path(module.__file__)), "--shards", "6",
                                 "--verify-coverage", "--require-files", *required],
                                cwd=api, check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(result.stdout), {"status": "complete_api_shard_coverage",
                                                   "files": len(names), "shards": 6,
                                                   "required_files": 68})
        # The duplicate invocation adds no different API-level environment contract.
        gate = workflow.split("  api-release-gate:\n", 1)[1].split("  api-suite:\n", 1)[0]
        suite = workflow.split("  api-suite:\n", 1)[1].split("  orchestrator-release-gate:\n", 1)[0]
        def environment(section):
            return re.search(r"^    env:\n(.*?)^    services:", section,
                             re.MULTILINE | re.DOTALL).group(1)
        self.assertEqual(environment(gate), environment(suite))

    def test_actual_repository_manifest_matches_all_legacy_files_exactly_once(self):
        module = self.module()
        api = Path(__file__).resolve().parents[2] / "apps/api"
        expected = sorted("tests/" + path.name for path in (api / "tests").glob("test_*.py"))
        self.assertGreaterEqual(len(expected), 363)
        observed = []
        for shard in range(1, 7):
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(module.__file__).resolve()),
                    "--shards",
                    "6",
                    "--shard",
                    str(shard),
                ],
                cwd=api,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            observed.extend(result.stdout.splitlines())
        self.assertEqual(sorted(observed), expected)
        self.assertEqual(len(observed), len(set(observed)))


if __name__ == "__main__":
    unittest.main()
