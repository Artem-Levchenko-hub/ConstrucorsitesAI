"""The API partition changes scheduling, never test membership or file order."""

import importlib.util
import random
import subprocess
import sys
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
